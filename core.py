"""Local query policy and durable subscription baselines; no upstream database access."""
import hashlib
import json
import math
import re
import shlex
import sqlite3
import time
from dataclasses import dataclass, replace
from urllib.parse import urlsplit, urlunsplit

SOURCES={'epic':'Epic','linuxdo':'LINUX DO','v2ex':'V2EX','nodeseek':'NodeSeek','appinn':'小众软件','ghxi':'果核剥壳','iplaysoft':'异次元'}
CATEGORIES=('免费白嫖','限免','低价优惠','会员订阅','游戏','其他')
ALIASES={'免费':'免费白嫖','白嫖':'免费白嫖','优惠':'低价优惠','会员':'会员订阅','软件':'软件'}

@dataclass(frozen=True)
class Query:
    source: str = ''
    category: str = ''
    term: str = ''
    page: int = 1

def page_number(text):
    m=re.fullmatch(r'第?([0-9一二三四五六七八九十两]+)页',text)
    if not m:return None
    value=m[1];digits={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    if value.isdigit():number=int(value)
    elif '十' in value:
        a,b=value.split('十',1);number=(digits.get(a,1))*10+digits.get(b,0)
    else:number=digits.get(value,0)
    if not 1<=number<=100:raise ValueError('页码范围为1～100。')
    return number

def parse(text):
    if not isinstance(text,str) or len(text)>250:raise ValueError('查询内容过长。')
    try:tokens=shlex.split(text)
    except ValueError:raise ValueError('引号不完整。') from None
    q=Query();words=[]
    for token in tokens:
        if token in ('搜索','查询','最新','看看','全部'):continue
        page=page_number(token)
        source=next((k for k,v in SOURCES.items() if token.casefold() in (k.casefold(),v.casefold())),None)
        if page:q=replace(q,page=page)
        elif source:q=replace(q,source=source)
        elif token in CATEGORIES or token in ALIASES:q=replace(q,category=ALIASES.get(token,token))
        else:words.append(token)
    return replace(q,term=' '.join(words))

def clean_url(value):
    if not isinstance(value,str) or len(value)>2048:return ''
    try:
        p=urlsplit(value)
        if p.scheme not in ('https','http') or not p.hostname or p.username or p.password:return ''
        return urlunsplit((p.scheme,p.netloc,p.path,p.query,''))
    except ValueError:return ''

def normalize(item):
    if not isinstance(item,dict):return None
    url=clean_url(item.get('canonicalUrl') or item.get('url'))
    if not url or not isinstance(item.get('title'),str) or not item['title'].strip():return None
    out={k:item.get(k) for k in ('id','publishedAt','expiresAt','sourceId','sourceName','category')}
    for key in ('publishedAt','expiresAt'):
        value=out[key]
        if value is not None and (type(value) not in (int,float) or not math.isfinite(value)):return None
    out.update(url=url,title=item['title'].strip()[:300],summary=str(item.get('summary') or '')[:160])
    out['sourceName']=str(out['sourceName'] or out['sourceId'] or '未知来源')[:40]
    out['sourceId']=str(out['sourceId'] or '')[:40]
    out['category']=str(out['category'] or '其他')[:30]
    return out

def key(item):return hashlib.sha256((item['sourceId']+'\n'+item['url']).encode()).hexdigest()

def select(items,q,now=None):
    now=(time.time()*1000 if now is None else now);out=[];seen=set()
    for item in items:
        item=normalize(item)
        if item is None or (item['expiresAt'] is not None and item['expiresAt']<=now):continue
        digest=key(item)
        if digest in seen:continue
        seen.add(digest)
        if q.source and item['sourceId']!=q.source:continue
        if q.category=='软件':
            if item['sourceId'] not in ('appinn','ghxi','iplaysoft'):continue
        elif q.category and item['category']!=q.category:continue
        if q.term.casefold() not in item['title'].casefold():continue
        out.append(item)
    return sorted(out,key=lambda i:(i['publishedAt'] or 0,str(i['id'] or '')),reverse=True)

class State:
    def __init__(self,path):
        self.db=sqlite3.connect(path);self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS targets(origin TEXT PRIMARY KEY,bot TEXT,actor TEXT);
        CREATE TABLE IF NOT EXISTS filters(origin TEXT,query TEXT,PRIMARY KEY(origin,query));
        CREATE TABLE IF NOT EXISTS seen(origin TEXT,digest TEXT,stamp REAL,PRIMARY KEY(origin,digest));
        CREATE TABLE IF NOT EXISTS deliveries(origin TEXT,digest TEXT,status TEXT,stamp REAL,PRIMARY KEY(origin,digest));''');self.db.commit()
    def subscribe(self,origin,bot,actor,query,items):
        parse(query)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO targets VALUES(?,?,?)',(origin,bot,actor))
            self.db.execute('INSERT OR IGNORE INTO filters VALUES(?,?)',(origin,query))
            self.baseline(origin,items)
    def baseline(self,origin,items):
        for item in items:self.db.execute('INSERT OR IGNORE INTO seen VALUES(?,?,?)',(origin,key(item),time.time()))
        self.db.commit()
    def targets(self):return [dict(r) for r in self.db.execute('SELECT * FROM targets')]
    def filters(self,origin):return [r[0] for r in self.db.execute('SELECT query FROM filters WHERE origin=?',(origin,))]
    def unsubscribe(self,origin,query=''):
        with self.db:
            if query:self.db.execute('DELETE FROM filters WHERE origin=? AND query=?',(origin,query))
            else:self.db.execute('DELETE FROM filters WHERE origin=?',(origin,))
            if not self.filters(origin):self.db.execute('DELETE FROM targets WHERE origin=?',(origin,))
    def unseen(self,origin,items):
        known={r[0] for r in self.db.execute('SELECT digest FROM seen WHERE origin=?',(origin,))}
        out=[i for i in items if key(i) not in known]
        self.baseline(origin,items)
        return out
    def delivered(self,origin,items,status):
        with self.db:
            for item in items:self.db.execute('INSERT OR REPLACE INTO deliveries VALUES(?,?,?,?)',(origin,key(item),status,time.time()))
    def close(self):self.db.close()
