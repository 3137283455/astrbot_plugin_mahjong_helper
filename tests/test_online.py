import copy
import asyncio
import tempfile
import unittest
from pathlib import Path
from astrbot_plugin_group_data.online import OnlineSync,normalize
from astrbot_plugin_group_data.feishu import Feishu,FeishuError
from astrbot_plugin_group_data import periods
from astrbot_plugin_group_data.store import Store
from test_periods import normal


class FakeFeishu:
    def __init__(self):self.rows={k:{} for k in ['matches','periods','players','sync_log']};self.counter=0;self.uploads=0;self.updates=0;self.fail=False
    async def records(self,table):
        if self.fail:raise FeishuError('simulated listing failure')
        return copy.deepcopy(list(self.rows[table].values()))
    async def ensure_fields(self,table,names):pass
    async def create(self,table,fields):
        self.counter+=1;rid='rec'+str(self.counter)
        row={'record_id':rid,'fields':copy.deepcopy(fields),'last_modified_by':{'id':'bot'}}
        self.rows[table][rid]=row;return copy.deepcopy(row)
    async def batch_create(self,table,fields):return [await self.create(table,f) for f in fields]
    async def update(self,table,rid,fields):
        self.updates+=1
        self.rows[table][rid]['fields'].update(copy.deepcopy(fields));self.rows[table][rid]['last_modified_by']={'id':'bot'}
    async def upload(self,path):self.uploads+=1;return 'token'+str(self.uploads)
    async def download(self,token,folder):return 'onlineimage',Path(folder)/'online.png'
    def edit(self,rid,fields,actor='owner'):
        row=self.rows['matches'][rid];row['fields'].update(fields);row['last_modified_by']={'id':actor}


class OnlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_score_fields_are_derived_for_current_season_and_cleared_after_removal(self):
        key,rid,source=await self.current()
        all_fields=[normalize(r['fields']) for r in self.fake.rows['players'].values()]
        self.assertEqual({r['赛季'] for r in all_fields},{'S1赛季'})
        self.assertTrue(all(r['评分有效场次']==1 and r['综合评分状态']=='暂定' for r in all_fields))
        self.assertTrue(all('综合评分排名' not in r for r in all_fields))
        self.assertTrue(all(0<=r['综合评分']<=100 for r in all_fields))
        self.fake.edit(rid,{'确认状态':'停用'});await self.online.once()
        current=periods.select(self.store,self.scope)['id']
        cleared=[normalize(r['fields']) for r in self.fake.rows['players'].values() if r['fields']['周期ID']==current]
        self.assertTrue(all(r['评分有效场次']==0 and '综合评分' not in r and '综合评分排名' not in r for r in cleared))
        self.assertTrue(all(r['综合评分状态']=='无有效评分' for r in cleared))
    def test_real_feishu_numeric_strings_are_normalized_before_validation(self):
        self.assertEqual(normalize({'模式':'4','一位持点':'-1000','被飞率':'0.25','精华时间':'1793590000000','对局ID':'214'}),
                         {'模式':4,'一位持点':-1000,'被飞率':0.25,'精华时间':1793590000000,'对局ID':'214'})
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.folder=Path(self.tmp.name);self.store=Store(self.folder/'db')
        self.scope='bot:group';self.store.enable(self.scope,'bot','group');self.seed('1','old')
        periods.cutover(self.store,self.scope,now=100)
        self.fake=FakeFeishu();self.online=OnlineSync(self.store,self.folder,{'app_token':'base','scope':self.scope,'editor_ids':['owner'],'bot_open_id':'bot',
            'tables':{k:k for k in self.fake.rows}},self.fake)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def seed(self,msg,image):
        entries=[{'msg':r['msg'],'idx':0,'stamp':r['stamp']} for r in self.store.db.execute('SELECT * FROM sources WHERE scope=?',(self.scope,))]
        self.store.reconcile(self.scope,[*entries,{'msg':msg,'idx':0,'stamp':200}]);r=self.store.source(self.scope,msg,0)
        self.store.attach(r['id'],image,'test.png');self.store.save(image,'accepted',normal());return self.store.get_source(self.scope,r['id'])
    async def current(self):
        source=self.seed('2','new');await self.online.once()
        key=self.scope+':'+periods.select(self.store,self.scope)['id']+':'+str(source['id'])
        return key,self.online.links()[key]['record_id'],source
    async def test_export_idempotency_preserves_archives_and_uploads_original_once(self):
        await self.current();updates=self.fake.updates
        await self.online.once()
        self.assertEqual(len(self.fake.rows['matches']),1);self.assertEqual(self.fake.uploads,1)
        self.assertEqual(len(self.fake.rows['players']),4)
        # Only the successful-sync timestamp changes on a no-op cycle.
        self.assertLessEqual(self.fake.updates-updates,1)
    async def test_online_edit_changes_local_cards_source_and_derived_stats_same_cycle(self):
        key,rid,source=await self.current()
        self.fake.edit(rid,{'一位昵称':'生僻鱈灥','一位持点':51000,'二位持点':29000})
        await self.online.once()
        match=self.store.matches(self.scope)[0]
        self.assertEqual(match['result']['players'][0]['name'],'生僻鱈灥')
        current=periods.select(self.store,self.scope)['id']
        stats=[normalize(r['fields']) for r in self.fake.rows['players'].values() if r['fields']['周期ID']==current and r['fields']['场次']]
        self.assertEqual(sum(r['场次'] for r in stats),4)
        self.assertIn('生僻鱈灥',[r['显示昵称'] for r in stats])
        self.assertEqual(self.store.matches(self.scope,period='历史')[0]['result']['players'][0]['name'],'甲')
    async def test_invalid_partial_edit_never_changes_local_results(self):
        key,rid,source=await self.current();before=self.store.matches(self.scope)
        self.fake.edit(rid,{'二位昵称':'甲','三位持点':None})
        info=await self.online.once()
        self.assertTrue(info['errors']);self.assertEqual(self.store.matches(self.scope),before)
    async def test_native_and_online_conflict_requires_explicit_resolution(self):
        key,rid,source=await self.current()
        self.store.override(self.scope,source,'accepted',normal(['本地','乙','丙','丁']))
        self.fake.edit(rid,{'一位昵称':'在线'})
        info=await self.online.once();self.assertTrue(info['errors'])
        self.assertEqual(self.store.matches(self.scope)[0]['result']['players'][0]['name'],'本地')
        self.fake.edit(rid,{'冲突处理':'采用在线'})
        await self.online.once()
        self.assertEqual(self.store.matches(self.scope)[0]['result']['players'][0]['name'],'在线')
    async def test_native_correction_pushes_to_online_and_unauthorized_edit_is_rejected(self):
        key,rid,source=await self.current()
        self.store.override(self.scope,source,'accepted',normal(['原生命令','乙','丙','丁']))
        await self.online.once();self.assertEqual(self.fake.rows['matches'][rid]['fields']['一位昵称'],'原生命令')
        self.fake.edit(rid,{'一位昵称':'攻击者'},actor='stranger')
        info=await self.online.once();self.assertTrue(info['errors'])
        self.assertEqual(self.store.matches(self.scope)[0]['result']['players'][0]['name'],'原生命令')
    async def test_soft_disable_reenable_and_qq_reconcile_retains_admin_choice(self):
        key,rid,source=await self.current();self.fake.edit(rid,{'确认状态':'停用'})
        await self.online.once();self.assertEqual(self.store.matches(self.scope),[])
        self.store.reconcile(self.scope,[{'msg':str(n),'idx':0,'stamp':200} for n in (1,2)])
        await self.online.once();self.assertEqual(self.store.matches(self.scope),[])
        self.fake.edit(rid,{'确认状态':'已确认'});await self.online.once()
        self.assertEqual(len(self.store.matches(self.scope)),1)
    async def test_essence_removal_and_restore_wake_sync_without_waiting_for_periodic_pull(self):
        key,rid,source=await self.current();self.online.interval=3600
        completed=asyncio.Queue();original=self.online.once
        async def tracked():
            result=await original();completed.put_nowait(result);return result
        self.online.once=tracked;task=asyncio.create_task(self.online.loop())
        try:
            await asyncio.wait_for(completed.get(),5)
            self.store.reconcile(self.scope,[{'msg':'1','idx':0,'stamp':200}])
            info=await asyncio.wait_for(completed.get(),5)
            self.assertEqual(info['games'],0)
            self.assertEqual(self.fake.rows['matches'][rid]['fields']['确认状态'],'停用')
            current=periods.select(self.store,self.scope)['id']
            self.assertTrue(all(normalize(r['fields']).get('场次')==0 for r in self.fake.rows['players'].values() if r['fields']['周期ID']==current))
            self.assertEqual(len(self.store.matches(self.scope,period='S0')),1)
            self.store.reconcile(self.scope,[{'msg':str(n),'idx':0,'stamp':200} for n in (1,2)])
            info=await asyncio.wait_for(completed.get(),5)
            self.assertEqual(info['games'],1)
            self.assertEqual(self.fake.rows['matches'][rid]['fields']['确认状态'],'已确认')
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
    async def test_unchanged_scan_and_other_groups_do_not_wake_online_sync(self):
        await self.current();self.online.wake.clear()
        self.store.reconcile(self.scope,[{'msg':str(n),'idx':0,'stamp':200} for n in (1,2)])
        self.store.save('new','accepted',normal())
        self.assertFalse(self.online.wake.is_set())
        self.store.enable('other:group','other','group')
        self.store.reconcile('other:group',[{'msg':'1','idx':0,'stamp':200}])
        self.assertFalse(self.online.wake.is_set())
    async def test_archived_online_rows_are_never_updated_zeroed_or_recreated(self):
        archived=periods.select(self.store,self.scope,'历史')['id']
        old=await self.fake.create('matches',{'对局ID':self.scope+':'+archived+':1','周期ID':archived,'群标识':self.scope,'一位昵称':'历史原值'})
        player=await self.fake.create('players',{'统计ID':archived+':4:old','周期ID':archived,'群标识':self.scope,'场次':20})
        self.fake.edit(old['record_id'],{'一位昵称':'手工编辑的历史行'})
        before=copy.deepcopy(self.fake.rows)
        await self.current()
        self.assertEqual(self.fake.rows['matches'][old['record_id']],before['matches'][old['record_id']])
        self.assertEqual(self.fake.rows['players'][player['record_id']],before['players'][player['record_id']])
        del self.fake.rows['matches'][old['record_id']]
        await self.online.once();self.assertNotIn(old['record_id'],self.fake.rows['matches'])
        self.assertTrue(all(r['fields'].get('周期ID')!=archived for r in self.fake.rows['matches'].values()))
        self.fake.fail=True
        with self.assertRaises(FeishuError):await self.online.once()
        self.assertEqual(len(self.store.matches(self.scope,period='历史')),1)
    async def test_manual_add_requires_original_and_admin_and_survives_qq_listing(self):
        fields={'模式':4,'确认状态':'已确认','一位昵称':'新甲','二位昵称':'新乙','三位昵称':'新丙','四位昵称（三麻留空）':'新丁',
                '一位持点':50000,'二位持点':30000,'三位持点':15000,'四位持点（三麻留空）':5000,'原图附件':[{'file_token':'screenshot'}]}
        row=await self.fake.create('matches',fields);rid=row['record_id'];self.fake.edit(rid,{})
        await self.online.once();self.assertEqual(len(self.store.matches(self.scope)),1)
        self.store.reconcile(self.scope,[]);await self.online.once()
        self.assertEqual(len(self.store.matches(self.scope)),1)
        self.assertTrue(self.fake.rows['matches'][rid]['fields'].get('对局ID'))
    async def test_deleted_row_is_restored_without_deleting_bot_statistics(self):
        key,rid,source=await self.current();del self.fake.rows['matches'][rid]
        info=await self.online.once();self.assertTrue(info['errors'])
        self.assertEqual(len(self.store.matches(self.scope)),1)
    async def test_duplicate_stable_ids_pause_sync_instead_of_counting_twice(self):
        key,rid,source=await self.current();await self.fake.create('matches',self.fake.rows['matches'][rid]['fields'])
        with self.assertRaises(ValueError):await self.online.once()
        self.assertEqual(len(self.store.matches(self.scope)),1)
    async def test_uncertain_batch_response_recovers_by_stable_id_without_duplicates(self):
        self.seed('2','new')
        original=self.fake.batch_create;failed=False
        async def uncertain(table,fields):
            nonlocal failed
            rows=await original(table,fields)
            if not failed:
                failed=True;raise FeishuError('response lost after creation')
            return rows
        self.fake.batch_create=uncertain
        with self.assertRaises(FeishuError):await self.online.once()
        await self.online.once()
        self.assertEqual(len(self.fake.rows['matches']),1)
    async def test_other_group_rows_and_derived_stats_are_never_modified(self):
        other=await self.fake.create('players',{'统计ID':'other','群标识':'another:group','场次':9})
        before=copy.deepcopy(self.fake.rows['players'][other['record_id']])
        await self.online.once()
        self.assertEqual(self.fake.rows['players'][other['record_id']],before)
    async def test_incomplete_pagination_raises_without_returning_partial_results(self):
        from unittest.mock import AsyncMock
        client=Feishu(self.folder/'absent','app');client.request=AsyncMock(return_value={'items':[{'record_id':'one'}],'has_more':True,'page_token':'repeated'})
        with self.assertRaises(FeishuError):await client.records('table')


if __name__=='__main__':unittest.main()
