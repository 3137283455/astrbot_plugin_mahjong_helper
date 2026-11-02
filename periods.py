"""Immutable period snapshots and source membership, independent of QQ retention."""
from __future__ import annotations
import hashlib
import json
import re
import time
import uuid


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def install(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS periods(
        id TEXT PRIMARY KEY,scope TEXT NOT NULL,name TEXT NOT NULL,state TEXT NOT NULL,
        started REAL NOT NULL,ended REAL NOT NULL DEFAULT 0,snapshot TEXT,checksum TEXT DEFAULT '');
    CREATE UNIQUE INDEX IF NOT EXISTS one_current_period ON periods(scope) WHERE state='current';
    CREATE TABLE IF NOT EXISTS source_periods(source_id INTEGER PRIMARY KEY,period_id TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS online_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS online_links(
        key TEXT PRIMARY KEY,table_key TEXT NOT NULL,record_id TEXT NOT NULL,
        baseline TEXT NOT NULL,local_digest TEXT NOT NULL,error TEXT DEFAULT '',deleted INTEGER DEFAULT 0);
    ''')
    columns={r['name'] for r in db.execute('PRAGMA table_info(sources)')}
    if 'origin' not in columns:
        db.execute("ALTER TABLE sources ADD COLUMN origin TEXT NOT NULL DEFAULT 'qq'")
    db.commit()


def all_periods(store, scope):
    # The preparation tool also opens pre-V2 databases read-only.
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='periods'").fetchone():
        return []
    return [dict(r) for r in store.db.execute('SELECT * FROM periods WHERE scope=? ORDER BY started DESC,id DESC',(scope,))]


def select(store, scope, period=None):
    rows=all_periods(store,scope)
    if period in (None,'current','当前'):
        return next((r for r in rows if r['state']=='current'),None)
    if period in ('历史','上一期'):
        found=next((r for r in rows if r['state']=='archived'),None)
    else:
        season=str(period).upper().removesuffix('赛季')
        name=season+'赛季' if re.fullmatch(r'S\d+',season) else str(period)
        found=next((r for r in rows if r['id']==period or r['name']==name),None)
    if not found:raise ValueError('没有这个统计周期，发送 /群数据 周期 查看')
    return found


def cutover(store, scope, name='S1赛季', now=None, baseline_source_max=None):
    if select(store,scope):raise ValueError('已经切换过周期，不能重复清零')
    if not store.group(scope):raise ValueError('该群尚未接入')
    now=time.time() if now is None else now
    snapshot={'matches':store.matches(scope),'rows':store.rows(scope),'aliases':store.aliases(scope),
              'summary':store.summary(scope),'freshness':store.freshness(scope),
              'sources':[dict(r) for r in store.db.execute('SELECT * FROM sources WHERE scope=? ORDER BY id',(scope,))],
              'exclusions':[dict(r) for r in store.db.execute('SELECT * FROM source_exclusions WHERE scope=?',(scope,))]}
    carried=0;previous_import=snapshot['freshness']['last_import']
    if baseline_source_max is not None:
        for key in ('matches','rows','sources'):snapshot[key]=[r for r in snapshot[key] if r['id']<=baseline_source_max]
        def latest(rows):
            row=max(rows,key=lambda r:(r['stamp'],r['id']),default=None)
            return {'stamp':row['stamp'],'id':row['id']} if row else {'stamp':0,'id':None}
        snapshot['freshness']['data_until']=latest(snapshot['matches'])
        for mode in (3,4):
            snapshot['freshness']['latest_fly'][mode]=latest([m for m in snapshot['matches'] if m['result']['mode']==mode and any(p['points']<0 for p in m['result']['players'])])
        # A group-wide import clock cannot attribute a new processing time to old games.
        snapshot['freshness']['last_import']=0
        rows=snapshot['rows'];counts={}
        for row in rows:
            status=row['override_status'] or row['status'] or 'download_error';counts[status]=counts.get(status,0)+1
        snapshot['summary']={'images':len(rows),'unique_images':len({r['hash'] for r in rows if r['hash']}),'games':len(snapshot['matches']),**counts}
        carried=store.db.execute('SELECT COUNT(*) FROM sources WHERE scope=? AND id>?',(scope,baseline_source_max)).fetchone()[0]
    checksum=hashlib.sha256(packed(snapshot).encode()).hexdigest()
    old='H'+uuid.uuid4().hex[:12];new='P'+uuid.uuid4().hex[:12]
    stamps=[r['stamp'] for r in snapshot['sources'] if r['stamp']]
    with store.db:
        store.db.execute('INSERT INTO periods VALUES(?,?,?,?,?,?,?,?)',
            (old,scope,'S0赛季','archived',min(stamps,default=now),now,packed(snapshot),checksum))
        store.db.execute('INSERT INTO periods VALUES(?,?,?,?,?,?,?,?)',(new,scope,name,'current',now,0,None,''))
        store.db.execute('INSERT INTO source_periods SELECT id,CASE WHEN id<=? THEN ? ELSE ? END FROM sources WHERE scope=?',
                         (baseline_source_max if baseline_source_max is not None else 2**63-1,old,new,scope))
        store.db.execute('UPDATE groups SET last_import=? WHERE scope=?',(previous_import if carried else 0,scope))
        store.db.execute('INSERT INTO audit(scope,actor,action,payload,stamp) VALUES(?,?,?,?,?)',
                         (scope,'system','封存切换',packed({'old':old,'new':new,'checksum':checksum,'baseline_source_max':baseline_source_max,'carried_sources':carried}),now))
    return {'archived':old,'current':new,'started':now,'games':len(snapshot['matches']),'checksum':checksum,'baseline_source_max':baseline_source_max,'carried_sources':carried}


def assign(store, scope):
    current=select(store,scope)
    if current:
        store.db.execute('INSERT OR IGNORE INTO source_periods SELECT id,? FROM sources WHERE scope=?',
                         (current['id'],scope))


def current_ids(store,scope):
    current=select(store,scope)
    if not current:return None
    return {r[0] for r in store.db.execute('''SELECT s.id FROM sources s JOIN source_periods p ON p.source_id=s.id
        WHERE s.scope=? AND p.period_id=? AND NOT EXISTS(
            SELECT 1 FROM sources old JOIN source_periods op ON op.source_id=old.id
            JOIN periods h ON h.id=op.period_id
            WHERE old.scope=s.scope AND old.hash=s.hash AND s.hash<>'' AND h.state='archived')''',
        (scope,current['id']))}


def assert_current(store,scope,row):
    current=select(store,scope)
    if row['scope']!=scope:raise ValueError('不能修改其他群的记录')
    if current and row['id'] not in current_ids(store,scope):
        raise ValueError('该记录已封存，不能修改；历史统计保持原样')
