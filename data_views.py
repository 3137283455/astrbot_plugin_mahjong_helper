"""Bounded, read-only views. Never initialize plugin stores or replay archived seasons."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATASETS = {
    'matches': ('群对局', ['图号', '群号', '赛季', '模式', '结算时间', '玩家与持点', '状态']),
    'nicknames': ('昵称库', ['群号', '昵称', '字形样本', '状态']),
    'songs': ('音乐歌单', ['编号', '所属会话', '歌名', 'UP主', 'BV号']),
    'replays': ('复盘任务', ['任务号', '发起人', '状态', '开局席位', '创建时间', '结果通知']),
    'offers': ('优惠订阅', ['目标会话', '筛选条件']),
    'bilibili': ('B站订阅', ['目标会话', '订阅信息']),
}


@contextmanager
def connect(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('PRAGMA busy_timeout=2000')
    try:yield db
    finally:db.close()


def match_rows(db):
    # Do not SELECT archived snapshots, even for previews or deployment checks.
    return db.execute('''SELECT s.id,s.scope,s.stamp,s.hash,g.gid,p.name season,
        i.status,i.result,i.reason,o.status override_status,o.result override_result
        FROM sources s JOIN source_periods sp ON sp.source_id=s.id
        JOIN periods p ON p.id=sp.period_id AND p.state='current'
        JOIN groups g ON g.scope=s.scope LEFT JOIN images i ON i.hash=s.hash
        LEFT JOIN overrides o ON o.scope=s.scope AND o.hash=s.hash
        WHERE s.active=1 AND NOT EXISTS (SELECT 1 FROM source_exclusions e
          WHERE e.scope=s.scope AND e.msg=s.msg AND e.idx=s.idx)
        AND NOT EXISTS (SELECT 1 FROM sources old JOIN source_periods op ON op.source_id=old.id
          JOIN periods h ON h.id=op.period_id AND h.state='archived'
          WHERE old.scope=s.scope AND old.hash=s.hash AND s.hash<>'')
        ORDER BY s.stamp DESC,s.id DESC LIMIT 5000''').fetchall()


def records(root, kind):
    root = Path(root)
    if kind == 'matches':
        with connect(root/'astrbot_plugin_group_data/group_data.db') as db:
            aliases = {(r['scope'], r['old']): r['new'] for r in db.execute('SELECT * FROM aliases')}
            seen = set()
            out = []
            for row in match_rows(db):
                identity = (row['scope'], row['hash']) if row['hash'] else (row['id'],)
                if identity in seen:
                    continue
                seen.add(identity)
                result = json.loads(row['override_result'] or row['result'] or '{}')
                players = []
                for p in result.get('players', []):
                    name = p.get('name', '未知'); visited = set()
                    while (row['scope'], name) in aliases and name not in visited:
                        visited.add(name); name = aliases[row['scope'], name]
                    players.append(f"{name} {p.get('points', '—')}")
                status = row['override_status'] or row['status'] or '待下载'
                status = {'accepted':'已确认','review':'待复核','rejected':'已跳过','new':'待识别','error':'失败'}.get(status,status)
                out.append({'id':row['id'], 'cells':[row['id'],row['gid'],row['season'],str(result.get('mode','—'))+'麻',row['stamp'],' / '.join(players),status]})
            return out
    if kind == 'nicknames':
        with connect(root/'astrbot_plugin_group_data/group_data.db') as db:
            return [{'cells':[r['scope'].split(':')[-1],r['name'],r['samples'],'已录入']} for r in db.execute('SELECT scope,name,COUNT(*) samples FROM nickname_glyphs GROUP BY scope,name ORDER BY name LIMIT 5000')]
    if kind == 'songs':
        with connect(root/'astrbot_plugin_bili_player/playlist.sqlite3') as db:
            return [{'cells':[r['id'],r['owner'],r['title'],r['uploader'],r['bvid']]} for r in db.execute('SELECT id,owner,title,uploader,bvid FROM songs ORDER BY id DESC LIMIT 5000')]
    if kind == 'replays':
        with connect(root/'astrbot_plugin_mahjong_helper/replays/replay.db') as db:
            statuses={'done':'已完成','queued':'排队中','running':'分析中','failed':'失败','cancelled':'已取消','manual':'等待中'}
            return [{'cells':[r['id'],r['actor'],statuses.get(r['status'],r['status']),('东南西北'[r['seat']]+'家') if r['seat'] in range(4) else '—',r['created'],{0:'尚未通知',1:'已确认',2:'未确认'}.get(r['notified'],'—')]} for r in db.execute('SELECT id,actor,status,seat,created,notified FROM jobs ORDER BY created DESC LIMIT 5000')]
    if kind == 'offers':
        with connect(root/'astrbot_plugin_perkradar/subscriptions.db') as db:
            return [{'cells':[r['origin'],r['query'] or '全部']} for r in db.execute('SELECT origin,query FROM filters ORDER BY origin LIMIT 5000')]
    if kind == 'bilibili':
        doc=json.loads((root/'astrbot_plugin_bilibili/astrbot_plugin_bilibili.json').read_text())
        # Credential is deliberately neither visited nor returned.
        out=[]
        for origin,subs in doc.get('bili_sub_list',{}).items():
            if isinstance(subs,dict):
                for uid,meta in subs.items():
                    label=str(uid)
                    if isinstance(meta,dict):
                        name=meta.get('name') or meta.get('uname') or ''
                        if name:label+=' · '+str(name)
                    out.append({'cells':[origin,label]})
            elif isinstance(subs,list):
                for sub in subs:
                    if isinstance(sub,dict):label=str(sub.get('uid') or sub.get('mid') or sub.get('name') or '订阅条目')
                    else:label=str(sub)
                    out.append({'cells':[origin,label]})
        return out[:5000]
    raise ValueError('没有这个数据集')


def view(root, kind, query='', page=1):
    if kind not in DATASETS:
        raise ValueError('没有这个数据集')
    try:
        rows=records(root,kind)
    except (FileNotFoundError,sqlite3.OperationalError):
        return {'title':DATASETS[kind][0],'columns':DATASETS[kind][1],'rows':[],'total':0,'page':1,'pages':1,'available':False,'message':'此模块数据尚未就绪'}
    if query:
        rows=[r for r in rows if query.casefold() in ' '.join(map(str,r['cells'])).casefold()]
    pages=max(1,(len(rows)+29)//30);page=max(1,min(page,pages))
    return {'title':DATASETS[kind][0],'columns':DATASETS[kind][1],'rows':rows[(page-1)*30:page*30], 'total':len(rows),'page':page,'pages':pages,'available':True}
