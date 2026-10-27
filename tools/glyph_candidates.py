"""Offline font-glyph candidates. Install numpy separately for this admin tool.

Example: python glyph_candidates.py --font /path/to/font.ttf --image glyph.png
Candidates require visual verification; this tool never edits the group database.
"""
import argparse,json,struct,hashlib
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont,ImageFilter,ImageChops


def supported_cjk(path):
    b=Path(path).read_bytes();base=struct.unpack_from('>I',b,12)[0] if b[:4]==b'ttcf' else 0
    count=struct.unpack_from('>H',b,base+4)[0];tables={}
    for i in range(count):
        tag,_,offset,length=struct.unpack_from('>4sIII',b,base+12+16*i);tables[tag]=(offset,length)
    offset,_=tables[b'cmap'];count=struct.unpack_from('>H',b,offset+2)[0];out=set()
    for i in range(count):
        platform,_,relative=struct.unpack_from('>HHI',b,offset+4+8*i);start=offset+relative;fmt=struct.unpack_from('>H',b,start)[0]
        if platform not in (0,3):continue
        if fmt==4:
            n=struct.unpack_from('>H',b,start+6)[0]//2;ends=struct.unpack_from('>'+str(n)+'H',b,start+14);starts=struct.unpack_from('>'+str(n)+'H',b,start+16+n*2)
            for j,(a,z) in enumerate(zip(starts,ends)):
                delta=struct.unpack_from('>h',b,start+16+n*4+j*2)[0];rp=start+16+n*6+j*2;ro=struct.unpack_from('>H',b,rp)[0]
                for cp in range(a,z+1):
                    glyph=(cp+delta)&65535 if ro==0 else struct.unpack_from('>H',b,rp+ro+2*(cp-a))[0]
                    if glyph:out.add(cp)
        elif fmt==12:
            n=struct.unpack_from('>I',b,start+12)[0]
            for j in range(n):
                a,z,g=struct.unpack_from('>III',b,start+16+j*12)
                out.update(range(a if g else a+1,z+1))
    return sorted(cp for cp in out if 0x3400<=cp<=0x9fff or 0x20000<=cp<=0x323af)


def vector(im,source=False):
    if source:
        r,g,b=im.convert('RGB').split();im=ImageChops.darker(ImageChops.darker(r,g),b).point([max(0,min(255,round((v-110)*255/120))) for v in range(256)])
    else:im=im.convert('L')
    bounds=im.point(lambda v:255 if v>70 else 0).getbbox()
    if not bounds:raise ValueError('No foreground glyph found; crop one light glyph on a dark background.')
    im=im.crop(bounds).resize((40,40),Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(.6));v=np.asarray(im,dtype=np.float32).ravel()/255;v-=v.mean();return v/(np.linalg.norm(v)+1e-8)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--font',required=True);p.add_argument('--image',required=True);p.add_argument('--box',type=int,nargs=4);p.add_argument('--cache');p.add_argument('--top',type=int,default=10);p.add_argument('--output');args=p.parse_args()
    cache=Path(args.cache) if args.cache else None;digest=hashlib.sha256(Path(args.font).read_bytes()).hexdigest();valid=False
    if cache and cache.exists():
        with np.load(cache,allow_pickle=False) as d:
            valid='font_sha256' in d and str(d['font_sha256'])==digest and 'format_version' in d and int(d['format_version'])==1
            if valid:chars=d['chars'].copy();matrix=d['vectors'].copy()
    if not valid:
        font=ImageFont.truetype(args.font,56);chars=[];vectors=[]
        for cp in supported_cjk(args.font):
            im=Image.new('L',(100,110));ImageDraw.Draw(im).text((8,2),chr(cp),font=font,fill=255)
            try:v=vector(im)
            except ValueError:continue
            chars.append(chr(cp));vectors.append(v)
        chars=np.array(chars);matrix=np.array(vectors)
        if cache:np.savez_compressed(cache,chars=chars,vectors=matrix,font_sha256=np.array(digest),format_version=np.array(1))
    with Image.open(args.image) as raw:im=raw.convert('RGB')
    if args.box:im=im.crop(tuple(args.box))
    scores=np.clip(matrix@vector(im,source=True),-1,1);indices=np.argsort(scores)[-max(1,min(args.top,50)):][::-1]
    result={'font':Path(args.font).name,'glyphs_compared':len(chars),'candidates':[{'character':str(chars[i]),'unicode':f'U+{ord(str(chars[i])):04X}','similarity':round(float(scores[i]),5)} for i in indices],'note':'Similarity is not a probability. Verify strokes against originals before correcting data.'}
    text=json.dumps(result,ensure_ascii=False,indent=2);print(text)
    if args.output:Path(args.output).write_text(text,encoding='utf8')

if __name__=='__main__':main()
