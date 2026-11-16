"""Independent authenticated console and existing report gateway. No global bot patches."""
import asyncio
import csv
import hashlib
import io
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import quote, urlsplit

import aiohttp
from aiohttp import web
from data_views import DATASETS, records, view

ROOT=Path(__file__).resolve().parent
SECRET=re.compile(r'password|passwd|secret|token|api_key|apikey|sessdata|cookie|credential|authorization|buvid|bili_jct',re.I)
PROTECTED={'replay_web_port','replay_web_host','replay_public_url','replay_backend','replay_runtime','state_dir','credentials_file','ocr_python'}
EXCLUDED={'astrbot_plugin_inventory'}
COOKIE='huihui_admin'


class Problem(Exception):
    def __init__(self,message,status=400): self.message=message;self.status=status


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def masked(value, key=''):
    if SECRET.search(key): return ''
    if isinstance(value,dict):return {k:masked(v,k) for k,v in value.items()}
    if isinstance(value,list):return [masked(v,key) for v in value]
    return value


def contains_secret(value):
    if isinstance(value,dict):return any(SECRET.search(k) or contains_secret(v) for k,v in value.items())
    if isinstance(value,list):return any(contains_secret(v) for v in value)
    return False


def schema_fields(schema, config, path=()):
    out=[]
    for key,meta in schema.items():
        if not isinstance(meta,dict):continue
        p=path+(key,);value=config.get(key,meta.get('default')) if isinstance(config,dict) else meta.get('default')
        if meta.get('type')=='object' and isinstance(meta.get('items'),dict):
            out.extend(schema_fields(meta['items'],value or {},p));continue
        secret=bool(SECRET.search(key));protected=key in PROTECTED or contains_secret(value)
        out.append({'path':list(p),'key':'.'.join(p),'type':meta.get('type','string'),'description':str(meta.get('description') or key),'hint':str(meta.get('hint') or ''),'options':meta.get('options',[]),'value':masked(value,key),'secret':secret,'configured':bool(value) if secret else False,'protected':protected})
    return out


def merged_config(current, fields, changes):
    if not isinstance(changes,dict) or len(changes)>300:raise Problem('配置格式不正确')
    output=json.loads(json.dumps(current));known={f['key']:f for f in fields}
    for key,value in changes.items():
        f=known.get(key)
        if f is None or f['protected']:raise Problem('此配置项不能在这里修改')
        if f['secret']:
            if value=='':continue
            if not isinstance(value,str):raise Problem('凭据格式不正确')
        typ=f['type']
        valid={'bool':lambda:type(value) is bool,'int':lambda:type(value) is int,'float':lambda:type(value) in (int,float),'list':lambda:isinstance(value,list),'object':lambda:isinstance(value,dict),'dict':lambda:isinstance(value,dict),'string':lambda:isinstance(value,str),'text':lambda:isinstance(value,str)}
        if typ in valid and not valid[typ]():raise Problem(f['description']+'：值类型不正确')
        if isinstance(value,float) and not math.isfinite(value):raise Problem('请填写有效数字')
        if typ=='template_list' and not isinstance(value,list):raise Problem('请填写列表')
        if f['options'] and value not in f['options']:raise Problem(f['description']+'：请选择可用选项')
        if isinstance(value,(str,list,dict)) and len(json.dumps(value,ensure_ascii=False))>100000:raise Problem('配置内容过长')
        if f['path'][-1] in ('poll_seconds','subscribe_interval_3','subscribe_interval_4','interval_secs') and (type(value) is not int or value<30):raise Problem('检查间隔至少30秒')
        node=output
        for part in f['path'][:-1]:node=node.setdefault(part,{})
        node[f['path'][-1]]=value
    return output


class Console:
    def __init__(self, base=None, data=None):
        self.base=Path(base or os.environ.get('ASTRBOT_DIR','/bot/AstrBot'))
        self.data=Path(data or os.environ.get('CONSOLE_DATA','/bot/huihui-console/data'))
        self.data.mkdir(parents=True,exist_ok=True);self.data.chmod(0o700)
        self.backups=self.data/'backups';self.backups.mkdir(exist_ok=True);self.backups.chmod(0o700)
        self.db=sqlite3.connect(self.data/'console.db');self.db.row_factory=sqlite3.Row
        self.db.executescript('''CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,user TEXT,upstream TEXT,csrf TEXT,created REAL,expires REAL,agent TEXT);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,stamp REAL,user TEXT,action TEXT,target TEXT,status TEXT);
        ''');self.db.commit();(self.data/'console.db').chmod(0o600)
        self.rate=defaultdict(deque);self.locks=defaultdict(asyncio.Lock);self.cache={};self.http=None
        self.upstream=os.environ.get('ASTRBOT_URL','http://127.0.0.1:6185')
        self.reports=os.environ.get('REPORT_URL','http://127.0.0.1:6193')
    async def start(self,app):
        self.http=aiohttp.ClientSession(trust_env=False,cookie_jar=aiohttp.DummyCookieJar(),timeout=aiohttp.ClientTimeout(total=15))
    async def close(self,app):
        if self.http:await self.http.close()
        self.db.close()
    def audit(self,user,action,target,status='成功'):
        self.db.execute('INSERT INTO audit(stamp,user,action,target,status) VALUES(?,?,?,?,?)',(time.time(),user,action,target,status));self.db.commit()
    def session(self,request):
        token=request.cookies.get(COOKIE,'')
        if not token:raise Problem('请先登录',401)
        row=self.db.execute('SELECT * FROM sessions WHERE id=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row or row['expires']<time.time():raise Problem('登录已过期，请重新登录',401)
        return dict(row)
    async def call(self,session,method,path,payload=None):
        try:
            async with self.http.request(method,self.upstream+path,json=payload,headers={'Authorization':'Bearer '+session['upstream']}) as r:
                obj=await r.json()
                if r.status==401:raise Problem('登录已过期，请重新登录',401)
                if r.status>=400 or obj.get('status') not in ('ok','success'):raise Problem(str(obj.get('message') or '机器人接口暂时不可用')[:180],502)
                return obj.get('data')
        except (aiohttp.ClientError,asyncio.TimeoutError):raise Problem('机器人暂时无法连接，请稍后重试',503) from None
    async def plugins(self,session,force=False):
        cached=self.cache.get('plugins')
        if cached and not force and time.monotonic()-cached[0]<10:return cached[1]
        raw=await self.call(session,'GET','/api/plugin/get')
        out=[{k:p.get(k) for k in ('name','display_name','desc','version','activated','repo','updates_enabled','root_dir_name')} for p in raw if not p.get('reserved') and p.get('name') not in EXCLUDED]
        self.cache['plugins']=(time.monotonic(),out);return out
    async def plugin(self,session,name):
        if not re.fullmatch(r'astrbot_plugin_[a-zA-Z0-9_]+',name):raise Problem('没有这个插件',404)
        p=next((p for p in await self.plugins(session) if p['name']==name),None)
        if not p:raise Problem('没有这个插件',404)
        return p
    async def configuration(self,session,name):
        await self.plugin(session,name)
        raw=await self.call(session,'GET','/api/v1/plugins/'+quote(name,safe='')+'/config')
        schema=raw.get('metadata',{});items=next(iter(schema.values()),{}).get('items',{})
        config=raw.get('config',{})
        return config,schema_fields(items,config)
    def save_backup(self,user,target,value):
        ident=time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(4)
        p=self.backups/(ident+'.json')
        with p.open('x',encoding='utf-8') as f:json.dump({'id':ident,'target':target,'user':user,'created':time.time(),'value':value},f,ensure_ascii=False)
        p.chmod(0o600);return ident


def create_app(console=None):
    c=console or Console()
    @web.middleware
    async def policy(request,handler):
        try:
            if request.path.startswith('/admin/api/') and request.path!='/admin/api/login':
                request['session']=c.session(request)
                if request.method not in ('GET','HEAD'):
                    origin=request.headers.get('Origin')
                    if origin and urlsplit(origin).netloc!=request.host:raise Problem('请求来源不正确',403)
                    if not secrets.compare_digest(request.headers.get('X-CSRF-Token',''),request['session']['csrf']):raise Problem('页面已过期，请刷新后重试',403)
            response=await handler(request)
        except Problem as e:response=web.json_response({'error':e.message},status=e.status)
        except (json.JSONDecodeError,TypeError,ValueError):response=web.json_response({'error':'提交内容格式不正确'},status=400)
        except web.HTTPException:raise
        except Exception:
            logging.exception('Console request failed: %s %s',request.method,request.path)
            response=web.json_response({'error':'操作未完成，请查看运行日志后重试'},status=500)
        response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='no-referrer';response.headers['X-Frame-Options']='DENY'
        if request.path.startswith('/admin'):
            response.headers['Cache-Control']='no-store'
            response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response
    app=web.Application(middlewares=[policy],client_max_size=1024*1024)
    app['console']=c;app.on_startup.append(c.start);app.on_cleanup.append(c.close)
    async def login(request):
        origin=request.headers.get('Origin')
        if origin and urlsplit(origin).netloc!=request.host:raise Problem('请求来源不正确',403)
        now=time.monotonic();key=request.remote or 'unknown';attempts=c.rate[key]
        while attempts and attempts[0]<now-60:attempts.popleft()
        if len(attempts)>=5:raise Problem('尝试次数较多，请一分钟后重试',429)
        attempts.append(now)
        if len(c.rate)>1024:c.rate.pop(next(iter(c.rate)),None)
        body=await request.json()
        if not isinstance(body,dict) or not isinstance(body.get('username'),str) or not isinstance(body.get('password'),str):raise Problem('请填写账号与密码')
        if len(body['username'])>100 or len(body['password'])>256:raise Problem('账号或密码过长')
        async with c.http.post(c.upstream+'/api/v1/auth/login',json={k:body.get(k) for k in ('username','password','code')},timeout=aiohttp.ClientTimeout(total=12)) as r:
            obj=await r.json()
        if obj.get('status') not in ('ok','success') or not (obj.get('data') or {}).get('token'):
            c.audit(body['username'],'登录','管理界面','失败')
            raise Problem(str(obj.get('message') or '用户名或密码错误')[:120],401)
        token=secrets.token_urlsafe(40);sid=hashlib.sha256(token.encode()).hexdigest();csrf=secrets.token_urlsafe(32);now=time.time()
        c.db.execute('DELETE FROM sessions WHERE expires<?',(now,));c.db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?)',(sid,body['username'],obj['data']['token'],csrf,now,now+12*3600,request.headers.get('User-Agent','')[:180]));c.db.commit()
        c.audit(body['username'],'登录','管理界面')
        response=web.json_response({'user':body['username'],'csrf':csrf})
        response.set_cookie(COOKIE,token,max_age=12*3600,httponly=True,samesite='Strict',secure=request.secure,path='/admin')
        return response
    async def session(request):
        s=request['session'];return web.json_response({'user':s['user'],'csrf':s['csrf'],'expires':s['expires']})
    async def logout(request):
        c.db.execute('DELETE FROM sessions WHERE id=?',(request['session']['id'],));c.db.commit()
        response=web.json_response({'ok':True});response.del_cookie(COOKIE,path='/admin');return response
    async def plugins(request):return web.json_response({'plugins':await c.plugins(request['session'])})
    async def config(request):
        name=request.match_info['name'];current,fields=await c.configuration(request['session'],name)
        return web.json_response({'fields':fields,'version':digest(current)})
    async def save(request):
        s=request['session'];name=request.match_info['name'];body=await request.json()
        async with c.locks[name]:
            current,fields=await c.configuration(s,name)
            if body.get('version')!=digest(current):raise Problem('配置已在其他页面更新，请刷新后重新修改',409)
            value=merged_config(current,fields,body.get('changes'))
            if value==current:return web.json_response({'ok':True,'unchanged':True})
            backup=c.save_backup(s['user'],name,current)
            try:await c.call(s,'PUT','/api/v1/plugins/'+name+'/config',{'config':value})
            except Problem:
                c.audit(s['user'],'保存配置',name,'未确认');raise
            c.audit(s['user'],'保存配置',name);c.cache.clear()
        return web.json_response({'ok':True,'backup':backup,'message':'已保存并重新加载此插件'})
    async def enabled(request):
        s=request['session'];name=request.match_info['name'];await c.plugin(s,name);body=await request.json()
        if type(body.get('enabled')) is not bool:raise Problem('请选择启用或停用')
        async with c.locks[name]:
            await c.call(s,'PATCH','/api/v1/plugins/'+name+'/enabled',{'enabled':body['enabled']})
            c.cache.clear();c.audit(s['user'],'启用插件' if body['enabled'] else '停用插件',name)
        return web.json_response({'ok':True})
    async def reload_plugin(request):
        s=request['session'];name=request.match_info['name'];await c.plugin(s,name)
        async with c.locks[name]:await c.call(s,'POST','/api/v1/plugins/'+name+'/reload')
        c.cache.clear();c.audit(s['user'],'重新加载',name)
        return web.json_response({'ok':True})
    async def overview(request):
        plugins=await c.plugins(request['session']);qq=False
        # Probe local OneBot status only; never send a message or force a reconnect.
        try:
            path=c.base.parent/'napcat/napcat/config/onebot11_3113357165.json'
            cfg=json.loads(path.read_text())['network']['httpServers'][0]
            async with c.http.post('http://127.0.0.1:'+str(cfg['port'])+'/get_status',headers={'Authorization':'Bearer '+cfg['token']},timeout=aiohttp.ClientTimeout(total=2)) as r:
                obj=await r.json();qq=bool((obj.get('data') or {}).get('online'))
        except Exception:pass
        groups=[]
        try:
            from data_views import connect
            with connect(c.base/'data/plugin_data/astrbot_plugin_group_data/group_data.db') as db:
                groups=[dict(r) for r in db.execute("SELECT g.gid,g.last_scan,g.last_import,p.name FROM groups g LEFT JOIN periods p ON p.scope=g.scope AND p.state='current'")]
        except (OSError,sqlite3.Error):pass
        return web.json_response({'qq_online':qq,'enabled':sum(bool(p['activated']) for p in plugins),'plugins':len(plugins),'groups':groups,'updated':time.time(),'console_version':'0.1.0'})
    async def datasets(request):return web.json_response({'datasets':[{'id':k,'name':v[0]} for k,v in DATASETS.items()]})
    async def dataset(request):
        kind=request.match_info['kind'];q=request.query.get('q','')[:100]
        return web.json_response(await asyncio.to_thread(view,c.base/'data/plugin_data',kind,q,int(request.query.get('page','1'))))
    async def export(request):
        kind=request.match_info['kind']
        if kind not in DATASETS:raise Problem('没有这个数据集',404)
        rows=await asyncio.to_thread(records,c.base/'data/plugin_data',kind);q=request.query.get('q','')[:100].casefold()
        if q:rows=[r for r in rows if q in ' '.join(map(str,r['cells'])).casefold()]
        buf=io.StringIO();writer=csv.writer(buf);writer.writerow(DATASETS[kind][1])
        # Spreadsheet formula injection must stay text on export.
        for row in rows:writer.writerow([("'"+str(v)) if str(v).startswith(('=','+','-','@','\t','\r')) else v for v in row['cells']])
        c.audit(request['session']['user'],'导出数据',kind)
        return web.Response(body=('\ufeff'+buf.getvalue()).encode(),headers={'Content-Type':'text/csv; charset=utf-8','Content-Disposition':'attachment; filename="'+kind+'.csv"'})
    async def audit(request):
        rows=[dict(r) for r in c.db.execute('SELECT stamp,user,action,target,status FROM audit ORDER BY id DESC LIMIT 100')]
        return web.json_response({'rows':rows})
    async def backups(request):
        out=[]
        for p in sorted(c.backups.glob('*.json'),reverse=True)[:100]:
            d=json.loads(p.read_text());out.append({k:d[k] for k in ('id','target','created','user')})
        return web.json_response({'backups':out})
    async def backup(request):
        s=request['session'];name=(await request.json()).get('plugin');current,_=await c.configuration(s,name)
        ident=c.save_backup(s['user'],name,current);c.audit(s['user'],'配置备份',name)
        return web.json_response({'ok':True,'id':ident})
    async def restore(request):
        ident=request.match_info['ident'];body=await request.json();s=request['session']
        if not re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]{8}',ident):raise Problem('没有这个备份',404)
        p=c.backups/(ident+'.json')
        if not p.is_file():raise Problem('没有这个备份',404)
        saved=json.loads(p.read_text());name=saved['target']
        if not name.startswith('astrbot_plugin_'):raise Problem('此备份不能从这里恢复')
        async with c.locks[name]:
            current,fields=await c.configuration(s,name)
            if body.get('version')!=digest(current):raise Problem('当前配置已变化，请重新打开恢复操作',409)
            # Preserve deployment wiring and fields absent from the current schema.
            changes={f['key']:get_value(saved['value'],f['path']) for f in fields if not f['protected'] and has_value(saved['value'],f['path'])}
            value=merged_config(current,fields,changes);c.save_backup(s['user'],name,current)
            await c.call(s,'PUT','/api/v1/plugins/'+name+'/config',{'config':value})
            c.audit(s['user'],'恢复配置',name);c.cache.clear()
        return web.json_response({'ok':True})
    async def personas(request):
        data=await c.call(request['session'],'GET','/api/v1/personas')
        return web.json_response({'personas':[{'id':p['persona_id'],'prompt':p.get('system_prompt',''),'version':digest(p)} for p in data]})
    async def persona_save(request):
        s=request['session'];ident=request.match_info['ident'];body=await request.json()
        async with c.locks['persona:'+ident]:
            all_personas=await c.call(s,'GET','/api/v1/personas');current=next((p for p in all_personas if p['persona_id']==ident),None)
            if current is None:raise Problem('没有这个人格',404)
            if body.get('version')!=digest(current):raise Problem('人格已更新，请刷新后修改',409)
            prompt=body.get('prompt')
            if not isinstance(prompt,str) or len(prompt)>50000:raise Problem('人格内容格式不正确')
            c.save_backup(s['user'],'persona:'+ident,current)
            payload={k:current.get(k) for k in ('persona_id','begin_dialogs','tools','skills','custom_error_message','folder_id','sort_order')};payload['system_prompt']=prompt
            await c.call(s,'PUT','/api/v1/personas/'+quote(ident,safe=''),payload);c.audit(s['user'],'编辑人格',ident)
        return web.json_response({'ok':True})
    async def providers(request):
        raw=await c.call(request['session'],'GET','/api/v1/providers')
        return web.json_response({'providers':[{k:masked(p.get(k),k) for k in ('id','model','type','enable','enabled')} for p in raw.get('providers',[])]})
    async def sessions(request):
        s=request['session'];rows=c.db.execute('SELECT id,created,expires,agent FROM sessions WHERE user=? AND expires>?',(s['user'],time.time())).fetchall()
        return web.json_response({'sessions':[{'current':r['id']==s['id'],'id':r['id'],'created':r['created'],'expires':r['expires'],'agent':r['agent']} for r in rows]})
    async def revoke(request):
        s=request['session'];ident=request.match_info['ident']
        c.db.execute('DELETE FROM sessions WHERE id=? AND user=?',(ident,s['user']));c.db.commit();return web.json_response({'ok':True})
    async def health(request):return web.json_response({'ok':True,'version':'0.1.0'})
    async def report(request):
        token=request.match_info['token']
        if not re.fullmatch('[0-9a-f]{40}',token):raise web.HTTPNotFound()
        try:
            async with c.http.get(c.reports+'/r/'+token,timeout=aiohttp.ClientTimeout(total=15)) as r:
                body=await r.read()
                return web.Response(body=body,status=r.status,headers={k:v for k,v in r.headers.items() if k.lower() in ('content-type','content-security-policy','cache-control')})
        except (aiohttp.ClientError,asyncio.TimeoutError):return web.Response(text='报告暂时无法加载，请稍后刷新。',status=503)
    async def index(request):return web.FileResponse(ROOT/'static/index.html')
    async def asset(request):
        name=request.match_info['name']
        if name not in ('app.js','style.css'):raise web.HTTPNotFound()
        return web.FileResponse(ROOT/'static'/name)
    async def redirect(request):raise web.HTTPFound('/admin/')
    app.add_routes([web.get('/',redirect),web.get('/admin',redirect),web.get('/admin/',index),web.get('/admin/assets/{name}',asset),web.get('/health',health),web.get('/r/{token}',report),web.get('/r/{token}/',report),
        web.post('/admin/api/login',login),web.get('/admin/api/session',session),web.post('/admin/api/logout',logout),web.get('/admin/api/overview',overview),web.get('/admin/api/plugins',plugins),
        web.get('/admin/api/plugins/{name}/config',config),web.put('/admin/api/plugins/{name}/config',save),web.patch('/admin/api/plugins/{name}/enabled',enabled),web.post('/admin/api/plugins/{name}/reload',reload_plugin),
        web.get('/admin/api/datasets',datasets),web.get('/admin/api/data/{kind}',dataset),web.get('/admin/api/export/{kind}',export),web.get('/admin/api/audit',audit),web.get('/admin/api/backups',backups),web.post('/admin/api/backups',backup),web.post('/admin/api/backups/{ident}/restore',restore),
        web.get('/admin/api/personas',personas),web.put('/admin/api/personas/{ident}',persona_save),web.get('/admin/api/providers',providers),web.get('/admin/api/sessions',sessions),web.delete('/admin/api/sessions/{ident}',revoke)])
    return app


def has_value(value,path):
    for key in path:
        if not isinstance(value,dict) or key not in value:return False
        value=value[key]
    return True


def get_value(value,path):
    for key in path:value=value[key]
    return value


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    web.run_app(create_app(),host=os.environ.get('CONSOLE_HOST','127.0.0.1'),port=int(os.environ.get('CONSOLE_PORT','6194')),print=None)
