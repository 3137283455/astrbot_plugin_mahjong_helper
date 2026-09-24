import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mahjong_helper.database import MahjongDatabase
from astrbot_plugin_mahjong_helper.main import MahjongHelperPlugin


class _Event:
    def __init__(self, group="group", fail=False):
        self.group = group
        self.fail = fail
        self.sent = []

    def get_group_id(self):
        return self.group

    async def send(self, chain):
        if self.fail:
            raise RuntimeError("send failed")
        self.sent.append(chain)


class _Plugin:
    def __init__(self, db):
        self.db = db
        self._room_broadcast_lock = asyncio.Lock()

    def _ready(self):
        return None, None, self.db, None

    def _actor_id(self, event):
        return "alice"


class RoomBroadcastTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_real_at_all_and_charges_only_success(self):
        with tempfile.TemporaryDirectory() as directory:
            db = MahjongDatabase(Path(directory) / "state.db")
            plugin = _Plugin(db)
            private = _Event(group=None)
            self.assertIn("QQ 群", await MahjongHelperPlugin._broadcast_room(plugin, private, "12345"))
            self.assertEqual(db.room_broadcast_wait("alice"), 0)

            failed = _Event(fail=True)
            self.assertIn("广播失败", await MahjongHelperPlugin._broadcast_room(plugin, failed, "12345"))
            self.assertEqual(db.room_broadcast_wait("alice"), 0)

            event = _Event()
            self.assertIsNone(await MahjongHelperPlugin._broadcast_room(plugin, event, "https://example.com/room=12345"))
            self.assertEqual(event.sent[0].chain[0].toDict()["data"]["qq"], "all")
            self.assertIn("https://example.com/room=12345", event.sent[0].chain[1].text)
            self.assertGreater(db.room_broadcast_wait("alice"), 0)

            repeat = _Event()
            self.assertIn("冷却中", await MahjongHelperPlugin._broadcast_room(plugin, repeat, "12345"))
            self.assertEqual(repeat.sent, [])
