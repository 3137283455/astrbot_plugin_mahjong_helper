"""Transactional local store. Cached OCR survives plugin upgrades/restarts."""
from __future__ import annotations
import json
import sqlite3
import time
from pathlib import Path
from .core import signature, normalize_name

class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path);self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS groups(scope TEXT PRIMARY KEY, bot TEXT, gid TEXT, enabled INTEGER DEFAULT 1, last_scan REAL DEFAULT 0, error TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS images(hash TEXT PRIMARY KEY,path TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'new',result TEXT,raw TEXT,reason TEXT DEFAULT '',model TEXT DEFAULT '',attempts INTEGER DEFAULT 0,retry REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY,scope TEXT NOT NULL,msg TEXT NOT NULL,idx INTEGER NOT NULL,hash TEXT NOT NULL DEFAULT '',active INTEGER DEFAULT 1,stamp INTEGER DEFAULT 0,seen REAL DEFAULT 0,error TEXT DEFAULT '',UNIQUE(scope,msg,idx));
        CREATE TABLE IF NOT EXISTS overrides(scope TEXT,hash TEXT,status TEXT,result TEXT,reason TEXT,PRIMARY KEY(scope,hash));
        CREATE TABLE IF NOT EXISTS aliases(scope TEXT,old TEXT,new TEXT,PRIMARY KEY(scope,old));
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,scope TEXT,actor TEXT,action TEXT,payload TEXT,stamp REAL);
        CREATE INDEX IF NOT EXISTS source_group ON sources(scope,active);
        ''');self.db.commit()
    def close(self): self.db.close()
    def group(self,scope):
        row=self.db.execute('SELECT * FROM groups WHERE scope=?',(scope,)).fetchone()
        return dict(row) if row else None
    def enable(self,scope,bot,gid,on=True):
        self.db.execute('INSERT INTO groups(scope,bot,gid,enabled) VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET enabled=excluded.enabled',(scope,bot,gid,int(on)));self.db.commit()
    def groups(self):return [dict(r) for r in self.db.execute('SELECT * FROM groups WHERE enabled=1')]
    def audit(self,scope,actor,action,payload):
        self.db.execute('INSERT INTO audit(scope,actor,action,payload,stamp) VALUES(?,?,?,?,?)',(scope,str(actor),action,json.dumps(payload,ensure_ascii=False),time.time()));self.db.commit()
    def reconcile(self,scope,entries):
        """Call only after a fully successful listing. Preserve old hash on URL refresh."""
        now=time.time()
        with self.db:
            self.db.execute('UPDATE sources SET active=0 WHERE scope=?',(scope,))
            for e in entries:
                self.db.execute('INSERT INTO sources(scope,msg,idx,stamp,seen) VALUES(?,?,?,?,?) ON CONFLICT(scope,msg,idx) DO UPDATE SET active=1,stamp=excluded.stamp,seen=excluded.seen',(scope,e['msg'],e['idx'],e['stamp'],now))
            self.db.execute("UPDATE groups SET last_scan=?,error='' WHERE scope=?",(now,scope))
    def source(self,scope,msg,idx):
        return dict(self.db.execute('SELECT * FROM sources WHERE scope=? AND msg=? AND idx=?',(scope,msg,idx)).fetchone())
    def source_error(self,id,reason):
        self.db.execute('UPDATE sources SET error=? WHERE id=?',(reason,id));self.db.commit()
    def attach(self,id,digest,path):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO images(hash,path) VALUES(?,?)',(digest,str(path)))
            self.db.execute("UPDATE sources SET hash=?,error='' WHERE id=?",(digest,id))
    def image(self,digest):
        row=self.db.execute('SELECT * FROM images WHERE hash=?',(digest,)).fetchone()
        return dict(row) if row else None
    def save(self,digest,status,result=None,raw=None,reason='',model='',retry=0):
        self.db.execute('UPDATE images SET status=?,result=?,raw=?,reason=?,model=?,attempts=attempts+1,retry=? WHERE hash=?',(status,json.dumps(result,ensure_ascii=False) if result else None,json.dumps(raw,ensure_ascii=False) if raw is not None else None,reason,model,retry,digest));self.db.commit()
    def scan_error(self,scope,reason):
        self.db.execute('UPDATE groups SET error=? WHERE scope=?',(reason,scope));self.db.commit()
    def rows(self,scope):
        return [dict(r) for r in self.db.execute('''SELECT s.*,i.path,i.status,i.result,i.raw,i.reason,i.attempts,i.retry,o.status AS override_status,o.result AS override_result,o.reason AS override_reason FROM sources s LEFT JOIN images i ON i.hash=s.hash LEFT JOIN overrides o ON o.hash=s.hash AND o.scope=s.scope WHERE s.scope=? AND s.active=1 ORDER BY s.stamp DESC,s.id DESC''',(scope,))]
    def matches(self,scope,days=0):
        out=[];seen=set();cut=time.time()-days*86400 if days else 0;aliases=self.aliases(scope)
        for r in self.rows(scope):
            status=r['override_status'] or r['status'];payload=r['override_result'] if r['override_status'] else r['result']
            if status!='accepted' or not payload or r['stamp']<cut:continue
            result=json.loads(payload)
            canonical=[]
            for player in result['players']:
                name=player['name'];visited=set()
                while name in aliases and name not in visited:visited.add(name);name=aliases[name]
                canonical.append({**player,'name':name})
            sig=signature({**result,'players':canonical})
            if sig in seen:continue
            seen.add(sig);out.append({**r,'result':result})
        return out
    def aliases(self,scope):return {r['old']:r['new'] for r in self.db.execute('SELECT old,new FROM aliases WHERE scope=?',(scope,))}
    def merge(self,scope,old,new):
        old=normalize_name(old);new=normalize_name(new);aliases=self.aliases(scope)
        if not old or not new or old==new:raise ValueError('请给出两个不同的完整游戏昵称')
        aliases[old]=new
        for start in aliases:
            seen=set();node=start
            while node in aliases:
                if node in seen:raise ValueError('别名不能形成循环')
                seen.add(node);node=aliases[node]
        for m in self.matches(scope):
            names=[]
            for p in m['result']['players']:
                n=p['name']
                while n in aliases:n=aliases[n]
                names.append(n)
            if len(set(names))!=len(names):raise ValueError('这两个昵称出现在同一局，不能合并成一人')
        self.db.execute('INSERT OR REPLACE INTO aliases VALUES(?,?,?)',(scope,old,new));self.db.commit()
    def unmerge(self,scope,old):
        self.db.execute('DELETE FROM aliases WHERE scope=? AND old=?',(scope,normalize_name(old)));self.db.commit()
    def get_source(self,scope,id):
        return next((r for r in self.rows(scope) if r['id']==id),None)
    def override(self,scope,row,status,result=None,reason='人工处理'):
        self.db.execute('INSERT OR REPLACE INTO overrides VALUES(?,?,?,?,?)',(scope,row['hash'],status,json.dumps(result,ensure_ascii=False) if result else None,reason));self.db.commit()
    def summary(self,scope):
        rows=self.rows(scope);counts={};hashes=set()
        for r in rows:
            status=r['override_status'] or r['status'] or 'download_error'
            counts[status]=counts.get(status,0)+1
            if r['hash']:hashes.add(r['hash'])
        return {'images':len(rows),'unique_images':len(hashes),'games':len(self.matches(scope)),**counts}
