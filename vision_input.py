"""Lossless-source OCR crops. Never invent or alter glyphs/points."""
from __future__ import annotations
import base64
import io
import re
from pathlib import Path
from PIL import Image,ImageDraw
from .cards import font

def data_uri(im):
    data=io.BytesIO();im.save(data,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(data.getvalue()).decode()

def input_images(path,ocr=None):
    with Image.open(path) as raw:
        mime=Image.MIME.get(raw.format or 'PNG','image/png');im=raw.convert('RGB')
    original='data:'+mime+';base64,'+base64.b64encode(Path(path).read_bytes()).decode()
    orientation=(ocr or {}).get('orientation_degrees',0)
    if orientation in (90,180,270):
        im=im.rotate(orientation,expand=True);original=data_uri(im)
    w,h=im.size
    # Final marker stays visible, including early flying endings in East 1/2.
    pieces=[('终局标记区域',im.crop((int(w*.27),0,int(w*.73),int(h*.19))))]
    points=[]
    for row in (ocr or {}).get('full',[]):
        text=str(row.get('text','')).replace(',','').replace('−','-').replace('—','-').replace(' ','')
        box=row.get('box') or []
        if len(box)!=4 or not re.fullmatch(r'-?(?:[1-9][0-9]{2,5}|0)',text):continue
        x0=min(p[0] for p in box);y0=min(p[1] for p in box);x1=max(p[0] for p in box);y1=max(p[1] for p in box)
        if int(text)%100 or y1-y0<h*.028:continue
        points.append((y1-y0,x0,y0,x1,y1))
    points=sorted(sorted(points,reverse=True)[:4],key=lambda p:p[2])
    if len(points) in (3,4):
        for i,(_,x0,y0,x1,y1) in enumerate(points,1):
            area=(max(0,int(x0-w*.035)),max(0,int(y0-h*.085)),min(w,int(x0+w*.27)),min(h,int(y1+h*.012)))
            pieces.append((f'局部文字区域{i}（顺位以原图为准）',im.crop(area)))
    else:
        # OCR may have been unavailable. These are candidate regions, never
        # used to infer player count or ranking; the original remains primary.
        for i in range(4):
            area=(int(w*(.46+i*.045)),int(h*(.14+i*.18)),min(w,int(w*(.86+i*.04))),min(h,int(h*(.30+i*.18))))
            pieces.append((f'候选局部区域{i+1}（不是额外玩家）',im.crop(area)))
    tiles=[]
    for title,crop in pieces:
        scale=min(4,1120/max(1,crop.width));crop=crop.resize((int(crop.width*scale),int(crop.height*scale)),Image.Resampling.LANCZOS)
        tiles.append((title,crop))
    out=Image.new('RGB',(1200,sum(tile.height+55 for _,tile in tiles)+20),'#EEEEEE');d=ImageDraw.Draw(out);y=10
    for title,tile in tiles:
        d.text((20,y),title,font=font(23),fill='#111111');out.paste(tile,(20,y+38));y+=tile.height+55
    # Separate images avoid shrinking all nicknames into the original UI.
    return [original,data_uri(out)]
