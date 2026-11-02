import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_group_data.core import validate
from astrbot_plugin_group_data.store import Store
from astrbot_plugin_group_data.tools.prepare_v2 import inspect_database


class PreparationTests(unittest.TestCase):
    def test_preview_keeps_normal_games_and_exclusions_without_changing_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.db';store=Store(path);scope='bot:group'
            store.enable(scope,'bot','group')
            store.reconcile(scope,[{'msg':str(n),'idx':0,'stamp':n*100} for n in [1,2]])
            row=store.source(scope,'1',0);original=Path(folder)/'original.png';original.write_bytes(b'cached')
            store.attach(row['id'],'image',original)
            store.save('image','accepted',validate({'is_result':True,'mode':4,'confidence':1,
                'players':[{'name':str(n),'place':n,'points':points}
                           for n,points in enumerate([50000,30000,15000,5000],1)]}))
            excluded=store.source(scope,'2',0);store.exclude_source(scope,excluded)
            before='\n'.join(store.db.iterdump())
            report=inspect_database(path,scope)
            self.assertEqual(report['games_by_mode'],{'3':0,'4':1})
            self.assertEqual(report['latest_fly'][4]['stamp'],0)
            self.assertEqual(len(report['known_sources']),2)
            self.assertEqual(len(report['source_exclusions']),1)
            self.assertEqual(report['accepted_sources_with_missing_originals'],[])
            self.assertTrue(report['preview_only'])
            self.assertFalse(report['cutover']['performed'])
            self.assertFalse(report['online_sync']['enabled'])
            self.assertEqual(report['effective_data_sha256'],inspect_database(path,scope)['effective_data_sha256'])
            self.assertEqual('\n'.join(store.db.iterdump()),before)
            store.close()

    def test_unknown_group_and_missing_database_do_not_create_or_modify_data(self):
        with tempfile.TemporaryDirectory() as folder:
            missing=Path(folder)/'missing.db'
            with self.assertRaises(ValueError):inspect_database(missing,'bot:group')
            self.assertFalse(missing.exists())
            path=Path(folder)/'test.db';store=Store(path)
            before='\n'.join(store.db.iterdump())
            with self.assertRaises(ValueError):inspect_database(path,'unknown')
            self.assertEqual('\n'.join(store.db.iterdump()),before)
            store.close()


if __name__=='__main__':unittest.main()
