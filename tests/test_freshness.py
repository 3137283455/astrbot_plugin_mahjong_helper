import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from astrbot_plugin_group_data.core import validate
from astrbot_plugin_group_data.main import parse_query
from astrbot_plugin_group_data.store import Store
import test_commands
from test_commands import Event
from test_core import result


class FreshnessStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'test.db')
        self.scope='bot:a';self.store.enable(self.scope,'bot','a')

    def tearDown(self):
        self.store.close();self.temp.cleanup()

    def attach(self,scope='bot:a',digest='image'):
        self.store.reconcile(scope,[{'msg':'1','idx':0,'stamp':1}])
        row=self.store.source(scope,'1',0)
        self.store.attach(row['id'],digest,'image.png')
        return self.store.source(scope,'1',0)

    def test_scan_pending_and_failure_do_not_advance_import_time(self):
        with patch('astrbot_plugin_group_data.store.time.time',return_value=100):self.attach()
        with patch('astrbot_plugin_group_data.store.time.time',return_value=200):
            self.store.save('image','review',raw=[result()])
        self.assertEqual(self.store.freshness(self.scope)['last_import'],0)
        data=validate(result())
        with patch('astrbot_plugin_group_data.store.time.time',return_value=300):self.store.save('image','accepted',data)
        with patch('astrbot_plugin_group_data.store.time.time',return_value=400):self.attach()
        with patch('astrbot_plugin_group_data.store.time.time',return_value=500):self.store.save('image','accepted',data)
        self.store.scan_error(self.scope,'网络超时')
        info=self.store.freshness(self.scope)
        self.assertEqual((info['last_scan'],info['last_import']),(400,300))

    def test_cached_results_and_manual_overrides_are_group_scoped(self):
        row=self.attach();data=validate(result())
        with patch('astrbot_plugin_group_data.store.time.time',return_value=100):self.store.save('image','accepted',data)
        self.store.override(self.scope,row,'ignored')
        self.store.enable('bot:b','bot','b')
        with patch('astrbot_plugin_group_data.store.time.time',return_value=200):self.attach('bot:b')
        self.assertEqual(self.store.freshness('bot:b')['last_import'],200)
        with patch('astrbot_plugin_group_data.store.time.time',return_value=300):
            self.store.save('image','accepted',validate(result(names=['甲甲','乙乙','丙丙'])))
        self.assertEqual(self.store.freshness(self.scope)['last_import'],100)
        self.assertEqual(self.store.freshness('bot:b')['last_import'],300)
        with patch('astrbot_plugin_group_data.store.time.time',return_value=400):self.store.override(self.scope,row,'accepted',data)
        self.assertEqual(self.store.freshness(self.scope)['last_import'],400)
        self.assertEqual(self.store.freshness('bot:b')['last_import'],300)

    def test_excluded_sources_never_advance_import_time(self):
        row=self.attach();self.store.exclude_source(self.scope,row)
        self.store.save('image','accepted',validate(result()))
        self.assertEqual(self.store.freshness(self.scope)['last_import'],0)

    def test_old_database_migration_is_additive_and_does_not_invent_history(self):
        path=Path(self.temp.name)/'old.db'
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE groups(scope TEXT PRIMARY KEY, bot TEXT, gid TEXT, enabled INTEGER DEFAULT 1, last_scan REAL DEFAULT 0, error TEXT DEFAULT '')")
            db.execute("INSERT INTO groups VALUES('old','bot','group',1,123,'')")
        old=Store(path)
        self.assertEqual((old.group('old')['last_scan'],old.group('old')['last_import']),(123,0))
        row_scope='old';old.reconcile(row_scope,[{'msg':'a','idx':0,'stamp':1}]);row=old.source(row_scope,'a',0)
        old.attach(row['id'],'hash','a.png')
        with patch('astrbot_plugin_group_data.store.time.time',return_value=456):old.save('hash','accepted',validate(result()))
        old.close();old=Store(path)
        self.assertEqual(old.group('old')['last_import'],456)
        self.assertEqual(len(old.matches('old')),1)
        old.close()


class FreshnessCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_members_are_rejected_admin_gets_plain_text_and_aliases_work(self):
        helper=test_commands.CommandTests()
        try:
            with tempfile.TemporaryDirectory() as folder:
                plugin=helper.setup_plugin(folder)
                for name in ['时间','更新时间','统计时间','同步时间']:
                    self.assertEqual(parse_query(name)['action'],'时间')
                    member=Event();await plugin.handle(member,name)
                    self.assertEqual(len(member.sent),1)
                    self.assertIn('仅限本群群主',member.sent[0])
                admin=Event(admin=True)
                with patch('astrbot_plugin_group_data.main.cards.text_card',side_effect=AssertionError('must not render a card')):
                    await plugin.handle(admin,'时间')
                self.assertEqual(len(admin.sent),1)
                message=admin.sent[0]
                self.assertIsInstance(message,str)
                for label in ['北京时间','查询时间','最近成功检查精华','最近结果入库','待识别','升级后暂无新入库']:
                    self.assertIn(label,message)
                self.assertRegex(message,r'查询时间：\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')
        finally:helper.doCleanups()


if __name__=='__main__':unittest.main()
