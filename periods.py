"""Immutable period snapshots and source membership, independent of QQ retention."""
from __future__ import annotations
import hashlib
import json
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
        found=next((r for r in rows if r['id']==period),None)
    if not found:raise ValueError('没有这个统计周期，发送 /群数据 周期 查看')
    return found


def cutover(store, scope, name='新周期', now=None):
    if select(store,scope):raise ValueError('已经切换过周期，不能重复清零')
    if not store.group(scope):raise ValueError('该群尚未接入')
    now=time.time() if now is None else now
    snapshot={'matches':store.matches(scope),'rows':store.rows(scope),'aliases':store.aliases(scope),
              'summary':store.summary(scope),'freshness':store.freshness(scope),
              'sources':[dict(r) for r in store.db.execute('SELECT * FROM sources WHERE scope=? ORDER BY id',(scope,))],
              'exclusions':[dict(r) for r in store.db.execute('SELECT * FROM source_exclusions WHERE scope=?',(scope,))]}
    checksum=hashlib.sha256(packed(snapshot).encode()).hexdigest()
    old='H'+uuid.uuid4().hex[:12];new='P'+uuid.uuid4().hex[:12]
    stamps=[r['stamp'] for r in snapshot['sources'] if r['stamp']]
    with store.db:
        store.db.execute('INSERT INTO periods VALUES(?,?,?,?,?,?,?,?)',
            (old,scope,'历史封存','archived',min(stamps,default=now),now,packed(snapshot),checksum))
        store.db.execute('INSERT INTO source_periods SELECT id,? FROM sources WHERE scope=?',(old,scope))
        store.db.execute('INSERT INTO periods VALUES(?,?,?,?,?,?,?,?)',(new,scope,name,'current',now,0,None,''))
        store.db.execute('UPDATE groups SET last_import=0 WHERE scope=?',(scope,))
        store.db.execute('INSERT INTO audit(scope,actor,action,payload,stamp) VALUES(?,?,?,?,?)',
                         (scope,'system','封存切换',packed({'old':old,'new':new,'checksum':checksum}),now))
    return {'archived':old,'current':new,'started':now,'games':len(snapshot['matches']),'checksum':checksum}


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
