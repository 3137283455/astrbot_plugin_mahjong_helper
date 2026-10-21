import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_main import listen_main, _SendingEvent
from astrbot_plugin_bili_player.core.models import BilibiliCandidate, SearchSnapshot
from astrbot_plugin_bili_player.core.playlist import PlaylistStore
from astrbot_plugin_bili_player.core.selection import SearchSnapshotStore


def fixture(directory):
    p = listen_main.ListenMusicPlugin.__new__(listen_main.ListenMusicPlugin)
    p._settings = listen_main.PluginSettings()
    p._playlist = PlaylistStore(Path(directory) / "songs.db")
    p._playlist_scope = "personal"
    p._music_contexts = {}
    p._recent_searches = {}
    p._last_played = {}
    p._llm_searches = {}
    p._selection_waits = {}
    p._selection_lock = asyncio.Lock()
    p._initialized = True
    snapshots = SearchSnapshotStore()
    songs = (BilibiliCandidate("BVfirst", 1, "晴天 周杰伦", "歌手", 1000),
             BilibiliCandidate("BVsecond", 2, "稻香 周杰伦", "歌手", 1000))
    remote = snapshots.create(session_id="chat-a", query="在线", candidates=songs)
    p._search = SimpleNamespace(search=AsyncMock(return_value=remote), from_candidates=snapshots.create)
    p._cancel_selection_wait = AsyncMock()
    p._start_selection_wait = AsyncMock(return_value=True)
    p._deliver_media = AsyncMock()
    return p, songs


class MusicContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_uses_saved_exact_page_and_only_misses_search_online(self):
        with tempfile.TemporaryDirectory() as d:
            p, songs = fixture(d)
            p._playlist.add("user:user", songs[0])
            event = _SendingEvent("chat-a", "搜索晴天")
            results = [result async for result in p._run_search_command(event, "晴天 周杰伦")]
            p._search.search.assert_not_awaited()
            self.assertIn("歌单", results[0][1])
            self.assertEqual(p._music_contexts[p._playlist_key(event)].snapshot.candidate_at(1), songs[0])
            [result async for result in p._run_search_command(event, "不存在的歌")]
            p._search.search.assert_awaited_once()

    async def test_ai_direct_play_search_also_uses_saved_song_first(self):
        with tempfile.TemporaryDirectory() as d:
            p, songs = fixture(d)
            p._playlist.add("user:user", songs[0])
            event = _SendingEvent("chat-a", "播放晴天")
            result = json.loads(await p.find_in_bilibili_for_llm(event, "晴天", artist="周杰伦", delivery="audio"))
            self.assertEqual(result["status"], "candidates")
            p._search.search.assert_not_awaited()
            self.assertEqual(result["candidates"][0]["title"], songs[0].display_title)

    async def test_add_then_play_followups_keep_original_list_and_do_not_search(self):
        with tempfile.TemporaryDirectory() as d:
            p, songs = fixture(d)
            event = _SendingEvent("chat-a", "把第二个加入歌单")
            snapshot = p._search.from_candidates(session_id="chat-a", query="歌曲", candidates=songs)
            p._remember_music_list(event, snapshot)
            await p.music_followup(event)
            self.assertEqual(p._playlist.get("user:user", 1), songs[1])
            reply = _SendingEvent("chat-a", "放第二首")
            await p.music_followup(reply)
            self.assertEqual(p._deliver_media.await_args.kwargs["candidate"], songs[1])
            self.assertEqual(p._deliver_media.await_args.kwargs["action"], listen_main._DeliveryMode.VOICE)
            p._search.search.assert_not_awaited()

    async def test_members_do_not_use_each_others_numbered_lists(self):
        with tempfile.TemporaryDirectory() as d:
            p, songs = fixture(d)
            owner = _SendingEvent("chat-a")
            snapshot = p._search.from_candidates(session_id="chat-a", query="歌曲", candidates=songs)
            p._remember_music_list(owner, snapshot)
            other = _SendingEvent("chat-a", "把第二首加入歌单")
            other.get_sender_id = lambda: "another-user"
            await p.music_action(other, "add", 2)
            self.assertEqual(p._playlist.list("user:another-user"), [])
            self.assertNotIn("稻香", p.music_context_text(other))
            p._deliver_media.assert_not_awaited()
            selection_filter = listen_main._SelectionSessionFilter("chat-a", "user")
            self.assertEqual(selection_filter.filter(other), "")

    async def test_expired_search_does_not_silently_select_playlist_position(self):
        with tempfile.TemporaryDirectory() as d:
            p, songs = fixture(d)
            event = _SendingEvent("chat-a")
            p._playlist.add("user:user", songs[0])
            now = time.monotonic()
            snapshot = SearchSnapshot("old", "chat-a", "旧搜索", songs, now - 500, now - 100)
            p._remember_music_list(event, snapshot)
            await p.music_action(event, "play", 1)
            p._deliver_media.assert_not_awaited()
            self.assertIn("5分钟", event.sent[-1][1])

    async def test_playlist_view_freezes_indices_until_next_visible_list(self):
        with tempfile.TemporaryDirectory() as d:
            p, songs = fixture(d)
            for song in songs:
                p._playlist.add("user:user", song)
            event = _SendingEvent("chat-a")
            [result async for result in p.playlist_command(event)]
            p._playlist.remove("user:user", 1)
            await p.music_action(event, "play", 2)
            self.assertEqual(p._deliver_media.await_args.kwargs["candidate"], songs[1])

    async def test_natural_grammar_preserves_selection_and_playlist_intents(self):
        for phrase in ("放第2个", "播放第二首", "放第二个"):
            self.assertEqual(listen_main._parse_music_followup(phrase), ("play", 2, "auto"))
        for phrase in ("把第2个加入歌单", "第二首加到歌单里", "收藏第二首"):
            self.assertEqual(listen_main._parse_music_followup(phrase), ("add", 2, "auto"))
        self.assertEqual(listen_main._parse_music_followup("播放歌单第二首"), ("play", 2, "playlist"))
        self.assertIsNone(listen_main._parse_music_followup("放第几个"))
