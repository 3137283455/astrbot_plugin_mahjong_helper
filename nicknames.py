"""Search and short-lived selections. Never modify stored names or aliases."""
from __future__ import annotations
import re
import time
import unicodedata
from collections import OrderedDict
from .core import normalize_name


def search_key(value):
    return ''.join(c for c in normalize_name(value).casefold()
                   if unicodedata.category(c)[0] in 'LNM')


def candidates(value,names,aliases=None):
    value=normalize_name(value);names=set(names);aliases=aliases or {}
    if not value:return None,[]
    canonical=value;seen=set()
    while canonical in aliases and canonical not in seen:
        seen.add(canonical);canonical=aliases[canonical]
    if canonical in names:return canonical,[]
    needle=search_key(value);literal=value.casefold();found=set()
    for name in names:
        variants=[name]+[old for old in aliases if resolve(old,aliases)==name]
        if any(literal in item.casefold() or (needle and needle in search_key(item)) for item in variants):found.add(name)
    return None,sorted(found,key=lambda name:(search_key(name)!=needle,len(name),name))


def resolve(name,aliases):
    seen=set()
    while name in aliases and name not in seen:seen.add(name);name=aliases[name]
    return name


def choice(value):
    value=unicodedata.normalize('NFKC',str(value)).strip()
    if value in ('取消','取消选择','不选了','算了'):return 'cancel'
    match=re.fullmatch(r'(?:选(?:择)?\s*|就\s*)?(?:第\s*)?([0-9]{1,3}|[一二三四五六七八九十两]{1,3})\s*(?:个|位|号)?',value)
    if not match:return None
    n=match[1]
    if n.isdigit():return int(n)
    digits=dict(zip('一二三四五六七八九两',(*range(1,10),2)))
    if n=='十':return 10
    if n in digits:return digits[n]
    if n.startswith('十') and n[1:] in digits:return 10+digits[n[1:]]
    if len(n)==2 and n[0] in digits and n[1]=='十':return digits[n[0]]*10
    return None


class SelectionCache:
    def __init__(self,ttl=180,limit=512,clock=time.monotonic):
        self.ttl=ttl;self.limit=limit;self.clock=clock;self.items=OrderedDict()
    def get(self,key):
        now=self.clock()
        for old,item in list(self.items.items()):
            if now-item['stamp']>=self.ttl:self.items.pop(old,None)
        return self.items.get(key)
    def put(self,key,value):
        self.get(key);self.items.pop(key,None)
        self.items[key]={**value,'stamp':self.clock()}
        while len(self.items)>self.limit:self.items.popitem(last=False)
    def pop(self,key):
        value=self.get(key);self.items.pop(key,None);return value
