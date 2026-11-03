import tempfile,time,unittest
from fractions import Fraction as F
from pathlib import Path
from unittest.mock import patch
from astrbot_plugin_group_data.scoring import score_matches,display,cents,online_fields
from astrbot_plugin_group_data import cards,periods
from astrbot_plugin_group_data.core import validate,aggregate
from test_core import result
from test_nicknames import Event
import test_commands

def match(i,mode=4,names=None,points=None):
    return {'id':i,'result':validate({**result(mode,points,names),'game_id':'score-test-'+str(i)})}

class ScoringTests(unittest.TestCase):
    def test_known_components_and_90_10_total(self):
        data=score_matches([match(i,points=[40000,30000,20000,10000]) for i in range(10)],4)
        row=next(r for r in data['rows'] if r['name']=='甲')
        self.assertEqual((row['R'],row['P'],row['performance'],row['attendance']),(100,80,93,F(10,3)))
        self.assertEqual(display(row['score']),'87.03');self.assertEqual(row['position'],1)
    def test_threshold_separate_mode_minimum_and_provisional_not_ranked(self):
        nine=score_matches([match(i) for i in range(9)],4)
        self.assertEqual(nine['formal_count'],0);self.assertTrue(all(r['position'] is None for r in nine['rows']))
        data=score_matches([*[match(i) for i in range(10)],match(50,3)],4,minimum=10)
        self.assertEqual(data['games'],10);self.assertEqual(data['formal_count'],4)
        self.assertEqual(score_matches([match(1)],4,minimum=2)['rows'],[])
        three=score_matches([match(20,3,points=[35000,35000,35000])],3)
        self.assertEqual(sorted(r['R'] for r in three['rows']),[0,50,100]);self.assertTrue(all(r['P']==50 for r in three['rows']))
    def test_clamp_each_game_before_average_and_no_double_fly_penalty(self):
        data=score_matches([match(1,points=[80000,30000,0,-10000]),match(2,points=[30000,25000,25000,20000])],4)
        rows={r['name']:r for r in data['rows']}
        self.assertEqual(rows['甲']['P'],80) # (100 capped + 60) / 2; not 100 from average points.
        self.assertEqual(rows['丁']['P'],20)
        self.assertEqual(rows['丁']['score'],F(9,10)*F(35,100)*20+F(20,22))
    def test_deduplication_aliases_invalid_totals_and_unknown_identity(self):
        m=match(1);data=score_matches([m,m,match(2)],4,{'甲':'新甲'})
        self.assertEqual(data['games'],2);self.assertIn('新甲',[r['name'] for r in data['rows']])
        self.assertEqual(score_matches([m],4,{'甲':'乙'})['games'],0)
        nonstandard=match(2);nonstandard['result']['players'][0]['points']+=1000
        excluded=score_matches([nonstandard],4);self.assertEqual(len(excluded['excluded']),1);self.assertEqual(excluded['rows'],[])
        self.assertEqual(score_matches([],3)['formal_count'],0)
    def test_same_performance_more_games_adds_only_bounded_diminishing_points(self):
        def one(n):return next(r for r in score_matches([match(i) for i in range(n)],4)['rows'] if r['name']=='甲')
        a,b,c=one(10),one(20),one(30)
        self.assertEqual(a['performance'],b['performance']);self.assertEqual(b['score']-a['score'],F(5,3))
        self.assertEqual(c['score']-b['score'],1);self.assertLess(c['attendance'],10)
    def test_ties_use_displayed_two_decimals_and_skip_positions(self):
        self.assertEqual(cents(F(12345,1000)),1235)
        games=[]
        for i in range(10):games.append(match(i,names=['甲','乙','丙','丁'] if i%2 else ['乙','甲','丙','丁'],points=[40000,30000,20000,10000]))
        rows=score_matches(games,4)['rows'];tied=[r for r in rows if r['name'] in ['甲','乙']]
        self.assertEqual([r['position'] for r in tied],[1,1]);self.assertTrue(all(r['tied'] for r in tied));self.assertEqual(rows[2]['position'],3)
    def test_online_fields_do_not_give_provisional_a_rank(self):
        row=score_matches([match(1)],4)['rows'][0];fields=online_fields(row)
        self.assertEqual(fields['综合评分状态'],'暂定');self.assertNotIn('综合评分排名',fields)
        self.assertEqual(online_fields(None)['综合评分状态'],'无有效评分')

class ScoringCommandsTests(unittest.IsolatedAsyncioTestCase):
    def seed(self,folder):
        p=test_commands.CommandTests.setup_plugin(self,folder);scope='12345:987654321'
        entries=[{'msg':str(i),'idx':0,'stamp':time.time()} for i in range(10)]
        p.store.reconcile(scope,entries)
        for i in range(10):
            row=p.store.source(scope,str(i),0);p.store.attach(row['id'],'score'+str(i),Path(folder)/'original.png')
            p.store.save('score'+str(i),'accepted',match(i,names=['甲完整','乙','丙','丁'])['result'])
        return p
    async def test_both_seasons_default_s1_and_parameter_permutations(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.seed(folder);scope='12345:987654321';periods.cutover(p.store,scope)
            with patch('astrbot_plugin_group_data.main.cards.score_board',wraps=cards.score_board) as render:
                await p.handle(Event(),'综合榜 四麻 S0');self.assertEqual(render.call_args.args[2]['formal_count'],4)
                self.assertEqual(render.call_args.args[4],'S0赛季')
                await p.handle(Event(),'S1 四麻 综合排名');self.assertEqual(render.call_args.args[2]['formal_count'],0)
                self.assertEqual(render.call_args.args[4],'S1赛季')
                await p.handle(Event(),'综合榜 30天 S0 四麻 至少11场');self.assertEqual(render.call_args.args[2]['rows'],[])
            archived=p.store.db.execute("SELECT checksum FROM periods WHERE state='archived'").fetchone()[0]
            await p.handle(Event(),'评分规则');self.assertEqual(p.store.db.execute("SELECT checksum FROM periods WHERE state='archived'").fetchone()[0],archived)
    async def test_personal_card_and_analysis_share_score_nickname_and_filters(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.seed(folder);e=Event();await p.handle(e,'综合分析 甲 四麻')
            self.assertIsInstance(e.sent[0],str);self.assertIn('甲完整',e.sent[0])
            selected=Event('1');await p.nickname_followup(selected);self.assertTrue(selected.stopped)
            self.assertIn('评分 甲完整 四麻',p.selection_context(selected)['query'])
            with patch('astrbot_plugin_group_data.main.cards.profile',wraps=cards.profile) as render:
                await p.handle(Event(),'个人 甲完整 四麻')
            data=render.call_args.kwargs['score_data'];row=next(r for r in data['rows'] if r['name']=='甲完整')
            self.assertEqual(row['games'],10);self.assertEqual(data['formal_count'],4)
            text=cards.profile_text('group',4,aggregate(p.store.matches('12345:987654321'),4),'甲完整',score_data=data)
            self.assertIn(display(row['score']),text);self.assertIn('第1/4名',text)
    async def test_current_season_recalculates_after_essence_removal_while_s0_stays_frozen(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.seed(folder);scope='12345:987654321';periods.cutover(p.store,scope)
            old=score_matches(p.store.matches(scope,period='S0'),4)
            entries=[{'msg':'new'+str(i),'idx':0,'stamp':time.time()} for i in range(10)]
            p.store.reconcile(scope,entries)
            for i in range(10):
                row=p.store.source(scope,'new'+str(i),0);p.store.attach(row['id'],'newscore'+str(i),'original.png');p.store.save('newscore'+str(i),'accepted',match(100+i)['result'])
            self.assertEqual(score_matches(p.store.matches(scope),4)['formal_count'],4)
            p.store.reconcile(scope,entries[:-1]);new=score_matches(p.store.matches(scope),4)
            self.assertEqual(new['formal_count'],0);self.assertTrue(all(r['games']==9 for r in new['rows']))
            self.assertEqual(old,score_matches(p.store.matches(scope,period='S0'),4))

if __name__=='__main__':unittest.main()
