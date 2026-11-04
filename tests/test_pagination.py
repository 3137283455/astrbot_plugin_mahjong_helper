import tempfile,time,unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch,AsyncMock
from astrbot_plugin_group_data import cards,periods
from astrbot_plugin_group_data.main import parse_query
from astrbot_plugin_group_data.core import aggregate,ranking
from astrbot_plugin_group_data.pagination import page_rows,page_number
import test_commands
from test_nicknames import Event

class PageTests(unittest.TestCase):
    def test_global_ties_are_not_renumbered_or_lost_across_pages(self):
        stats=[{'name':f'玩家{i:02}','games':1,'fly':(30-i)//3} for i in range(27)]
        rows=ranking(stats,'被飞')
        pages=[page_rows(rows,p)['rows'] for p in (1,2,3)]
        self.assertEqual(sum(pages,[]),rows)
        self.assertEqual(len({r['name'] for p in pages for r in p}),27)
        self.assertEqual(pages[1][0]['position'],rows[10]['position'])
        for n in (0,4):
            with self.assertRaises(ValueError):page_rows(rows,n)
    def test_page_filters_are_order_independent_and_record_numbers_unchanged(self):
        self.assertEqual(parse_query('综合榜 四麻 S0 第2页 30天 5场'),parse_query('第2页 S0 5场 综合榜 30天 四麻'))
        self.assertEqual(parse_query('原图 20')['rest'],['20'])
        self.assertEqual(page_number('第二十一页'),21)
        self.assertEqual(page_number('第１００页'),100)
        for word in ('第0页','第10001页'):
            with self.assertRaises(ValueError):parse_query('被飞 '+word)
    def test_score_formal_and_provisional_pages_have_identical_text_and_image_rows(self):
        rows=[{'name':f'正式{i:02}','qualified':True,'position':i+1,'games':12,'performance':Fraction(80-i),'attendance':Fraction(3),'score':Fraction(75-i)} for i in range(23)]
        rows += [{'name':f'暂定{i:02}','qualified':False,'position':None,'games':1,'performance':Fraction(80-i),'attendance':Fraction(1),'score':Fraction(70-i)} for i in range(13)]
        data={'rows':rows,'mode':4,'minimum':1,'games':90,'excluded':[]}
        formal=cards.score_board_text(data,'S0赛季',page=2,provisional=False)
        pending=cards.score_board_text(data,'S0赛季',page=2,provisional=True)
        self.assertIn('11. 正式10',formal);self.assertNotIn('正式00 ·',formal);self.assertNotIn('暂定00 ·',formal)
        self.assertIn('暂定 暂定10',pending);self.assertNotIn('None.',pending)
        with tempfile.TemporaryDirectory() as folder:
            captured=[];original=cards.fit
            def fit(d,text,*args,**kw):captured.append(str(text));return original(d,text,*args,**kw)
            with patch.object(cards,'fit',side_effect=fit):
                cards.score_board(Path(folder)/'formal.png','1',data,period_name='S0赛季',page=2,provisional=False)
            self.assertTrue(all(f'正式{i:02}' in captured for i in range(10,20)))
            self.assertFalse(any(f'正式{i:02}' in captured for i in range(10)))

class PagingFlowTests(unittest.IsolatedAsyncioTestCase):
    def seed(self,folder):
        p=test_commands.CommandTests.setup_plugin(self,folder);scope='12345:987654321'
        stamp=int(time.time())
        p.store.reconcile(scope,[{'msg':str(i),'idx':0,'stamp':stamp+i} for i in range(25)])
        for i in range(25):
            row=p.store.source(scope,str(i),0);p.store.attach(row['id'],f'hash{i}','fake.png')
            result={'is_result':True,'mode':4,'players':[{'name':name,'place':j+1,'points':points} for j,(name,points) in enumerate([(f'玩家{i:02}',40000+i*100),('甲',30000),('乙',20000),('丙',10000-i*100)])]}
            p.store.save(f'hash{i}','accepted',result)
        return p
    async def test_full_board_paging_keeps_filters_owner_mode_and_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.seed(folder);before=p.store.db.total_changes
            with patch.object(cards,'leaderboard') as render:
                first=Event();await p.handle(first,'被飞榜 四麻 30天 第1页')
                self.assertEqual(render.call_args.kwargs['page'],1)
                next_event=Event();await p.handle(next_event,'下一页')
                self.assertEqual(render.call_args.kwargs['page'],2);self.assertEqual(render.call_args.args[5],30)
                self.assertIn('第3页',render.call_args.kwargs['nav'][1])
                await p.handle(Event(),'第3页');self.assertEqual(render.call_args.kwargs['page'],3)
                end=Event();await p.handle(end,'下一页');self.assertIn('最后一页',end.sent[-1])
                self.assertEqual(p.board_cache().get(p.selection_key(end))['modes'][4]['page'],3)
                await p.handle(Event(),'上一页');self.assertEqual(render.call_args.kwargs['page'],2)
                for other in (Event(sender='other'),Event(group='999'),Event(bot='other')):
                    await p.handle(other,'下一页');self.assertIn('没有最近',other.sent[-1])
                expired=p.board_cache().get(p.selection_key(first));expired['stamp']-=601
                e=Event();await p.handle(e,'下一页');self.assertIn('超过10分钟',e.sent[-1])
            self.assertEqual(p.store.db.total_changes,before)
    async def test_pending_score_pages_and_formal_board_stay_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.seed(folder)
            with patch.object(cards,'score_board') as render:
                await p.handle(Event(),'综合榜 四麻')
                self.assertFalse(render.call_args.kwargs['provisional'])
                await p.handle(Event(),'暂定榜 四麻 第2页')
                self.assertTrue(render.call_args.kwargs['provisional']);self.assertEqual(render.call_args.kwargs['page'],2)
                await p.handle(Event(),'下一页');self.assertEqual(render.call_args.kwargs['page'],3)
                await p.handle(Event(),'上一页');self.assertEqual(render.call_args.kwargs['page'],2)
                self.assertTrue(render.call_args.kwargs['provisional'])
    async def test_default_score_without_formal_players_and_archive_context(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.seed(folder);periods.cutover(p.store,'12345:987654321')
            with patch.object(cards,'score_board') as render:
                await p.handle(Event(),'暂定榜 四麻 S0')
                await p.handle(Event(),'下一页')
                self.assertEqual(render.call_args.args[4],'S0赛季')
                e=Event();await p.handle(e,'综合榜 四麻 S1');self.assertTrue(render.call_args.kwargs['provisional'])
                e=Event();await p.handle(e,'下一页');self.assertIn('最后一页',e.sent[-1])
    async def test_dual_mode_requires_explicit_choice_and_extremes_do_not_repeat_original(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.seed(folder)
            with patch.object(cards,'leaderboard'),patch.object(p,'original',new=AsyncMock()) as original:
                await p.handle(Event(),'最高 四麻');original.assert_awaited_once()
                await p.handle(Event(),'下一页');original.assert_awaited_once()
                await p.handle(Event(),'被飞榜')
                # Both-mode context with nonempty pools requires an explicit mode.
                context=p.board_cache().get(p.selection_key(Event()));context['modes'][3]['total']=12
                e=Event();await p.handle(e,'下一页');self.assertIn('指定模式',e.sent[-1])
                e=Event();await p.handle(e,'下一页 四麻');self.assertIsInstance(e.sent[-1],list)
                e=Event();await p.handle(e,'原图 20 第2页');self.assertIn('页码只用于榜单',e.sent[-1])
