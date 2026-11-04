"""Validated single-log requests, factual report summaries, and persistent jobs."""
from __future__ import annotations
import hashlib,json,math,re,secrets,sqlite3,time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,urlencode

MODEL='4.1b'
FORM_URL='https://mjai.ekyu.moe/zh-cn.html'
HOSTS={'game.maj-soul.com','game.maj-soul.net','www.maj-soul.net','www.maj-soul.com','mahjongsoul.game.yo-star.com','game.mahjongsoul.com','mahjongsoul.com','www.mahjongsoul.com','tenhou.net'}
SEATS={'东':0,'东家':0,'东起':0,'起家东家':0,'南':1,'南家':1,'南起':1,'西':2,'西家':2,'西起':2,'北':3,'北家':3,'北起':3}
SEAT_NAMES=('东起','南起','西起','北起')
URL_RE=re.compile(r'https://[^\s<>"`]+',re.I)
TASK_RE=re.compile(r'^[a-zA-Z0-9_-]{8,100}$')
HAND_RE=re.compile(r'^([东南西北ESWN])\s*([一二三四1-4])(?:[局]?(?:[.．]|[\s]*)(\d+)本?场?)?$',re.I)

class ReplayError(ValueError):pass

def parse_url(value):
    text=str(value).strip().rstrip('，。；;)]}）')
    p=urlsplit(text)
    if p.scheme!='https' or p.username or p.password or p.port not in (None,443):raise ReplayError('请使用完整的 https 雀魂牌谱链接或 Mortal 报告链接。')
    q=parse_qs(p.query)
    if p.hostname=='mjai.ekyu.moe':
        task=(q.get('task') or [''])[0]
        if not task:
            data=(q.get('data') or [''])[0]
            match=re.fullmatch(r'(?:https://mjai\.ekyu\.moe)?/report/([a-zA-Z0-9_-]{8,100})\.json',data)
            task=match[1] if match else ''
        if not task:
            match=re.fullmatch(r'/report/([a-zA-Z0-9_-]{8,100})\.(?:json|html)',p.path)
            task=match[1] if match else ''
        if not TASK_RE.fullmatch(task):raise ReplayError('这不是有效的 Mortal 任务报告链接。')
        return {'kind':'report','url':f'https://mjai.ekyu.moe/report/{task}.json','replay_id':'report:'+task,'remote_task':task,'seat':None}
    if p.hostname not in HOSTS:raise ReplayError('目前接受雀魂、天凤牌谱或 mjai.ekyu.moe 报告链接。')
    key='log' if p.hostname=='tenhou.net' else 'paipu';ids=q.get(key) or []
    if len(ids)!=1 or not re.fullmatch(r'[A-Za-z0-9_-]{12,220}',ids[0]):raise ReplayError('链接缺少有效的牌谱编号，请复制游戏内完整牌谱链接。')
    target=(q.get('tw') or [''])[0];seat=int(target) if key=='log' and target in ('0','1','2','3') else None
    canonical=('https://tenhou.net/0/?' if key=='log' else f'https://{p.hostname}/1/?')+urlencode({key:ids[0]})
    return {'kind':'replay','url':canonical,'replay_id':('tenhou:' if key=='log' else 'majsoul:')+ids[0],'seat':seat}

def parse_request(text):
    text=str(text).strip();match=URL_RE.search(text);source=parse_url(match[0]) if match else None
    rest=(text[:match.start()]+' '+text[match.end():]).strip() if match else text
    seat=None;hand=None;job='';action='submit';unknown=[]
    actions={'状态':'status','进度':'status','取消':'cancel','记录':'history','历史':'history','帮助':'help','帮':'help','菜单':'help','分歧':'differences','分局':'hand','详情':'result','结果':'result','讲评':'comment','点评':'comment','导入':'import','重试':'retry'}
    for token in rest.split():
        if token in ('复盘','分析','牌谱'):continue
        if token in actions:action=actions[token];continue
        token=token.removeprefix('开局')
        if token in SEATS:
            if seat is not None and seat!=SEATS[token]:raise ReplayError('一次只分析一个席位，请保留一个开局席位参数。')
            seat=SEATS[token];continue
        if token in ('三麻','3麻','三','东风','东风战'):raise ReplayError('当前 Mortal 复盘只支持四麻东南战，暂不支持三麻或东风战。')
        hm=HAND_RE.fullmatch(token)
        if hm:
            wind='东南西北'.find(hm[1]) if hm[1] in '东南西北' else 'ESWN'.index(hm[1].upper())
            num=int(hm[2]) if hm[2].isdigit() else '一二三四'.index(hm[2])+1
            hand=wind*4+num-1;continue
        if re.fullmatch(r'#?[0-9a-f]{12}',token,re.I):job=token.lstrip('#').lower();continue
        unknown.append(token)
    if unknown:raise ReplayError('未识别的参数：'+ ' '.join(unknown)[:100]+'。分析对象请用开局东家/南家/西家/北家，不要填写当前轮庄家。')
    if source:
        source['seat']=seat if seat is not None else source['seat']
        action='import' if source['kind']=='report' else action
    return {'action':action,'source':source,'seat':seat,'hand':hand,'job':job}

def action_text(action):
    if not isinstance(action,dict):return '未提供'
    kind=action.get('type','');pai=action.get('pai','')
    labels={'dahai':'打','tsumo':'摸','reach':'立直','chi':'吃','pon':'碰','daiminkan':'大明杠','ankan':'暗杠','kakan':'加杠','hora':'和牌','ryukyoku':'流局','none':'跳过','pass':'跳过'}
    def tile(value):
        value=str(value);winds={'E':'东','S':'南','W':'西','N':'北','P':'白','F':'发','C':'中'}
        if value in winds:return winds[value]
        match=re.fullmatch(r'([1-9])([mps])(r?)',value)
        return ('赤' if match[3] else '')+match[1]+{'m':'万','p':'筒','s':'索'}[match[2]] if match else value
    consumed=action.get('consumed') or []
    detail='（'+','.join(tile(p) for p in consumed)+'）' if isinstance(consumed,list) and consumed else ''
    return labels.get(kind,kind or '未提供')+(f' {tile(pai)}' if pai else '')+detail

def finite(value):return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)

def summarize(report,expected_seat=None,expected_model=MODEL):
    if not isinstance(report,dict) or not isinstance(report.get('review'),dict):raise ReplayError('返回内容不是有效的 Mortal JSON 报告。')
    review=report['review'];seat=report.get('player_id',expected_seat)
    if type(seat) is not int or not 0<=seat<=3:raise ReplayError('报告没有提供有效的分析席位。')
    if expected_seat is not None and expected_seat!=seat:raise ReplayError('报告席位与请求不一致，未使用这份结果。')
    model=str(review.get('model_tag') or report.get('model_tag') or '')
    if not model:raise ReplayError('报告缺少模型版本，暂不混用未知来源结果。')
    if expected_model and model!=expected_model:raise ReplayError(f'报告模型为{model}，本任务要求{expected_model}，请确认没有导入别的报告。')
    total=review.get('total_reviewed');matches=review.get('total_matches')
    if type(total) is not int or type(matches) is not int or total<=0 or not 0<=matches<=total:raise ReplayError('报告的评估决策数或一致数无效，不能计算一致率。')
    kyokus=review.get('kyokus')
    if not isinstance(kyokus,list) or len(kyokus)>100:raise ReplayError('报告分局内容无效。')
    hands=[];diffs=[];num_entries=0
    for hand in kyokus:
        if not isinstance(hand,dict):raise ReplayError('报告分局格式不正确。')
        k=hand.get('kyoku');honba=hand.get('honba',0);entries=hand.get('entries')
        if type(k) is not int or not 0<=k<=15 or type(honba) is not int or not isinstance(entries,list):raise ReplayError('报告局号或逐巡内容不正确。')
        htotal=0;hmatches=0
        for e in entries:
            if not isinstance(e,dict) or type(e.get('is_equal')) is not bool:raise ReplayError('报告逐巡内容缺少是否一致标记。')
            htotal+=1;hmatches+=int(e['is_equal']);num_entries+=1
            if num_entries>20000:raise ReplayError('报告内容超过单份牌谱大小限制。')
            if not e['is_equal']:
                differences={'kyoku':k,'honba':honba,'turn':e.get('junme'),'actual':action_text(e.get('actual')),'expected':action_text(e.get('expected')),'state':e.get('state') if isinstance(e.get('state'),dict) else {},'shanten':e.get('shanten')}
                details=e.get('details') or [];idx=e.get('actual_index');rank=idx+1 if type(idx) is int and 0<=idx<len(details) else None
                differences['choice_rank']=rank;differences['choices']=len(details)
                # This only orders review points; no point-loss or "mistake" claim.
                differences['priority']=rank or 0;diffs.append(differences)
        hands.append({'kyoku':k,'honba':honba,'total':htotal,'matches':hmatches,'diffs':htotal-hmatches})
    if num_entries!=total or sum(h['matches'] for h in hands)!=matches:raise ReplayError('逐巡明细与总计不一致，暂不输出可能错误的分析。')
    if str(report.get('game_length','')).lower() in ('tonpu','tonpuusen','east','tonpuu'):raise ReplayError('这份报告是东风战，当前版本仅接入四麻东南战。')
    names=[]
    for event in report.get('mjai_log',[])[:10]:
        if isinstance(event,dict) and event.get('type')=='start_game' and isinstance(event.get('names'),list):
            names=event['names'];break
    for source in (report.get('log'),report.get('game'),report.get('raw_log'),report.get('log_info')):
        if isinstance(source,dict):
            value=source.get('names',source.get('name'))
            if isinstance(value,list) and not names:names=value;break
    if names and len(names)!=4:raise ReplayError('这份报告不是四麻牌谱。')
    anonymous=names==['Aさん','Bさん','Cさん','Dさん']
    nickname=('开局'+'东南西北'[seat]+'家') if anonymous else str(names[seat])[:60] if names else SEAT_NAMES[seat]
    rating=review.get('rating');rating=round(rating*100,2) if finite(rating) and 0<=rating<=1 else None
    return {'seat':seat,'nickname':nickname,'anonymous':anonymous,'names':[str(n)[:60] for n in names],'model':model,'matches':matches,'total':total,'accordance':matches/total,'rating':rating,'hands':hands,'differences':sorted(diffs,key=lambda d:d['priority'],reverse=True),'difference_count':total-matches}

def hand_name(k,honba=0):return '东南西北'[k//4]+str(k%4+1)+(' · '+str(honba)+'本场' if honba else '')

class ReplayStore:
    def __init__(self,folder):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True);self.path=self.folder/'replay.db'
        with self.db() as db:db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,scope TEXT,actor TEXT,cache_key TEXT,status TEXT,url TEXT,seat INTEGER,model TEXT,remote_task TEXT DEFAULT '',report_path TEXT DEFAULT '',error TEXT DEFAULT '',created REAL,updated REAL,notified INTEGER DEFAULT 0);
        CREATE INDEX IF NOT EXISTS job_owner ON jobs(scope,actor,created);
        CREATE TABLE IF NOT EXISTS cache(cache_key TEXT PRIMARY KEY,report_path TEXT,remote_task TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS pending(scope TEXT,actor TEXT,payload TEXT,expires REAL,PRIMARY KEY(scope,actor));''')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()
    def save_pending(self,scope,actor,source):
        with self.db() as db:db.execute('INSERT OR REPLACE INTO pending VALUES(?,?,?,?)',(scope,actor,json.dumps(source),time.time()+300))
    def get_pending(self,scope,actor):
        with self.db() as db:r=db.execute('SELECT * FROM pending WHERE scope=? AND actor=? AND expires>?',(scope,actor,time.time())).fetchone()
        return json.loads(r['payload']) if r else None
    def create(self,scope,actor,source):
        if source['kind']=='replay' and source.get('seat') not in range(4):raise ReplayError('请指定开局东家、南家、西家或北家。')
        key=hashlib.sha256(json.dumps([source['replay_id'],source.get('seat'),MODEL,'all',0.1,'v1']).encode()).hexdigest()
        with self.db() as db:
            same=db.execute("SELECT * FROM jobs WHERE scope=? AND actor=? AND cache_key=? AND status IN ('queued','running','done','manual') ORDER BY created DESC LIMIT 1",(scope,actor,key)).fetchone()
            if same:return dict(same),False
            if db.execute("SELECT 1 FROM jobs WHERE scope=? AND actor=? AND status IN ('queued','running')",(scope,actor)).fetchone():raise ReplayError('你在本会话已有一个复盘任务，请先查看状态或取消。')
            if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]>=6:raise ReplayError('复盘队列已满，请稍后再试。')
            cached=db.execute('SELECT * FROM cache WHERE cache_key=?',(key,)).fetchone();cached=cached if cached and Path(cached['report_path']).is_file() else None
            now=time.time();job_id=secrets.token_hex(6);status='done' if cached else 'queued';report_path=cached['report_path'] if cached else ''
            db.execute('INSERT INTO jobs(id,scope,actor,cache_key,status,url,seat,model,remote_task,report_path,created,updated,notified) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(job_id,scope,actor,key,status,source['url'],source.get('seat'),MODEL,source.get('remote_task',''),report_path,now,now,1 if cached else 0))
            return dict(db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()),True
    def get(self,scope,actor,job_id=''):
        with self.db() as db:
            r=db.execute('SELECT * FROM jobs WHERE scope=? AND actor=?'+(' AND id=?' if job_id else '')+' ORDER BY created DESC LIMIT 1',(scope,actor,job_id) if job_id else (scope,actor)).fetchone()
        if not r:raise ReplayError('没有找到你在当前会话的复盘任务。')
        return dict(r)
    def history(self,scope,actor):
        with self.db() as db:return [dict(r) for r in db.execute('SELECT * FROM jobs WHERE scope=? AND actor=? ORDER BY created DESC LIMIT 5',(scope,actor))]
    def update(self,job_id,**fields):
        if not set(fields)<= {'status','remote_task','report_path','error','notified'}:raise ValueError('Invalid replay fields')
        with self.db() as db:db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+',updated=? WHERE id=?',(*fields.values(),time.time(),job_id))
    def next(self):
        with self.db() as db:
            r=db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if r:db.execute("UPDATE jobs SET status='running',updated=? WHERE id=?",(time.time(),r['id']))
        return dict(r) if r else None
    def recover(self):
        with self.db() as db:db.execute("UPDATE jobs SET status='queued' WHERE status='running'")
    def complete(self,job,report):
        summary=summarize(report,job['seat'],job['model'] if not job['url'].startswith('https://mjai.ekyu.moe/') else '')
        path=self.folder/'reports'/(job['id']+'.json');path.parent.mkdir(exist_ok=True);temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8');temporary.replace(path)
        with self.db() as db:
            status=db.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]
            if status=='cancelled':return None
            db.execute("UPDATE jobs SET status='done',report_path=?,seat=?,model=?,error='',updated=? WHERE id=?",(str(path),summary['seat'],summary['model'],time.time(),job['id']))
            db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?,?)',(job['cache_key'],str(path),job.get('remote_task',''),time.time()))
        return summary
