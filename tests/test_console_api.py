import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_group_data.store import Store
from astrbot_plugin_group_data.console_api import ConsoleActions,PREFIX


class ConsoleActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();folder=Path(self.tmp.name)
        store=Store(folder/'fixture.db');store.enable('1:123','1','123')
        store.db.execute("INSERT INTO periods VALUES('current','1:123','S1赛季','current',1,0,NULL,'')");store.db.commit()
        client=SimpleNamespace(call_action=AsyncMock(return_value={'user_id':1}))
        platform=SimpleNamespace(meta=lambda:SimpleNamespace(name='aiocqhttp'),get_client=lambda:client)
        context=SimpleNamespace(platform_manager=SimpleNamespace(get_insts=lambda:[platform]),registered_web_apis=[('/other/status',None,['GET'],'other')])
        context.register_web_api=lambda route,fn,methods,desc:context.registered_web_apis.append((route,fn,methods,desc))
        self.tasks=[]
        plugin=SimpleNamespace(folder=folder,store=store,source=SimpleNamespace(locks={},sync=AsyncMock(return_value=True)),recognizer=SimpleNamespace(running=set()),online=None,context=context)
        plugin.spawn=lambda coro:self.tasks.append(asyncio.create_task(coro)) or self.tasks[-1]
        self.plugin=plugin;self.actions=ConsoleActions(plugin)
    async def asyncTearDown(self):
        for task in self.tasks:task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        self.plugin.store.close();self.tmp.cleanup()
    async def test_double_submit_starts_one_existing_sync_only(self):
        gate=asyncio.Event()
        async def sync(*args):await gate.wait();return True
        self.plugin.source.sync.side_effect=sync
        first=self.actions.start('1:123','essence','admin');second=self.actions.start('1:123','essence','admin')
        self.assertEqual(first['job']['id'],second['job']['id']);self.assertTrue(second['reused']);self.assertEqual(len(self.tasks),1)
        gate.set();await self.tasks[0]
        self.plugin.source.sync.assert_awaited_once();self.assertEqual(first['job']['status'],'done')
        self.assertIn('识别队列',first['job']['phase'])
    async def test_join_automatic_scan_without_starting_another(self):
        lock=asyncio.Lock();await lock.acquire();self.plugin.source.locks['1:123']=lock
        job=self.actions.start('1:123','essence','admin')['job'];await asyncio.sleep(.01)
        self.assertEqual(job['status'],'running');lock.release();await self.tasks[0]
        self.plugin.source.sync.assert_not_awaited();self.assertEqual(job['status'],'done')
    async def test_invalid_scope_action_or_disabled_group_never_starts(self):
        for scope,action in [('absent','essence'),('1:123','reidentify'),('1:123','feishu')]:
            with self.assertRaises(ValueError):self.actions.start(scope,action,'admin')
        self.plugin.store.enable('1:123','1','123',False)
        with self.assertRaises(ValueError):self.actions.start('1:123','essence','admin')
        self.assertEqual(self.tasks,[])
    async def test_failed_source_is_not_reported_successful(self):
        self.plugin.source.sync.return_value=False
        job=self.actions.start('1:123','essence','admin')['job'];await self.tasks[0]
        self.assertEqual(job['status'],'failed')
    async def test_feishu_uses_existing_current_sync_and_reports_issues(self):
        online=SimpleNamespace(scope='1:123',lock=asyncio.Lock(),get=lambda key,default:default,once=AsyncMock(return_value={'errors':['conflict']}))
        self.plugin.online=online
        job=self.actions.start('1:123','feishu','admin')['job'];await self.tasks[0]
        online.once.assert_awaited_once();self.assertEqual(job['status'],'partial')
    async def test_no_access_to_archived_period_via_web(self):
        from astrbot.api.web import json_response
        request=SimpleNamespace(username='admin',json=AsyncMock(return_value={'scope':'1:123','action':'essence','period':'S0'}))
        with patch('astrbot.api.web.request',request):response=await self.actions.run_api()
        self.assertNotEqual(response.status_code,200);self.assertEqual(self.tasks,[])
    async def test_web_access_requires_authenticated_user(self):
        with patch('astrbot.api.web.request',SimpleNamespace(username=None)):
            response=await self.actions.status_api()
        self.assertEqual(response.status_code,403)
    async def test_reload_history_and_unregistration(self):
        self.actions.register();self.actions.start('1:123','essence','admin')
        loaded=ConsoleActions(self.plugin)
        self.assertEqual(next(iter(loaded.jobs.values()))['status'],'interrupted')
        self.actions.close();self.assertEqual([r[0] for r in self.plugin.context.registered_web_apis],['/other/status'])
        with self.assertRaises(ValueError):self.actions.start('1:123','essence','admin')
