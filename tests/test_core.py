import tempfile
import unittest
from pathlib import Path

from database import MahjongDatabase
from formatters import format_record, format_stats, room_modes
from majsoul_api import KoromoClient
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


class KoromoClientTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
