import copy,json,tempfile,unittest
from pathlib import Path
from astrbot_plugin_mahjong_helper.replay_core import *

def fixture(seat=1):
    entries=[{'junme':i+1,'is_equal':i!=1,'actual':{'type':'dahai','pai':'3m'},'expected':{'type':'dahai','pai':'9p'},'actual_index':1,'details':[{},{}]} for i in range(3)]
    return {'player_id':seat,'game_length':'hanchan','mjai_log':[{'type':'start_game','names':['甲','乙','丙','丁']}],'review':{'model_tag':MODEL,'total_reviewed':3,'total_matches':2,'rating':0.891,'kyokus':[{'kyoku':0,'honba':0,'entries':entries}]}}

class ReplayTests(unittest.TestCase):
    def test_parameters_any_order_and_seat_not_account_id(self):
        url='https://game.maj-soul.com/1/?paipu=261104-12345678-1234-1234-1234-123456789abc_a12345678'
        self.assertIsNone(parse_request(url)['source']['seat'])
        for query in (url+' 开局南家','南家 '+url):self.assertEqual(parse_request(query)['source']['seat'],1)
        self.assertEqual(parse_request('分局 南二 #123456abcdef')['hand'],5)
        self.assertEqual(parse_request('分局 南二 #123456abcdef')['job'],'123456abcdef')
    def test_allowed_urls_only(self):
        for url in ('https://localhost/?paipu=abcdefghijkl','https://game.maj-soul.com.evil.test/?paipu=abcdefghijkl','https://root:pass@game.maj-soul.com/1/?paipu=abcdefghijkl','http://game.maj-soul.com/1/?paipu=abcdefghijkl','https://mjai.ekyu.moe/report/../../secret.json','https://game.maj-soul.com:6191/?paipu=abcdefghijkl'):
            with self.subTest(url=url),self.assertRaises(ReplayError):parse_url(url)
        self.assertEqual(parse_url('https://mjai.ekyu.moe/review?task=12345678-abcd')['kind'],'report')
        self.assertEqual(parse_url('https://mjai.ekyu.moe/killerducky/?data=/report/953bc4e73316d17b.json')['remote_task'],'953bc4e73316d17b')
        self.assertEqual(parse_url('https://tenhou.net/0/?log=2019050417gm-0029-0000-4f2a8622&tw=2')['seat'],2)
        for text in ('三麻','东风','东家 南家'):
            with self.assertRaises(ReplayError):parse_request(text)
    def test_factual_summary(self):
        s=summarize(fixture(),1)
        self.assertEqual((s['nickname'],s['matches'],s['total'],s['difference_count']),('乙',2,3,1))
        self.assertAlmostEqual(s['accordance'],2/3);self.assertEqual(s['rating'],89.1)
        self.assertEqual(s['differences'][0]['choice_rank'],2)
    def test_reject_wrong_or_incomplete_reports(self):
        for mutate in (lambda r:r.update(player_id=2),lambda r:r['review'].update(model_tag='4.0'),lambda r:r['review'].update(total_reviewed=9),lambda r:r['review'].update(total_matches=0),lambda r:r['review'].update(kyokus=[])):
            r=fixture();mutate(r)
            with self.assertRaises(ReplayError):summarize(r,1)
        r=fixture();r['review']['model_tag']='4.1c'
        self.assertEqual(summarize(r,1,'')['model'],'4.1c')
    def test_queue_cache_isolation_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ReplayStore(folder);source={'kind':'replay','url':'https://game.maj-soul.com/1/?paipu=abcdefghijklmnop','replay_id':'majsoul:abcdefghijklmnop','seat':1}
            j,created=store.create('group:a','a',source);self.assertTrue(created)
            again,created=store.create('group:a','a',source);self.assertFalse(created);self.assertEqual(j['id'],again['id'])
            with self.assertRaises(ReplayError):store.get('group:a','b',j['id'])
            with self.assertRaises(ReplayError):store.get('group:b','a',j['id'])
            self.assertEqual(store.next()['id'],j['id']);store.recover();self.assertEqual(store.next()['id'],j['id'])
            store.complete(j,fixture());self.assertEqual(store.get('group:a','a')['status'],'done')
            cached,_=store.create('group:a','b',source);self.assertEqual(cached['status'],'done')
            different,_=store.create('group:a','c',{**source,'seat':2});self.assertEqual(different['status'],'queued')
            store.update(different['id'],status='cancelled');self.assertIsNone(store.complete(different,fixture(2)))
            self.assertEqual(store.get('group:a','c')['status'],'cancelled')
    def test_pending_seats_are_per_user_and_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            s=ReplayStore(folder);s.save_pending('group:a','a',{'seat':None,'kind':'replay'})
            self.assertIsNone(s.get_pending('group:b','a'));self.assertIsNone(s.get_pending('group:a','b'));self.assertEqual(s.get_pending('group:a','a')['kind'],'replay')
    def test_import_cache_keeps_actual_model_and_seat(self):
        with tempfile.TemporaryDirectory() as folder:
            s=ReplayStore(folder);source=parse_url('https://mjai.ekyu.moe/report/12345678-abcd.json')
            j,_=s.create('group:a','a',source);r=fixture(2);r['review']['model_tag']='4.1c';s.complete(j,r)
            cached,_=s.create('group:b','b',source)
            self.assertEqual((cached['status'],cached['seat'],cached['model']),('done',2,'4.1c'))

if __name__=='__main__':unittest.main()
