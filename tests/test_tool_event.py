import asyncio
import tempfile
import unittest

from test_ai_entry import Event, plugin
from astrbot_plugin_mahjong_helper.tool_event import ToolEvent


class ToolEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_stop_does_not_cancel_a_pending_ai_image_send(self):
        for section in ('fu', 'all', 'quiz'):
            with self.subTest(section=section), tempfile.TemporaryDirectory() as directory:
                p = plugin(directory)
                event = Event(text='发送菜单', message_id=section)
                event.set_extra('mahjong_ai_request', True)
                original_send = event.send

                async def slow_send(result):
                    await asyncio.sleep(0.03)
                    self.assertFalse(event.stopped)
                    await original_send(result)

                event.send = slow_send
                await p.menu_ai(event, section=section)
                self.assertFalse(event.stopped)
                self.assertTrue(event.sent)
                self.assertTrue(event.get_extra('mahjong_ai_tool_used'))

    async def test_native_fu_command_still_stops_propagation(self):
        with tempfile.TemporaryDirectory() as directory:
            p = plugin(directory)
            event = Event()
            results = [r async for r in p.fu_reference(event)]
            self.assertTrue(event.stopped)
            self.assertEqual(len(results[0].chain), 2)

    def test_platform_type_permissions_and_writes_stay_on_original_event(self):
        original = Event()
        view = ToolEvent(original)
        self.assertIsInstance(view, Event)
        self.assertEqual(view.get_sender_id(), original.get_sender_id())
        view.role = 'admin'
        view.set_extra('test', True)
        self.assertEqual(original.role, 'admin')
        self.assertTrue(original.get_extra('test'))
        view.stop_event()
        self.assertFalse(original.stopped)
        original.stop_event()
        self.assertTrue(view.stopped)  # genuine framework/user stop is preserved

    async def test_cancelled_send_is_not_shielded_or_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            p = plugin(directory)
            event = Event()
            event.set_extra('mahjong_ai_request', True)
            entered = asyncio.Event()

            async def pending_send(result):
                entered.set()
                await asyncio.Future()

            event.send = pending_send
            task = asyncio.create_task(p.menu_ai(event, section='fu'))
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(event.sent, [])
