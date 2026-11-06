from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from inspect import isawaitable
import json
from pathlib import Path
import re
import time
from typing import Any, Awaitable, Callable, Literal, Optional
from urllib.parse import urlsplit

from astrbot.api import logger
from astrbot.api.event import MessageEventResult
from astrbot.api.message_components import Node

from .delivery_receipts import DeliveryReceipts

NotificationCategory = Literal["dynamic", "live"]
SentHook = Callable[["SubscriptionNotification"], None | Awaitable[None]]


@dataclass(frozen=True)
class SubscriptionNotification:
    sub_user: str
    chain_parts: list[Any]
    send_node: bool = False
    category: NotificationCategory = "dynamic"
    dyn_id: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DispatchResult:
    sent: bool
    dropped: bool = False
    reason: str = ""


class SubscriptionNotificationDispatcher:
    def __init__(
        self,
        context: Any,
        on_sent: Optional[SentHook] = None,
        state_path: Path | None = None,
    ):
        self.context = context
        self.on_sent = on_sent
        self.silent_until_ts = 0
        self.receipts = DeliveryReceipts(state_path)
        self._delivery_lock = asyncio.Lock()
        self._self_ids: dict[str, str] = {}

    async def publish(self, notification: SubscriptionNotification) -> DispatchResult:
        if self._is_silent(notification):
            return DispatchResult(sent=False, dropped=True, reason="silent_mode")

        key = self._receipt_key(notification)
        platform_lookup = getattr(self.context, "get_platform_inst", None)
        platform = platform_lookup(notification.sub_user.split(":", 1)[0]) if callable(platform_lookup) else None
        if platform is None or platform.meta().name != "aiocqhttp":
            key = None
        # Only automatic dynamic cards have a stable public URL to reconcile.
        if key is None:
            return await self._dispatch(notification)
        async with self._delivery_lock:
            record = self.receipts.records.get(key)
            if record is not None:
                if record.get("status") == "confirmed":
                    return DispatchResult(sent=True, reason="already_delivered")
                evidence = await self._history_delivery(notification, record)
                if evidence == "found":
                    self.receipts.confirm(key, record)
                    await self._on_sent(notification)
                    logger.info("Subscription delivery confirmed from history: target=%s dynamic=%s",
                                notification.sub_user, notification.dyn_id)
                    return DispatchResult(sent=True, reason="history_confirmed")
                elapsed = time.time() - record["last_attempt"]
                delay = min(30 * 2 ** min(record.get("attempts", 1) - 1, 5), 900)
                if evidence != "missing" or elapsed < delay:
                    logger.warning("Subscription retry deferred: target=%s dynamic=%s evidence=%s",
                                   notification.sub_user, notification.dyn_id, evidence)
                    return DispatchResult(sent=False, reason="delivery_unconfirmed")
            now = time.time()
            record = {
                "status": "pending", "first_attempt": (record or {}).get("first_attempt", now),
                "last_attempt": now, "attempts": (record or {}).get("attempts", 0) + 1,
                "updated_at": now,
            }
            # Write before sending so a cancelled task cannot cause a blind retry.
            self.receipts.put(key, record)
            result = await self._dispatch(notification, run_hook=False)
            if result.sent:
                self.receipts.confirm(key, record)
                await self._on_sent(notification)
                return result
            evidence = await self._history_delivery(notification, record)
            if evidence == "found":
                self.receipts.confirm(key, record)
                await self._on_sent(notification)
                return DispatchResult(sent=True, reason="history_confirmed")
            return DispatchResult(sent=False, reason="delivery_unconfirmed")

    @staticmethod
    def _receipt_key(notification: SubscriptionNotification) -> str | None:
        if (notification.category != "dynamic" or not notification.dyn_id
                or not notification.meta.get("source_url") or notification.meta.get("summary")):
            return None
        return json.dumps([notification.sub_user, notification.category, notification.dyn_id])

    async def _dispatch(self, notification: SubscriptionNotification, run_hook: bool = True) -> DispatchResult:

        result = self._build_event_result(notification)
        try:
            matched = await self.context.send_message(notification.sub_user, result)
            if matched is False:
                raise RuntimeError("Notification platform was not found")
        except Exception as e:
            logger.error(
                f"发送订阅通知失败: sub_user={notification.sub_user} "
                f"category={notification.category} dyn_id={notification.dyn_id} "
                f"error={e}"
            )
            return DispatchResult(sent=False, reason=str(e))
        if run_hook:
            await self._on_sent(notification)
        return DispatchResult(sent=True)

    @staticmethod
    def _action_data(value: Any) -> dict:
        if not isinstance(value, dict) or value.get("status") == "failed":
            raise ValueError("History API did not return a successful object")
        data = value.get("data", value)
        if not isinstance(data, dict):
            raise ValueError("History API returned an invalid payload")
        return data

    @staticmethod
    def _message_text(message: dict) -> str:
        texts = [str(message.get("raw_message", ""))]
        for part in message.get("message", []) or []:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(str((part.get("data") or {}).get("text", "")))
        return "\n".join(texts)

    @staticmethod
    def _forward_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n".join(SubscriptionNotificationDispatcher._forward_text(item) for item in value)
        if isinstance(value, dict):
            return "\n".join(SubscriptionNotificationDispatcher._forward_text(item)
                             for key, item in value.items()
                             if key in {"text", "raw_message", "message", "messages", "content", "data"})
        return ""

    @staticmethod
    def _same_dynamic(text: str, notification: SubscriptionNotification) -> bool:
        expected = urlsplit(notification.meta["source_url"])
        for raw in re.findall(r"https?://[^\s\[\]<>\"']+", text):
            parsed = urlsplit(raw.rstrip(".,，。)"))
            host = (parsed.hostname or "").removeprefix("www.")
            if host not in {"bilibili.com", "t.bilibili.com"}:
                continue
            if (host == (expected.hostname or "").removeprefix("www.")
                    and parsed.path.rstrip("/") == expected.path.rstrip("/")):
                return True
            if re.fullmatch(r"/(?:opus/|dynamic/)?" + re.escape(str(notification.dyn_id)) + r"/?", parsed.path):
                return True
        return False

    async def _history_delivery(self, notification: SubscriptionNotification, record: dict) -> str:
        """Return found/missing/unknown; incomplete history never permits a resend."""
        try:
            async with asyncio.timeout(12):
                return await self._read_delivery_history(notification, record)
        except Exception as exc:
            logger.warning("Subscription history check unavailable: target=%s dynamic=%s error_type=%s",
                           notification.sub_user, notification.dyn_id, type(exc).__name__)
            return "unknown"

    async def _read_delivery_history(self, notification: SubscriptionNotification, record: dict) -> str:
        platform_id, kind, target = notification.sub_user.split(":", 2)
        platform = self.context.get_platform_inst(platform_id)
        if platform is None or platform.meta().name != "aiocqhttp":
            return "unknown"
        client = platform.get_client()
        if platform_id not in self._self_ids:
            login = self._action_data(await client.call_action("get_login_info"))
            self._self_ids[platform_id] = str(login["user_id"])
        is_group = kind == "GroupMessage"
        if not is_group and kind != "FriendMessage":
            return "unknown"
        action = "get_group_msg_history" if is_group else "get_friend_msg_history"
        target = target.split("_")[-1] if is_group else target
        params = {
            "group_id" if is_group else "user_id": int(target), "count": 50,
            "disable_get_url": True, "parse_mult_msg": False,
        }
        first_attempt = record["first_attempt"] - 2
        observed_pending = False
        cursors = set()
        for _ in range(3):
            history = self._action_data(await client.call_action(action, **params))
            messages = history.get("messages")
            if not isinstance(messages, list):
                return "unknown"
            for message in messages:
                if not isinstance(message, dict):
                    return "unknown"
                stamp = int(message.get("time", 0))
                sender = str((message.get("sender") or {}).get("user_id", message.get("user_id", "")))
                if stamp < first_attempt or sender != self._self_ids[platform_id]:
                    continue
                if is_group and str(message.get("group_id", target)) != target:
                    continue
                text = self._message_text(message)
                if notification.send_node:
                    for part in message.get("message", []) or []:
                        if isinstance(part, dict) and part.get("type") == "forward":
                            forward_id = (part.get("data") or {}).get("id")
                            if forward_id:
                                forward = self._action_data(await client.call_action("get_forward_msg", message_id=str(forward_id)))
                                text += "\n" + self._forward_text(forward)
                if self._same_dynamic(text, notification):
                    # NapCat exposes the server sequence as real_seq. A local
                    # pending message without it is not delivery evidence.
                    if int(message.get("real_seq", 0)) > 0:
                        return "found"
                    observed_pending = True
            times = [int(m.get("time", 0)) for m in messages]
            if messages and (not all(times) or max(times) > time.time() + 60):
                return "unknown"
            if not messages:
                return "unknown"
            if min(times) <= first_attempt:
                return "unknown" if observed_pending else "missing"
            oldest = min(messages, key=lambda m: int(m["time"]))
            cursor = oldest.get("message_seq") or oldest.get("message_id")
            if not cursor or cursor in cursors:
                return "unknown"
            cursors.add(cursor)
            params["message_seq"] = cursor
        return "unknown"

    def set_silent_until_ts(self, silent_until_ts: int) -> None:
        self.silent_until_ts = max(int(silent_until_ts), 0)

    async def _on_sent(self, notification: SubscriptionNotification) -> None:
        hook = self.on_sent
        if hook is None:
            return
        try:
            result = hook(notification)
            if isawaitable(result):
                await result
        except Exception:
            # A bookkeeping error must not resend a confirmed message.
            logger.exception("Subscription delivery success hook failed")

    def _is_silent(self, notification: SubscriptionNotification) -> bool:
        if notification.category not in ("dynamic", "live"):
            return False
        now_ts = max(int(time.time()), 0)
        if now_ts >= self.silent_until_ts:
            return False
        logger.info(
            f"订阅通知被静默丢弃: sub_user={notification.sub_user} category={notification.category} dyn_id={notification.dyn_id}"
        )
        return True

    @staticmethod
    def _build_event_result(
        notification: SubscriptionNotification,
    ) -> MessageEventResult:
        if notification.send_node:
            qq_node = Node(
                uin=0,
                name="AstrBot",
                content=notification.chain_parts,
            )
            return MessageEventResult(chain=[qq_node])
        return MessageEventResult(chain=notification.chain_parts).use_t2i(False)
