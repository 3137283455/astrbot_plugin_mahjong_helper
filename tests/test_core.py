import json
import tempfile
import unittest
from pathlib import Path
from astrbot_plugin_group_data.core import aggregate,ranking,validate,parse_json,signature
from astrbot_plugin_group_data.store import Store
from astrbot_plugin_group_data.source import extract_entries,trusted_url


def result(mode=3,points=None,names=None):
    points=points or ([65000,42000,-2000] if mode==3 else [60000,30000,12000,-2000])
    names=names or ['甲','乙','丙','丁'][:mode]
    return {'is_result':True,'mode':mode,'players':[{'name':n,'place':i+1,'points':p} for i,(n,p) in enumerate(zip(names,points))],'confidence':.99}

class CoreTests(unittest.TestCase):
    def test_flying_is_negative_not_last_and_zero_is_not_flying(self):
        matches=[{'id':1,'result':validate(result(points=[60000,45000,0]))},{'id':2,'result':validate(result())}]
        s=aggregate(matches,3);c=next(p for p in s if p['name']=='丙')
        self.assertEqual((c['fly'],c['last'],c['games']),(1,2,2))
        self.assertEqual(str(ranking(s,'被飞率')[0]['value']),'1/2')
    def test_three_and_four_never_mix(self):
        m=[{'id':1,'result':validate(result(3))},{'id':2,'result':validate(result(4))}]
        self.assertEqual(sum(s['games'] for s in aggregate(m,3)),3)
        self.assertEqual(sum(s['games'] for s in aggregate(m,4)),4)
    def test_tie_ranks_and_minimum(self):
        m=[{'id':1,'result':validate(result())},{'id':2,'result':validate(result(points=[65000,42000,-2000],names=['乙','甲','丁']))}]
        r=ranking(aggregate(m,3),'一位率',2)
        self.assertEqual([x['position'] for x in r],[1,1])
        self.assertEqual([x['games'] for x in r],[2,2])
    def test_reject_rank_scores_missing_negative_and_duplicate_names(self):
        for obj in [result(points=[45.7,3.4,-49.1]),result(points=[100,200,300]),result(names=['甲','甲','乙']),{**result(),'mode':True},{**result(),'confidence':.7}]:
            with self.assertRaises(ValueError):validate(obj)
        self.assertIsNone(validate({'is_result':False}))
    def test_nonstandard_total_requires_explicit_manual_confirmation(self):
        obj=result(points=[65000,42000,2000])
        with self.assertRaises(ValueError):validate(obj)
        self.assertIsNotNone(validate(obj,approve=True))
    def test_json_and_complete_names(self):
        obj=result(names=['一剑风起醉英豪','昵称 空格','かなABC_'])
        self.assertEqual(validate(parse_json('```json\n'+json.dumps(obj)+'\n```'))['players'][0]['name'],'一剑风起醉英豪')
        with self.assertRaises(ValueError):parse_json('说明'+json.dumps(obj))
    def test_multiperson_flying_and_profile_extremes(self):
        m=[{'id':8,'result':validate(result(points=[109000,-1000,-3000]))}]
        s=aggregate(m,3);self.assertEqual(sum(x['fly'] for x in s),2)
        self.assertEqual(ranking(s,'最低')[0]['min_points_source'],8)
    def test_essence_ignores_text_and_retains_image_positions(self):
        r,c=extract_entries([{'message_id':1,'operator_time':10,'content':[{'type':'text','data':{'text':'hello'}}]},{'message_id':2,'content':[{'type':'text'},{'type':'image','data':{'url':'https://qpic.cn/a'}},{'type':'image','data':{'url':'https://qpic.cn/b'}}]}])
        self.assertEqual(c,{'messages':2,'text':1});self.assertEqual([x['idx'] for x in r],[1,2])
        for obj in [None,{'list':[]},[{'message_id':1}]]:
            with self.assertRaises(ValueError):extract_entries(obj)
    def test_attachment_domains_no_ssrf(self):
        for url in ['http://127.0.0.1/a','file:///bot/a','https://qpic.cn.attacker.com/a','https://qpic.cn:8788/a','https://user:pass@qpic.cn/a']:
            self.assertFalse(trusted_url(url))
        self.assertTrue(trusted_url('https://multimedia.nt.qq.com.cn/download?rkey=x'))

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'test.db');self.scope='bot:group';self.s.enable(self.scope,'bot','group')
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def seed(self,msg,digest='same',data=None):
        entries=[{'msg':str(msg),'idx':0,'stamp':100}]
        self.s.reconcile(self.scope,entries);r=self.s.source(self.scope,str(msg),0);self.s.attach(r['id'],digest,'fake.png');self.s.save(digest,'accepted',validate(data or result()));return r
    def test_idempotent_import_removed_and_restored(self):
        r=self.seed(1);self.seed(1)
        self.assertEqual(len(self.s.matches(self.scope)),1)
        self.s.reconcile(self.scope,[]);self.assertEqual(self.s.matches(self.scope),[])
        self.s.reconcile(self.scope,[{'msg':'1','idx':0,'stamp':100}]);self.assertEqual(len(self.s.matches(self.scope)),1)
        self.assertEqual(self.s.matches('another-group'),[])
    def test_same_result_multiple_pictures_deduplicated(self):
        entries=[{'msg':str(i),'idx':0,'stamp':100} for i in (1,2)]
        self.s.reconcile(self.scope,entries)
        for i in (1,2):
            r=self.s.source(self.scope,str(i),0);self.s.attach(r['id'],str(i),'fake.png');self.s.save(str(i),'accepted',validate(result()))
        self.assertEqual(len(self.s.matches(self.scope)),1)
        self.s.reconcile(self.scope,entries[:1]);self.assertEqual(len(self.s.matches(self.scope)),1)
    def test_overrides_are_group_scoped_and_persist_restart(self):
        r=self.seed(1);r=self.s.get_source(self.scope,r['id']);self.s.override(self.scope,r,'ignored')
        self.assertFalse(self.s.matches(self.scope));self.s.close();self.s=Store(Path(self.tmp.name)/'test.db')
        self.assertFalse(self.s.matches(self.scope))
        self.s.enable('other','bot','other');self.s.reconcile('other',[{'msg':'1','idx':0,'stamp':100}]);r=self.s.source('other','1',0);self.s.attach(r['id'],'same','fake.png')
        self.assertEqual(len(self.s.matches('other')),1)
    def test_merge_rejects_players_in_same_game_and_cycles(self):
        self.seed(1)
        with self.assertRaises(ValueError):self.s.merge(self.scope,'甲','乙')
        self.s.merge(self.scope,'旧名','新名')
        with self.assertRaises(ValueError):self.s.merge(self.scope,'新名','旧名')
        self.s.unmerge(self.scope,'旧名');self.assertEqual(self.s.aliases(self.scope),{})
    def test_model_error_does_not_drop_previous_data(self):
        self.seed(1);self.s.scan_error(self.scope,'API失败')
        self.assertEqual(len(self.s.matches(self.scope)),1)
    def test_unapproved_results_do_not_enter_statistics(self):
        r=self.seed(1);self.s.save('same','review',raw=[result()],reason='名字不清')
        self.assertFalse(self.s.matches(self.scope));self.assertEqual(self.s.summary(self.scope)['review'],1)

if __name__=='__main__':unittest.main()
