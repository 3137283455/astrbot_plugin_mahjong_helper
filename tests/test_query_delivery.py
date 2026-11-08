import asyncio
import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import astrbot.api.message_components as C
from astrbot.core.provider.entities import LLMResponse
from astrbot_plugin_mahjong_helper.query_delivery import send_query_result
from test_ai_entry import Event, plugin


class QueryDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_delivered_text_timeout_does_not_add_failure_or_model_chatter(self):
        with tempfile.TemporaryDirectory() as folder:
            p = plugin(folder)
            event = Event()
            event.set_extra('mahjong_ai_request', True)
            async def view(event, query):
                yield event.plain_result('四麻、三麻都没有查到战绩数据。')
            p._query_player_view = view
            event.send = AsyncMock(side_effect=TimeoutError())
            event.bot = SimpleNamespace(call_action=AsyncMock(return_value={'messages': [{
                'user_id': 'bot', 'real_seq': 10, 'time': time.time(),
                'message': [{'type': 'text', 'data': {'text': '四麻、三麻都没有查到战绩数据。'}}],
            }]}))
            event.get_group_id = lambda: '1036516712'
            with patch('astrbot_plugin_mahjong_helper.query_delivery.asyncio.sleep', new=AsyncMock()):
                await p.query_ai(event, player='18183')
            self.assertEqual(event.send.await_count, 1)
            response = LLMResponse(role='assistant', completion_text='卡片发送失败，要重试吗？')
            await p.ai_no_chat_response(event, response)
            self.assertEqual(response.completion_text, '')

    async def test_image_retries_original_object_only(self):
        event = Event()
        result = event.chain_result([C.Image.fromFileSystem('/unavailable-test.png')])
        event.send = AsyncMock(side_effect=[TimeoutError(), TimeoutError(), None])
        with patch('astrbot_plugin_mahjong_helper.query_delivery.asyncio.sleep', new=AsyncMock()), \
                patch('astrbot_plugin_mahjong_helper.query_delivery.history_receipt', new=AsyncMock(return_value=False)):
            self.assertTrue(await send_query_result(event, result))
        self.assertEqual(event.send.await_count, 3)
        self.assertTrue(all(call.args[0] is result for call in event.send.await_args_list))

    async def test_history_checks_sender_sequence_time_and_image(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'card.png'
            path.write_bytes(b'test-image')
            digest = hashlib.md5(path.read_bytes()).hexdigest()
            event = Event()
            event.get_group_id = lambda: '1036516712'
            result = event.chain_result([C.Image.fromFileSystem(str(path))])
            event.send = AsyncMock(side_effect=TimeoutError())
            good = {'user_id': 'bot', 'real_seq': 9, 'time': time.time(),
                    'message': [{'type': 'image', 'data': {'file': digest.upper()+'.png'}}]}
            event.bot = SimpleNamespace(call_action=AsyncMock(return_value={'messages': [good]}))
            with patch('astrbot_plugin_mahjong_helper.query_delivery.asyncio.sleep', new=AsyncMock()):
                self.assertTrue(await send_query_result(event, result))
            self.assertEqual(event.send.await_count, 1)

    async def test_tool_exception_before_completion_hook_still_suppresses_model_reply(self):
        with tempfile.TemporaryDirectory() as folder:
            p = plugin(folder)
            event = Event()
            event.set_extra('mahjong_ai_request', True)
            self.assertTrue(p._claim_ai_action(event))
            response = LLMResponse(role='assistant', completion_text='发送超时，再试一次吗？')
            await p.ai_no_chat_response(event, response)
            self.assertEqual(response.completion_text, '')
