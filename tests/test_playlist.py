import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_bili_player.core.models import BilibiliCandidate
from astrbot_plugin_bili_player.core.playlist import PlaylistStore


class PlaylistStoreTests(unittest.TestCase):
    def test_exact_page_persists_without_search_and_is_isolated_by_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "playlist.sqlite3"
            first = BilibiliCandidate("BV1234567890", 11, "晴天", "歌手", 180000)
            second = BilibiliCandidate("BV1234567890", 22, "晴天", "歌手", 200000)
            store = PlaylistStore(path)
            self.assertTrue(store.add("user:1", first))
            self.assertFalse(store.add("user:1", first))
            self.assertTrue(store.add("user:1", second))
            self.assertEqual(store.list("user:2"), [])

            reopened = PlaylistStore(path)
            self.assertEqual(reopened.get("user:1", 1), first)
            self.assertEqual(reopened.get("user:1", 2), second)
            self.assertEqual(reopened.remove("user:1", 1), first)
            self.assertEqual(reopened.get("user:1", 1), second)
            self.assertIsNone(reopened.get("user:1", 2))


if __name__ == "__main__":
    unittest.main()
