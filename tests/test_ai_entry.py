import asyncio
import json
import sys
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.star.filter.command import CommandFilter
from mcp.types import CallToolResult, TextContent

from astrbot_plugin_mahjong_helper.ai_entry import AI_TOOLS, eligible, modelscope_provider
from astrbot_plugin_mahjong_helper.database import MahjongDatabase
from astrbot_plugin_mahjong_helper.main import MahjongHelperPlugin
from astrbot_plugin_mahjong_helper.nanikiru_core import QuestionStore, StateStore


class Event:
    def __init__(self, text="来道何切题", mention="bot", group="group", message_id="1"):
        self.message_str = text
        self.parts = [Comp.Plain(text)]
        if mention:
            self.parts.insert(0, Comp.At(qq=mention))
        self.group = group
        self.message_obj = SimpleNamespace(message_id=message_id)
        self.unified_msg_origin = "napcat:GroupMessage:" + str(group) if group else "napcat:FriendMessage:user"
        self.extras = {}
        self._has_send_oper = False
        self.stopped = False
        self.sent = []
        self.result = None
        self.role = "member"
        self.call_llm = False

    def get_platform_name(self): return "aiocqhttp"
    def get_group_id(self): return self.group
    def is_private_chat(self): return not self.group
    def get_sender_id(self): return "user"
    def get_self_id(self): return "bot"
    def get_messages(self): return self.parts
    def get_extra(self, key, default=None): return self.extras.get(key, default)
    def set_extra(self, key, value): self.extras[key] = value
    def should_call_llm(self, value): self.call_llm = value
    def stop_event(self): self.stopped = True
    def get_result(self): return self.result
    def plain_result(self, text): return MessageEventResult().message(text)
    def chain_result(self, chain): return MessageEventResult(chain=chain)

    async def send(self, chain):
        self.sent.append(chain)
        self._has_send_oper = True


def plugin(directory):
    p = MahjongHelperPlugin.__new__(MahjongHelperPlugin)
    p.config = {"ai_enabled": True, "ai_provider_id": "modelscope/test", "enhance_images": False}
    provider = SimpleNamespace(provider_config={"api_base": "https://api-inference.modelscope.cn/v1", "enable": True})
    p.context = SimpleNamespace(get_provider_by_id=lambda name: provider if name == "modelscope/test" else None)
    p._ai_seen = OrderedDict()
    p._lock = asyncio.Lock()
    p.plugin_dir = Path(__file__).resolve().parents[1]
    p.state_dir = Path(directory)
    p.questions = QuestionStore(p.plugin_dir / "data")
    p.state = StateStore(Path(directory) / "state.db", p.questions.by_id)
    p.db = MahjongDatabase(Path(directory) / "state.db")
    p.koromo = object()
    return p


def tools(*names):
    return ToolSet([FunctionTool(name=name, description=name, parameters={"type": "object", "properties": {}}) for name in names])


class AIEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_music_search_displays_native_candidates_without_auto_play(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            called = []
            async def search(event, query):
                called.append(query)
                yield event.plain_result("原有选歌列表，回复序号")
            instance = SimpleNamespace(search_song=search)
            p.context.get_registered_star = lambda name: SimpleNamespace(activated=True, star_cls=instance)
            event = Event(text="搜索一下晴天")
            event.set_extra("mahjong_ai_request", True)
            await p.music_search_ai(event, "晴天 周杰伦")
            self.assertEqual(called, ["晴天 周杰伦"])
            self.assertEqual(event.sent[0].get_plain_text(), "原有选歌列表，回复序号")
            await p.music_search_ai(event, "晴天")
            self.assertEqual(len(called), 1)

    async def test_menu_requests_reuse_existing_images_and_respect_missing_plugin(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            event = Event(text="给我功能菜单", group=None, mention=None)
            event.set_extra("mahjong_ai_request", True)
            await p.menu_ai(event)
            self.assertEqual(len(event.sent), 1)
            self.assertTrue(any(isinstance(part, Comp.Image) for part in event.sent[0].chain))
            called = []
            async def menu(event, category):
                called.append(category)
                yield event.plain_result("existing scoped menu")
            instance = SimpleNamespace(_accessible_entries=lambda event: [], entertainment_menu=menu)
            p.context.get_registered_star = lambda name: SimpleNamespace(activated=True, star_cls=instance)
            entertainment = Event(text="娱乐图片菜单", message_id="2")
            entertainment.set_extra("mahjong_ai_request", True)
            await p.menu_ai(entertainment, section="entertainment", category="图片")
            self.assertEqual(called, ["图片"])
            self.assertEqual(entertainment.sent[0].get_plain_text(), "existing scoped menu")
            p.context.get_registered_star = lambda name: SimpleNamespace(activated=False, star_cls=instance)
            self.assertIsNone(p._entertainment_plugin())

    async def test_catalog_only_includes_accessible_names_keys_and_availability(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            entries = [SimpleNamespace(name="今日老婆", type="image", params={"id": "SECRET"}, valid=True),
                       SimpleNamespace(name="invalid-api", type="text", params={}, valid=False)]
            instance = SimpleNamespace(_accessible_entries=lambda event: entries)
            p.context.get_registered_star = lambda name: SimpleNamespace(activated=True, star_cls=instance)
            event = Event(text="看看今天的老婆")
            await p.ai_before_model(event)
            req = ProviderRequest(prompt=event.message_str, func_tool=tools(*AI_TOOLS))
            await p.ai_limit_request(event, req)
            self.assertIn("今日老婆 | image | 参数: id", req.system_prompt)
            self.assertNotIn("SECRET", req.system_prompt)
            self.assertIn("invalid-api | text | 参数: 无 | 暂不可用", req.system_prompt)

    async def test_entertainment_lookup_then_call_sends_text_and_stops_without_summary(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            event = Event(text="讲个笑话")
            event.set_extra("mahjong_ai_request", True)
            selected = tools(*AI_TOOLS)
            event.set_extra("mahjong_ai_toolset", selected)
            def content(value):
                return CallToolResult(content=[TextContent(type="text", text=value)])
            await p.ai_tool_finished(event, selected.get_tool("query_available_apis"), {}, content("Accessible APIs: 1\n- 笑话"))
            self.assertEqual(selected.names(), ["call_api_by_name"])
            self.assertEqual(event.sent, [])
            await p.ai_tool_finished(event, selected.get_tool("call_api_by_name"), {}, content("这是接口返回的笑话"))
            self.assertTrue(selected.empty())
            self.assertEqual(event.sent[0].get_plain_text(), "这是接口返回的笑话")
            selected = tools("call_api_by_name")
            event.set_extra("mahjong_ai_toolset", selected)
            await p.ai_tool_finished(event, selected.get_tool("call_api_by_name"), {}, content("API call succeeded: sent image result for 今日老婆."))
            self.assertEqual(len(event.sent), 1)
            self.assertTrue(selected.empty())

    async def test_non_mentions_replies_and_greetings_never_pass_model_gate(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            for event in [Event(mention=None), Event(mention="other"), Event(mention="all"),
                          Event(group=None, mention=None, text="你好"), Event(text="你好"), Event(text="hello!")]:
                await p.ai_message_gate(event)
                self.assertTrue(event.call_llm)
                await p.ai_before_model(event)
                self.assertTrue(event.stopped)
                self.assertFalse(event.get_extra("mahjong_ai_started"))
                self.assertEqual(event.sent, [])

    async def test_private_feature_request_reaches_tools_without_mention(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            event = Event(text="给我何切答案", mention=None, group=None)
            self.assertTrue(eligible(event))
            await p.ai_message_gate(event)
            self.assertFalse(event.call_llm)
            await p.ai_before_model(event)
            req = ProviderRequest(prompt=event.message_str, func_tool=tools(*AI_TOOLS))
            await p.ai_limit_request(event, req)
            self.assertFalse(event.stopped)
            self.assertTrue(event.get_extra("mahjong_ai_request"))
            self.assertIn("mahjong_quiz", req.func_tool.names())

    async def test_private_native_command_and_self_echo_do_not_enter_ai(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            native = Event(text="何切", mention=None, group=None)
            native.set_extra("activated_handlers", [SimpleNamespace(event_filters=[CommandFilter("何切")])])
            await p.ai_message_gate(native)
            await p.ai_before_model(native)
            self.assertTrue(native.call_llm)
            self.assertTrue(native.stopped)
            own = Event(mention=None, group=None)
            own.get_sender_id = lambda: "bot"
            self.assertFalse(eligible(own))

    async def test_framework_matched_native_command_is_excluded_even_with_at(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            event = Event(text="何切")  # AstrBot has already stripped the slash.
            event.set_extra("activated_handlers", [SimpleNamespace(event_filters=[CommandFilter("何切")])])
            self.assertFalse(eligible(event))
            await p.ai_message_gate(event)
            await p.ai_before_model(event)
            self.assertTrue(event.call_llm)
            self.assertTrue(event.stopped)
            self.assertEqual(len(p._ai_seen), 0)

    async def test_only_approved_provider_and_tools_with_no_shared_history(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            event = Event()
            await p.ai_message_gate(event)
            await p.ai_before_model(event)
            req = ProviderRequest(prompt=event.message_str, contexts=[{"role": "user", "content": "another member"}],
                                  func_tool=tools(*AI_TOOLS, "astrbot_execute_shell", "future_task"), model="other-model")
            await p.ai_limit_request(event, req)
            self.assertFalse(event.stopped)
            self.assertEqual(set(req.func_tool.names()), AI_TOOLS)
            self.assertEqual(req.contexts, [])
            self.assertIsNone(req.model)
            self.assertEqual(event.get_extra("selected_provider"), "modelscope/test")
            self.assertFalse(event.get_extra("enable_streaming"))
            self.assertFalse(modelscope_provider(SimpleNamespace(provider_config={"api_base": "https://example.com/v1"})))
            p.config["ai_provider_id"] = "paid/other"
            blocked = Event(message_id="2")
            await p.ai_before_model(blocked)
            self.assertTrue(blocked.stopped)
            self.assertIn("AI 功能识别暂时不可用", blocked.sent[0].get_plain_text())

    async def test_duplicate_platform_event_does_not_start_a_second_request(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            first, duplicate, other = Event(), Event(), Event(message_id="2")
            await p.ai_before_model(first)
            await p.ai_before_model(duplicate)
            await p.ai_before_model(other)
            self.assertFalse(first.stopped)
            self.assertTrue(duplicate.stopped)
            self.assertFalse(other.stopped)

    async def test_batch_quiz_uses_existing_state_once_and_validates_before_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            event = Event()
            event.set_extra("mahjong_ai_request", True)
            await p.quiz_ai(event, count=5)
            self.assertEqual(len(event.sent), 5)
            ids = [int(result.get_plain_text().split("#")[1].split()[0]) for result in event.sent]
            self.assertEqual(len(set(ids)), 5)
            self.assertEqual(p.state.get(event.unified_msg_origin)["current_question"], ids[-1])
            await p.quiz_ai(event, count=5)
            self.assertEqual(len(event.sent), 5)
            before = p.state.get(event.unified_msg_origin)
            invalid = Event(message_id="2")
            invalid.set_extra("mahjong_ai_request", True)
            await p.quiz_ai(invalid, count=6)
            self.assertIn("1～5", invalid.sent[0].get_plain_text())
            self.assertEqual(p.state.get(event.unified_msg_origin), before)
            # Model failure does not prevent the existing command using the same state.
            native = Event(mention=None)
            result = [item async for item in p.nanikiru(native)]
            self.assertEqual(len(result), 1)

    async def test_query_arguments_do_not_route_into_binding_or_admin_commands(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            received = []
            async def view(event, query):
                received.append(query)
                yield event.plain_result("existing view")
            p._query_player_view = view
            event = Event(text="查一剑风起醉英豪三麻放铳")
            event.set_extra("mahjong_ai_request", True)
            await p.query_ai(event, player="一剑风起醉英豪", mode=3, section="和铳", days=30)
            self.assertEqual(received[0].player, "一剑风起醉英豪")
            self.assertEqual((received[0].mode, received[0].section, received[0].days), (3, "和铳", 30))
            native = [item async for item in p.koromo_menu(Event(mention=None), "一剑风起醉英豪 三 和 30天")]
            self.assertEqual(received[0], received[1])
            self.assertEqual(native[0].get_plain_text(), "existing view")
            blocked = Event(message_id="2")
            blocked.set_extra("mahjong_ai_request", True)
            await p.query_ai(blocked, section="绑定")
            self.assertEqual(len(received), 2)
            self.assertEqual(p.db.list_bindings("user"), [])

    async def test_concurrent_quiz_requests_keep_existing_draws_unique(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            events = [Event(message_id=str(i)) for i in (1, 2)]
            for event in events:
                event.set_extra("mahjong_ai_request", True)
            await asyncio.gather(*(p.quiz_ai(event, count=5) for event in events))
            ids = [int(result.get_plain_text().split("#")[1].split()[0]) for event in events for result in event.sent]
            self.assertEqual(len(ids), 10)
            self.assertEqual(len(set(ids)), 10)

    async def test_completed_tools_removed_and_music_error_delivered_without_model_chat(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            event = Event()
            event.set_extra("mahjong_ai_request", True)
            selected = tools(*AI_TOOLS)
            event.set_extra("mahjong_ai_toolset", selected)
            result = CallToolResult(content=[TextContent(type="text", text='{"status":"candidates"}')])
            await p.ai_tool_finished(event, selected.get_tool("find_in_bilibili"), {}, result)
            self.assertEqual(selected.names(), ["deliver_media"])
            error = CallToolResult(content=[TextContent(type="text", text=json.dumps({"status": "error", "message": "候选已过期"}))])
            await p.ai_tool_finished(event, selected.get_tool("deliver_media"), {}, error)
            self.assertTrue(selected.empty())
            self.assertEqual(event.sent[0].get_plain_text(), "候选已过期")
            response = LLMResponse(role="assistant", completion_text="你好，我已经替你播放了")
            await p.ai_no_chat_response(event, response)
            self.assertEqual(response.completion_text, "")
            event.result = MessageEventResult(chain=[Comp.Plain("模型的执行预告")], result_content_type=ResultContentType.LLM_RESULT)
            await p.ai_no_chat_result(event)
            self.assertEqual(event.result.chain, [])
            event.result = MessageEventResult().message("HTTP 429: quota exhausted")
            await p.ai_no_chat_result(event)
            self.assertIn("AI 功能识别暂时不可用", event.result.get_plain_text())


if __name__ == "__main__":
    unittest.main()
