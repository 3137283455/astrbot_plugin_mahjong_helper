"""Loopback-only, authenticated queue gateway for a desktop browser worker."""
from __future__ import annotations
import hmac,json,secrets,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from .replay_core import ReplayError,MODEL,TASK_RE,summarize
from .replay_recent import recent_sources
from .replay_batch import ReplayBatches

LEASE_SECONDS=90
PIPELINE_FETCHING_PHASES=('fetching','uploading')

class LeaseLost(ReplayError):pass

class PCQueue:
    def __init__(self,store,worker_id,config=None):
        config=config or {}
        self.pipeline=bool(config.get('pipeline',False))
        self.max_fetching=max(1,int(config.get('pipeline_max_fetching',4)))
        self.fetching_lease=max(LEASE_SECONDS,int(config.get('pipeline_fetching_lease',300)))
        self.store=store;self.worker_id=worker_id;self.batches=ReplayBatches(store)
        with store.db() as db:
            columns={r['name'] for r in db.execute('PRAGMA table_info(jobs)')}
            for name,definition in {'lease_token':"TEXT NOT NULL DEFAULT ''",'lease_until':'REAL NOT NULL DEFAULT 0','worker_id':"TEXT NOT NULL DEFAULT ''",'phase':"TEXT NOT NULL DEFAULT ''",'attention_notified':'INTEGER NOT NULL DEFAULT 0'}.items():
                if name not in columns:db.execute(f'ALTER TABLE jobs ADD COLUMN {name} {definition}')
            db.execute('CREATE TABLE IF NOT EXISTS pc_workers(id TEXT PRIMARY KEY,last_seen REAL NOT NULL,phase TEXT NOT NULL)')
            # A server worker interrupted by migration has no desktop lease.
            db.execute("UPDATE jobs SET status='queued' WHERE status='running' AND lease_token=''")

    def _seen(self,db,phase='online'):
        db.execute('INSERT OR REPLACE INTO pc_workers VALUES(?,?,?)',(self.worker_id,time.time(),phase))

    def status(self):
        with self.store.db() as db:r=db.execute('SELECT * FROM pc_workers WHERE id=?',(self.worker_id,)).fetchone()
        return {'online':bool(r and time.time()-r['last_seen']<45),'phase':r['phase'] if r else 'offline'}

    @staticmethod
    def public(job):
        result={k:job[k] for k in ('id','url','seat','model','remote_task','lease_token','phase','status','kind')}
        if job['kind']=='recent':result['request']=json.loads(job['request_json'])
        return result

    def _owned(self,db,payload):
        r=db.execute('SELECT * FROM jobs WHERE id=?',(str(payload.get('id','')),)).fetchone()
        if not r or r['worker_id']!=self.worker_id or not hmac.compare_digest(str(r['lease_token']),str(payload.get('lease_token',''))):raise LeaseLost('任务租约已失效，请重新领取。')
        return dict(r)

    def call(self,action,payload):
        if action=='result':return self.result(payload)
        now=time.time()
        with self.store.db() as db:
            self._seen(db,str(payload.get('phase','online'))[:40])
            if action=='claim':
                db.execute("UPDATE jobs SET status='queued' WHERE status='running' AND lease_until<?",(now,))
                if self.pipeline:
                    active=db.execute("SELECT 1 FROM jobs WHERE status='running' AND phase NOT IN ('fetching','uploading') LIMIT 1").fetchone()
                    if active:return {'job':None}
                    slots=db.execute("SELECT COUNT(*) FROM jobs WHERE status='running' AND phase IN ('fetching','uploading')").fetchone()[0]
                    if slots>=self.max_fetching:return {'job':None}
                else:
                    active=db.execute("SELECT 1 FROM jobs WHERE status='running' LIMIT 1").fetchone()
                    if active:return {'job':None}
                row=db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created,rowid LIMIT 1").fetchone()
                if not row:return {'job':None}
                db.execute("UPDATE jobs SET status='running',lease_token=?,lease_until=?,worker_id=?,phase='starting',attention_notified=0,updated=? WHERE id=?",(secrets.token_urlsafe(24),now+LEASE_SECONDS,self.worker_id,now,row['id']))
                return {'job':self.public(dict(db.execute('SELECT * FROM jobs WHERE id=?',(row['id'],)).fetchone()))}
            if action=='heartbeat' and not payload.get('id'):return {'online':True}
            job=self._owned(db,payload)
            if job['status'] in ('done','failed','manual','cancelled'):return {'status':job['status'],'keep':False}
            if action=='resume':
                if self.pipeline:
                    other=db.execute("SELECT 1 FROM jobs WHERE status='running' AND id!=? AND lease_until>? AND phase NOT IN ('fetching','uploading')",(job['id'],now)).fetchone()
                else:
                    other=db.execute("SELECT 1 FROM jobs WHERE status='running' AND id!=? AND lease_until>?",(job['id'],now)).fetchone()
                if other:raise LeaseLost('另一任务正在处理，旧租约不再续接。')
                if job['status'] not in ('queued','running'):raise LeaseLost('任务不可恢复。')
                db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
            elif job['status']!='running':raise LeaseLost('任务已重新排队，请恢复租约。')
            if action in ('heartbeat','resume','progress'):
                remote=str(payload.get('remote_task') or job['remote_task'])
                if remote and not TASK_RE.fullmatch(remote):raise ReplayError('无效的报告编号。')
                phase=str(payload.get('phase') or job['phase'])
                if phase not in ('starting','browser','verification','submitted','fetching','uploading','resolving','records'):phase='browser'
                lease=self.fetching_lease if (self.pipeline and phase in PIPELINE_FETCHING_PHASES) else LEASE_SECONDS
                db.execute('UPDATE jobs SET lease_until=?,phase=?,remote_task=?,updated=? WHERE id=?',(now+lease,phase,remote,now,job['id']))
                return {'keep':True,'status':'running','job':self.public(dict(db.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone()))}
            if action=='fail':
                db.execute("UPDATE jobs SET status='failed',error=?,lease_until=0,updated=? WHERE id=?",(str(payload.get('message','电脑工作端分析失败'))[:350],now,job['id']))
                return {'status':'failed'}
            raise ReplayError('未知的工作端操作。')

    def result(self,payload):
        # Validate outside the DB transaction; only the final ownership check writes.
        with self.store.db() as db:
            job=self._owned(db,payload)
            if job['status']=='done':return {'status':'done'}
            if job['status']!='running':raise LeaseLost('任务已结束或租约需要恢复。')
        if job['kind']=='recent':
            request=json.loads(job['request_json']);result=payload.get('records_result');sources=recent_sources(request,result)
            nickname=str(result.get('nickname') or result['uid'])[:100]
            batch=self.batches.create(job['scope'],job['actor'],sources,job['delivery'],'最近对局 · '+nickname,'time',lookup=job,lookup_result={'uid':str(result['uid']),'nickname':nickname,'requested':request['count']})
            return {'status':'done','batch_id':batch['id']}
        report=payload.get('report');summary=summarize(report,job['seat'],MODEL)
        path=self.store.folder/'reports'/f"pc-{job['id']}-{job['lease_token'][:12]}.json";path.parent.mkdir(exist_ok=True)
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8');temp.replace(path)
        with self.store.db() as db:
            current=self._owned(db,payload)
            if current['status']=='done':return {'status':'done'}
            if current['status']!='running':raise LeaseLost('任务已结束。')
            db.execute("UPDATE jobs SET status='done',report_path=?,seat=?,model=?,error='',phase='',lease_until=0,updated=? WHERE id=?",(str(path),summary['seat'],MODEL,time.time(),job['id']))
            db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?,?)',(job['cache_key'],str(path),current['remote_task'],time.time()))
        return {'status':'done'}

    def attention(self):
        with self.store.db() as db:
            rows=[dict(r) for r in db.execute("SELECT * FROM jobs WHERE status='running' AND phase='verification' AND attention_notified=0")]
            for r in rows:db.execute('UPDATE jobs SET attention_notified=1 WHERE id=?',(r['id'],))
        return rows

class PCGateway:
    def __init__(self,store,config_path):
        config=json.loads(Path(config_path).read_text(encoding='utf-8'))
        self.token=config['token'];assert len(self.token)>=32
        self.queue=PCQueue(store,config['worker_id'],config)
        queue=self.queue;token=self.token
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.connection.settimeout(15)
                try:
                    supplied=self.headers.get('Authorization','')
                    if not hmac.compare_digest(supplied,'Bearer '+token):self.reply(401,{'error':'工作端认证失败'});return
                    if self.headers.get('Transfer-Encoding'):raise ReplayError('不支持的请求格式')
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=21*1024*1024:raise ReplayError('请求大小不正确')
                    data=json.loads(self.rfile.read(size))
                    if not isinstance(data,dict):raise ReplayError('请求格式不正确')
                    action=self.path.removeprefix('/worker/') if self.path.startswith('/worker/') else ''
                    result=queue.call(action,data);self.reply(200,result)
                except LeaseLost as exc:self.reply(409,{'error':str(exc)})
                except (ReplayError,ValueError,TypeError) as exc:self.reply(400,{'error':str(exc)[:250]})
                except Exception:self.reply(500,{'error':'工作端网关处理失败，请查看服务器日志'})
            def reply(self,status,data):
                content=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(content)));self.end_headers()
                try:self.wfile.write(content)
                except OSError:pass
        self.server=ThreadingHTTPServer(('127.0.0.1',int(config.get('port',18769))),Handler)
        self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,name='mortal-pc-gateway',daemon=True)
    def start(self):self.thread.start()
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)
