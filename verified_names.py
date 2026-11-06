"""Persistent group-local nickname glyph memory from checked originals.

Uses confirmed labels only, never fresh model guesses or fuzzy name merging.
"""
from __future__ import annotations
import copy
import json
import math
import re
import time
from pathlib import Path
from PIL import Image, ImageFilter


def _box(row):
    points=row['box']
    return min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)


def name_regions(path,ocr):
    """Locate nickname lines above a complete vertical final-points list."""
    if not ocr or not ocr.get('width') or not ocr.get('height'):return []
    w,h=ocr['width'],ocr['height'];rows=ocr.get('full',[]);points=[]
    for row in rows:
        text=str(row.get('text','')).replace(',','').replace('−','-').replace('—','-').replace(' ','')
        if not row.get('box') or not re.fullmatch(r'-?\d+',text):continue
        b=_box(row)
        if int(text)%100==0 and b[3]-b[1]>=h*.035 and b[0]>w*.37:points.append((b,row,int(text)))
    points.sort(key=lambda item:item[0][1])
    if len(points) not in (3,4) or any(a[2]<b[2] or b[0][1]-a[0][1]<h*.08 for a,b in zip(points,points[1:])):return []
    with Image.open(path) as source:im=source.convert('RGB').rotate(ocr.get('orientation_degrees',0),expand=True)
    if im.size!=(w,h):return []
    out=[]
    for place,(b,point,value) in enumerate(points,1):
        nearby=[]
        for row in rows:
            text=str(row.get('text',''))
            if row is point or not row.get('box') or re.fullmatch(r'[+\-\d., ]+',text) or text in ('自家','PT','位','终局','終局'):continue
            z=_box(row)
            if b[0]-w*.025<=z[0]<=b[0]+w*.045 and b[1]-h*.10<=z[1] and z[3]<=b[1]+h*.014 and z[3]-z[1]<=h*.07:
                nearby.append((abs(z[0]-b[0])*.5+abs(b[1]-z[3]),row))
        if not nearby:continue
        row=min(nearby,key=lambda item:item[0])[1];z=_box(row);pad=max(2,(z[3]-z[1])*.18)
        area=(max(0,int(z[0]-pad)),max(0,int(z[1]-pad)),min(w,int(z[2]+pad)),min(h,int(z[3]+pad)))
        if z[3]-z[1]<13:continue
        out.append({'place':place,'points':value,'mode':len(points),'crop':im.crop(area),'box':list(area)})
    return out


def fingerprint(im):
    """Normalize visible white glyphs; preserve aspect to reject partial names."""
    im=im.convert('RGB');r,g,b=im.split()
    from PIL import ImageChops
    gray=ImageChops.darker(ImageChops.darker(r,g),b)
    mask=gray.point([max(0,min(255,round((i-105)*255/125))) for i in range(256)])
    bounds=mask.point(lambda i:255 if i>95 else 0).getbbox()
    if not bounds:return None
    mask=mask.crop(bounds);width,height=mask.size
    if min(width,height)<9:return None
    aspect=width/height
    mask=mask.resize((48,20),Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(.7))
    values=list(mask.tobytes());mean=sum(values)/len(values);vec=[v-mean for v in values];norm=math.sqrt(sum(v*v for v in vec))
    if norm<1:return None
    return aspect,tuple(v/norm for v in vec)


def similarity(a,b):
    if a is None or b is None or abs(math.log(a[0]/b[0]))>.12:return -1.
    return sum(x*y for x,y in zip(a[1],b[1]))


class VerifiedNames:
    def __init__(self,store):
        self.store=store;self.cache={}
        if hasattr(store,'db'):
            store.db.executescript('''CREATE TABLE IF NOT EXISTS nickname_glyphs(
                scope TEXT,hash TEXT,place INTEGER,name TEXT,source INTEGER,fp TEXT,
                explicit INTEGER DEFAULT 0,stamp REAL,PRIMARY KEY(scope,hash,place,name));
                CREATE TABLE IF NOT EXISTS nickname_glyph_blocks(scope TEXT,name TEXT,PRIMARY KEY(scope,name));''')
            store.db.commit()

    def enroll(self,scope,row,place,name):
        """An explicitly checked nickname region, not an alias or whole-game approval."""
        from .core import normalize_name
        name=normalize_name(name)
        if row['scope']!=scope or not name or len(name)>100 or any(ord(c)<32 for c in name) or name in ('?','？','未知'):
            raise ValueError('请填写本群截图中的完整昵称')
        raw=json.loads(row.get('raw') or '[]');ocr=next((x['local_ocr'] for x in raw if 'local_ocr' in x),None)
        region=next((r for r in name_regions(row['path'],ocr) if r['place']==place),None)
        if region is None:raise ValueError('未找到该顺位的完整昵称区域，不能登记字形')
        fp=fingerprint(region['crop'])
        if fp is None:raise ValueError('昵称字形过小或不清晰，不能登记')
        with self.store.db:
            self.store.db.execute('DELETE FROM nickname_glyph_blocks WHERE scope=? AND name=?',(scope,name))
            self.store.db.execute('DELETE FROM nickname_glyphs WHERE scope=? AND hash=? AND place=?',(scope,row['hash'],place))
            self.store.db.execute('INSERT INTO nickname_glyphs VALUES(?,?,?,?,?,?,1,?)',
                                  (scope,row['hash'],place,name,row['id'],json.dumps(fp),time.time()))
        self.cache.pop(scope,None)
        return {'name':name,'source':row['id'],'place':place}

    def remove(self,scope,name):
        with self.store.db:
            self.store.db.execute('DELETE FROM nickname_glyphs WHERE scope=? AND name=?',(scope,name))
            self.store.db.execute('INSERT OR IGNORE INTO nickname_glyph_blocks VALUES(?,?)',(scope,name))
        self.cache.pop(scope,None)

    def summary(self,scope):
        counts={}
        for item in self.templates(scope):counts[item['name']]=counts.get(item['name'],0)+1
        return counts

    def templates(self,scope):
        # Overrides are the explicit administrator verification boundary. Mere
        # agreement between OCR/LLM passes is never used as a training label.
        evidence=self.store.training_rows(scope) if hasattr(self.store,'training_rows') else self.store.rows(scope)
        rows=[]
        for row in evidence:
            label=row.get('override_result') if row.get('override_status')=='accepted' else (
                row.get('result') if row.get('_glyph_archived') and not row.get('override_status') and row.get('status')=='accepted' else None)
            if label:rows.append({**row,'glyph_label':label})
        saved=[];blocked=set()
        if hasattr(self.store,'db'):
            saved=[dict(r) for r in self.store.db.execute('SELECT * FROM nickname_glyphs WHERE scope=?',(scope,))]
            blocked={r[0] for r in self.store.db.execute('SELECT name FROM nickname_glyph_blocks WHERE scope=?',(scope,))}
        key=(tuple(sorted((r['hash'],r['glyph_label']) for r in rows)),
             tuple(sorted((r['hash'],r['place'],r['name'],r['stamp']) for r in saved if r['explicit'])),tuple(sorted(blocked)))
        if self.cache.get(scope,{}).get('key')==key:return self.cache[scope]['items']
        items=[];counts={};writes=[]
        persisted={(r['hash'],r['place'],r['name']):r for r in saved}
        corrected={(r['hash'],p['place']):p['name'] for r in rows for p in json.loads(r['glyph_label'])['players']}
        for row in saved:
            if row['explicit'] and row['name'] not in blocked:
                if corrected.get((row['hash'],row['place']),row['name'])!=row['name']:continue
                items.append({'name':row['name'],'hash':row['hash'],'source':row['source'],'place':row['place'],
                              'fp':json.loads(row['fp']),'explicit':True,'origin':'registered'})
        for row in rows:
            try:
                result=json.loads(row['glyph_label']);raw=json.loads(row.get('raw') or '[]');ocr=next((x['local_ocr'] for x in raw if 'local_ocr' in x),None)
                cached=[persisted.get((row['hash'],p['place'],p['name'])) for p in result['players']]
                if all(cached):
                    for p,c in zip(result['players'],cached):
                        if p['name'] in blocked or c['explicit']:continue
                        items.append({'name':p['name'],'hash':row['hash'],'source':row['id'],'fp':json.loads(c['fp']),'explicit':False,
                                      'origin':'archived-confirmed' if row.get('_glyph_archived') else 'manual-confirmed'})
                    continue
                for region in name_regions(row['path'],ocr):
                    p=result['players'][region['place']-1]
                    if p['name'] in blocked or p['points']!=region['points'] or result['mode']!=region['mode'] or counts.get(p['name'],0)>=24:continue
                    if any(x['hash']==row['hash'] and x['name']==p['name'] and x.get('explicit') for x in items):continue
                    fp=fingerprint(region['crop'])
                    if fp is None:continue
                    items.append({'name':p['name'],'hash':row['hash'],'source':row['id'],'fp':fp,'explicit':False,
                                  'origin':'archived-confirmed' if row.get('_glyph_archived') else 'manual-confirmed'});counts[p['name']]=counts.get(p['name'],0)+1
                    writes.append((scope,row['hash'],region['place'],p['name'],row['id'],json.dumps(fp),time.time()))
            except (OSError,ValueError,KeyError,TypeError,IndexError):continue
        if writes and hasattr(self.store,'db'):
            self.store.db.executemany('INSERT OR IGNORE INTO nickname_glyphs VALUES(?,?,?,?,?,?,0,?)',writes);self.store.db.commit()
        self.cache[scope]={'key':key,'items':items};return items
    def match(self,row,ocr):
        if not row.get('scope'):return []
        try:regions=name_regions(row['path'],ocr)
        except (OSError,ValueError,KeyError,TypeError):return []
        templates=self.templates(row['scope']);out=[]
        for region in regions:
            direct=[p for p in templates if p.get('explicit') and p['hash']==row['hash'] and p.get('place')==region['place']]
            if len(direct)==1:
                out.append({'place':region['place'],'points':region['points'],'mode':region['mode'],'name':direct[0]['name'],
                            'score':1.,'sources':[direct[0]['source']],'box':region['box'],'method':'registered-region'})
                continue
            fp=fingerprint(region['crop']);names={}
            for p in templates:
                if p['hash']==row['hash']:continue
                score=similarity(fp,p['fp'])
                if score<.82:continue
                names.setdefault(p['name'],[]).append((score,p['hash'],p['source'],p.get('explicit',False)))
            ranked=[]
            for name,reads in names.items():
                reads.sort(reverse=True);unique={}
                for score,digest,source,explicit in reads:
                    if digest not in unique:unique[digest]=(score,source,explicit)
                values=sorted(unique.values(),reverse=True)
                ranked.append((values[0][0],name,values))
            ranked.sort(reverse=True)
            if not ranked:continue
            score,name,values=ranked[0];runner=ranked[1][0] if len(ranked)>1 else .82
            # Two different manually verified originals must agree. Neither the
            # same screenshot nor a close competing name can certify itself.
            enrolled=any(v[2] and v[0]>=.985 for v in values) and score-runner>=.08
            if not enrolled and (len(values)<2 or values[1][0]<.945 or score<.96 or score-runner<.055):continue
            out.append({'place':region['place'],'points':region['points'],'mode':region['mode'],'name':name,'score':round(score,5),'sources':[v[1] for v in values[:2]],'box':region['box'],
                        'method':'registered-match' if enrolled else 'historical-pair'})
        return out


def guard_historical_labels(readings,evidence):
    """Old inferred labels may not silently rewrite two agreeing model reads.

    Explicit registered corrections remain authoritative and fully traceable.
    """
    from collections import Counter
    kept=[]
    for match in evidence:
        if str(match.get('method','')).startswith('registered-'):
            kept.append(match);continue
        votes=[]
        for reading in readings:
            confidence=reading.get('confidence',0)
            if reading.get('is_result') is not True or reading.get('mode')!=match['mode'] or type(confidence) not in (int,float) or confidence<.94:continue
            votes.extend(p.get('name') for p in reading.get('players',[])
                         if p.get('place')==match['place'] and p.get('points')==match['points'] and isinstance(p.get('name'),str))
        name,count=Counter(votes).most_common(1)[0] if votes else ('',0)
        if count>=2 and name!=match['name']:continue
        kept.append(match)
    return kept


def apply_verified(obj,evidence):
    obj=copy.deepcopy(obj)
    if obj.get('is_result') is not True or obj.get('mode') not in (3,4):return obj
    players=obj.get('players',[])
    for match in evidence:
        if obj['mode']!=match['mode']:continue
        candidates=[p for p in players if p.get('place')==match['place'] and p.get('points')==match['points']]
        if len(candidates)==1:candidates[0]['name']=match['name']
    return obj
