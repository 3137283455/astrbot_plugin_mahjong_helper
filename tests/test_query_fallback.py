import tempfile
import unittest
from unittest.mock import patch

from test_ai_entry import Event, plugin
from astrbot_plugin_mahjong_helper.koromo_views import PlayerQuery, parse_player_query
from astrbot_plugin_mahjong_helper.majsoul_api import MajsoulApiError


class KoromoStub:
    def __init__(self, stats, search=None, error=None):
        self.stats = stats
        self.search = search or {}
        self.calls = []
        self.searches = []
        self.error = error

    async def player_stats(self, uid, mode, *args):
        self.calls.append((uid, mode, args))
        if self.error:
            raise self.error
        return self.stats.get(mode)

    async def extended_stats(self, uid, mode, *args):
        return {}

    async def search_player(self, name, mode):
        self.searches.append(mode)
        return self.search.get(mode, [])


class QueryFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_image_queries_both_modes_with_matching_filters(self):
        with tempfile.TemporaryDirectory() as d:
            p=plugin(d)
            p.koromo=KoromoStub({3:{'nickname':'玩家','count':10},4:{'nickname':'玩家','count':20}})
            with patch('astrbot_plugin_mahjong_helper.main.render_player_card') as render:
                results=[r async for r in p._query_player_view(Event(),parse_player_query('12105509','玉','30天'))]
            self.assertEqual(len(results),1)
            panels=render.call_args.args[3]
            self.assertEqual(panels[3]['stats']['count'],10)
            self.assertEqual(panels[4]['stats']['count'],20)
            self.assertEqual({c[1]:c[2][0] for c in p.koromo.calls},{3:'24.23',4:'12.11'})
            self.assertEqual(p.koromo.calls[0][2][1],p.koromo.calls[1][2][1])

    async def test_one_failed_panel_does_not_hide_other_mode(self):
        with tempfile.TemporaryDirectory() as d:
            p=plugin(d)
            class Partial(KoromoStub):
                async def player_stats(self,uid,mode,*args):
                    if mode==4:raise MajsoulApiError('暂不可用')
                    return {'nickname':'玩家','count':10}
            p.koromo=Partial({})
            with patch('astrbot_plugin_mahjong_helper.main.render_player_card') as render:
                results=[r async for r in p._query_player_view(Event(),parse_player_query('12105509'))]
            self.assertEqual(len(results),1)
            panels=render.call_args.args[3]
            self.assertEqual(panels[3]['stats']['count'],10)
            self.assertTrue(panels[4]['error'])

    async def test_both_empty_image_returns_clear_text(self):
        with tempfile.TemporaryDirectory() as d:
            p=plugin(d);p.koromo=KoromoStub({3:{'count':0},4:{'count':0}})
            results=[r async for r in p._query_player_view(Event(),parse_player_query('12105509'))]
            self.assertIn('四麻、三麻都没有',results[0].get_plain_text())

    async def test_unspecified_mode_tries_four_then_three_with_same_uid_and_filters(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            p.koromo = KoromoStub({3: {"nickname": "三麻玩家", "count": 10}})
            query = parse_player_query("12105509", "玉", "30天", "文")
            results = [result async for result in p._query_player_view(Event(), query)]
            self.assertEqual([row[1] for row in p.koromo.calls], [4, 3])
            self.assertEqual([row[0] for row in p.koromo.calls], ["12105509"] * 2)
            self.assertEqual(p.koromo.calls[0][2][0], "12.11")
            self.assertEqual(p.koromo.calls[1][2][0], "24.23")
            self.assertIn("三麻", results[0].get_plain_text())
            self.assertEqual(len(results), 1)

    async def test_explicit_four_and_nonempty_four_never_fall_back(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            for query, stats in [(parse_player_query("12105509", "四", "文"), {}),
                                 (parse_player_query("12105509", "文"), {4: {"nickname": "四麻玩家", "count": 1}})]:
                p.koromo = KoromoStub(stats)
                [result async for result in p._query_player_view(Event(), query)]
                self.assertEqual([row[1] for row in p.koromo.calls], [4])

    async def test_player_only_found_in_three_and_both_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            p.koromo = KoromoStub({3: {"nickname": "某玩家", "count": 2}},
                                  {3: [{"id": 12105509, "nickname": "某玩家"}]})
            results = [r async for r in p._query_player_view(Event(), parse_player_query("某玩家", "文"))]
            self.assertEqual(p.koromo.searches, [4, 3])
            self.assertIn("三麻", results[0].get_plain_text())
            p.koromo = KoromoStub({4: {"count": 0}, 3: {"count": 0}})
            results = [r async for r in p._query_player_view(Event(), parse_player_query("12105509", "文"))]
            self.assertIn("四麻、三麻都没有", results[0].get_plain_text())
            self.assertEqual(len(results), 1)

    async def test_transport_errors_are_not_misreported_as_missing_data(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            p.koromo = KoromoStub({}, error=MajsoulApiError("接口暂时不可用"))
            results = [r async for r in p._query_player_view(Event(), parse_player_query("12105509", "文"))]
            self.assertIn("接口暂时不可用", results[0].get_plain_text())
            self.assertEqual([row[1] for row in p.koromo.calls], [4])
