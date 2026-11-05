import tempfile,unittest
from pathlib import Path
from astrbot_plugin_mahjong_helper.replay_core import ReplayStore,ReplayError,parse_url,review_positions,summarize,report_url
from astrbot_plugin_mahjong_helper.replay_batch import ReplayBatches,parse_sources
from test_replay import fixture

class BatchTests(unittest.TestCase):
    def source(self,n=0):
        source=parse_url('https://game.maj-soul.com/1/?paipu=abcdefghijklmnop'+str(n));source['seat']=1;return source
    def test_parse_each_link_seat_and_reject_missing(self):
        sources=parse_sources('批量\nhttps://game.maj-soul.com/1/?paipu=abcdefghijklmnop 西家\nhttps://game.maj-soul.com/1/?paipu=abcdefghijklmnop1 南家')
        self.assertEqual([s['seat'] for s in sources],[2,1])
        with self.assertRaises(ReplayError):parse_sources('批量\nhttps://game.maj-soul.com/1/?paipu=abcdefghijklmnop')
    def test_atomic_enqueue_weighted_summary_and_notification_once(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ReplayStore(folder);batches=ReplayBatches(store)
            batch=batches.create('qq:GroupMessage:12345','10001',[self.source(0),self.source(1),self.source(0)],'group')
            self.assertEqual(len(batch['items']),2);self.assertEqual(batches.ready(),[])
            one=store.next();report=fixture();store.complete(one,report)
            two=store.next();report=fixture();report['review']['kyokus'][0]['entries']=report['review']['kyokus'][0]['entries'][:1];report['review'].update(total_matches=1,total_reviewed=1);store.complete(two,report)
            finished=batches.ready();self.assertEqual(len(finished),1);self.assertEqual(batches.ready(),[])
            summary=batches.summary(finished[0]);self.assertEqual((summary['matches'],summary['total']),(3,4));self.assertEqual(summary['accordance'],.75)
            self.assertTrue(batches.latest_is_batch('qq:GroupMessage:12345','10001'))
            with self.assertRaises(ReplayError):batches.get('qq:GroupMessage:12345','10002',batch['id'])
    def test_cancel_only_unfinished_items_and_offline_queue_persists(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ReplayStore(folder);batches=ReplayBatches(store);batch=batches.create('scope','10001',[self.source(0),self.source(1)],'private')
            job=store.next();store.complete(job,fixture());batches.cancel('scope','10001',batch['id'])
            reloaded=ReplayBatches(ReplayStore(folder)).get('scope','10001',batch['id'])
            self.assertEqual([j['status'] for j in reloaded['items']],['done','cancelled'])
            self.assertIsNone(store.next());self.assertEqual(reloaded['pending'],0)
    def test_no_partial_batch_on_capacity_error(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ReplayStore(folder);batches=ReplayBatches(store)
            with self.assertRaises(ReplayError):batches.create('scope','10001',[self.source(n) for n in range(11)],'private')
            with store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM replay_batches').fetchone()[0],0)

class DeepLinkTests(unittest.TestCase):
    def test_links_map_to_exact_review_event(self):
        r=fixture();r['mjai_log']=[{'type':'start_game','names':['甲','乙','丙','丁']},{'type':'start_kyoku'},
          {'type':'tsumo','actor':1,'pai':'1m'},{'type':'dahai','actor':1,'pai':'2m'},
          {'type':'dahai','actor':0,'pai':'3m'},{'type':'tsumo','actor':2,'pai':'9m'},
          {'type':'tsumo','actor':1,'pai':'4m'},{'type':'end_kyoku'},{'type':'end_game'}]
        for entry,actor,tile in zip(r['review']['kyokus'][0]['entries'],(1,0,1),('1m','3m','4m')):entry.update(last_actor=actor,tile=tile)
        positions=review_positions(r);self.assertEqual(positions,{(0,0):0,(0,1):2,(0,2):4})
        difference=summarize(r)['differences'][0];self.assertEqual(difference['ply'],2)
        self.assertTrue(report_url({'remote_task':'953bc4e73316d17b'},difference['hand_index'],difference['ply']).endswith('&hand=0&ply=2'))
        self.assertEqual(report_url({'remote_task':'../../evil'}),'')

if __name__=='__main__':unittest.main()
