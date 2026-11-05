"""Atomic multi-replay jobs, weighted summaries, and chronological display data."""
import json,re,secrets,time
from pathlib import Path
from .replay_core import ReplayError,MODEL,parse_request,URL_RE,summarize

LIMIT=10;CAPACITY=30

def parse_sources(text):
    lines=text.strip().splitlines();sources=[]
    if lines:lines[0]=re.sub(r'^批量\s*','',lines[0]).strip()
    for line in lines:
        if not line.strip():continue
        if len(URL_RE.findall(line))!=1:raise ReplayError('批量请每行放一条完整链接，并在该行末尾填写开局席位。')
        request=parse_request(line);source=request['source']
        if not source:raise ReplayError('批量中有一行没有有效链接。')
        if source['kind']=='replay' and source['seat'] is None:raise ReplayError(f'第{len(sources)+1}条牌谱缺少席位，例如“链接 开局西家”。')
        sources.append(source)
    if not sources:raise ReplayError('用法：/复盘 批量，然后每行填写“牌谱链接 开局西家”。')
    if len(sources)>LIMIT:raise ReplayError(f'每批最多{LIMIT}条牌谱，请分批提交。')
    return sources

class ReplayBatches:
    def __init__(self,store):
        self.store=store
        with store.db() as db:
            if 'batch_id' not in {r['name'] for r in db.execute('PRAGMA table_info(jobs)')}:
                db.execute("ALTER TABLE jobs ADD COLUMN batch_id TEXT NOT NULL DEFAULT ''")
            db.executescript('''CREATE TABLE IF NOT EXISTS replay_batches(id TEXT PRIMARY KEY,scope TEXT,actor TEXT,delivery TEXT,created REAL,notified INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS replay_batches_owner ON replay_batches(scope,actor,created);
            CREATE TABLE IF NOT EXISTS replay_batch_items(batch_id TEXT,position INTEGER,job_id TEXT,PRIMARY KEY(batch_id,position));''')
            columns={r['name'] for r in db.execute('PRAGMA table_info(replay_batches)')}
            for name in ('label','ordered_by'):
                if name not in columns:db.execute(f"ALTER TABLE replay_batches ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
            if 'metadata' not in {r['name'] for r in db.execute('PRAGMA table_info(replay_batch_items)')}:
                db.execute("ALTER TABLE replay_batch_items ADD COLUMN metadata TEXT NOT NULL DEFAULT ''")

    def create(self,scope,actor,sources,delivery,label='',ordered_by='provided',lookup=None,lookup_result=None):
        unique={self.store.cache_key(source):source for source in sources};sources=list(unique.values())
        if not 1<=len(sources)<=LIMIT:raise ReplayError(f'每批支持1～{LIMIT}份不同的牌谱。')
        now=time.time();batch_id='B'+secrets.token_hex(6)
        with self.store.db() as db:
            lookup_id=lookup['id'] if lookup else ''
            if lookup:
                current=db.execute('SELECT * FROM jobs WHERE id=?',(lookup_id,)).fetchone()
                if not current or current['status']!='running' or current['lease_token']!=lookup['lease_token'] or current['worker_id']!=lookup['worker_id']:raise ReplayError('获取列表任务已取消或重新分配，未提交分析。')
            if db.execute("SELECT 1 FROM jobs WHERE scope=? AND actor=? AND status IN ('queued','running') AND id!=?",(scope,actor,lookup_id)).fetchone():raise ReplayError('你已有正在进行的复盘任务，请先完成或取消后再提交批量。')
            prepared=[]
            for source in sources:
                key=self.store.cache_key(source);cached=db.execute('SELECT * FROM cache WHERE cache_key=?',(key,)).fetchone()
                cached=cached if cached and Path(cached['report_path']).is_file() else None
                summary=summarize(json.loads(Path(cached['report_path']).read_text(encoding='utf-8')),source.get('seat'),MODEL) if cached else None
                prepared.append((source,key,cached,summary))
            pending=sum(cached is None for _,_,cached,_ in prepared)
            if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running') AND id!=?",(lookup_id,)).fetchone()[0]+pending>CAPACITY:raise ReplayError('复盘队列已满，请稍后提交此批次。')
            db.execute('INSERT INTO replay_batches(id,scope,actor,delivery,created,notified,label,ordered_by) VALUES(?,?,?,?,?,?,?,?)',(batch_id,scope,actor,delivery,now,1 if not pending and not lookup else 0,label[:100],ordered_by))
            for index,(source,key,cached,summary) in enumerate(prepared):
                job_id=secrets.token_hex(6)
                db.execute('INSERT INTO jobs(id,scope,actor,cache_key,status,url,seat,model,remote_task,report_path,created,updated,notified,delivery,batch_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(job_id,scope,actor,key,'done' if cached else 'queued',source['url'],summary['seat'] if summary else source.get('seat'),MODEL,(cached['remote_task'] if cached else '') or source.get('remote_task',''),cached['report_path'] if cached else '',now+index*.000001,now,1,delivery,batch_id))
                db.execute('INSERT INTO replay_batch_items(batch_id,position,job_id,metadata) VALUES(?,?,?,?)',(batch_id,index+1,job_id,json.dumps(source.get('record',{}))))
            if lookup:
                result={**(lookup_result or {}),'batch_id':batch_id,'count':len(sources)}
                db.execute("UPDATE jobs SET status='done',phase='',lease_until=0,error='',result_json=?,updated=? WHERE id=?",(json.dumps(result,ensure_ascii=False),now,lookup_id))
        return self.get(scope,actor,batch_id)

    def get(self,scope,actor,batch_id=''):
        with self.store.db() as db:
            row=db.execute('SELECT * FROM replay_batches WHERE scope=? AND actor=?'+(' AND id=?' if batch_id else '')+' ORDER BY created DESC LIMIT 1',(scope,actor,batch_id) if batch_id else (scope,actor)).fetchone()
            if not row:raise ReplayError('没有找到你在当前会话的批量复盘。')
            result=dict(row);result['items']=[dict(r) for r in db.execute('SELECT j.*,i.position,i.metadata FROM replay_batch_items i JOIN jobs j ON j.id=i.job_id WHERE i.batch_id=? ORDER BY i.position',(row['id'],))]
        result['pending']=sum(j['status'] in ('queued','running') for j in result['items']);result['done']=sum(j['status']=='done' for j in result['items']);result['failed']=len(result['items'])-result['pending']-result['done']
        return result

    def latest_is_batch(self,scope,actor):
        with self.store.db() as db:
            batch=db.execute('SELECT created FROM replay_batches WHERE scope=? AND actor=? ORDER BY created DESC LIMIT 1',(scope,actor)).fetchone()
            job=db.execute("SELECT created FROM jobs WHERE scope=? AND actor=? AND batch_id='' ORDER BY created DESC LIMIT 1",(scope,actor)).fetchone()
        return bool(batch and (not job or batch['created']>=job['created']))

    def ready(self):
        with self.store.db() as db:rows=[dict(r) for r in db.execute('SELECT * FROM replay_batches WHERE notified=0')]
        results=[]
        for row in rows:
            result=self.get(row['scope'],row['actor'],row['id'])
            if result['pending']:continue
            with self.store.db() as db:
                changed=db.execute('UPDATE replay_batches SET notified=1 WHERE id=? AND notified=0',(row['id'],)).rowcount
            if changed:results.append(result)
        return results

    def cancel(self,scope,actor,batch_id=''):
        batch=self.get(scope,actor,batch_id)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='cancelled',notified=1,updated=? WHERE batch_id=? AND status IN ('queued','running')",(time.time(),batch['id']))
            db.execute('UPDATE replay_batches SET notified=1 WHERE id=?',(batch['id'],))
        return batch['id']

    def summary(self,batch):
        games=[];total=0;matches=0
        for job in batch['items']:
            entry={'position':job['position'],'status':job['status'],'job_id':job['id'],'url':job['url'],'remote_task':job['remote_task'],'error':job['error'],'seat':job['seat']}
            entry.update(json.loads(job.get('metadata') or '{}'))
            if job['status']=='done':
                data=summarize(json.loads(Path(job['report_path']).read_text(encoding='utf-8')),job['seat'],MODEL)
                entry.update({k:data[k] for k in ('accordance','matches','total','rating','nickname')});total+=data['total'];matches+=data['matches']
            games.append(entry)
        return {'id':batch['id'],'label':batch.get('label',''),'ordered_by':batch.get('ordered_by','provided'),'games':games,'pending':batch['pending'],'done':batch['done'],'failed':batch['failed'],'matches':matches,'total':total,'accordance':matches/total if total else None,'model':MODEL}
