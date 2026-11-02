import json
import tempfile
import unittest
from pathlib import Path
from astrbot_plugin_group_data.store import Store
from astrbot_plugin_group_data import periods
from astrbot_plugin_group_data.core import validate,aggregate


def normal(names=None):
    return validate({'is_result':True,'mode':4,'confidence':1,'players':[
        {'name':name,'place':i+1,'points':points} for i,(name,points) in enumerate(zip(names or ['甲','乙','丙','丁'],[50000,30000,15000,5000]))]})


class PeriodTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'test.db');self.scope='bot:group'
        self.store.enable(self.scope,'bot','group')
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def seed(self,msg,image='a',names=None):
        old=[{'msg':r['msg'],'idx':r['idx'],'stamp':r['stamp']} for r in self.store.db.execute('SELECT * FROM sources WHERE scope=?',(self.scope,))]
        self.store.reconcile(self.scope,[*old,{'msg':str(msg),'idx':0,'stamp':int(msg)}])
        row=self.store.source(self.scope,str(msg),0);self.store.attach(row['id'],image,'test.png')
        self.store.save(image,'accepted',normal(names));return self.store.source(self.scope,str(msg),0)
    def test_atomic_cutover_preserves_all_history_and_zeroes_only_new_period(self):
        row=self.seed(1);self.store.merge(self.scope,'旧名','新名')
        c=periods.cutover(self.store,self.scope,now=200)
        self.assertEqual(self.store.matches(self.scope),[])
        self.assertEqual(self.store.summary(self.scope)['games'],0)
        self.assertEqual(self.store.freshness(self.scope)['last_import'],0)
        self.assertEqual(len(self.store.matches(self.scope,period='历史')),1)
        old=periods.select(self.store,self.scope,'历史');snapshot=old['snapshot']
        self.store.unmerge(self.scope,'旧名')
        self.assertEqual(self.store.aliases(self.scope,'历史'),{'旧名':'新名'})
        self.store.reconcile(self.scope,[]);self.store.save('a','ignored')
        self.assertEqual(len(self.store.matches(self.scope,period='历史')),1)
        self.assertEqual(periods.select(self.store,self.scope,'历史')['snapshot'],snapshot)
        self.assertEqual(self.store.original_source(self.scope,row['id'])['id'],row['id'])
        with self.assertRaises(ValueError):periods.cutover(self.store,self.scope)
        self.assertEqual(c['games'],1)
    def test_old_sources_and_reposted_hashes_never_reenter_after_rescan_or_restart(self):
        row=self.seed(1);periods.cutover(self.store,self.scope)
        self.seed(1);self.seed(2,'a')
        self.assertEqual(self.store.rows(self.scope),[])
        self.assertEqual(self.store.freshness(self.scope)['last_import'],0)
        self.store.close();self.store=Store(Path(self.tmp.name)/'test.db')
        self.assertEqual(self.store.matches(self.scope),[])
        self.assertEqual(len(self.store.matches(self.scope,period='历史')),1)
        with self.assertRaises(ValueError):self.store.override(self.scope,row,'ignored')
    def test_normal_positive_games_count_all_places_and_rates_have_new_period_denominator(self):
        self.seed(1);periods.cutover(self.store,self.scope)
        self.seed(2,'new',['A','B','C','D']);matches=self.store.matches(self.scope)
        self.assertEqual(len(matches),1);stats=aggregate(matches,4)
        self.assertEqual(sum(s['fly'] for s in stats),0)
        self.assertEqual(sum(s['games'] for s in stats),4)
        self.assertEqual([s['rank'+str(i+1)] for i,s in enumerate(stats)],[1,1,1,1])
    def test_other_groups_legacy_data_unaffected(self):
        self.seed(1);self.store.enable('bot:other','bot','other')
        self.store.reconcile('bot:other',[{'msg':'1','idx':0,'stamp':100}]);r=self.store.source('bot:other','1',0)
        self.store.attach(r['id'],'a','test.png');periods.cutover(self.store,self.scope)
        self.assertEqual(len(self.store.matches('bot:other')),1)
    def test_manual_rare_name_evidence_survives_without_entering_new_statistics(self):
        row=self.seed(1);row=self.store.get_source(self.scope,row['id'])
        self.store.override(self.scope,row,'accepted',normal(['慪龢','鱈灥','I咲仡','姊睇归']))
        periods.cutover(self.store,self.scope)
        rows=self.store.training_rows(self.scope)
        self.assertEqual(len(rows),1)
        self.assertEqual(json.loads(rows[0]['override_result'])['players'][0]['name'],'慪龢')
        self.assertEqual(self.store.matches(self.scope),[])
    def test_failed_transaction_leaves_legacy_period_and_data_untouched(self):
        self.seed(1)
        self.store.db.execute("CREATE TRIGGER fail_period BEFORE INSERT ON periods BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END")
        with self.assertRaises(Exception):periods.cutover(self.store,self.scope)
        self.assertEqual(periods.all_periods(self.store,self.scope),[])
        self.assertEqual(len(self.store.matches(self.scope)),1)


if __name__=='__main__':unittest.main()
