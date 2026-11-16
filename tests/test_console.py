import asyncio
import hashlib
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase,TestServer
from server import Console,Problem,create_app,digest,masked,merged_config,schema_fields
from data_views import records,view


class ConfigTests(unittest.TestCase):
    def test_secret_redaction_and_nested_credentials_are_not_lost(self):
        config={'token':'private','rules':[{'api_key':'private','x':1}],'nested':{'enabled':True},'extra':'preserved','replay_web_port':6193}
        fields=schema_fields({'token':{'type':'string'},'rules':{'type':'list'},'nested':{'type':'object','items':{'enabled':{'type':'bool'}}},'replay_web_port':{'type':'int'}},config)
        self.assertNotIn('private',json.dumps(fields))
        self.assertTrue(next(f for f in fields if f['key']=='rules')['protected'])
        out=merged_config(config,fields,{'token':'','nested.enabled':False})
        self.assertEqual(out['token'],'private');self.assertEqual(out['rules'],config['rules']);self.assertEqual(out['extra'],'preserved')
        self.assertFalse(out['nested']['enabled'])
        for changes in ({'replay_web_port':1},{'unknown':1},{'nested.enabled':'false'},{'rules':[]}):
            with self.assertRaises(Problem):merged_config(config,fields,changes)
    def test_types_options_and_fingerprint(self):
        fields=schema_fields({'mode':{'type':'string','options':['a','b']},'poll_seconds':{'type':'int'}},{'mode':'a','poll_seconds':60})
        for change in ({'mode':'other'},{'poll_seconds':True},{'poll_seconds':0}):
            with self.assertRaises(Problem):merged_config({},fields,change)
        self.assertEqual(digest({'a':1,'b':2}),digest({'b':2,'a':1}))


class SecurityTests(AioHTTPTestCase):
    async def get_application(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.console=Console(root,root/'console');self.console.call=AsyncMock()
        return create_app(self.console)
    async def tearDownAsync(self):
        await super().tearDownAsync();self.tmp.cleanup()
    def session_headers(self):
        token='test-session';csrf='test-csrf'
        self.console.db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?,?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),'admin','upstream',csrf,time.time(),time.time()+60,'test'));self.console.db.commit()
        return {'Cookie':'huihui_admin='+token,'X-CSRF-Token':csrf}
    async def test_no_auth_no_data_and_no_write(self):
        for method,path in [('GET','/admin/api/plugins'),('GET','/admin/api/data/matches'),('POST','/admin/api/plugins/unknown/reload')]:
            r=await self.client.request(method,path,json={});self.assertEqual(r.status,401)
        self.console.call.assert_not_awaited()
    async def test_csrf_and_cross_origin_rejected(self):
        h=self.session_headers();r=await self.client.post('/admin/api/logout',headers={'Cookie':h['Cookie']});self.assertEqual(r.status,403)
        h['Origin']='https://attacker.invalid';r=await self.client.post('/admin/api/logout',headers=h);self.assertEqual(r.status,403)
    async def test_unknown_plugin_never_reloads_all(self):
        self.console.call.return_value=[{'name':'astrbot_plugin_example','activated':True,'reserved':False}]
        r=await self.client.post('/admin/api/plugins/astrbot_plugin_absent/reload',headers=self.session_headers())
        self.assertEqual(r.status,404);self.assertEqual(self.console.call.await_count,1)
    async def test_conflict_no_save_and_no_secret_in_get(self):
        raw={'metadata':{'astrbot_plugin_example':{'items':{'token':{'type':'string'},'enabled':{'type':'bool'}}}},'config':{'token':'private-value','enabled':True}}
        self.console.call.side_effect=[[{'name':'astrbot_plugin_example'}],raw,raw]
        h=self.session_headers();r=await self.client.get('/admin/api/plugins/astrbot_plugin_example/config',headers=h)
        body=await r.json();self.assertNotIn('private-value',json.dumps(body))
        r=await self.client.put('/admin/api/plugins/astrbot_plugin_example/config',headers=h,json={'version':'outdated','changes':{'enabled':False}})
        self.assertEqual(r.status,409);self.assertEqual(len(list(self.console.backups.iterdir())),0)
    async def test_save_targets_one_plugin_and_backups_private(self):
        raw={'metadata':{'example':{'items':{'enabled':{'type':'bool'}}}},'config':{'enabled':True,'extra':7}}
        self.console.call.side_effect=[[{'name':'astrbot_plugin_example'}],raw,None]
        r=await self.client.put('/admin/api/plugins/astrbot_plugin_example/config',headers=self.session_headers(),json={'version':digest(raw['config']),'changes':{'enabled':False}})
        self.assertEqual(r.status,200)
        args=self.console.call.call_args.args
        self.assertEqual(args[1:3],('PUT','/api/v1/plugins/astrbot_plugin_example/config'));self.assertEqual(args[3]['config'],{'enabled':False,'extra':7})
        backups=list(self.console.backups.iterdir());self.assertEqual(len(backups),1);self.assertEqual(json.loads(backups[0].read_text())['value'],raw['config'])
    async def test_static_pages_and_no_backup_file_access(self):
        r=await self.client.get('/admin/');self.assertEqual(r.status,200);self.assertIn('慧慧控制台',await r.text())
        r=await self.client.get('/admin/data/console.db');self.assertEqual(r.status,404)
        r=await self.client.get('/admin/api/backups/../../console.db');self.assertIn(r.status,(401,404))
    async def test_logout_revokes_session(self):
        h=self.session_headers();r=await self.client.post('/admin/api/logout',headers=h);self.assertEqual(r.status,200)
        r=await self.client.get('/admin/api/session',headers=h);self.assertEqual(r.status,401)


class DataTests(unittest.TestCase):
    def test_current_only_and_archived_hashes_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'astrbot_plugin_group_data';folder.mkdir();p=folder/'group_data.db'
            with sqlite3.connect(p) as db:
                db.executescript('''CREATE TABLE periods(id TEXT,scope TEXT,name TEXT,state TEXT,snapshot TEXT); CREATE TABLE source_periods(source_id INTEGER,period_id TEXT);
                CREATE TABLE sources(id INTEGER,scope TEXT,stamp REAL,hash TEXT,msg TEXT,idx INTEGER,active INTEGER); CREATE TABLE groups(scope TEXT,gid TEXT);
                CREATE TABLE images(hash TEXT,status TEXT,result TEXT,reason TEXT); CREATE TABLE overrides(scope TEXT,hash TEXT,status TEXT,result TEXT);
                CREATE TABLE source_exclusions(scope TEXT,msg TEXT,idx INTEGER); CREATE TABLE aliases(scope TEXT,old TEXT,new TEXT);
                INSERT INTO periods VALUES('old','scope','S0','archived','DO NOT PARSE'),('new','scope','S1','current',NULL);
                INSERT INTO groups VALUES('scope','123'); INSERT INTO source_periods VALUES(1,'old'),(2,'new'),(3,'new');
                INSERT INTO sources VALUES(1,'scope',1,'old','1',0,1),(2,'scope',2,'old','2',0,1),(3,'scope',3,'new','3',0,1);
                ''')
                db.execute('INSERT INTO images VALUES(?,?,?,?)',('new','accepted',json.dumps({'mode':4,'players':[{'name':'玩家','points':35000}]}),''))
            before=p.read_bytes();rows=records(tmp,'matches');self.assertEqual(len(rows),1);self.assertEqual(rows[0]['id'],3);self.assertIn('玩家',rows[0]['cells'][5]);self.assertEqual(p.read_bytes(),before)
    def test_bilibili_credentials_never_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'astrbot_plugin_bilibili';f.mkdir();(f/'astrbot_plugin_bilibili.json').write_text(json.dumps({'credential':{'sessdata':'private'},'bili_sub_list':{'group':{'123':{'name':'UP主'}}}}))
            out=records(tmp,'bilibili');self.assertNotIn('private',json.dumps(out));self.assertEqual(len(out),1)
    def test_dataset_allowlist(self):
        with self.assertRaises(ValueError):view('/not-used','../../private')


class LoginTests(AioHTTPTestCase):
    async def get_application(self):
        self.tmp=tempfile.TemporaryDirectory();self.console=Console(self.tmp.name,Path(self.tmp.name)/'state')
        upstream=web.Application()
        async def auth(request):
            body=await request.json()
            if body.get('password')!='test-only-password':return web.json_response({'status':'error','message':'用户名或密码错误'},status=401)
            if body.get('username')=='totp' and body.get('code')!='123456':return web.json_response({'status':'error','message':'需要 TOTP 验证'},status=401)
            return web.json_response({'status':'ok','data':{'token':'upstream-private-token'}})
        upstream.router.add_post('/api/v1/auth/login',auth)
        async def extension(request):return web.json_response({'groups':[],'jobs':[]})
        async def extension_error(request):return web.json_response({'status':'error','message':'任务已存在','data':None},status=400)
        upstream.router.add_get('/api/v1/plugins/extensions/example/status',extension)
        upstream.router.add_get('/api/v1/plugins/extensions/example/error',extension_error)
        self.upstream=TestServer(upstream);await self.upstream.start_server()
        self.console.upstream=str(self.upstream.make_url('')).rstrip('/')
        return create_app(self.console)
    async def tearDownAsync(self):
        await super().tearDownAsync();await self.upstream.close();self.tmp.cleanup()
    async def test_login_cookie_session_and_no_plain_password_persisted(self):
        r=await self.client.post('/admin/api/login',json={'username':'admin','password':'test-only-password'})
        self.assertEqual(r.status,200);body=await r.json();self.assertTrue(body['csrf'])
        cookie=r.headers.get('Set-Cookie','');self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie);self.assertIn('Path=/admin',cookie)
        self.assertNotIn('upstream-private-token',json.dumps(body))
        stored=dict(self.console.db.execute('SELECT * FROM sessions').fetchone());self.assertNotIn('test-only-password',json.dumps(stored))
        r=await self.client.get('/admin/api/session');self.assertEqual(r.status,200)
    async def test_wrong_password_is_rejected_and_limited(self):
        for _ in range(5):
            r=await self.client.post('/admin/api/login',json={'username':'admin','password':'wrong'});self.assertEqual(r.status,401)
        r=await self.client.post('/admin/api/login',json={'username':'admin','password':'wrong'});self.assertEqual(r.status,429)
        self.assertEqual(self.console.db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],0)
    async def test_totp_respected(self):
        r=await self.client.post('/admin/api/login',json={'username':'totp','password':'test-only-password'});self.assertEqual(r.status,401);self.assertIn('TOTP',(await r.json())['error'])
        r=await self.client.post('/admin/api/login',json={'username':'totp','password':'test-only-password','code':'123456'});self.assertEqual(r.status,200)
    async def test_native_plugin_response_contract_and_errors(self):
        response=await self.console.call({'upstream':'test'},'GET','/api/v1/plugins/extensions/example/status')
        self.assertEqual(response,{'groups':[],'jobs':[]})
        with self.assertRaises(Problem):await self.console.call({'upstream':'test'},'GET','/api/v1/plugins/extensions/example/error')


if __name__=='__main__':unittest.main()
