import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Console,Problem
from operations import GROUP,BILI,RADAR,EXT


class OperationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.c=Console(self.tmp.name,Path(self.tmp.name)/'state')
        self.c.plugin=AsyncMock(return_value={'activated':True});self.c.call=AsyncMock();self.s={'user':'admin','upstream':'test'}
    async def asyncTearDown(self):
        await self.c.close(None);self.tmp.cleanup()
    async def test_group_uses_real_bridge_and_forces_current_period(self):
        self.c.call.return_value={'job':{'id':'1','status':'running'}}
        await self.c.operations.run(self.s,GROUP,{'action':'essence','scope':'1:2','period':'S0'})
        args=self.c.call.call_args.args
        self.assertEqual(args[1:3],('POST',EXT+GROUP+'/console/run'));self.assertEqual(args[3],{'scope':'1:2','action':'essence','period':'current'})
        for name,action in [(GROUP,'reidentify'),(RADAR,'shell'),(BILI,'delete-all')]:
            with self.assertRaises(Problem):await self.c.operations.run(self.s,name,{'action':action})
    async def test_disabled_plugin_does_not_run(self):
        self.c.plugin.return_value={'activated':False}
        with self.assertRaises(Problem):await self.c.operations.run(self.s,GROUP,{'action':'essence'})
        self.c.call.assert_not_awaited()
    async def test_refresh_is_background_deduplicated_and_honest_partial(self):
        gate=asyncio.Event()
        async def refresh():await gate.wait();return {'sourceHealth':{'ok':4,'total':7}}
        self.c.operations.radar=SimpleNamespace(refresh=refresh)
        first=await asyncio.wait_for(self.c.operations.run(self.s,RADAR,{'action':'refresh'}),.2)
        second=await self.c.operations.run(self.s,RADAR,{'action':'refresh'})
        self.assertTrue(second['reused']);self.assertEqual(first['job']['id'],second['job']['id'])
        gate.set();await self.c.operations.radar_task
        self.assertEqual(self.c.operations.jobs(RADAR)[0]['status'],'partial')
    async def test_bili_runtime_changes_do_not_break_edit_fingerprint(self):
        data={'sessions':[{'umo':'napcat:GroupMessage:2','subscriptions':[{'uid':'3','last':'old','filter_types':[],'filter_regex':[],'live_atall':False,'at_all':False,'at_sub_users':[]}]}]}
        before=self.c.operations.bili_version(data);data['sessions'][0]['subscriptions'][0]['last']='new'
        self.assertEqual(before,self.c.operations.bili_version(data))
        data['sessions'][0]['subscriptions'][0]['at_all']=True
        self.assertNotEqual(before,self.c.operations.bili_version(data))
    async def test_bili_conflict_writes_nothing(self):
        self.c.call.return_value={'sessions':[]}
        with self.assertRaises(Problem):await self.c.operations.run(self.s,BILI,{'action':'delete','version':'old','subscription':{'uid':'1','umo':'a'}})
        self.assertEqual(self.c.call.await_count,1);self.assertEqual(list(self.c.backups.iterdir()),[])
    async def test_bili_save_reuses_original_service_and_backs_up(self):
        data={'sessions':[]};self.c.call.side_effect=[data,{'updated':False}]
        await self.c.operations.run(self.s,BILI,{'action':'create','version':self.c.operations.bili_version(data),'subscription':{'uid':'1','umo':'napcat:GroupMessage:2'}})
        args=self.c.call.call_args.args;self.assertEqual(args[1:3],('POST',EXT+BILI+'/subscriptions/create'))
        self.assertEqual(len(list(self.c.backups.iterdir())),1)
    async def test_add_does_not_silently_overwrite_existing_subscription(self):
        data={'sessions':[{'umo':'napcat:GroupMessage:2','subscriptions':[{'uid':'1'}]}]}
        self.c.call.return_value=data
        for uid in ('1','0001'):
            with self.assertRaises(Problem):await self.c.operations.run(self.s,BILI,{'action':'create','version':self.c.operations.bili_version(data),'subscription':{'uid':uid,'umo':'napcat:GroupMessage:2'}})
        self.assertEqual(self.c.call.await_count,2);self.assertEqual(list(self.c.backups.iterdir()),[])
    async def test_restart_does_not_leave_running_jobs_forever(self):
        self.c.db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?)',('old',RADAR,'refresh','running','old',1,0,''));self.c.db.commit()
        from operations import Operations
        loaded=Operations(self.c,Problem);self.assertEqual(loaded.jobs(RADAR)[0]['status'],'interrupted')
