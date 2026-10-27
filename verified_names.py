"""Group-local nickname glyph memory learned only from explicit human corrections.

No fuzzy spelling, model-generated training labels or public screenshot uploads.
"""
from __future__ import annotations
import copy
import json
import math
import re
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
    def __init__(self,store):self.store=store;self.cache={}
    def templates(self,scope):
        # Overrides are the explicit administrator verification boundary. Mere
        # agreement between OCR/LLM passes is never used as a training label.
        rows=[r for r in self.store.rows(scope) if r.get('override_status')=='accepted' and r.get('override_result')]
        key=tuple(sorted((r['hash'],r['override_result']) for r in rows))
        if self.cache.get(scope,{}).get('key')==key:return self.cache[scope]['items']
        items=[];counts={}
        for row in rows:
            try:
                result=json.loads(row['override_result']);raw=json.loads(row.get('raw') or '[]');ocr=next((x['local_ocr'] for x in raw if 'local_ocr' in x),None)
                for region in name_regions(row['path'],ocr):
                    p=result['players'][region['place']-1]
                    if p['points']!=region['points'] or result['mode']!=region['mode'] or counts.get(p['name'],0)>=24:continue
                    fp=fingerprint(region['crop'])
                    if fp is None:continue
                    items.append({'name':p['name'],'hash':row['hash'],'source':row['id'],'fp':fp});counts[p['name']]=counts.get(p['name'],0)+1
            except (OSError,ValueError,KeyError,TypeError,IndexError):continue
        self.cache[scope]={'key':key,'items':items};return items
    def match(self,row,ocr):
        if not row.get('scope'):return []
        try:regions=name_regions(row['path'],ocr)
        except (OSError,ValueError,KeyError,TypeError):return []
        templates=[p for p in self.templates(row['scope']) if p['hash']!=row['hash']];out=[]
        for region in regions:
            fp=fingerprint(region['crop']);names={}
            for p in templates:
                score=similarity(fp,p['fp'])
                if score<.82:continue
                names.setdefault(p['name'],[]).append((score,p['hash'],p['source']))
            ranked=[]
            for name,reads in names.items():
                reads.sort(reverse=True);unique={}
                for score,digest,source in reads:
                    if digest not in unique:unique[digest]=(score,source)
                values=sorted(unique.values(),reverse=True)
                ranked.append((values[0][0],name,values))
            ranked.sort(reverse=True)
            if not ranked:continue
            score,name,values=ranked[0];runner=ranked[1][0] if len(ranked)>1 else .82
            # Two different manually verified originals must agree. Neither the
            # same screenshot nor a close competing name can certify itself.
            if len(values)<2 or values[1][0]<.945 or score<.96 or score-runner<.055:continue
            out.append({'place':region['place'],'points':region['points'],'mode':region['mode'],'name':name,'score':round(score,5),'sources':[v[1] for v in values[:2]],'box':region['box']})
        return out


def apply_verified(obj,evidence):
    obj=copy.deepcopy(obj)
    if obj.get('is_result') is not True or obj.get('mode') not in (3,4):return obj
    players=obj.get('players',[])
    for match in evidence:
        if obj['mode']!=match['mode']:continue
        candidates=[p for p in players if p.get('place')==match['place'] and p.get('points')==match['points']]
        if len(candidates)==1:candidates[0]['name']=match['name']
    return obj
