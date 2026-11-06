"""Validated two-way detail sync; statistics are always derived locally.

Only confirmed edits by configured Feishu administrators can change local data.
Three-way comparisons preserve concurrent edits for explicit conflict resolution.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import time
from decimal import Decimal,InvalidOperation
from pathlib import Path
from . import periods
from .core import aggregate,validate,normalize_name
from .feishu import Feishu,FeishuError,NUMBERS,DATES
from .scoring import score_matches,online_fields,SCORE_FIELDS,SCORE_NUMBERS,VERSION as SCORE_VERSION

NAMES=['一位昵称','二位昵称','三位昵称','四位昵称（三麻留空）']
POINTS=['一位持点','二位持点','三位持点','四位持点（三麻留空）']
EDITABLE=['模式',*NAMES,*POINTS,'确认状态','备注','对局时间（可空）']


def digest(value):return hashlib.sha256(periods.packed(value).encode()).hexdigest()


def normalize(fields):
    out={}
    for key,value in fields.items():
        if value in (None,'',[]):continue
        if isinstance(value,list):
            if key=='原图附件':value=[{'file_token':r['file_token']} for r in value if r.get('file_token')]
            else:value=''.join(str(r.get('text','')) for r in value if isinstance(r,dict))
        if isinstance(value,str) and key in NUMBERS|DATES:
            try:
                number=Decimal(value)
                if number.is_finite():value=int(number) if number==number.to_integral_value() else float(number)
            except (InvalidOperation,ValueError,OverflowError):pass
        out[key]=value
    return out


def editable(fields):
    fields=normalize(fields)
    return {k:fields[k] for k in EDITABLE if k in fields}


class OnlineSync:
    def __init__(self,store,folder,target,client=None):
        self.store=store;self.folder=Path(folder);self.target=target
        self.client=client or Feishu(self.folder/'feishu_credentials.json',target['app_token'])
        self.tables=target['tables'];self.scope=target['scope'];self.lock=asyncio.Lock()
        self.allowed=set(target.get('editor_ids',[]));self.bot_id=target.get('bot_open_id','')
        if not self.allowed:raise ValueError('飞书管理员名单不能为空')
        self.interval=max(120,int(target.get('poll_seconds',600)))
        self.wake=asyncio.Event()
        self.store.subscribe(self._changed)

    def _changed(self,scope):
        if scope==self.scope:self.wake.set()

    def get(self,key,default=None):
        row=self.store.db.execute('SELECT value FROM online_state WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self,key,value):
        self.store.db.execute('INSERT OR REPLACE INTO online_state VALUES(?,?)',(key,periods.packed(value)));self.store.db.commit()

    def links(self,table='matches'):
        return {r['key']:dict(r) for r in self.store.db.execute('SELECT * FROM online_links WHERE table_key=?',(table,))}

    def remember(self,key,record,fields,table='matches',error=''):
        self.store.db.execute('INSERT OR REPLACE INTO online_links VALUES(?,?,?,?,?,?,0)',
            (key,table,record,periods.packed(normalize(fields)),digest(fields),error));self.store.db.commit()

    def authorized(self,row):
        return (row.get('last_modified_by') or {}).get('id') in self.allowed

    def annotations(self,key):return self.get('annotations:'+key,{})

    def frozen_fields(self,fields):
        """Archived online rows are outside the ongoing synchronization contract."""
        keys=[str(fields.get(k) or '') for k in ('对局ID','统计ID','周期ID')]
        for p in periods.all_periods(self.store,self.scope):
            if p['state']!='archived':continue
            if fields.get('赛季')==p['name'] or any(p['id'] in key.split(':') for key in keys):return True
        return False

    def detail(self,scope,period,match,status='已确认'):
        data=match['result'];key=scope+':'+period['id']+':'+str(match['id'])
        fields={'对局ID':key,'群标识':scope,'周期ID':period['id'],'赛季':period['name'],'模式':data['mode'],
                '精华来源编号':match['id'],'精华时间':match['stamp']*1000,'确认状态':status,
                **self.annotations(key)}
        for i,p in enumerate(data['players']):fields[NAMES[i]]=p['name'];fields[POINTS[i]]=p['points']
        token=self.get('attachment:'+match['hash'])
        if token:fields['原图附件']=[{'file_token':token}]
        fields['数据版本']=digest(fields)[:16];fields['同步状态']='已同步'
        return key,fields

    def projection(self):
        scope=self.scope;out={};metadata={};players={};source_rows={}
        for p in periods.all_periods(self.store,scope):
            if p['state']!='current':continue
            pid=p['id'];matches=self.store.matches(scope,period=pid);aliases=self.store.aliases(scope,pid)
            metadata[pid]={'周期ID':pid,'群标识':scope,'周期名称':p['name'],'状态':'进行中' if p['state']=='current' else '已封存',
                           '开始时间':int(p['started']*1000),'封存清单校验值':p['checksum']}
            if p['ended']:metadata[pid]['封存时间']=int(p['ended']*1000)
            for match in matches:
                key,fields=self.detail(scope,p,match);out[key]=fields;source_rows[key]=(p,match)
            for mode in (3,4):
                scored={r['name']:r for r in score_matches(matches,mode,aliases)['rows']}
                for s in aggregate(matches,mode,aliases):
                    key=pid+':'+str(mode)+':'+digest(s['name'])[:24]
                    fields={'统计ID':key,'群标识':scope,'周期ID':pid,'赛季':p['name'],'模式':mode,'玩家标识':s['name'],'显示昵称':s['name'],
                            '场次':s['games'],'被飞次数':s['fly'],'被飞率':s['fly']/s['games'],
                            '平均顺位':float(s['avg_place']),'平均终局持点':float(s['avg_points']),
                            '最高终局持点':s['max_points'],'最低终局持点':s['min_points'],
                            '最高记录ID':scope+':'+pid+':'+str(s['max_points_source']),
                            '最低记录ID':scope+':'+pid+':'+str(s['min_points_source'])}
                    for i in range(1,5):
                        word=['一','二','三','四'][i-1]
                        fields[word+'位次数']=s['rank'+str(i)];fields[word+'位率']=s['rank'+str(i)]/s['games']
                    fields.update(online_fields(scored.get(s['name'])))
                    fields['统计版本']=digest(fields)[:16];players[key]=fields
        # A removed essence/ignored result remains visible as a soft-disabled row.
        for key,link in self.links().items():
            if key in out:continue
            old=json.loads(link['baseline']);pid=old.get('周期ID')
            p=next((p for p in periods.all_periods(self.store,scope) if p['id']==pid),None)
            if not p or p['state']!='current':continue
            fields={**old,**self.annotations(key),'赛季':p['name'],'确认状态':'停用','同步状态':'已同步'}
            source=self.store.get_source(scope,int(old.get('精华来源编号',0)))
            payload=(source.get('override_result') or source.get('result')) if source else None
            if payload:
                source={**source,'result':json.loads(payload)}
                _,fields=self.detail(scope,p,source,'停用')
            if source and (source['override_status'] or source['status']) in ('review','llm_pending','local_error','new'):
                fields['确认状态']='待核对'
            fields.pop('冲突处理',None);fields['数据版本']=digest(editable(fields))[:16];out[key]=fields
        return out,metadata,players,source_rows

    async def attachments(self,source_rows):
        queue=asyncio.Queue();seen=set();errors=[]
        for _,row in source_rows.values():
            if row['hash'] not in seen and not self.get('attachment:'+row['hash']):queue.put_nowait(row)
            seen.add(row['hash'])
        async def worker():
            while not queue.empty() and not errors:
                row=queue.get_nowait()
                try:
                    token=await self.client.upload(row['path']);self.put('attachment:'+row['hash'],token)
                except (FeishuError,OSError,ValueError) as exc:
                    errors.append('原图附件暂未全部上传：'+(str(exc) if isinstance(exc,FeishuError) else type(exc).__name__)+'；本地原图保留，后续自动重试')
        await asyncio.gather(*(worker() for _ in range(3)))
        return errors[:1]

    async def apply(self,key,fields):
        fields=normalize(fields);baseline=json.loads(self.links()[key]['baseline'])
        p=periods.select(self.store,self.scope,baseline['周期ID'])
        if p['state']!='current':raise ValueError('封存周期不可修改')
        source=self.store.get_source(self.scope,int(baseline['精华来源编号']))
        if not source:raise ValueError('来源已取消精华或不属于本周期，不能在线恢复')
        if fields.get('确认状态') not in ('已确认','停用','待核对'):raise ValueError('确认状态只能填写：已确认、停用、待核对')
        mode=fields.get('模式')
        if type(mode) not in (int,float) or mode not in (3,4):raise ValueError('模式只能填写3或4')
        mode=int(mode)
        if mode==3 and any(fields.get(k) not in (None,'') for k in (NAMES[3],POINTS[3])):raise ValueError('三麻的四位字段必须留空')
        players=[]
        for i in range(mode):
            points=fields.get(POINTS[i])
            if type(points) not in (int,float) or int(points)!=points:raise ValueError('持点必须是完整整数')
            players.append({'name':fields.get(NAMES[i]),'place':i+1,'points':int(points)})
        result=validate({'is_result':True,'mode':mode,'players':players},approve=True)
        notes=fields.get('备注','')
        if not isinstance(notes,str) or len(notes)>2000:raise ValueError('备注不能超过2000字')
        played=fields.get('对局时间（可空）')
        if played is not None and (type(played) not in (int,float) or not 0<played<time.time()*1000+86400000):raise ValueError('对局时间无效')
        # No await inside this block: the comparison and mutation share one event-loop turn.
        status={'已确认':'accepted','停用':'ignored','待核对':'review'}[fields['确认状态']]
        self.store.override(self.scope,source,status,result,'飞书管理员核对')
        self.put('annotations:'+key,{k:fields[k] for k in ('备注','对局时间（可空）') if k in fields})
        self.store.audit(self.scope,'feishu','在线修正',{'key':key,'fields':editable(fields)})

    async def import_new(self,row):
        fields=normalize(row.get('fields') or {})
        if fields.get('对局ID') or not any(k in fields for k in NAMES+POINTS):return
        if not self.authorized(row):raise ValueError('新增行的修改者不在飞书管理员名单')
        current=periods.select(self.store,self.scope)
        if not current:raise ValueError('尚未切换统计周期')
        if fields.get('群标识',self.scope)!=self.scope or fields.get('周期ID',current['id'])!=current['id']:raise ValueError('新增行只能录入本群当前周期')
        if fields.get('确认状态')!='已确认':raise ValueError('新增行填好完整结算并核对原图后，将确认状态改为“已确认”')
        attachments=fields.get('原图附件') or []
        if len(attachments)!=1:raise ValueError('新增对局需要且只需一张原始结算截图')
        # First validate without changing the database or downloading anything.
        mode=fields.get('模式')
        if mode not in (3,4):raise ValueError('模式只能填写3或4')
        mode=int(mode)
        if mode==3 and any(fields.get(k) not in (None,'') for k in (NAMES[3],POINTS[3])):raise ValueError('三麻的四位字段必须留空')
        players=[]
        for i in range(mode):
            value=fields.get(POINTS[i])
            if type(value) not in (int,float) or value!=int(value):raise ValueError('持点必须是完整整数')
            players.append({'name':fields.get(NAMES[i]),'place':i+1,'points':int(value)})
        result=validate({'is_result':True,'mode':mode,'players':players},approve=True)
        image,path=await self.client.download(attachments[0]['file_token'],self.folder/'images')
        if self.store.db.execute('''SELECT 1 FROM sources s JOIN source_periods sp ON sp.source_id=s.id JOIN periods p ON p.id=sp.period_id
            WHERE s.scope=? AND s.hash=? AND p.state='archived' ''',(self.scope,image)).fetchone():raise ValueError('这张图片已封存，不能重新计入新周期')
        rid=row['record_id'];msg='feishu:'+rid
        # Idempotent even if a response is lost after the local import.
        with self.store.db:
            self.store.db.execute("INSERT OR IGNORE INTO sources(scope,msg,idx,stamp,seen,origin) VALUES(?,?,0,?,?,'feishu')",(self.scope,msg,int(time.time()),time.time()))
            periods.assign(self.store,self.scope)
        source=self.store.source(self.scope,msg,0);self.store.attach(source['id'],image,path)
        self.store.override(self.scope,self.store.get_source(self.scope,source['id']),'accepted',result,'飞书管理员新增并核对原图')
        key=self.scope+':'+current['id']+':'+str(source['id'])
        self.put('attachment:'+image,attachments[0]['file_token'])
        self.put('annotations:'+key,{k:fields[k] for k in ('备注','对局时间（可空）') if k in fields})
        desired=self.projection()[0][key]
        await self.client.update(self.tables['matches'],rid,desired)
        self.remember(key,rid,desired)
        row['fields']=desired
        self.store.audit(self.scope,'feishu','在线新增',{'key':key})

    async def error(self,key,row,message):
        # Error text is ours, never a raw API response containing private data.
        message=message[:250]
        if key in self.links():
            self.store.db.execute('UPDATE online_links SET error=? WHERE key=?',(message,key));self.store.db.commit()
        if normalize(row.get('fields') or {}).get('同步状态')!=message:
            await self.client.update(self.tables['matches'],row['record_id'],{'同步状态':message})

    async def mirror(self,kind,wanted,id_field):
        table=self.tables[kind];rows=await self.client.records(table);index={}
        for row in rows:
            normalized=normalize(row.get('fields') or {})
            if self.frozen_fields(normalized):continue
            if normalized.get('群标识')!=self.scope:continue
            key=normalized.get(id_field)
            if key in index:raise ValueError('派生表存在重复ID：'+kind)
            if key:index[key]=row
        pending=[normalize(fields) for key,fields in wanted.items() if key not in index]
        if kind=='players':pending=[{**f,'更新时间':int(time.time()*1000)} for f in pending]
        for start in range(0,len(pending),50):
            for row in await self.client.batch_create(table,pending[start:start+50]):
                index[normalize(row['fields'])[id_field]]=row
        for key,fields in wanted.items():
            fields=normalize(fields)
            row=index.get(key)
            old=normalize(row.get('fields') or {}) if row else {}
            # Derived values are authoritative; timestamp advances only on actual change.
            if {k:v for k,v in old.items() if k!='更新时间'}==fields:continue
            payload={**fields}
            if kind=='players':payload['更新时间']=int(time.time()*1000)
            if row:
                await self.client.update(table,row['record_id'],{**{k:None for k in old if k not in payload},**payload})
            else:await self.client.create(table,payload)
        # Never delete administrator data. Stale derived rows become explicit zero samples.
        if kind=='players':
            for key,row in index.items():
                if key not in wanted and normalize(row['fields']).get('场次',0)!=0:
                    old=normalize(row['fields']);zero={k:0 for k in old if k.endswith(('次数','位率')) or k in ('场次','被飞率','平均顺位','平均终局持点','最高终局持点','最低终局持点')}
                    cleared={k:None for k in SCORE_FIELDS}
                    await self.client.update(table,row['record_id'],{**zero,**cleared,'评分有效场次':0,'综合评分状态':'无有效评分','综合评分版本':SCORE_VERSION,'统计版本':'无有效对局','更新时间':int(time.time()*1000)})

    async def once(self):
        async with self.lock:
            if not periods.select(self.store,self.scope):raise ValueError('未切换周期，不执行在线导入')
            if not getattr(self,'_score_schema_ready',False):
                await self.client.ensure_fields(self.tables['players'],SCORE_FIELDS)
                self._score_schema_ready=True
            table=self.tables['matches'];remote=await self.client.records(table);errors=[]
            remote=[r for r in remote if normalize(r.get('fields') or {}).get('群标识') in (None,self.scope)
                    and not self.frozen_fields(normalize(r.get('fields') or {}))]
            for row in remote:
                try:await self.import_new(row)
                except ValueError as exc:
                    errors.append(str(exc));await self.error('',row,str(exc))
            index={}
            for row in remote:
                key=normalize(row.get('fields') or {}).get('对局ID')
                if not key:continue
                if key in index:raise ValueError('对局明细出现重复对局ID；本轮暂停，防止覆盖')
                index[key]=row
            desired,meta,players,sources=self.projection()
            errors.extend(await self.attachments(sources))
            desired,meta,players,sources=self.projection()
            links=self.links()
            # Initial history imports can contain hundreds of games. Batched creates
            # avoid one network round trip per row; stable IDs recover uncertain POSTs.
            pending=[normalize(f) for key,f in desired.items() if key not in index]
            for key in desired:
                if key not in index and key in links:errors.append('在线行被删除，已按本地数据恢复；停用请修改确认状态')
            for start in range(0,len(pending),50):
                batch=await self.client.batch_create(table,pending[start:start+50])
                if {normalize(r['fields']).get('对局ID') for r in batch}!={f['对局ID'] for f in pending[start:start+50]}:
                    raise FeishuError('飞书批量创建响应不完整；下轮按稳定ID核对，不盲目重试')
                for row in batch:
                    fields=normalize(row['fields']);key=fields['对局ID'];index[key]=row
                    self.remember(key,row['record_id'],desired[key])
            links=self.links()
            for key,fields in desired.items():
                # Re-read local state after earlier network awaits. Native corrections
                # made during a long image upload must participate in the comparison.
                if sources.get(key,({},None))[0].get('state')=='current':
                    fields=self.projection()[0].get(key,fields)
                fields=normalize(fields)
                row=index.get(key);link=links.get(key)
                if row and link and row['record_id']!=link['record_id']:
                    message='对局ID被移到其他行；请恢复原行ID，避免串局';errors.append(message);await self.error(key,row,message);continue
                if not row:
                    created=await self.client.create(table,fields)
                    self.remember(key,created['record_id'],fields)
                    if link:errors.append('在线行被删除，已按本地数据恢复；停用请修改确认状态')
                    continue
                actual=normalize(row.get('fields') or {});baseline=json.loads(link['baseline']) if link else fields
                local_change=editable(fields)!=editable(baseline)
                remote_change=editable(actual)!=editable(baseline)
                resolution=actual.get('冲突处理','')
                if remote_change:
                    period=periods.select(self.store,self.scope,fields['周期ID'])
                    if period['state']=='archived':
                        errors.append('已还原对封存记录的修改')
                    elif not self.authorized(row):
                        message='在线编辑者未获管理员授权；数据未采用';errors.append(message);await self.error(key,row,message);continue
                    elif local_change and editable(actual)!=editable(fields) and resolution not in ('采用在线','采用机器人'):
                        message='双方同时修改：核对后在冲突处理填写“采用在线”或“采用机器人”'
                        errors.append(message);await self.error(key,row,message);continue
                    elif not link:
                        message='首次同步发现未登记的在线改动；先恢复机器人原值再修改'
                        errors.append(message);await self.error(key,row,message);continue
                    elif resolution!='采用机器人':
                        try:
                            await self.apply(key,actual)
                            fields=self.projection()[0][key]
                        except ValueError as exc:
                            errors.append(str(exc));await self.error(key,row,str(exc));continue
                payload={**{k:None for k in actual if k not in fields},**fields}
                if actual!=normalize(fields):await self.client.update(table,row['record_id'],payload)
                self.remember(key,row['record_id'],fields)
            # Refresh after accepted online edits so the same cycle updates player stats.
            _,meta,players,_=self.projection()
            await self.mirror('periods',meta,'周期ID');await self.mirror('players',players,'统计ID')
            self.put('last_sync',{'stamp':time.time(),'errors':errors,'games':len(self.store.matches(self.scope))})
            status={'任务ID':self.scope,'群标识':self.scope,'周期ID':periods.select(self.store,self.scope)['id'],
                    '同步方向':'双向校验；玩家统计由对局自动计算','状态':'有待处理项' if errors else '正常',
                    '最近成功时间':int(time.time()*1000),'异常说明':'；'.join(errors[:8]) if errors else '在线改整局数据后自动重算；停用请修改确认状态。历史已封存不可改。',
                    '重试次数':0}
            await self.mirror('sync_log',{self.scope:status},'任务ID')
            return self.get('last_sync')

    async def loop(self):
        while True:
            self.wake.clear()
            try:await self.once()
            except asyncio.CancelledError:raise
            except Exception as exc:
                # API/network failures never reconcile deletions or clear statistics.
                self.put('last_sync_error',{'stamp':time.time(),'type':type(exc).__name__,'message':str(exc) if isinstance(exc,(ValueError,FeishuError)) else '网络或同步异常，将自动重试'})
                # Keep failures from causing a tight retry loop when local data changes.
                await asyncio.sleep(min(60,self.interval));continue
            try:
                await asyncio.wait_for(self.wake.wait(),timeout=self.interval)
                # Coalesce attachment/recognition changes from the same message.
                await asyncio.sleep(1)
            except asyncio.TimeoutError:pass
