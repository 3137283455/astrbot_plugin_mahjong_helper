import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mahjong_helper.database import MahjongDatabase
from astrbot_plugin_mahjong_helper.main import MahjongHelperPlugin


class _Bot:
    def __init__(self, role="admin", can_at_all=True, echo_at_all=True,
                 consume_quota=True, fail_send=False):
        self.role = role
        self.can_at_all = can_at_all
        self.echo_at_all = echo_at_all
        self.consume_quota = consume_quota
        self.fail_send = fail_send
        self.sent = []

    async def call_action(self, action, **params):
        if action == "get_group_member_info":
            return {"role": self.role}
        if action == "get_group_at_all_remain":
            return {
                "can_at_all": self.can_at_all,
                "remain_at_all_count_for_group": 15,
                "remain_at_all_count_for_uin": 8 if self.sent and self.consume_quota else 9,
            }
        if action == "send_group_msg":
            if self.fail_send:
                raise RuntimeError("send failed")
            self.sent.append(params)
            return {"message_id": 123}
        if action == "get_msg":
            return {"message": self.sent[-1]["message"] if self.echo_at_all else []}
        raise AssertionError(action)


class _Event:
    def __init__(self, group="1036516712", bot=None):
        self.group = group
        self.bot = bot or _Bot()
        self.message_obj = SimpleNamespace(self_id="3113357165")

    def get_group_id(self):
        return self.group


class _Plugin:
    def __init__(self, db):
        self.db = db
        self._room_broadcast_lock = asyncio.Lock()

    def _ready(self):
        return None, None, self.db, None

    def _actor_id(self, event):
        return "alice"


class RoomBroadcastTests(unittest.IsolatedAsyncioTestCase):
    async def test_checks_bot_permission_and_confirms_at_all_before_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            db = MahjongDatabase(Path(directory) / "state.db")
            plugin = _Plugin(db)
            private = _Event(group=None)
            self.assertIn("QQ 群", await MahjongHelperPlugin._broadcast_room(plugin, private, "12345"))

            member = _Event(bot=_Bot(role="member"))
            self.assertIn("机器人还不是本群管理员", await MahjongHelperPlugin._broadcast_room(plugin, member, "12345"))
            self.assertEqual(member.bot.sent, [])

            exhausted = _Event(bot=_Bot(can_at_all=False))
            self.assertIn("无法在本群 @全体", await MahjongHelperPlugin._broadcast_room(plugin, exhausted, "12345"))
            self.assertEqual(exhausted.bot.sent, [])

            failed = _Event(bot=_Bot(fail_send=True))
            self.assertIn("无法确认 @全体广播", await MahjongHelperPlugin._broadcast_room(plugin, failed, "12345"))
            self.assertEqual(db.room_broadcast_wait("alice"), 0)

            unconfirmed = _Event(bot=_Bot(echo_at_all=False))
            self.assertIn("未确认 @全体次数", await MahjongHelperPlugin._broadcast_room(plugin, unconfirmed, "12345"))
            unchanged_quota = _Event(bot=_Bot(consume_quota=False))
            self.assertIn("未确认 @全体次数", await MahjongHelperPlugin._broadcast_room(plugin, unchanged_quota, "12345"))
            self.assertEqual(db.room_broadcast_wait("alice"), 0)

            event = _Event()
            self.assertIsNone(await MahjongHelperPlugin._broadcast_room(plugin, event, "https://example.com/room=12345"))
            self.assertEqual(event.bot.sent[0]["message"][0], {"type": "at", "data": {"qq": "all"}})
            self.assertIn("https://example.com/room=12345", event.bot.sent[0]["message"][1]["data"]["text"])
            self.assertGreater(db.room_broadcast_wait("alice"), 0)

            repeat = _Event()
            self.assertIn("冷却中", await MahjongHelperPlugin._broadcast_room(plugin, repeat, "12345"))
            self.assertEqual(repeat.bot.sent, [])
