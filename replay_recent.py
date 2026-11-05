"""Validated desktop recent-record requests; no protected API calls on the server."""
import json,math,re,secrets,time
from .replay_core import MODEL,ReplayError,parse_url

def recent_request(query,binding=''):
    count=5;modes=[9,12,16];name=[]
    for token in query.split():
        if token in ('金','玉','王','王座'):modes=[{'金':9,'玉':12,'王':16,'王座':16}[token]]
        elif token.isdigit() and 1<=int(token)<=10:count=int(token)
        else:name.append(token)
    player=' '.join(name) or str(binding or '')
    if not player:raise ReplayError('请提供玩家UID/完整昵称，或先 /雀 绑 UID。')
    if len(player)>100:raise ReplayError('玩家昵称或UID过长。')
    return {'kind':'recent','player':player,'count':count,'modes':modes}

def create_recent(store,scope,actor,request,delivery):
    now=time.time();encoded=json.dumps(request,ensure_ascii=False,sort_keys=True)
    with store.db() as db:
        active=db.execute("SELECT * FROM jobs WHERE scope=? AND actor=? AND status IN ('queued','running')",(scope,actor)).fetchone()
        if active:
            if active['kind']=='recent' and active['request_json']==encoded:return dict(active),False
            raise ReplayError('你在本会话已有复盘任务，请先查看状态或取消。')
        if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]>=30:raise ReplayError('复盘队列已满，请稍后再试。')
        job_id=secrets.token_hex(6)
        db.execute('INSERT INTO jobs(id,scope,actor,cache_key,status,url,seat,model,created,updated,delivery,kind,request_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(job_id,scope,actor,'recent:'+job_id,'queued','',None,MODEL,now,now,delivery,'recent',encoded))
        return dict(db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()),True

def recent_sources(request,result):
    """Treat browser data as untrusted. Select newest N before chronological ordering."""
    if not isinstance(result,dict):raise ReplayError('工作端没有返回有效的最近对局列表。')
    uid=str(result.get('uid',''))
    if not re.fullmatch(r'[1-9][0-9]{0,14}',uid):raise ReplayError('工作端返回的玩家UID无效。')
    if request['player'].isdigit() and uid!=request['player']:raise ReplayError('返回的玩家UID与请求不一致。')
    records=result.get('records')
    if not isinstance(records,list) or len(records)>450:raise ReplayError('工作端返回的对局列表格式或长度无效。')
    unique={}
    for row in records:
        if not isinstance(row,dict):raise ReplayError('对局记录格式无效。')
        if row.get('modeId') not in request['modes']:continue
        players=row.get('players')
        if not isinstance(players,list) or len(players)!=4:raise ReplayError('最近对局包含非四麻记录，未自动分析。')
        seats=[i for i,p in enumerate(players) if isinstance(p,dict) and str(p.get('accountId'))==uid]
        if len(seats)!=1:raise ReplayError('对局没有明确且唯一的目标玩家席位，未自动选择。')
        if any(not isinstance(p,dict) or type(p.get('score')) not in (int,float) or not math.isfinite(p['score']) for p in players):raise ReplayError('对局结算点数不完整。')
        started=row.get('startTime')
        if type(started) not in (int,float) or not math.isfinite(started) or not 0<started<1e11:raise ReplayError('对局时间无效。')
        uuid=str(row.get('uuid',''))
        if not re.fullmatch(r'[A-Za-z0-9-]{16,100}',uuid):raise ReplayError('对局牌谱编号无效。')
        source=parse_url('https://game.maj-soul.com/1/?paipu='+uuid+'_a'+uid);source['seat']=seats[0]
        score=players[seats[0]]['score'];rank=1+sum(p['score']>score or (p['score']==score and i<seats[0]) for i,p in enumerate(players))
        source['record']={'start_time':int(started),'mode':row['modeId'],'rank':rank,'score':score}
        unique[uuid]=source
    sources=sorted(unique.values(),key=lambda s:(s['record']['start_time'],s['replay_id']),reverse=True)[:request['count']]
    if not sources:raise ReplayError('网页没有取得可分析的四麻东南对局；仅收录金之间及以上。')
    return list(reversed(sources))
