"""Persist platform receipts independently from review results. Never infer recipient receipt."""
import asyncio,hashlib,json,re,time
from pathlib import Path
from urllib.parse import unquote
from aiocqhttp.exceptions import ActionFailed
from astrbot.api.event import MessageChain
import astrbot.api.message_components as Comp
from .replay_core import URL_RE

CONFIRMED=('acknowledged','history_acknowledged','fallback_acknowledged')

async def private_history_receipt(bot,job,chain,since,bot_uin):
    def flatten(items):
        for p in items:
            if isinstance(p,Comp.Nodes):
                for node in p.nodes:yield from flatten(node.content)
            elif isinstance(p,Comp.Node):yield from flatten(p.content)
            else:yield p
    content=list(flatten(chain.chain))
    if not content or not all(isinstance(p,(Comp.Plain,Comp.Image)) for p in content):return None
    expected=' '.join(''.join(p.text for p in content if isinstance(p,Comp.Plain)).split())
    hashes=[]
    for part in content:
        if isinstance(part,Comp.Image):
            path=str(getattr(part,'path','') or getattr(part,'file',''))
            if path.startswith('file://'):path=unquote(path[7:])
            try:hashes.append(await asyncio.to_thread(lambda p=path:hashlib.md5(Path(p).read_bytes()).hexdigest()))
            except (OSError,ValueError):return None
    if not (expected or hashes) or not str(bot_uin).isdigit() or str(bot_uin)=='0':return None
    params={'user_id':int(job['actor']),'count':40}
    if ':GroupMessage:' in job['scope']:
        group=str(job['scope']).rsplit(':',1)[-1].split('_')[-1]
        if not group.isdigit():return None
        params['group_id']=int(group)
    data=await bot.call_action('get_friend_msg_history',**params)
    if isinstance(data,dict) and 'data' in data:data=data['data']
    for msg in data.get('messages',[]) if isinstance(data,dict) else []:
        try:
            sender=str(msg.get('user_id') or msg.get('sender',{}).get('user_id',''))
            seq=int(msg.get('real_seq') or 0);stamp=float(msg.get('time') or 0)
            if sender!=str(bot_uin) or seq<=0 or stamp<since-2:continue
            parts=msg.get('message',[])
            if not isinstance(parts,list):continue
            if any(isinstance(p,(Comp.Node,Comp.Nodes)) for p in chain.chain):
                forwards=[p.get('data',{}).get('id') for p in parts if isinstance(p,dict) and p.get('type')=='forward']
                if len(forwards)!=1 or not forwards[0]:continue
                forwarded=await asyncio.wait_for(bot.call_action('get_forward_msg',message_id=forwards[0]),4)
                if isinstance(forwarded,dict) and 'data' in forwarded:forwarded=forwarded['data']
                nodes=forwarded.get('messages',[]) if isinstance(forwarded,dict) else []
                parts=[p for node in nodes for p in (node.get('content') or node.get('message') or []) if isinstance(p,dict)]
            text=''.join(str(p.get('data',{}).get('text','')) for p in parts if isinstance(p,dict) and p.get('type')=='text')
            if ' '.join(text.split())!=expected:continue
            found=[]
            for p in parts:
                if isinstance(p,dict) and p.get('type')=='image':
                    found.extend(re.findall(r'(?i)(?<![a-f0-9])[a-f0-9]{32}(?![a-f0-9])',str(p.get('data',{}).get('file',''))))
            if sorted(x.lower() for x in found)!=sorted(hashes):continue
            return {'message_id':msg.get('message_id'),'real_seq':seq,'time':stamp}
        except (TypeError,ValueError):continue
    return None

def forward_text(chain):
    """A compact, different payload when QQ rejects a merged forward."""
    if not any(isinstance(p,(Comp.Node,Comp.Nodes)) for p in chain.chain):return []
    texts=[]
    def collect(parts):
        for part in parts:
            if isinstance(part,Comp.Plain):texts.append(part.text)
            elif isinstance(part,Comp.Nodes):
                for node in part.nodes:collect(node.content)
            elif isinstance(part,Comp.Node):collect(part.content)
    collect(chain.chain)
    return split_result(MessageChain().message('\n\n'.join(texts))) if texts else []

class GroupPrivateBot:
    """Keep the source group while AstrBot serializes private message segments."""
    def __init__(self,bot,group_id):self.bot=bot;self.group_id=int(group_id)
    def __getattr__(self,name):return getattr(self.bot,name)
    async def call_action(self,action,**params):
        if action not in ('send_private_msg','send_private_forward_msg'):
            raise ValueError('复盘临时会话只允许发送私聊结果')
        params['group_id']=self.group_id
        return await self.bot.call_action(action,**params)
    async def send_private_msg(self,**params):return await self.call_action('send_private_msg',**params)

def split_result(chain):
    """Send images separately, merge text segments, limit each message to two URLs."""
    if any(isinstance(p,(Comp.Node,Comp.Nodes)) for p in chain.chain):return [chain]
    mentions=[p for p in chain.chain if isinstance(p,Comp.At)]
    images=[p for p in chain.chain if isinstance(p,Comp.Image)]
    text=''.join(p.text for p in chain.chain if isinstance(p,Comp.Plain)).strip()
    parts=[MessageChain(chain=[p]) for p in images]
    chunk=[];size=0;urls=0
    for line in text.splitlines():
        n=len(URL_RE.findall(line))
        if chunk and (size+len(line)>1100 or urls+n>2):
            parts.append(MessageChain().message('\n'.join(chunk)));chunk=[];size=0;urls=0
        chunk.append(line);size+=len(line)+1;urls+=n
    if chunk:parts.append(MessageChain().message('\n'.join(chunk)))
    if parts and mentions:parts[0].chain[:0]=mentions+[Comp.Plain(' ')]
    return parts

def error_status(exc):
    # A timeout can leave a local outgoing record even when the recipient gets nothing.
    text=str(exc)
    return 'unknown' if isinstance(exc,(TimeoutError,ConnectionError)) or 'timeout' in text.lower() or isinstance(exc,ActionFailed) and getattr(exc,'retcode',None)==1200 else 'failed'

class DeliveryStore:
    def __init__(self,store):
        self.store=store
        with store.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS replay_delivery(owner TEXT,part INTEGER,status TEXT,error TEXT,updated REAL,PRIMARY KEY(owner,part))')
            db.execute('CREATE TABLE IF NOT EXISTS replay_delivery_attempts(owner TEXT,part INTEGER,attempts INTEGER,started REAL,PRIMARY KEY(owner,part))')
            db.execute("UPDATE replay_delivery SET status='unknown',error='发送过程中服务重启，回执未确认' WHERE status='sending'")
            db.execute('UPDATE jobs SET notified=0 WHERE notified=-1')
            db.execute('UPDATE replay_batches SET notified=0 WHERE notified=-1')

    def states(self,owner):
        with self.store.db() as db:return [dict(r) for r in db.execute('SELECT * FROM replay_delivery WHERE owner=? ORDER BY part',(owner,))]

    def claim(self,owner,part,limit=1):
        with self.store.db() as db:
            db.execute("INSERT OR IGNORE INTO replay_delivery VALUES(?,?,'pending','',?)",(owner,part,time.time()))
            row=db.execute('SELECT status,updated FROM replay_delivery WHERE owner=? AND part=?',(owner,part)).fetchone()
            if row['status'] in CONFIRMED or row['status']=='sending':return False
            db.execute('INSERT OR IGNORE INTO replay_delivery_attempts VALUES(?,?,0,?)',(owner,part,row['updated']))
            count=db.execute('SELECT attempts FROM replay_delivery_attempts WHERE owner=? AND part=?',(owner,part)).fetchone()[0]
            if count>=limit:return False
            db.execute('UPDATE replay_delivery_attempts SET attempts=attempts+1 WHERE owner=? AND part=?',(owner,part))
            db.execute("UPDATE replay_delivery SET status='sending',updated=? WHERE owner=? AND part=?",(time.time(),owner,part))
            return True

    def attempt(self,owner,part):
        with self.store.db() as db:
            row=db.execute('SELECT attempts,started FROM replay_delivery_attempts WHERE owner=? AND part=?',(owner,part)).fetchone()
            return dict(row) if row else {'attempts':0,'started':time.time()}

    def finish(self,owner,part,status,error=''):
        with self.store.db() as db:db.execute('UPDATE replay_delivery SET status=?,error=?,updated=? WHERE owner=? AND part=?',(status,error[:350],time.time(),owner,part))

    async def deliver(self,owner,parts,send,fallback=None,verify=None,text_retries=0):
        for index,chain in enumerate(parts):
            rows=await asyncio.to_thread(self.states,owner)
            current=next((r for r in rows if r['part']==index),None)
            if current and current['status'] in CONFIRMED:continue
            retries=min(2,max(0,text_retries))
            error=current['error'] if current else ''
            for attempt in range(retries+1):
                state=await asyncio.to_thread(self.attempt,owner,index)
                if state['attempts'] and verify:
                    try:evidence=await verify(chain,state['started'])
                    except Exception:evidence=None
                    if evidence:
                        await asyncio.to_thread(self.finish,owner,index,'history_acknowledged','历史确认 '+json.dumps(evidence,ensure_ascii=False));break
                if not await asyncio.to_thread(self.claim,owner,index,retries+1):break
                state=await asyncio.to_thread(self.attempt,owner,index)
                try:await send(chain)
                except Exception as exc:
                    error=type(exc).__name__+': '+str(exc)[:300]
                    await asyncio.to_thread(self.finish,owner,index,error_status(exc),error)
                    evidence=None
                    if verify:
                        try:evidence=await verify(chain,state['started'])
                        except Exception:pass  # An unavailable history API is not evidence of delivery.
                    if evidence:
                        await asyncio.to_thread(self.finish,owner,index,'history_acknowledged',
                            '原接口超时；历史确认 '+json.dumps(evidence,ensure_ascii=False))
                        break
                    if state['attempts']<=retries:
                        await asyncio.sleep(3*state['attempts']);continue
                else:await asyncio.to_thread(self.finish,owner,index,'acknowledged')
                break
            rows=await asyncio.to_thread(self.states,owner)
            if not any(r['part']==index and r['status'] in CONFIRMED for r in rows):
                alternate=fallback(chain) if fallback else []
                if alternate and await self.deliver(f'{owner}:fallback:{index}',alternate,send,verify=verify,text_retries=text_retries):
                    await asyncio.to_thread(self.finish,owner,index,'fallback_acknowledged',error)
            if index+1<len(parts):await asyncio.sleep(.6)
        rows=await asyncio.to_thread(self.states,owner)
        return all(r['status'] in CONFIRMED for r in rows) and len(rows)>=len(parts)

    def description(self,owner):
        with self.store.db() as db:
            latest=db.execute('SELECT owner FROM replay_delivery WHERE owner=? OR owner LIKE ? ORDER BY updated DESC LIMIT 1',(owner,'query:'+owner+':%')).fetchone()
        rows=self.states(latest['owner']) if latest else []
        if not rows:return ''
        if all(r['status'] in CONFIRMED for r in rows):
            if any(r['status']=='fallback_acknowledged' for r in rows):return '历史备用发送已确认；若未显示，可 /复盘 重发。'
            if any(r['status']=='history_acknowledged' for r in rows):return '结果发送：已通过QQ服务器历史确认；若客户端未显示，可 /复盘 重发。'
            return '结果发送：平台已确认接收；若客户端未显示，可 /复盘 重发。'
        return '结果已保存，部分发送失败或回执未确认；可 /复盘 重发，无需重新分析。'
