from collections import OrderedDict
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_bilibili.core.models import (
    DynamicParseResult, RenderPayload, SubscriptionRecord,
)
from astrbot_plugin_bilibili.services.dispatcher import SubscriptionNotificationDispatcher
from astrbot_plugin_bilibili.services.listener import DynamicListener


class DeliveryRetryTests(unittest.IsolatedAsyncioTestCase):
    def make_listener(self, mode="image"):
        listener = DynamicListener.__new__(DynamicListener)
        listener.node = False
        listener.rai = mode != "plain"
        listener.dynamic_limit = 5
        listener.render_cache = OrderedDict()
        listener.render_cache_limit = 32
        listener.plain_push_template = ""
        listener.plain_push_forward_template = ""
        listener.renderer = SimpleNamespace(
            notification_text=lambda *_: "New update",
            render_dynamic=AsyncMock(return_value=None if mode == "fallback" else "/tmp/mock.png"),
        )
        listener.context = SimpleNamespace(send_message=AsyncMock())
        listener.dispatcher = SubscriptionNotificationDispatcher(listener.context)
        listener._send_ai_summary = AsyncMock()
        listener._check_atall_permission = AsyncMock(return_value=False)
        listener._resolve_platform_name = lambda _: "aiocqhttp"
        listener.bili_client = SimpleNamespace(
            get_latest_dynamics=AsyncMock(return_value={"items": []}),
        )
        records = {}

        async def acknowledge(origin, uid, dynamic_id):
            records[origin].recent_ids.append(dynamic_id)
            records[origin].last = dynamic_id

        listener.data_manager = SimpleNamespace(update_last_dynamic_id=AsyncMock(side_effect=acknowledge))
        payload = RenderPayload(name="UP", type="DYNAMIC_TYPE_WORD", text="Update")
        listener._parse_and_filter_dynamics = lambda _, sub: [
            DynamicParseResult.deliver(payload, dyn_id)
            for dyn_id in ("102", "101") if dyn_id not in sub.recent_ids
        ]
        return listener, records

    async def test_failure_preserves_cursor_and_next_poll_delivers_in_order(self):
        for mode in ("image", "plain", "fallback"):
            with self.subTest(mode=mode):
                listener, records = self.make_listener(mode)
                origin = "test:GroupMessage:1"
                sub = records[origin] = SubscriptionRecord(uid=123, filter_types=["live"])
                listener.context.send_message.side_effect = TimeoutError("QQ send timed out")
                with patch("astrbot_plugin_bilibili.services.listener.is_height_valid", return_value=True):
                    await listener._run_uid_task(123, [(origin, sub)])
                    self.assertEqual(sub.recent_ids, [])
                    self.assertEqual(listener.context.send_message.await_count, 1)
                    listener._send_ai_summary.assert_not_awaited()
                    listener.context.send_message.side_effect = None
                    await listener._run_uid_task(123, [(origin, sub)])
                    self.assertEqual(sub.recent_ids, ["101", "102"])
                    self.assertEqual(sub.last, "102")
                    await listener._run_uid_task(123, [(origin, sub)])
                    self.assertEqual(listener.context.send_message.await_count, 3)

    async def test_one_failed_target_does_not_block_another_or_repeat_its_success(self):
        listener, records = self.make_listener("plain")
        first, second = "test:GroupMessage:1", "test:GroupMessage:2"
        targets = [(origin, records.setdefault(origin, SubscriptionRecord(uid=123, filter_types=["live"])))
                   for origin in (first, second)]
        fail_first = True

        async def send(origin, result):
            if origin == first and fail_first:
                raise TimeoutError("QQ send timed out")

        listener.context.send_message.side_effect = send
        await listener._run_uid_task(123, targets)
        self.assertEqual(records[first].recent_ids, [])
        self.assertEqual(records[second].recent_ids, ["101", "102"])
        fail_first = False
        await listener._run_uid_task(123, targets)
        self.assertEqual(records[first].recent_ids, ["101", "102"])
        self.assertEqual(sum(call.args[0] == second for call in listener.context.send_message.await_args_list), 2)

    async def test_explicit_reconnect_silence_still_discards_backlog(self):
        listener, records = self.make_listener("plain")
        origin = "test:GroupMessage:1"
        sub = records[origin] = SubscriptionRecord(uid=123, filter_types=["live"])
        listener.dispatcher.set_silent_until_ts(9999999999)
        await listener._run_uid_task(123, [(origin, sub)])
        self.assertEqual(sub.recent_ids, ["101", "102"])
        listener.context.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
