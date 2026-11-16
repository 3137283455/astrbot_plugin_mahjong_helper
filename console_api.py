"""Dashboard operations on the live plugin, sharing its existing work locks.

No fake QQ events, frozen-season replay, command injection, or chat notifications.
"""
import asyncio
import json
import secrets
import time
from pathlib import Path

from .source import unwrap


PREFIX='/astrbot_plugin_group_data/console'


class ConsoleActions:
    def __init__(self,plugin):
        self.plugin=plugin;self.closed=False;self.jobs={};self.active={};self.cooldown={}
        self.path=Path(plugin.folder)/'console_jobs.json'
        if self.path.exists():
            try:
                saved=json.loads(self.path.read_text())
                for job in saved[-30:]:
                    if job['status']=='running':job.update(status='interrupted',phase='插件已重新加载，可重新发起',finished=time.time())
                    self.jobs[job['id']]=job
            except (ValueError,KeyError,TypeError):self.jobs={}
    def persist(self):
        while len(self.jobs)>30:
            key=next((key for key,v in self.jobs.items() if v['status']!='running'),None)
            if key is None:break
            self.jobs.pop(key)
        tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(list(self.jobs.values()),ensure_ascii=False));tmp.replace(self.path)
    def register(self):
        self.plugin.context.register_web_api(PREFIX+'/status',self.status_api,['GET'],'Group data live synchronization status')
        self.plugin.context.register_web_api(PREFIX+'/run',self.run_api,['POST'],'Start current-season group synchronization')
    def close(self):
        self.closed=True
        context=self.plugin.context
        context.registered_web_apis[:]=[api for api in context.registered_web_apis if not api[0].startswith(PREFIX+'/')]
    def group(self,scope):
        group=self.plugin.store.group(scope)
        if not group:raise ValueError('该群尚未接入群数据')
        if not group['enabled']:raise ValueError('该群已暂停，请先在群内开启统计')
        return group
    def snapshot(self,scope):
        p=self.plugin;group=p.store.group(scope);counts=p.store.summary(scope)
        online=getattr(p,'online',None)
        if online is not None and online.scope!=scope:online=None
        rows=p.store.rows(scope);running=getattr(p.recognizer,'running',set())
        source_lock=p.source.locks.get(scope)
        period=p.store.db.execute("SELECT name FROM periods WHERE scope=? AND state='current'",(scope,)).fetchone()
        info=online.get('last_sync',{}) if online else {};error=online.get('last_sync_error',{}) if online else {}
        return {'scope':scope,'gid':group['gid'],'enabled':bool(group['enabled']),'period':period[0] if period else '当前赛季',
                'last_scan':group['last_scan'],'last_import':group.get('last_import',0),'scan_error':group.get('error',''),
                'scanning':bool(source_lock and source_lock.locked()),'games':counts.get('games',0),
                'images':counts.get('images',0),'pending':counts.get('new',0)+counts.get('llm_pending',0),
                'recognizing':sum(r['hash'] in running for r in rows),'review':counts.get('review',0),
                'errors':sum(counts.get(k,0) for k in ('local_error','download_error')),
                'feishu':{'configured':online is not None,'running':bool(online and online.lock.locked()),'last_sync':info.get('stamp',0),'issues':len(info.get('errors',[])),
                           'last_error':error.get('type','') if error.get('stamp',0)>info.get('stamp',0) else ''}}
    def status(self):
        groups=[self.snapshot(r['scope']) for r in self.plugin.store.db.execute('SELECT scope FROM groups ORDER BY gid').fetchall()]
        return {'groups':groups,'jobs':list(reversed(list(self.jobs.values())))[0:15]}
    async def bot(self,group):
        for platform in self.plugin.context.platform_manager.get_insts():
            if platform.meta().name!='aiocqhttp':continue
            client=platform.get_client()
            try:
                info=unwrap(await asyncio.wait_for(client.call_action(action='get_login_info'),8))
                if str(info['user_id'])==str(group['bot']):return client
            except Exception:continue
        raise ValueError('QQ连接暂不可用，请稍后重试')
    def start(self,scope,action,actor):
        if self.closed:raise ValueError('插件正在重新加载')
        if action not in ('essence','feishu'):raise ValueError('不支持这个操作')
        group=self.group(scope);key=(scope,action)
        existing=self.active.get(key)
        if existing and self.jobs[existing]['status']=='running':return {'job':self.jobs[existing],'reused':True}
        online=getattr(self.plugin,'online',None)
        if action=='feishu' and (online is None or online.scope!=scope):raise ValueError('该群尚未配置飞书同步')
        last=self.cooldown.get(key,0)
        if time.monotonic()-last<30:raise ValueError('刚发起过该操作，请稍后再试')
        self.cooldown[key]=time.monotonic();ident=secrets.token_hex(8)
        job={'id':ident,'scope':scope,'gid':group['gid'],'action':action,'status':'running','phase':'准备检查QQ精华' if action=='essence' else '准备同步飞书','started':time.time(),'finished':0,'actor':actor}
        self.jobs[ident]=job;self.active[key]=ident;self.persist()
        self.plugin.spawn(self.run(job,group))
        return {'job':job,'reused':False}
    def phase(self,job,text):job['phase']=text;self.persist()
    async def run(self,job,group):
        p=self.plugin;scope=job['scope']
        try:
            if job['action']=='essence':
                client=await self.bot(group);lock=p.source.locks.get(scope)
                if lock and lock.locked():
                    self.phase(job,'已有精华检查正在进行，等待本轮结果')
                    await asyncio.wait_for(self.wait_lock(lock),300)
                else:
                    self.phase(job,'正在读取精华列表与新增图片')
                    ok=await p.source.sync(scope,client,group['gid'])
                    if not ok and not (p.source.locks.get(scope) and p.source.locks[scope].locked()):
                        raise ValueError('精华检查未完成，请查看最近检查异常')
                    if not ok:await asyncio.wait_for(self.wait_lock(p.source.locks[scope]),300)
                state=self.snapshot(scope)
                if state['scan_error']:raise ValueError('精华检查失败，请查看最近检查异常')
                job.update(status='done',phase='精华检查完成；新图片由现有识别队列继续处理',result=state)
            else:
                online=p.online
                if online.lock.locked():
                    self.phase(job,'已有飞书同步正在进行，等待本轮结果')
                    await asyncio.wait_for(self.wait_lock(online.lock),600)
                    result=online.get('last_sync',{});error=online.get('last_sync_error',{})
                    if error.get('stamp',0)>result.get('stamp',0):raise ValueError('飞书同步失败，请查看最近同步异常')
                else:
                    self.phase(job,'正在校验并同步当前赛季数据')
                    result=await asyncio.wait_for(online.once(),600)
                issues=len(result.get('errors',[]));job.update(status='partial' if issues else 'done',phase=f'飞书同步完成，待处理项 {issues}',result=self.snapshot(scope))
        except asyncio.CancelledError:
            job.update(status='interrupted',phase='插件重新加载，任务已中断');raise
        except Exception as exc:
            job.update(status='failed',phase=str(exc)[:180] if isinstance(exc,ValueError) else '同步未完成：'+type(exc).__name__)
        finally:
            job['finished']=time.time();self.persist()
    @staticmethod
    async def wait_lock(lock):
        async with lock:pass
    async def status_api(self):
        from astrbot.api.web import request,json_response,error_response
        if not request.username:return error_response('仅限管理员访问',status_code=403)
        if self.closed:return error_response('插件正在重新加载',status_code=503)
        return json_response(self.status())
    async def run_api(self):
        from astrbot.api.web import request,json_response,error_response
        if not request.username:return error_response('仅限管理员访问',status_code=403)
        body=await request.json(default={})
        if not isinstance(body,dict):return error_response('请求格式不正确')
        if body.get('period') not in (None,'current'):return error_response('仅允许同步当前赛季，封存赛季只读')
        try:return json_response(self.start(str(body.get('scope','')),body.get('action'),request.username))
        except ValueError as exc:return error_response(str(exc))
