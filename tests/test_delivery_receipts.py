import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from astrbot.api.message_components import Plain
from astrbot_plugin_bilibili.services.dispatcher import (
    SubscriptionNotification, SubscriptionNotificationDispatcher,
)


class ReceiptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 1000.0
        self.clock = patch("astrbot_plugin_bilibili.services.dispatcher.time.time", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "receipts.json"
        self.url = "https://www.bilibili.com/opus/1234567890123456789"
        self.notice = SubscriptionNotification(
            sub_user="napcat:GroupMessage:42", dyn_id="1234567890123456789",
            chain_parts=[Plain(self.url)], meta={"source_url": self.url},
        )
        self.history = [self.message(time=900, sender={"user_id": 123})]
        self.calls = []
        self.history_error = None

        async def action(name, **params):
            self.calls.append((name, params))
            if name == "get_login_info":
                return {"user_id": 999}
            if self.history_error:
                raise self.history_error
            return {"messages": self.history}

        self.client = SimpleNamespace(call_action=AsyncMock(side_effect=action))
        self.platform = SimpleNamespace(meta=lambda: SimpleNamespace(name="aiocqhttp"), get_client=lambda: self.client)
        self.context = SimpleNamespace(send_message=AsyncMock(return_value=True), get_platform_inst=lambda _: self.platform)
        self.hook = AsyncMock()

    def dispatcher(self):
        return SubscriptionNotificationDispatcher(self.context, self.hook, state_path=self.path)

    def message(self, **overrides):
        return {"sender": {"user_id": 999}, "group_id": 42, "time": 1000,
                "real_seq": "88", "message_id": 80, "message_seq": 80,
                "message": [{"type": "text", "data": {"text": self.url}}], **overrides}

    async def uncertain(self, dispatcher):
        self.context.send_message.side_effect = TimeoutError("NTEvent sendMsg timeout")
        result = await dispatcher.publish(self.notice)
        self.assertFalse(result.sent)
        self.assertTrue(self.path.exists())
        self.context.send_message.side_effect = None

    async def test_timeout_with_delivery_evidence_is_success_without_resend(self):
        self.history = [self.message()]
        self.context.send_message.side_effect = TimeoutError("sendMsg timeout")
        dispatcher = self.dispatcher()
        result = await dispatcher.publish(self.notice)
        self.assertTrue(result.sent)
        self.assertEqual(result.reason, "history_confirmed")
        await dispatcher.publish(self.notice)
        self.assertEqual(self.context.send_message.await_count, 1)
        self.hook.assert_awaited_once()

    async def test_late_delivery_is_found_after_restart(self):
        await self.uncertain(self.dispatcher())
        self.now += 300
        self.history = [self.message(time=1010)]
        result = await self.dispatcher().publish(self.notice)
        self.assertTrue(result.sent)
        self.assertEqual(self.context.send_message.await_count, 1)

    async def test_verified_absence_retries_after_grace_and_only_once(self):
        dispatcher = self.dispatcher()
        await self.uncertain(dispatcher)
        self.assertFalse((await dispatcher.publish(self.notice)).sent)
        self.assertEqual(self.context.send_message.await_count, 1)
        self.now += 31
        self.assertTrue((await dispatcher.publish(self.notice)).sent)
        await dispatcher.publish(self.notice)
        self.assertEqual(self.context.send_message.await_count, 2)

    async def test_user_old_other_group_or_different_link_does_not_confirm(self):
        variants = [
            self.message(sender={"user_id": 123}), self.message(time=900),
            self.message(group_id=777),
            self.message(raw_message="https://www.bilibili.com/opus/12345678901234567890", message=[]),
        ]
        for i, message in enumerate(variants):
            with self.subTest(i=i):
                self.path = Path(self.folder.name) / f"receipts-{i}.json"
                self.now = 1000
                self.history = [message, self.message(time=900, sender={"user_id": 123})]
                self.context.send_message.reset_mock()
                dispatcher = self.dispatcher()
                await self.uncertain(dispatcher)
                self.now += 31
                self.assertTrue((await dispatcher.publish(self.notice)).sent)
                self.assertEqual(self.context.send_message.await_count, 2)

    async def test_pending_local_message_does_not_confirm_or_allow_resend(self):
        dispatcher = self.dispatcher()
        await self.uncertain(dispatcher)
        self.now += 300
        self.history = [self.message(real_seq="0")]
        self.assertFalse((await dispatcher.publish(self.notice)).sent)
        self.assertEqual(self.context.send_message.await_count, 1)

    async def test_history_failure_defers_instead_of_blind_resend(self):
        dispatcher = self.dispatcher()
        await self.uncertain(dispatcher)
        self.now += 300
        self.history_error = TimeoutError("history timeout")
        self.assertFalse((await dispatcher.publish(self.notice)).sent)
        self.assertEqual(self.context.send_message.await_count, 1)

    async def test_busy_group_requires_history_coverage(self):
        dispatcher = self.dispatcher()
        await self.uncertain(dispatcher)
        self.now += 300
        page = [self.message(time=1100 + n, message_id=n + 1, message_seq=n + 1,
                             sender={"user_id": 123}) for n in range(50)]
        self.history = page
        self.assertFalse((await dispatcher.publish(self.notice)).sent)
        self.assertEqual(self.context.send_message.await_count, 1)
        self.assertTrue(any("message_seq" in params for _, params in self.calls))

    async def test_empty_or_short_incomplete_history_does_not_allow_retry(self):
        dispatcher = self.dispatcher()
        await self.uncertain(dispatcher)
        self.now += 300
        for history in ([], [self.message(time=1100, sender={"user_id": 123})]):
            self.history = history
            self.assertFalse((await dispatcher.publish(self.notice)).sent)
        self.assertEqual(self.context.send_message.await_count, 1)

    async def test_second_history_page_can_confirm_late_delivery(self):
        dispatcher = self.dispatcher()
        await self.uncertain(dispatcher)
        self.now += 300
        original = self.client.call_action.side_effect

        async def action(name, **params):
            if name == "get_group_msg_history":
                if params.get("message_seq"):
                    return {"data": {"messages": [self.message()]}, "status": "ok"}
                return {"messages": [self.message(time=1100, sender={"user_id": 123})]}
            return await original(name, **params)

        self.client.call_action.side_effect = action
        self.assertTrue((await dispatcher.publish(self.notice)).sent)
        self.assertEqual(self.context.send_message.await_count, 1)

    async def test_full_polling_path_acknowledges_recovered_delivery(self):
        from test_delivery_retry import DeliveryRetryTests
        from astrbot_plugin_bilibili.core.models import DynamicParseResult, RenderPayload, SubscriptionRecord
        listener, records = DeliveryRetryTests().make_listener("plain")
        listener.context = self.context
        listener.dispatcher = self.dispatcher()
        origin = self.notice.sub_user
        sub = records[origin] = SubscriptionRecord(uid=123, filter_types=["live"])
        payload = RenderPayload(name="UP", type="DYNAMIC_TYPE_WORD", text="Update", url=self.url)
        listener._parse_and_filter_dynamics = lambda _, record: (
            [] if record.last == self.notice.dyn_id else [DynamicParseResult.deliver(payload, self.notice.dyn_id)])
        self.context.send_message.side_effect = TimeoutError("sendMsg timeout")
        await listener._run_uid_task(123, [(origin, sub)])
        self.assertEqual(sub.last, "")
        self.now += 300
        self.context.send_message.side_effect = None
        self.history = [self.message(time=1010)]
        await listener._run_uid_task(123, [(origin, sub)])
        self.assertEqual(sub.last, self.notice.dyn_id)
        await listener._run_uid_task(123, [(origin, sub)])
        self.assertEqual(self.context.send_message.await_count, 1)

    async def test_failed_history_envelope_does_not_allow_retry(self):
        dispatcher = self.dispatcher()
        await self.uncertain(dispatcher)
        self.now += 300
        self.client.call_action.side_effect = None
        self.client.call_action.return_value = {"status": "failed", "retcode": 1200}
        self.assertFalse((await dispatcher.publish(self.notice)).sent)
        self.assertEqual(self.context.send_message.await_count, 1)

    async def test_forward_card_and_equivalent_dynamic_url_are_recognized(self):
        self.notice = SubscriptionNotification(**{**self.notice.__dict__, "send_node": True})
        dispatcher = self.dispatcher()
        await self.uncertain(dispatcher)
        self.now += 31
        self.history = [self.message(message=[{"type": "forward", "data": {"id": "forward-1"}}])]
        original = self.client.call_action.side_effect

        async def action(name, **params):
            if name == "get_forward_msg":
                return {"messages": [{"content": [{"type": "text", "data": {"text":
                    "Update\nhttps://t.bilibili.com/1234567890123456789"}}]}]}
            return await original(name, **params)

        self.client.call_action.side_effect = action
        self.assertTrue((await dispatcher.publish(self.notice)).sent)
        self.assertEqual(self.context.send_message.await_count, 1)

    async def test_private_history_uses_private_target(self):
        self.notice = SubscriptionNotification(**{**self.notice.__dict__, "sub_user": "napcat:FriendMessage:321"})
        dispatcher = self.dispatcher()
        await self.uncertain(dispatcher)
        self.now += 31
        self.history = [self.message()]
        self.assertTrue((await dispatcher.publish(self.notice)).sent)
        self.assertTrue(any(name == "get_friend_msg_history" and params["user_id"] == 321 for name, params in self.calls))
        self.assertTrue(all(params.get("disable_get_url") is True and params.get("parse_mult_msg") is False
                            for name, params in self.calls if name == "get_friend_msg_history"))

    async def test_cancellation_during_send_survives_restart(self):
        self.context.send_message.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.dispatcher().publish(self.notice)
        self.context.send_message.side_effect = None
        self.history = [self.message()]
        self.now += 31
        self.assertTrue((await self.dispatcher().publish(self.notice)).sent)
        self.assertEqual(self.context.send_message.await_count, 1)

    async def test_concurrent_publishes_and_hook_failure_do_not_duplicate(self):
        dispatcher = self.dispatcher()
        self.hook.side_effect = RuntimeError("callback persistence failed")
        results = await asyncio.gather(dispatcher.publish(self.notice), dispatcher.publish(self.notice))
        self.assertTrue(all(result.sent for result in results))
        self.assertEqual(self.context.send_message.await_count, 1)

    async def test_summary_is_not_deduplicated_against_the_main_card(self):
        dispatcher = self.dispatcher()
        await dispatcher.publish(self.notice)
        summary = SubscriptionNotification(sub_user=self.notice.sub_user, dyn_id=self.notice.dyn_id,
                                           chain_parts=[Plain("AI summary")])
        await dispatcher.publish(summary)
        self.assertEqual(self.context.send_message.await_count, 2)


if __name__ == "__main__":
    unittest.main()
