"""Persist platform receipts independently from review results. Never infer recipient receipt."""
import asyncio,time
from aiocqhttp.exceptions import ActionFailed
from astrbot.api.event import MessageChain
import astrbot.api.message_components as Comp
from .replay_core import URL_RE

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
            db.execute("UPDATE replay_delivery SET status='unknown',error='发送过程中服务重启，回执未确认' WHERE status='sending'")
            db.execute('UPDATE jobs SET notified=0 WHERE notified=-1')
            db.execute('UPDATE replay_batches SET notified=0 WHERE notified=-1')

    def states(self,owner):
        with self.store.db() as db:return [dict(r) for r in db.execute('SELECT * FROM replay_delivery WHERE owner=? ORDER BY part',(owner,))]

    def claim(self,owner,part):
        with self.store.db() as db:
            db.execute("INSERT OR IGNORE INTO replay_delivery VALUES(?,?,'pending','',?)",(owner,part,time.time()))
            return bool(db.execute("UPDATE replay_delivery SET status='sending',updated=? WHERE owner=? AND part=? AND status='pending'",(time.time(),owner,part)).rowcount)

    def finish(self,owner,part,status,error=''):
        with self.store.db() as db:db.execute('UPDATE replay_delivery SET status=?,error=?,updated=? WHERE owner=? AND part=?',(status,error[:350],time.time(),owner,part))

    async def deliver(self,owner,parts,send):
        for index,chain in enumerate(parts):
            if not await asyncio.to_thread(self.claim,owner,index):continue
            try:await send(chain)
            except Exception as exc:await asyncio.to_thread(self.finish,owner,index,error_status(exc),type(exc).__name__+': '+str(exc)[:300])
            else:await asyncio.to_thread(self.finish,owner,index,'acknowledged')
            if index+1<len(parts):await asyncio.sleep(.6)
        rows=await asyncio.to_thread(self.states,owner)
        return all(r['status']=='acknowledged' for r in rows) and len(rows)>=len(parts)

    def description(self,owner):
        with self.store.db() as db:
            latest=db.execute('SELECT owner FROM replay_delivery WHERE owner=? OR owner LIKE ? ORDER BY updated DESC LIMIT 1',(owner,'query:'+owner+':%')).fetchone()
        rows=self.states(latest['owner']) if latest else []
        if not rows:return ''
        if all(r['status']=='acknowledged' for r in rows):return '结果发送：平台已确认接收；若客户端未显示，可 /复盘 重发。'
        return '结果已保存，部分发送失败或回执未确认；可 /复盘 重发，无需重新分析。'
