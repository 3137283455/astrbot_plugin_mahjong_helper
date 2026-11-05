import asyncio,tempfile,unittest
from contextlib import suppress
from types import SimpleNamespace
import astrbot.api.message_components as Comp
from astrbot_plugin_mahjong_helper.replay_bridge import ReplayService
from astrbot_plugin_mahjong_helper.majsoul_api import KoromoCapRequired
from test_ai_entry import plugin
from test_replay_queue import Request,Context,until
from test_replay import fixture

class FastService(ReplayService):
    async def run_job(self,job):
        job['remote_task']='953bc4e73316d17b';await self.db_call('update',job['id'],remote_task=job['remote_task'])
        await self.db_call('complete',job,fixture(job['seat']))

class BatchFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_que_menu_preserves_batch_line_breaks(self):
        with tempfile.TemporaryDirectory() as folder:
            p=plugin(folder);p.replays=ReplayService(Context(),folder);e=Request()
            async for _ in p.koromo_menu(e,'复盘 批量\nhttps://game.maj-soul.com/1/?paipu=abcdefghijklmnop 西家\nhttps://game.maj-soul.com/1/?paipu=abcdefghijklmnop1 南家'):pass
            batch=p.replays.batches.get(e.unified_msg_origin,e.actor)
            self.assertEqual(len(batch['items']),2)
    async def test_one_group_summary_no_individual_spam_and_query_by_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            c=Context();s=FastService(c,folder);e=Request()
            await s.handle(e,'批量\nhttps://game.maj-soul.com/1/?paipu=abcdefghijklmnop 西家\nhttps://game.maj-soul.com/1/?paipu=abcdefghijklmnop1 南家')
            self.assertIn('批次 B',e.sent[0].get_plain_text());task=asyncio.create_task(s.run())
            try:
                await until(lambda:len(c.sent)==1)
                chain=c.sent[0][1];self.assertEqual(str(chain.chain[0].qq),e.actor);self.assertTrue(any(isinstance(p,Comp.Image) for p in chain.chain))
                self.assertIn('完整链接可查看每一手',chain.get_plain_text());await asyncio.sleep(.1);self.assertEqual(len(c.sent),1)
                await s.handle(e,'汇总');self.assertTrue(any(isinstance(p,Comp.Image) for p in e.sent[-1].chain))
                await s.handle(e,'状态');self.assertIn('成功2',e.sent[-1].get_plain_text())
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):await task
    async def test_recent_cap_error_does_not_create_fake_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            s=ReplayService(Context(),folder);e=Request()
            async def load(event,text):raise KoromoCapRequired('对局接口需要CAP授权')
            s.recent_loader=load;await s.handle(e,'最近 玩家 5')
            self.assertIn('暂未提交复盘任务',e.sent[-1].get_plain_text())
            with s.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
    async def test_recent_uid_maps_actual_seats_and_chronological_order(self):
        with tempfile.TemporaryDirectory() as folder:
            p=plugin(folder);calls=[]
            async def records(uid,mode,limit,modes):
                calls.append((uid,mode,limit,modes))
                return [{'uuid':'abcdefghijklmnop2','players':[{'accountId':8},{'accountId':12105509},{'accountId':9},{'accountId':10}]},{'uuid':'abcdefghijklmnop1','players':[{'accountId':8},{'accountId':9},{'accountId':12105509},{'accountId':10}]}]
            p.koromo=SimpleNamespace(recent_records=records)
            sources=await p._recent_replay_sources(Request(),'12105509 2 玉')
            self.assertEqual([s['seat'] for s in sources],[2,1]);self.assertTrue(sources[0]['url'].endswith('abcdefghijklmnop1_a12105509'))
            self.assertEqual(calls,[('12105509',4,2,'12')])

if __name__=='__main__':unittest.main()
