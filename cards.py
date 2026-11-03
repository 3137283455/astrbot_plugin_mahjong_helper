"""High-resolution, deterministic Chinese cards; data never interpreted as markup."""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from .core import aggregate, ranking

W=1440
BG='#101720';PANEL='#1B2633';INK='#F2F5F8';MUTED='#A0B0C2';TEAL='#54D6C6';RED='#FF858B';GOLD='#FFCA78'
@lru_cache(maxsize=32)
def font(size,bold=False):
    for p,idx in [('/usr/share/fonts/opentype/noto/NotoSansCJK-'+('Bold' if bold else 'Regular')+'.ttc',2),('C:/Windows/Fonts/'+('msyhbd' if bold else 'msyh')+'.ttc',0)]:
        if Path(p).exists():return ImageFont.truetype(p,size,index=idx)
    raise RuntimeError('需要安装中文字体 NotoSansCJK 或微软雅黑')
def fit(d,text,size,width,bold=False):
    text=str(text)
    while text and d.textlength(text,font=font(size,bold))>width:
        text=text[:-2]+'…' if len(text)>2 else ''
    return text

def base(title,subtitle,height):
    im=Image.new('RGB',(W,height),BG);d=ImageDraw.Draw(im)
    d.rounded_rectangle((44,38,1396,206),radius=28,fill=PANEL)
    d.rounded_rectangle((70,74,82,158),radius=5,fill=TEAL)
    d.text((108,66),title,font=font(53,True),fill=INK)
    d.text((110,143),subtitle,font=font(26),fill=MUTED)
    return im,d

def footer(d,y,group,days,period_name='当前周期'):
    period=period_name+(f' · 近{days}天（按加入精华时间）' if days else ' · 已确认对局')
    d.text((55,y),f'群 {group} · {period} · 完整终局，三麻 / 四麻独立统计',font=font(25),fill=MUTED)
    d.text((55,y+43),'比例仅代表本群精华样本；昵称来自截图，不等同于发送者。',font=font(25),fill=MUTED)

def overview(path,group,matches,aliases,summary,days=0,selected_mode=None,period_name='当前周期'):
    modes=(selected_mode,) if selected_mode in (3,4) else (3,4)
    matches=[m for m in matches if m['result']['mode'] in modes]
    title='群数据 · '+period_name+(' · 三麻总览' if selected_mode==3 else ' · 四麻总览' if selected_mode==4 else ' · 对局总览')
    queued=summary.get('new',0)+summary.get('llm_pending',0)+summary.get('local_error',0)
    review=summary.get('review',0);failed=summary.get('download_error',0)
    im,d=base(title,f'正常结算 + 被飞结算    已确认 {len(matches)} 场    识别中 {queued} 张    待核对 {review} 张',1600)
    notice=f'还有 {review} 张待核对、{queued} 张识别中、{failed} 张读取失败；完成确认后才进入统计。' if review or queued or failed else '已确认对局均已计入；新赛季与历史赛季分别统计。'
    d.text((58,222),notice,font=font(25),fill=GOLD if review or queued or failed else MUTED)
    for col,mode in enumerate(modes):
        x=48+col*692;panel_width=1344 if len(modes)==1 else 654;stats=aggregate(matches,mode,aliases);games=sum(s['games'] for s in stats)//mode
        d.rounded_rectangle((x,278,x+panel_width,1190),radius=28,fill=PANEL)
        d.text((x+30,300),f'{"三麻" if mode==3 else "四麻"}  /  {games} 场',font=font(39,True),fill=TEAL)
        d.text((x+30,365),f'{len(stats)} 位玩家 · {sum(s["fly"] for s in stats)} 人次被飞',font=font(28),fill=MUTED)
        if not stats:
            d.text((x+30,500),'本赛季暂无该模式已确认对局',font=font(28),fill=INK)
            d.text((x+30,550),'旧赛季在查询后加 S0 查看',font=font(27),fill=MUTED)
            continue
        y=440
        for metric,label,suffix in [('场次','出场最多','场'),('一位率','一位率最高',''),('二位率','二位率最高',''),('平均顺位','平均顺位最佳',''),('最高','最高持点','点'),('最低','最低持点','点'),('被飞','被飞最多','次')]:
            rows=ranking(stats,metric)
            if rows and (metric not in ('被飞',) or rows[0]['value']>0):
                r=rows[0]
                if r['kind']=='rate':v=f'{float(r["value"]):.1%} · {r["numerator"]}次'
                elif metric=='平均顺位':v=f'{float(r["value"]):.2f}'
                else:v=f'{int(r["value"]):,}{suffix}'
                if metric in ('最高','最低'):label+=f' · 原图 #{r[("max_points" if metric=="最高" else "min_points")+"_source"]}'
                d.text((x+30,y),label,font=font(25),fill=MUTED)
                tie=sum(q['position']==1 for q in rows)>1
                name=fit(d,r['name']+(' 等（并列）' if tie else ''),29,panel_width-300,True)
                d.text((x+30,y+42),name,font=font(29,True),fill=INK)
                d.text((x+panel_width-254,y+42),v,font=font(28,True),fill=RED if metric in ('被飞','最低') else GOLD)
            else:
                d.text((x+30,y),label,font=font(25),fill=MUTED)
                d.text((x+30,y+42),'无人被飞' if metric=='被飞' else '暂无记录',font=font(30),fill=INK)
            y+=104
    d.text((58,1220),'最近录入的已确认结算',font=font(30,True),fill=TEAL)
    latest=sorted(matches,key=lambda m:(m.get('stamp',0),m['id']),reverse=True)[:2]
    for i,m in enumerate(latest):
        line=f'#{m["id"]} · {m["result"]["mode"]}麻 · '+' / '.join(f'{p["name"]} {p["points"]:,}' for p in m['result']['players'])
        d.text((58,1274+i*44),fit(d,line,25,1320),font=font(25),fill=INK)
    if not latest:d.text((58,1274),'暂无已确认结算；识别状态可发送 /群数据 状态 查看。',font=font(26),fill=MUTED)
    d.text((58,1387),'/群数据 个人 昵称    /群数据 一位率 四麻    /群数据 极值 昵称',font=font(28),fill=INK)
    d.text((58,1432),'/群数据 最近 5    /群数据 表格    历史查询加 S0    /群数据 帮助',font=font(27),fill=INK)
    footer(d,1490,group,days,period_name);im.save(path);return path

def leaderboard(path,group,mode,metric,stats,days=0,minimum=1,period_name='当前周期'):
    rows=ranking(stats,metric,minimum)[:10];height=max(690,390+len(rows)*91+145)
    im,d=base(f'群数据 · {period_name} · {"三麻" if mode==3 else "四麻"} · {metric}榜',f'每人至少 {minimum} 场  /  比率同时展示次数与场次  /  相同指标并列',height)
    d.text((65,240),'排名      游戏昵称',font=font(28),fill=MUTED)
    d.text((855,240),'场次',font=font(28),fill=MUTED);d.text((1080,240),metric,font=font(28),fill=MUTED)
    y=295
    if not rows:d.text((65,y+40),'这个筛选范围内还没有已确认的对局。',font=font(35),fill=INK)
    for r in rows:
        d.rounded_rectangle((45,y,1395,y+79),radius=15,fill=PANEL)
        d.text((70,y+15),f'{r["position"]:02d}',font=font(34,True),fill=TEAL)
        d.text((162,y+16),fit(d,r['name'],32,645,True),font=font(32,True),fill=INK)
        d.text((862,y+18),str(r['games']),font=font(29),fill=MUTED)
        if r['kind']=='rate':v=f'{float(r["value"])*100:.1f}% ({r["numerator"]}/{r["games"]})'
        elif metric.startswith('平均'):v=f'{float(r["value"]):.2f}'
        else:v=f'{int(r["value"]):,}'
        d.text((1020,y+18),fit(d,v,28,350,True),font=font(28,True),fill=RED if metric.startswith('被飞') or metric=='最低' else GOLD)
        y+=91
    footer(d,height-112,group,days,period_name);im.save(path);return path

def profile(path,group,mode,stats,name,days=0,period_name='当前周期'):
    im,d=base(f'群数据 · {period_name} · {"三麻" if mode==3 else "四麻"} · 个人档案',fit(ImageDraw.Draw(Image.new('RGB',(1,1))),name,29,1200),1060)
    p=next((s for s in stats if s['name']==name),None)
    if p is None:d.text((65,280),'该玩家在所选赛季、模式和时间范围内暂无已确认对局。',font=font(34),fill=INK)
    else:
        values=[('出场',str(p['games'])),('平均顺位',f'{float(p["avg_place"]):.2f}'),('被飞',f'{p["fly"]}次 / {p["fly"]/p["games"]:.1%}'),('平均持点',f'{float(p["avg_points"]):,.0f}'),('最高持点',f'{p["max_points"]:,}  #{p["max_points_source"]}'),('最低持点',f'{p["min_points"]:,}  #{p["min_points_source"]}')]
        for i,(label,value) in enumerate(values):
            x=48+(i%2)*694;y=240+(i//2)*157
            d.rounded_rectangle((x,y,x+650,y+135),radius=20,fill=PANEL)
            d.text((x+25,y+17),label,font=font(26),fill=MUTED);d.text((x+25,y+62),value,font=font(38,True),fill=TEAL)
        for i in range(1,mode+1):
            x=58+(i-1)*344
            d.text((x,746),f'{i}位   {p["rank"+str(i)]} 次',font=font(31),fill=INK)
            d.text((x,796),f'{p["rank"+str(i)]/p["games"]:.1%}',font=font(42,True),fill=GOLD)
        d.text((58,882),f'最高原图：/群数据 原图 {p["max_points_source"]}    最低原图：/群数据 原图 {p["min_points_source"]}',font=font(27),fill=MUTED)
    footer(d,944,group,days,period_name);im.save(path);return path

def text_card(path,title,lines):
    # Wrap long rows; never shrink an entire card into unreadable chat text.
    dummy=ImageDraw.Draw(Image.new('RGB',(1,1)));wrapped=[]
    for line in lines:
        line=str(line);part=''
        for ch in line:
            if dummy.textlength(part+ch,font=font(28))>1280:
                wrapped.append(part);part=''
            part+=ch
        wrapped.append(part)
    im,d=base(title,'仅统计本群精华中的雀魂完整终局结算截图',max(540,285+len(wrapped)*48))
    for i,line in enumerate(wrapped):d.text((65,244+i*48),line,font=font(28),fill=INK)
    im.save(path);return path
