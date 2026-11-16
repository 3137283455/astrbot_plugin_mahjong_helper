"""Explicit per-plugin operations. No shell commands or simulated QQ messages."""
import asyncio
import json
import hashlib
import secrets
import re
import sys
import time

GROUP='astrbot_plugin_group_data'
BILI='astrbot_plugin_bilibili'
RADAR='astrbot_plugin_perkradar'
EXT='/api/v1/plugins/extensions/'


class Operations:
    def __init__(self,console,problem):
        self.c=console;self.Problem=problem;self.radar=None;self.radar_task=None;self.radar_at=0
        self.c.db.execute('''CREATE TABLE IF NOT EXISTS operations(
            id TEXT PRIMARY KEY,plugin TEXT,action TEXT,status TEXT,phase TEXT,started REAL,finished REAL,result TEXT)''')
        self.c.db.execute("UPDATE operations SET status='interrupted',phase='管理服务已重新加载，可重新发起',finished=? WHERE status='running'",(time.time(),));self.c.db.commit()
    def client(self):
        if self.radar is None:
            parent=str(self.c.base/'data/plugins')
            if parent not in sys.path:sys.path.insert(0,parent)
            from astrbot_plugin_perkradar.client import Client
            self.radar=Client('/bot/perkradar/deploy/client.json')
        return self.radar
    def jobs(self,name):
        return [dict(r) for r in self.c.db.execute('SELECT id,plugin,action,status,phase,started,finished FROM operations WHERE plugin=? ORDER BY started DESC LIMIT 10',(name,))]
    async def status(self,session,name):
        plugin=await self.c.plugin(session,name)
        if not plugin['activated']:return {'supported':name in (GROUP,BILI,RADAR),'inactive':True}
        if name==GROUP:
            data=await self.c.call(session,'GET',EXT+name+'/console/status')
            return {'supported':True,'kind':'group',**data}
        if name==RADAR:
            data=await self.client().board()
            health=data.get('sourceHealth') or {}
            return {'supported':True,'kind':'radar','last_scan':(data.get('lastUpdatedAt') or 0)/1000,'sources':health,'items':len(data['deals']),
                    'running':bool(self.radar_task and not self.radar_task.done()),'jobs':self.jobs(name)}
        if name==BILI:
            data,status=await asyncio.gather(self.c.call(session,'GET',EXT+name+'/subscriptions'),self.c.call(session,'GET',EXT+name+'/status'))
            return {'supported':True,'kind':'bilibili',**data,'listener':status,'version':self.bili_version(data)}
        return {'supported':False}
    @staticmethod
    def bili_version(data):
        rows=[]
        for target in data.get('sessions',[]):
            for r in target['subscriptions']:
                rows.append({'umo':target['umo'],**{k:r.get(k) for k in ('uid','filter_types','filter_regex','live_atall','at_all','at_sub_users')}})
        return hashlib.sha256(json.dumps(sorted(rows,key=lambda r:(r['umo'],r['uid'])),sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    async def run(self,session,name,payload):
        plugin=await self.c.plugin(session,name,force=True)
        if not plugin['activated']:raise self.Problem('请先启用这个插件')
        action=payload.get('action')
        if name==GROUP:
            if action not in ('essence','feishu'):raise self.Problem('不支持这个操作')
            result=await self.c.call(session,'POST',EXT+name+'/console/run',{'scope':payload.get('scope'),'action':action,'period':'current'})
            self.c.audit(session['user'],'检查QQ精华' if action=='essence' else '同步飞书',str(payload.get('scope')),'已安排')
            return result
        if name==RADAR:
            if action!='refresh':raise self.Problem('不支持这个操作')
            if self.radar_task and not self.radar_task.done():return {'job':self.jobs(name)[0],'reused':True}
            if time.monotonic()-self.radar_at<60:raise self.Problem('刚抓取过，请一分钟后重试',429)
            self.radar_at=time.monotonic();ident=secrets.token_hex(8)
            self.c.db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?)',(ident,name,action,'running','正在抓取公开来源',time.time(),0,''));self.c.db.commit()
            self.radar_task=asyncio.create_task(self.refresh_radar(ident));self.c.background.add(self.radar_task);self.radar_task.add_done_callback(self.c.background.discard)
            self.c.audit(session['user'],'立即抓取优惠',name,'已安排')
            return {'job':self.jobs(name)[0],'reused':False}
        if name==BILI:
            if action not in ('create','update','delete'):raise self.Problem('不支持这个操作')
            async with self.c.locks['bili-subscriptions']:
                current=await self.c.call(session,'GET',EXT+name+'/subscriptions')
                if payload.get('version')!=self.bili_version(current):raise self.Problem('订阅已发生变化，请刷新后重新操作',409)
                value=payload.get('subscription')
                if not isinstance(value,dict):raise self.Problem('请填写订阅信息')
                uid=str(value.get('uid','')).strip()
                if not re.fullmatch(r'[0-9]{1,20}',uid) or int(uid)<1:raise self.Problem('请填写有效的UP主UID')
                value={**value,'uid':str(int(uid)),'umo':str(value.get('umo','')).strip()}
                present=any(t['umo']==value.get('umo') and str(r['uid'])==str(value.get('uid')) for t in current.get('sessions',[]) for r in t['subscriptions'])
                if action=='create' and present:raise self.Problem('这条订阅已经存在，请使用编辑',409)
                if action in ('update','delete') and not present:raise self.Problem('这条订阅已不存在，请刷新列表',404)
                self.c.save_backup(session['user'],'B站订阅',current)
                result=await self.c.call(session,'POST',EXT+name+'/subscriptions/'+action,value)
                self.c.audit(session['user'],{'create':'新增B站订阅','update':'修改B站订阅','delete':'删除B站订阅'}[action],str(value.get('uid')))
                return result
        raise self.Problem('此插件暂未接入专属操作',404)
    async def refresh_radar(self,ident):
        status='done';phase='抓取完成'
        try:
            data=await self.client().refresh();h=data.get('sourceHealth') or {}
            if h.get('ok',0)<h.get('total',7):status='partial';phase=f"抓取完成，正常来源 {h.get('ok',0)}/{h.get('total',7)}"
        except asyncio.CancelledError:status='interrupted';phase='管理服务重新加载，任务已中断';raise
        except Exception as exc:status='failed';phase='抓取未完成：'+type(exc).__name__
        finally:
            self.c.db.execute('UPDATE operations SET status=?,phase=?,finished=? WHERE id=?',(status,phase,time.time(),ident));self.c.db.commit()
