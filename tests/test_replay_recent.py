import asyncio,importlib.util,json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from astrbot_plugin_mahjong_helper.replay_core import ReplayStore,ReplayError,parse_url
from astrbot_plugin_mahjong_helper.replay_pc import PCQueue,LeaseLost
from astrbot_plugin_mahjong_helper.replay_recent import recent_request,create_recent,recent_sources
from astrbot_plugin_mahjong_helper.replay_bridge import ReplayService
from test_replay import fixture
from test_replay_queue import Request,Context
from test_ai_entry import plugin

def record(number=1,mode=12,seat=2):
    players=[{'accountId':i+10,'score':30000-i*5000} for i in range(4)]
    players[seat]['accountId']=12105509
    return {'uuid':'261105-abcdefghijklmnop'+str(number),'modeId':mode,'startTime':1730000000+number,'players':players}

class RecentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=ReplayStore(self.temp.name);self.queue=PCQueue(self.store,'desktop')
    def tearDown(self):self.temp.cleanup()
    def create(self,query='12105509 2 玉'):
        return create_recent(self.store,'napcat:GroupMessage:12345','10001',recent_request(query),'group')[0]
    def test_requests_and_binding(self):
        self.assertEqual(recent_request('3 王','12105509'),{'kind':'recent','player':'12105509','count':3,'modes':[16]})
        self.assertEqual(recent_request('5 名 字 玉')['player'],'名 字')
        with self.assertRaises(ReplayError):recent_request('')
    def test_recent_fifo_dedup_and_private_payload(self):
        job=self.create();self.assertEqual(self.create()['id'],job['id'])
        source=parse_url('https://game.maj-soul.com/1/?paipu=abcdefghijklmnop');source['seat']=1
        other=self.store.create('private','other',source,'private')[0]
        claimed=self.queue.call('claim',{})['job']
        self.assertEqual(claimed['kind'],'recent');self.assertEqual(claimed['request']['player'],'12105509')
        self.assertNotIn('actor',claimed);self.assertNotIn('scope',claimed);self.assertIsNone(self.queue.call('claim',{})['job'])
        self.store.update(job['id'],status='cancelled',notified=1)
        self.assertEqual(self.queue.call('claim',{})['job']['id'],other['id'])
    def test_atomic_conversion_and_retry_ack_does_not_duplicate(self):
        job=self.create();claimed=self.queue.call('claim',{})['job']
        self.queue.call('progress',{**claimed,'phase':'verification'})
        self.assertEqual(len(self.queue.attention()),1);self.assertEqual(self.queue.attention(),[])
        result={'uid':'12105509','nickname':'目标玩家','records':[record(3,seat=1),record(1),record(2)]}
        reply=self.queue.call('result',{**claimed,'records_result':result})
        self.assertEqual(reply['status'],'done');batch=self.queue.batches.get(job['scope'],job['actor'],reply['batch_id'])
        self.assertEqual([j['seat'] for j in batch['items']],[2,1])
        self.assertTrue(batch['items'][0]['url'].endswith('mnop2_a12105509'))
        self.assertEqual(json.loads(batch['items'][0]['metadata'])['rank'],3)
        self.assertEqual(self.queue.call('result',{**claimed,'records_result':None})['status'],'done')
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM replay_batches').fetchone()[0],1)
        self.assertTrue(self.queue.batches.latest_is_batch(job['scope'],job['actor']))
        self.assertEqual(self.queue.call('claim',{})['job']['kind'],'replay')
    def test_cancel_and_expired_lookup_never_create_batches(self):
        job=self.create();old=self.queue.call('claim',{})['job']
        with self.store.db() as db:db.execute('UPDATE jobs SET lease_until=0')
        new=self.queue.call('claim',{})['job']
        result={'uid':'12105509','records':[record()]}
        with self.assertRaises(LeaseLost):self.queue.call('result',{**old,'records_result':result})
        self.store.update(job['id'],status='cancelled',notified=1)
        with self.assertRaises(LeaseLost):self.queue.call('result',{**new,'records_result':result})
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM replay_batches').fetchone()[0],0)
    def test_reject_wrong_uid_seat_mode_and_malformed_replay(self):
        request=recent_request('12105509 2 玉')
        bad=record();bad['players'][2]['accountId']=999
        invalid=record();invalid['uuid']='https://evil.test'
        for result in ({'uid':'9','records':[record()]},{'uid':'12105509','records':[bad]},{'uid':'12105509','records':[record(mode=9)]},{'uid':'12105509','records':[invalid]}):
            with self.assertRaises(ReplayError):recent_sources(request,result)
    def test_combined_rooms_choose_newest_then_order_by_time(self):
        sources=recent_sources(recent_request('12105509 2'),{'uid':'12105509','records':[record(1,9),record(5,16),record(3,12),record(5,16)]})
        self.assertEqual([s['record']['mode'] for s in sources],[12,16])

class RecentFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_pc_entry_never_calls_server_records_or_search(self):
        with tempfile.TemporaryDirectory() as folder:
            p=plugin(folder);p.config['replay_backend']='pc'
            p.koromo=SimpleNamespace(recent_records=AsyncMock(side_effect=AssertionError('server records called')),search_player=AsyncMock(side_effect=AssertionError('server search called')))
            s=ReplayService(Context(),folder,{'replay_backend':'pc'});s.recent_loader=p._recent_replay_sources;e=Request()
            await s.handle(e,'最近 目标玩家 2 玉')
            job=s.store.get(e.unified_msg_origin,e.actor);self.assertEqual(job['kind'],'recent');self.assertEqual(job['status'],'queued')
            p.koromo.recent_records.assert_not_called();p.koromo.search_player.assert_not_called()
            await s.handle(e,'取消');self.assertEqual(s.store.get(e.unified_msg_origin,e.actor)['status'],'cancelled')
    async def test_list_then_one_final_summary_and_explicit_lookup_result(self):
        with tempfile.TemporaryDirectory() as folder:
            c=Context();s=ReplayService(c,folder,{'replay_backend':'pc'});q=PCQueue(s.store,'desktop');e=Request()
            lookup=create_recent(s.store,e.unified_msg_origin,e.actor,recent_request('12105509 2 玉'),'group')[0]
            claim=q.call('claim',{})['job'];reply=q.call('result',{**claim,'records_result':{'uid':'12105509','nickname':'玩家','records':[record(1),record(2)]}})
            lookup=s.store.get(e.unified_msg_origin,e.actor,lookup['id']);await s.notify(lookup)
            self.assertEqual(len(c.sent),1);self.assertIn('已取得 玩家 最近2场',c.sent[0][1].get_plain_text())
            for _ in range(2):
                job=q.call('claim',{})['job'];q.call('result',{**job,'report':fixture(2)})
            await s.notify_batches();await s.notify_batches();self.assertEqual(len(c.sent),2)
            chain=c.sent[-1][1];self.assertEqual(str(chain.chain[0].qq),e.actor)
            self.assertIn('第3位',chain.get_plain_text());self.assertIn('开局西家',chain.get_plain_text());self.assertIn('paipu=',chain.get_plain_text())
            await s.handle(e,'结果 '+lookup['id']);self.assertIn(reply['batch_id'],e.sent[-1].get_plain_text())

class BrowserPage:
    def __init__(self):self.handlers=[];self.urls=[];self.evaluations=[]
    def on(self,event,handler):self.handlers.append(handler)
    def remove_listener(self,event,handler):self.handlers.remove(handler)
    def is_closed(self):return False
    async def evaluate(self,script,args):
        self.evaluations.append(args);return {'status':200,'data':[{'id':12105509,'nickname':'玩家'}]}
    async def goto(self,url,**kwargs):
        self.urls.append(url)
        if '/player/' not in url:return
        mode=int(url.rsplit('/',1)[1])
        class Response:
            status=200
            url='https://5-data.amae-koromo.com/api/v2/pl4/player_records/12105509/999/0?mode='+str(mode)
            async def json(self):return [record(mode,mode)]
        for handler in self.handlers:handler(Response())

class BrowserCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_page_records_only_and_listener_cleanup(self):
        path=Path(__file__).resolve().parents[1]/'pc_worker/amae.py';spec=importlib.util.spec_from_file_location('amae_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        page=BrowserPage();worker=SimpleNamespace(current={'request':recent_request('玩家 2')},progress=AsyncMock(),ensure_valid=AsyncMock(),get_page=AsyncMock(return_value=page))
        result=await module.fetch_recent(worker)
        self.assertEqual(result['uid'],'12105509');self.assertEqual([r['modeId'] for r in result['records']],[16,12])
        self.assertEqual(page.handlers,[]);self.assertEqual(len(page.urls),4)
        # No protected records API is generated by the collector; only the page produces it.
        self.assertEqual(page.evaluations,[[module.API,'玩家']])

if __name__=='__main__':unittest.main()
