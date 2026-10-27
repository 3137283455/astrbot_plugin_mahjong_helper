"""Isolated local OCR worker. No network inference or API credentials."""
import json,sys,logging,re
logging.disable(logging.CRITICAL)
import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

def main():
    engine=RapidOCR(intra_op_num_threads=1,inter_op_num_threads=1,text_score=.4,det_limit_side_len=1280,det_limit_type='max')
    original=Image.open(sys.argv[1]).convert('RGB');im=original;orientation=0
    if im.height>im.width*1.1:im=original.rotate(90,expand=True);orientation=90
    w,h=im.size
    def rect(r):
        return min(p[0] for p in r['box']),min(p[1] for p in r['box']),max(p[0] for p in r['box']),max(p[1] for p in r['box'])
    def read(image,x=0,y=0,scale=1):
        rows,_=engine(np.array(image)[:,:,::-1],use_det=True,use_cls=False)
        return [{'box':[[float(round(a/scale+x,2)),float(round(b/scale+y,2))] for a,b in box],'text':text,'score':float(score)} for box,text,score in rows or []]
    def names(rows):
        pts=[]
        for r in rows:
            b=rect(r);s=r['text'].replace(',','').replace('−','-').replace('—','-').replace(' ','')
            if re.fullmatch(r'-?(?:[1-9][0-9]{2,5}|0)',s) and b[3]-b[1]>=h*.035 and b[0]>w*.37 and int(s)%100==0:pts.append((r,b))
        if len(pts) not in (3,4):return []
        selected=[]
        for p,b in pts:
            candidates=[]
            for r in rows:
                z=rect(r);s=r['text']
                if s in ('自家','PT','位','终局','終局') or re.fullmatch(r'[+\-\d., ]+',s):continue
                if b[0]-w*.025<=z[0]<=b[0]+w*.045 and b[1]-h*.10<=z[1] and z[3]<=b[1]+h*.014 and z[3]-z[1]<=h*.07:
                    candidates.append((abs(z[0]-b[0])*.5+abs(b[1]-z[3]),r))
            if not candidates:continue
            r=min(candidates,key=lambda x:x[0])[1]
            if r['score']<.965 or p['score']<.965:continue
            selected.append(r)
        return selected
    def check_edges(rows):
        for r in names(rows):
            x0,y0,x1,y1=rect(r);pad=max(5,(y1-y0)*.45)
            patch=im.crop((max(0,int(x0-pad)),max(0,int(y0-3)),min(w,int(x1+pad)),min(h,int(y1+4))))
            direct,_=engine(np.array(patch)[:,:,::-1],use_det=False,use_cls=False)
            if direct and len(direct[0])==2:r['crop_text']=str(direct[0][0]);r['crop_score']=float(direct[0][1])
    full=read(im)
    if orientation and not any(any(mark in row['text'] for mark in ('终局','終局','对局终结','對局終結')) for row in full):
        alternate=original.rotate(270,expand=True);im=alternate;other=read(im)
        if (any(any(mark in row['text'] for mark in ('终局','終局','对局终结','對局終結')) for row in other) or sum(len(r['text'])*r['score'] for r in other)>sum(len(r['text'])*r['score'] for r in full)):
            full=other;orientation=270
        else:im=original.rotate(90,expand=True)
    # Independently check the clear nickname regions; uncertain ones are never
    # promoted by a guess. Their images stay available for the default model.
    if names(full):
        check_edges(full)
        x=int(w*.36);y=int(h*.07);crop=im.crop((x,y,w,int(h*.94)))
        focused=read(crop.resize((crop.width*2,crop.height*2)),x,y,2);check_edges(focused)
    else:focused=full
    print(json.dumps({'width':w,'height':h,'orientation_degrees':orientation,'full':full,'focused':focused},ensure_ascii=False))
if __name__=='__main__':main()
