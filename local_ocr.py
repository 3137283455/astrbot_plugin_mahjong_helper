import asyncio
import json
import os
import sys
from pathlib import Path
from PIL import Image

async def run_ocr(python,worker,path):
    if not Path(python).is_file():raise RuntimeError('本地文字识别组件尚未安装')
    env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
    proc=await asyncio.create_subprocess_exec(str(python),str(worker),str(path),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env)
    try:out,err=await asyncio.wait_for(proc.communicate(),60)
    except BaseException:
        if proc.returncode is None:proc.kill()
        await proc.wait();raise
    if proc.returncode:raise RuntimeError('本地文字识别进程失败')
    try:
        # Some OCR versions emit initialization messages before the final JSON.
        rows=json.loads(out.decode('utf-8').strip().splitlines()[-1])
    except (ValueError,IndexError):raise RuntimeError('本地文字识别输出格式错误')
    return rows

def interpret(ocr):
    """Local admission requires a final marker, full board and two exact readings."""
    import re
    from .core import validate,same_reading,normalize_name
    w,h=ocr['width'],ocr['height']
    def box(r):
        xs=[p[0] for p in r['box']];ys=[p[1] for p in r['box']]
        return min(xs),min(ys),max(xs),max(ys)
    def point_rows(rows):
        found=[]
        for r in rows:
            text=normalize_name(r['text']).replace(',','').replace('−','-').replace('—','-').replace(' ','')
            b=box(r)
            if re.fullmatch(r'-?(?:[1-9][0-9]{2,5}|0)',text) and b[3]-b[1]>=h*.035 and b[0]>w*.37:
                value=int(text)
                if value%100==0:found.append((r,b,value))
        return sorted(found,key=lambda r:r[1][1])
    all_rows=ocr['full']+ocr['focused']
    final_words=('终局','終局','对局终结','對局終結','对局结束','対局終了','最終結果')
    final=any(any(word in r['text'] for word in final_words) and r['score']>=.8 for r in all_rows)
    points=point_rows(ocr['focused'])
    if not final and len(points)<3:
        global_points=[]
        for row in ocr['full']:
            text=normalize_name(row['text']).replace(',','').replace('−','-').replace('—','-').replace(' ','')
            bounds=box(row)
            if re.fullmatch(r'-?(?:[1-9][0-9]{2,5}|0)',text) and int(text)%100==0 and bounds[3]-bounds[1]>=h*.018:
                global_points.append(row)
        if len(global_points)>=3:
            return {'decision':'llm_pending','reason':'可能是网格布局结算，需图像确认终局覆盖层','raw':[]}
        return {'decision':'ignored','reason':'未检测到完整雀魂终局结算版式','raw':[]}
    def read(rows):
        pts=point_rows(rows)
        if not final or len(pts) not in (3,4):raise ValueError('未能确定完整的终局顺位表')
        # This automatic parser only supports the vertical final ranking list.
        if any(b[1]-a[1]<h*.08 for (_,a,_),(_,b,_) in zip(pts,pts[1:])):
            raise ValueError('不是支持的终局竖排榜单，需进一步核对')
        players=[];confidence=1.
        for i,(r,b,value) in enumerate(pts,1):
            candidates=[]
            for item in rows:
                z=box(item);text=normalize_name(item['text'])
                if item is r or text in ('自家','PT','位','终局','終局'):continue
                if re.fullmatch(r'[+\-\d., ]+',text):continue
                # Nickname line is directly above the final held points.
                if not (b[0]-w*.025<=z[0]<=b[0]+w*.045 and b[1]-h*.10<=z[1] and z[3]<=b[1]+h*.014):continue
                if z[3]-z[1]>h*.07:continue
                candidates.append((abs(z[0]-b[0])*.5+abs(b[1]-z[3]),item))
            if not candidates:raise ValueError(f'第{i}位昵称未能可靠读取')
            name=min(candidates,key=lambda x:x[0])[1]
            if 'crop_text' in name and normalize_name(name['crop_text'])!=normalize_name(name['text']):
                raise ValueError('昵称边缘字符复核不一致，不能自动录入')
            if 'crop_text' not in name:raise ValueError('昵称缺少独立区域复核')
            confidence=min(confidence,float(r['score']),float(name['score']),float(name.get('crop_score',0)))
            players.append({'name':normalize_name(name['text']),'place':i,'points':value})
        obj={'is_result':True,'mode':len(pts),'players':players,'confidence':confidence,'reason':'本地OCR竖排终局识别'}
        if confidence<.965:raise ValueError('昵称或持点本地识别把握不足')
        validate(obj);return obj
    raw=[]
    try:
        a=read(ocr['full']);raw.append(a)
        b=read(ocr['focused']);raw.append(b)
        if not same_reading(validate(a),validate(b)):raise ValueError('两次本地读取不一致')
        return {'decision':'accepted','result':validate(a),'raw':raw,'reason':'完整终局、本地两次读取一致'}
    except ValueError as exc:
        # Preserve a best-effort candidate for review, but never count it.
        if not raw:
            try:
                candidates=point_rows(ocr['focused']);players=[]
                for i,(r,b,value) in enumerate(candidates,1):
                    nearby=[]
                    for item in ocr['focused']:
                        z=box(item);txt=normalize_name(item['text'])
                        if not re.fullmatch(r'[+\-\d., ]+',txt) and b[0]-w*.025<=z[0]<=b[0]+w*.045 and b[1]-h*.10<=z[1] and z[3]<=b[1]+h*.014:
                            nearby.append((abs(z[0]-b[0])*.5+abs(b[1]-z[3]),txt))
                    players.append({'name':min(nearby)[1] if nearby else '?','place':i,'points':value})
                raw=[{'is_result':bool(final),'mode':len(candidates),'players':players,'confidence':0,'reason':str(exc)}]
            except Exception:pass
        return {'decision':'llm_pending','reason':str(exc),'raw':raw}


def trusted_names(ocr):
    import re
    from .core import normalize_name
    if not isinstance(ocr,dict):return {}
    w,h=ocr.get('width',0),ocr.get('height',0)
    if not w or not h:return {}
    def box(r):
        xs=[p[0] for p in r['box']];ys=[p[1] for p in r['box']]
        return min(xs),min(ys),max(xs),max(ys)
    readings=[]
    for key in ('full','focused'):
        rows=ocr.get(key,[]);points=[]
        for r in rows:
            text=normalize_name(r['text']).replace(',','').replace('−','-').replace('—','-').replace(' ','');b=box(r)
            if re.fullmatch(r'-?(?:[1-9][0-9]{2,5}|0)',text) and b[3]-b[1]>=h*.035 and b[0]>w*.37 and int(text)%100==0:points.append((r,b))
        points.sort(key=lambda x:x[1][1]);names={}
        if len(points) not in (3,4):return {}
        values=[int(normalize_name(r['text']).replace(',','').replace('−','-').replace('—','-').replace(' ','')) for r,_ in points]
        if any(a<b for a,b in zip(values,values[1:])):return {}
        for place,(point,b) in enumerate(points,1):
            candidates=[]
            for r in rows:
                z=box(r);name=normalize_name(r['text'])
                if name in ('自家','PT','位','终局','終局') or re.fullmatch(r'[+\-\d., ]+',name):continue
                if b[0]-w*.025<=z[0]<=b[0]+w*.045 and b[1]-h*.10<=z[1] and z[3]<=b[1]+h*.014 and z[3]-z[1]<=h*.07:
                    candidates.append((abs(z[0]-b[0])*.5+abs(b[1]-z[3]),r))
            if not candidates:continue
            r=min(candidates,key=lambda x:x[0])[1]
            if r.get('score',0)>=.985 and r.get('crop_score',0)>=.985 and normalize_name(r.get('crop_text',''))==normalize_name(r['text']):names[place]=normalize_name(r['text'])
        readings.append(names)
    return {i:name for i,name in readings[0].items() if readings[1].get(i)==name}
