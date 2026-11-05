import json,secrets,tempfile,time,unittest,urllib.request,urllib.error
from pathlib import Path
from astrbot_plugin_mahjong_helper.replay_core import ReplayStore,parse_url,ReplayError
from astrbot_plugin_mahjong_helper.replay_pc import PCQueue,PCGateway,LeaseLost
from test_replay import fixture

class PCQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=ReplayStore(self.temp.name);self.queue=PCQueue(self.store,'desktop')
    def tearDown(self):self.temp.cleanup()
    def create(self,actor='10001'):
        source=parse_url('https://game.maj-soul.com/1/?paipu=abcdefghijklmnop');source['seat']=1
        return self.store.create('napcat:GroupMessage:12345',actor,source,'group')[0]
    def claim(self):return self.queue.call('claim',{})['job']
    def test_serial_claim_and_recover_submitted_task(self):
        a=self.create();b=self.create('10002');job=self.claim();self.assertEqual(job['id'],a['id']);self.assertIsNone(self.claim())
        self.queue.call('progress',{**job,'remote_task':'953bc4e73316d17b','phase':'submitted'})
        with self.store.db() as db:db.execute('UPDATE jobs SET lease_until=0 WHERE id=?',(job['id'],))
        resumed=self.queue.call('resume',job)['job'];self.assertEqual(resumed['remote_task'],'953bc4e73316d17b')
        result=self.queue.call('result',{**job,'report':fixture()});self.assertEqual(result['status'],'done')
        self.assertEqual(self.queue.call('result',{**job,'report':None})['status'],'done')
        self.assertEqual(self.claim()['id'],b['id'])
    def test_expired_lease_cannot_overwrite_new_claim(self):
        self.create();old=self.claim()
        with self.store.db() as db:db.execute('UPDATE jobs SET lease_until=0')
        new=self.claim();self.assertNotEqual(old['lease_token'],new['lease_token'])
        with self.assertRaises(LeaseLost):self.queue.call('result',{**old,'report':fixture()})
    def test_cancel_and_retry_invalidate_old_browser(self):
        j=self.create();old=self.claim();self.store.update(j['id'],status='cancelled',notified=1)
        self.assertFalse(self.queue.call('heartbeat',old)['keep'])
        self.store.retry(j['scope'],j['actor'],j['id'])
        with self.assertRaises(LeaseLost):self.queue.call('resume',old)
        self.assertEqual(self.claim()['status'],'running')
    def test_only_official_41b_and_correct_seat(self):
        self.create();job=self.claim()
        for report in (fixture(2),{**fixture(),'review':{**fixture()['review'],'model_tag':'4.1c'}}):
            with self.assertRaises(ReplayError):self.queue.call('result',{**job,'report':report})
        self.assertEqual(self.store.get('napcat:GroupMessage:12345','10001')['status'],'running')
    def test_verification_notice_once_and_no_requeue_on_bot_restart(self):
        self.create();job=self.claim();self.queue.call('progress',{**job,'phase':'verification'})
        self.assertEqual(len(self.queue.attention()),1);self.assertEqual(self.queue.attention(),[])
        new=PCQueue(self.store,'desktop');self.assertIsNone(new.call('claim',{})['job'])
        self.assertTrue(new.status()['online'])
    def test_gateway_is_loopback_only_and_auth_required(self):
        token=secrets.token_urlsafe(32);config=Path(self.temp.name)/'pc_worker.json';config.write_text(json.dumps({'token':token,'worker_id':'desktop','port':0}))
        gateway=PCGateway(self.store,config);gateway.start()
        try:
            self.assertEqual(gateway.server.server_address[0],'127.0.0.1');port=gateway.server.server_address[1]
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req=urllib.request.Request(f'http://127.0.0.1:{port}/worker/heartbeat',data=b'{}')
            with self.assertRaises(urllib.error.HTTPError) as error:opener.open(req)
            self.assertEqual(error.exception.code,401)
            req.add_header('Authorization','Bearer '+token)
            with opener.open(req) as response:self.assertTrue(json.load(response)['online'])
        finally:gateway.close()

if __name__=='__main__':unittest.main()
