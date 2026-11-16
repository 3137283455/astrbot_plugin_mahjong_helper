from functools import lru_cache
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image,ImageDraw,ImageFont

BG='#101720';PANEL='#1B2633';INK='#F2F5F8';MUTED='#A0B0C2';TEAL='#54D6C6';GOLD='#FFCA78'
@lru_cache(maxsize=32)
def font(size,bold=False):
    for p,idx in [('/usr/share/fonts/opentype/noto/NotoSansCJK-'+('Bold' if bold else 'Regular')+'.ttc',2),('C:/Windows/Fonts/'+('msyhbd' if bold else 'msyh')+'.ttc',0)]:
        if Path(p).exists():return ImageFont.truetype(p,size,index=idx)
    raise RuntimeError('缺少中文字体。')
def stamp(value):
    try:return datetime.fromtimestamp(float(value)/1000,ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M') if value else '尚未抓取'
    except (ValueError,OverflowError,OSError):return '时间未知'
def wrap(draw,text,f,width,limit=2):
    lines=[];line=''
    for c in str(text).replace('\n',' '):
        if draw.textlength(line+c,font=f)>width:
            lines.append(line);line=c
            if len(lines)==limit:break
        else:line+=c
    if len(lines)<limit and line:lines.append(line)
    elif lines:lines[-1]=lines[-1][:-1]+'…'
    return lines
def render(path,title,subtitle,items=None,lines=None,footer=''):
    items=items or [];lines=lines or []
    h=290+len(items)*240+len(lines)*55
    im=Image.new('RGB',(1360,max(520,h)),BG);d=ImageDraw.Draw(im)
    d.rounded_rectangle((36,30,1324,202),radius=26,fill=PANEL)
    d.rounded_rectangle((59,60,69,165),radius=5,fill=TEAL)
    d.text((92,56),title,font=font(44,True),fill=INK)
    d.text((92,127),subtitle[:60],font=font(24),fill=MUTED)
    y=229
    for index,item in enumerate(items,1):
        d.rounded_rectangle((36,y,1324,y+222),radius=20,fill=PANEL)
        d.text((60,y+18),str(index).zfill(2),font=font(30,True),fill=TEAL)
        for j,line in enumerate(wrap(d,item['title'],font(30,True),1110)):
            d.text((126,y+18+j*43),line,font=font(30,True),fill=INK)
        summary=wrap(d,item.get('summary',''),font(22),1180,1)
        if summary:d.text((64,y+113),summary[0],font=font(22),fill=MUTED)
        label=item['sourceName']+'  ·  '+item['category']
        if item.get('expiresAt'):label+='  ·  截止 '+stamp(item['expiresAt'])
        elif item.get('publishedAt'):label+='  ·  发布 '+stamp(item['publishedAt'])
        d.text((64,y+169),label,font=font(22),fill=GOLD);y+=240
    for line in lines:
        for part in wrap(d,line,font(26),1220,1):d.text((60,y),part,font=font(26),fill=INK)
        y+=55
    if footer:d.text((60,im.height-48),footer[:85],font=font(21),fill=TEAL)
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);im.save(path,'PNG')
    return path
