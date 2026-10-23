import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mcp.types import CallToolResult, TextContent
from astrbot.core.provider.entities import LLMResponse
from astrbot.api.event import MessageChain
from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType
import astrbot.api.message_components as Comp

from test_ai_entry import Event, plugin, tools
from test_query_fallback import KoromoStub
from astrbot_plugin_mahjong_helper.ai_entry import AI_TOOLS
from astrbot_plugin_mahjong_helper.mahjong_review import build_review_data, attach_review_header
from astrbot_plugin_mahjong_helper.majsoul_api import MajsoulApiError


def review_event():
    event = Event(text="锐评我的雀魂数据")
    event.set_extra("mahjong_ai_request", True)
    event.set_extra("mahjong_ai_toolset", tools(*AI_TOOLS))
    return event


def content(data):
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(data, ensure_ascii=False))])


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    def test_generated_header_is_not_repeated(self):
        header = "🀄 慧慧锐评｜玩家\n三麻 · 近30天 · 21场\n仅收录金之间及以上公开对局\n\n"
        result = attach_review_header(header, "🀄 慧慧锐评｜玩家\n三麻 · 近30天 · 21场\n\n正文评价。")
        self.assertEqual(result, header + "正文评价。")

    def test_metrics_keep_units_skip_missing_and_do_not_expose_extra_api_fields(self):
        data = build_review_data("12105509", 4, "玉", 30,
                                 {"nickname": "玩家", "count": 20, "avg_rank": 2.5,
                                  "rank_rates": [2, 3, 3, 2], "negative_rate": .05},
                                 {"和牌率": .2345, "放铳率": .125, "立直率": float("nan"),
                                  "局收支": -123.4, "平均打点": 6500, "SECRET": "不应进入上下文"})
        self.assertEqual(data["scope"]["记录场数"], 20)
        self.assertEqual(data["metrics"]["1位率"], "20.00%")
        self.assertEqual(data["metrics"]["和牌率"], "23.45%")
        self.assertEqual(data["metrics"]["放铳率"], "12.50%")
        self.assertEqual(data["metrics"]["局收支"], -123.4)
        self.assertNotIn("立直率", data["metrics"])
        self.assertNotIn("SECRET", json.dumps(data))
        self.assertIn("记录场数较少", " ".join(data["limitations"]))
        json.dumps(data, allow_nan=False)

    async def test_default_binding_falls_back_to_three_with_same_time_and_room(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            p.db.add_binding("user", "12105509", "玩家")
            p.koromo = KoromoStub({3: {"count": 100, "nickname": "玩家", "avg_rank": 2,
                                     "rank_rates": [30, 40, 30]}})
            data = json.loads(await p.review_ai(review_event(), room="玉", days=30))
            self.assertEqual([row[1] for row in p.koromo.calls], [4, 3])
            self.assertEqual([row[0] for row in p.koromo.calls], ["12105509"] * 2)
            self.assertEqual(p.koromo.calls[0][2][0], "12.11")
            self.assertEqual(p.koromo.calls[1][2][0], "24.23")
            self.assertEqual(p.koromo.calls[0][2][1], p.koromo.calls[1][2][1])
            self.assertEqual(data["player"]["mode"], "三麻")
            self.assertNotIn("4位率", data["metrics"])

    async def test_explicit_mode_missing_and_transport_failure_do_not_switch_modes(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            p.koromo = KoromoStub({3: {"count": 100}})
            data = json.loads(await p.review_ai(review_event(), player="12105509", mode=4))
            self.assertEqual(data["status"], "error")
            self.assertEqual([row[1] for row in p.koromo.calls], [4])
            p.koromo = KoromoStub({}, error=MajsoulApiError("暂时限流"))
            data = json.loads(await p.review_ai(review_event(), player="12105509"))
            self.assertEqual(data["message"], "暂时限流")
            self.assertEqual([row[1] for row in p.koromo.calls], [4])

    async def test_extended_failure_retains_basic_evidence_without_invented_metrics(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            p.koromo = KoromoStub({4: {"count": 100, "avg_rank": 2.4}})
            p.koromo.extended_stats = AsyncMock(side_effect=MajsoulApiError("接口暂不可用"))
            data = json.loads(await p.review_ai(review_event(), player="12105509"))
            self.assertEqual(data["status"], "review_data")
            self.assertEqual(data["metrics"], {"平均顺位": 2.4})
            self.assertIn("扩展数据缺失", " ".join(data["limitations"]))

    async def test_validation_and_unaddressed_calls_never_query(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            p.koromo = KoromoStub({})
            for kwargs in [{"mode": True}, {"days": -1}, {"days": 12}, {"room": "铜"}, {"player": "x" * 101}]:
                result = json.loads(await p.review_ai(review_event(), **kwargs))
                self.assertEqual(result["status"], "error")
            ignored = review_event()
            ignored.parts = [Comp.Plain("锐评我的数据")]
            self.assertIsNone(await p.review_ai(ignored, player="12105509"))
            self.assertEqual(p.koromo.calls, [])

    async def test_success_permits_one_model_review_and_blocks_other_actions(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            event = review_event()
            p.koromo = KoromoStub({4: {"count": 100, "avg_rank": 2.4}})
            raw = await p.review_ai(event, player="12105509")
            self.assertIsNone(await p.review_ai(event, player="12105509"))
            self.assertEqual(len(p.koromo.calls), 1)
            tool = event.get_extra("mahjong_ai_toolset").get_tool("mahjong_review")
            messages = [SimpleNamespace(role=role, content=text) for role, text in
                        [("system", "慧慧"), ("user", "旧玩家四麻"), ("assistant", "旧评价"),
                         ("user", "新玩家三麻"), ("assistant", "tool call")]]
            run_context = SimpleNamespace(messages=messages)
            await p.ai_store_agent_context(event, run_context)
            await p.ai_tool_finished(event, tool, {}, content(json.loads(raw)))
            self.assertEqual([m.content for m in run_context.messages], ["慧慧", "新玩家三麻", "tool call"])
            self.assertTrue(event.get_extra("mahjong_ai_toolset").empty())
            self.assertEqual(event.sent, [])
            response = LLMResponse(role="assistant", result_chain=MessageChain().message("平均顺位2.4，先来看看你的打法。"))
            await p.ai_no_chat_response(event, response)
            self.assertTrue(response.completion_text)
            self.assertIn("平均顺位2.4", response.completion_text)
            self.assertIn("四麻 · 全部收录时间 · 全部收录房间 · 100场", response.completion_text)
            self.assertIn("仅收录金之间及以上", response.completion_text)
            event.result = MessageEventResult(chain=[Comp.Plain(response.completion_text)], result_content_type=ResultContentType.LLM_RESULT)
            await p.ai_no_chat_result(event)
            self.assertTrue(event.result.chain)
            await p.quiz_ai(event)
            self.assertEqual(event.sent, [])

    async def test_failed_review_sends_error_once_and_suppresses_model_fabrication(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            event = review_event()
            tool = event.get_extra("mahjong_ai_toolset").get_tool("mahjong_review")
            await p.ai_tool_finished(event, tool, {}, content({"status": "error", "message": "没有查到数据"}))
            self.assertEqual(len(event.sent), 1)
            response = LLMResponse(role="assistant", completion_text="你的放铳率很高")
            await p.ai_no_chat_response(event, response)
            self.assertEqual(response.completion_text, "")

    async def test_persona_upgrade_preserves_custom_character_and_tool_choices(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            p.config["state_dir"] = d
            existing = SimpleNamespace(system_prompt="自定义的屑慧慧", tools=["mahjong_query", "bot_menu"])
            manager = SimpleNamespace(get_persona=AsyncMock(return_value=existing), update_persona=AsyncMock())
            p.context.persona_manager = manager
            p.context.get_llm_tool_manager = lambda: SimpleNamespace(get_func=lambda name: None)
            try:
                await p.initialize()
                kwargs = manager.update_persona.await_args.kwargs
                self.assertTrue(kwargs["system_prompt"].startswith("自定义的屑慧慧"))
                self.assertIn("mahjong_review", kwargs["tools"])
                self.assertEqual(set(kwargs["tools"]), {"mahjong_query", "mahjong_review", "bot_menu"})
            finally:
                await p.terminate()


if __name__ == "__main__":
    unittest.main()
