import tempfile
import unittest
import sys
from pathlib import Path

from database import MahjongDatabase
from formatters import format_record, format_stats, room_modes
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_mahjong_helper.koromo_views import (
    format_trend, format_view, parse_player_query,
)
from astrbot_plugin_mahjong_helper.stat_card import (
    render_help_card, render_quick_menu_card, render_stats_card,
)
from majsoul_api import KoromoClient, ProtocolClient, extract_paipu_id
from nanikiru_core import StateStore


class StateStoreTests(unittest.TestCase):
    def test_draws_every_question_once_per_round(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.db", range(1, 8))
            drawn = [store.draw("session")[0] for _ in range(7)]
            self.assertEqual(set(drawn), set(range(1, 8)))
            self.assertEqual(store.get("session")["remaining_questions"], [])
            next_question, new_round = store.draw("session")
            self.assertTrue(new_round)
            self.assertIn(next_question, range(1, 8))

    def test_daily_claim_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.db", range(1, 3))
            store.set_auto("session", True, "19:30")
            self.assertEqual(store.due_auto_sessions("2026-09-22", "19:30"), ["session"])
            self.assertTrue(store.claim_auto("session", "2026-09-22"))
            self.assertFalse(store.claim_auto("session", "2026-09-22"))


class MahjongDatabaseTests(unittest.TestCase):
    def test_binding_main_account_and_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            db = MahjongDatabase(Path(directory) / "state.db")
            self.assertTrue(db.add_binding("actor", "10001", "甲"))
            self.assertTrue(db.add_binding("actor", "10002", "乙"))
            self.assertEqual(db.get_main_uid("actor"), "10001")
            self.assertTrue(db.set_main_uid("actor", "10002"))
            self.assertEqual(db.get_main_uid("actor"), "10002")
            self.assertEqual(db.remove_binding("actor", "10002"), 1)
            self.assertEqual(db.get_main_uid("actor"), "10001")

    def test_subscription_cursor_is_preserved_on_resubscribe(self):
        with tempfile.TemporaryDirectory() as directory:
            db = MahjongDatabase(Path(directory) / "state.db")
            db.upsert_subscription("group", "10001", "甲", 4, "first")
            db.upsert_subscription("group", "10001", "甲二", 4, "second")
            row = db.list_subscriptions("group", 4)[0]
            self.assertEqual(row["nickname"], "甲二")
            self.assertEqual(row["last_uuid"], "first")
            self.assertEqual(row["active"], 1)

    def test_secret_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            db = MahjongDatabase(Path(directory) / "state.db")
            db.set_secret("token", "secret")
            self.assertEqual(db.get_secret("token"), "secret")


class FormatterTests(unittest.TestCase):
    def test_room_aliases(self):
        self.assertEqual(room_modes(4, "玉"), "12.11")
        self.assertEqual(room_modes(3, "王座间"), "26.25")

    def test_stats_and_record_output(self):
        stats = {
            "nickname": "测试玩家",
            "count": 20,
            "avg_rank": 2.25,
            "win_rate": 0.24,
        }
        text = format_stats("10001", 4, stats)
        self.assertIn("测试玩家", text)
        self.assertIn("24.0%", text)
        record = {
            "uuid": "abc",
            "start_time": 1_700_000_000_000,
            "players": [
                {"id": "10001", "nickname": "测试玩家", "rank": 1, "score": 42000}
            ],
        }
        result = format_record(record, "10001")
        self.assertIn("第1位", result)
        self.assertIn("paipu=abc", result)

    def test_koromo_chinese_fields_and_rank_distribution(self):
        stats = {"nickname": "测试玩家", "count": 40, "avg_rank": 2.2,
                 "rank_rates": [12, 10, 10, 8]}
        extended = {"和牌率": 0.23, "放铳率": 0.12, "立直后和牌率": 0.43,
                    "立直收支": 1500, "立直和了": 9, "副露和了": 4,
                    "默听和了": 2, "放铳至立直": 3, "放铳至副露": 1,
                    "放铳至默听": 1}
        self.assertIn("和牌率：23.0%", format_view("10001", 4, "基本", stats, extended))
        self.assertIn("立直和了率：43.0%", format_view("10001", 4, "立直", stats, extended))
        self.assertIn("1位：30.0%", format_view("10001", 4, "顺位", stats, extended))
        self.assertIn("立直：9（60.0%）", format_view("10001", 4, "和铳", stats, extended))

    def test_trend_uses_account_id_and_score_rank(self):
        records = [{"players": [{"accountId": 10001, "score": 42000, "gradingScore": 30},
                                {"accountId": 10002, "score": 18000}]},
                   {"players": [{"accountId": 10001, "score": 20000, "gradingScore": -10},
                                {"accountId": 10002, "score": 40000}]}]
        result = format_trend("10001", 4, records)
        self.assertIn("平均顺位：1.500", result)
        self.assertIn("段位分合计：+20pt", result)

    def test_short_command_parsing(self):
        query = parse_player_query("立", "12105509", "玉", "30天")
        self.assertEqual((query.section, query.player, query.room, query.days),
                         ("立直", "12105509", "玉", 30))
        query = parse_player_query("12105509", "三", "王", "文")
        self.assertEqual((query.section, query.mode, query.room, query.text),
                         ("基本", 3, "王座", True))
        query = parse_player_query("局", "三", "3")
        self.assertEqual((query.mode, query.page), (3, 3))

    def test_stat_card_renders_readable_png(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "card.png"
            render_stats_card(
                target, "12105509", 4, "基本",
                {"nickname": "清风难胡", "count": 773, "avg_rank": 2.647,
                 "level": {"id": 20201, "score": 276}},
                {"和牌率": .203, "放铳率": .186, "自摸率": .313,
                 "立直率": .189, "平均打点": 6450}, "玉", "近30天",
            )
            self.assertTrue(target.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
            from PIL import Image
            with Image.open(target) as image:
                self.assertGreaterEqual(image.width, 1400)
                self.assertGreaterEqual(image.height, 1000)

    def test_menu_cards_render_for_users_and_admins(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cards = {
                "quick": render_quick_menu_card(root / "quick.png"),
                "help": render_help_card(root / "help.png"),
                "admin": render_help_card(root / "admin.png", admin=True),
            }
            for path in cards.values():
                self.assertTrue(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
                with Image.open(path) as image:
                    self.assertEqual(image.width, 1400)
                    self.assertGreaterEqual(image.height, 1500)
            with Image.open(cards["help"]) as user, Image.open(cards["admin"]) as admin:
                self.assertGreater(admin.height, user.height)


class KoromoClientTests(unittest.IsolatedAsyncioTestCase):
    def test_public_api_does_not_require_token(self):
        self.assertNotIn("Authorization", KoromoClient(lambda: None)._headers())
        self.assertEqual(
            KoromoClient(lambda: "secret")._headers()["Authorization"],
            "Bearer secret",
        )

    async def test_stats_date_filter_changes_api_path(self):
        client = KoromoClient(lambda: None)
        seen = []

        async def fake_get(path, params=None):
            seen.append(path)
            return {"count": 1}

        client._get = fake_get
        await client.player_stats("10001", 4, since_ms=1_700_000_000_000)
        await client.extended_stats("10001", 4, since_ms=1_700_000_000_000)
        self.assertTrue(all("/10001/1700000000000/" in path for path in seen))

    async def test_recent_records_uses_stats_count_and_correct_mode(self):
        client = KoromoClient(lambda: "token")
        calls = []

        async def fake_get(path, params=None):
            calls.append((path, params))
            if "player_stats" in path:
                return {"count": 42}
            return [{"uuid": "game"}]

        client._get = fake_get
        result = await client.recent_records("10001", 3, 5, "24.23")
        self.assertEqual(result[0]["uuid"], "game")
        self.assertIn("/pl3/player_stats/10001/", calls[0][0])
        self.assertEqual(calls[1][1]["mode"], "24.23")
        self.assertEqual(calls[1][1]["tag"], 42)

    async def test_records_page_advances_cursor_and_clears_tag(self):
        client = KoromoClient(lambda: None)
        calls = []

        async def fake_get(path, params=None):
            calls.append((path, params))
            if "player_stats" in path:
                return {"count": 42}
            if len(calls) == 2:
                return [{"startTime": 2000 - i, "uuid": str(i)} for i in range(100)]
            return [{"startTime": 1899 - i, "uuid": str(100 + i)} for i in range(10)]

        client._get = fake_get
        records = await client.records_page("10001", 4, page=11, page_size=10)
        self.assertEqual([row["uuid"] for row in records], [str(i) for i in range(100, 110)])
        self.assertEqual(calls[1][1]["tag"], 42)
        self.assertEqual(calls[2][1]["tag"], "")
        self.assertIn("/1900999/", calls[2][0])

    async def test_protocol_fetch_record_contract(self):
        client = ProtocolClient("http://127.0.0.1:5088")
        calls = []

        async def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return {"dataBase64": "AA=="}

        client._request = fake_request
        result = await client.fetch_record("game-id")
        self.assertEqual(result["dataBase64"], "AA==")
        self.assertEqual(calls[0][0:2], ("POST", "/api/records/fetch"))
        self.assertEqual(calls[0][2]["json"]["paipu"], "game-id")
        self.assertTrue(calls[0][2]["json"]["includeDataBase64"])

    def test_extract_paipu_id(self):
        url = "https://game.maj-soul.net/1/?paipu=game-id_a123"
        self.assertEqual(extract_paipu_id(url), "game-id_a123")
        self.assertEqual(extract_paipu_id("plain-id"), "plain-id")


if __name__ == "__main__":
    unittest.main()
