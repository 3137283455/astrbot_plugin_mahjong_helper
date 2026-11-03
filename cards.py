"""High-resolution, deterministic Chinese cards; data never interpreted as markup."""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from .core import aggregate, ranking, profile_positions
from .scoring import display as score_display,rank_label,personal_lines

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

def profile_text(group,mode,stats,name,days=0,period_name='当前周期',minimum=1,score_data=None):
    stats=[s for s in stats if s['games']>=minimum]
    p=next((s for s in stats if s['name']==name),None)
    title=f'群数据 · {period_name} · {mode}麻 · {name}'
    if p is None:return title+'\n所选条件下暂无该玩家已确认对局。'
    positions=profile_positions(stats,name,mode,minimum)
    def rank(metric):return positions[metric]['label']
    lines=[title,f'出场：{p["games"]}场（{rank("场次")}）',f'平均顺位：{float(p["avg_place"]):.2f}（{rank("平均顺位")}）',
        f'被飞：{p["fly"]}次（{rank("被飞")}） / {p["fly"]/p["games"]:.1%}（{rank("被飞率")}）',
        f'平均持点：{float(p["avg_points"]):,.0f}（{rank("平均持点")}）',
        f'最高持点：{p["max_points"]:,}（{rank("最高")}）· 原图 #{p["max_points_source"]}',
        f'最低持点：{p["min_points"]:,}（{rank("最低")}）· 原图 #{p["min_points_source"]}']
    for i in range(1,mode+1):lines.append(f'{i}位：{p["rank"+str(i)]}次（{rank("一二三四"[i-1]+"位")}） / {p["rank"+str(i)]/p["games"]:.1%}（{rank("一二三四"[i-1]+"位率")}）')
    if score_data is not None:lines.extend(personal_lines(score_data,name)[:3])
    lines.append(f'同筛选玩家 {len(stats)} 位，至少{minimum}场'+(f'，近{days}天' if days else '')+'；平均顺位、最低持点从低到高，其余从高到低。')
    return '\n'.join(lines)


def profile(path,group,mode,stats,name,days=0,period_name='当前周期',minimum=1,score_data=None):
    stats=[s for s in stats if s['games']>=minimum]
    im=Image.new('RGB',(W,1370),BG);d=ImageDraw.Draw(im)
    d.rectangle((0,0,W,282),fill=PANEL)
    d.text((72,40),'群数据  /  个人档案',font=font(25,True),fill=MUTED)
    def line(x,y,text,size,width,bold=False,color=TEAL,anchor=None):
        text=str(text)
        while size>20 and d.textlength(text,font=font(size,bold))>width:size-=1
        text=fit(d,text,size,width,bold)
        d.text((x,y),text,font=font(size,bold),fill=color,anchor=anchor)
        return d.textlength(text,font=font(size,bold))
    name_width=line(72,106,name,65,580 if score_data is not None else 1090,True,INK)
    score_row=next((r for r in score_data['rows'] if r['name']==name),None) if score_data is not None else None
    if score_data is not None:
        x=max(360,int(72+name_width+28))
        d.rounded_rectangle((x,116,x+220,182),radius=14,fill='#233442')
        d.rounded_rectangle((x+236,116,x+476,182),radius=14,fill='#233442')
        line(x+110,149,'综合 '+score_display(score_row['score']) if score_row else '暂无评分',29,200,True,TEAL,'mm')
        line(x+356,149,rank_label(score_row,score_data) if score_row else '0有效场',25,220,True,GOLD,'mm')
    scope=period_name+'   /   '+('三麻' if mode==3 else '四麻')+'   /   '+(f'近{days}天' if days else '全部已确认对局')
    line(75,204,scope,29,1080,color=MUTED)
    line(1368,139,len(stats),58,240,True,GOLD,'ra')
    d.text((1368,210),'本次参排人数',font=font(25),fill=MUTED,anchor='ra')
    p=next((s for s in stats if s['name']==name),None)
    if p is None:
        d.rounded_rectangle((64,326,1376,1026),radius=22,fill=PANEL)
        line(94,382,'该玩家在所选赛季、模式和时间范围内暂无已确认对局。',34,1220,color=INK)
    else:
        positions=profile_positions(stats,name,mode,minimum)
        def rank(metric):
            row=positions[metric]
            return ('并列第' if row['tied'] else '第')+str(row['position'])+'名'
        basic=[('出场次数',str(p['games'])+' 场','场次'),('平均顺位',f'{float(p["avg_place"]):.2f}','平均顺位'),
               ('平均持点',f'{float(p["avg_points"]):,.0f}','平均持点'),('被飞次数',str(p['fly'])+' 次','被飞'),
               ('被飞率',f'{p["fly"]/p["games"]:.1%}','被飞率'),('最高持点',f'{p["max_points"]:,}','最高'),('最低持点',f'{p["min_points"]:,}','最低')]
        places=[]
        for i in range(1,mode+1):
            label='一二三四'[i-1]+'位';count=p['rank'+str(i)]
            places.extend([(label+'次数',str(count)+' 次',label),(label+'率',f'{count/p["games"]:.1%}',label+'率')])
        row_count=max(len(basic),len(places));step=67
        for col,(title,items) in enumerate([('综合表现',basic),('顺位表现',places)]):
            left=64+col*674;right=left+638;top=326
            d.rounded_rectangle((left,top,right,1026),radius=22,fill=PANEL)
            d.rounded_rectangle((left+24,top+28,left+31,top+65),radius=3,fill=TEAL)
            d.text((left+47,top+27),title,font=font(33,True),fill=INK)
            d.text((left+30,top+107),'指标',font=font(24),fill=MUTED)
            d.text((left+332,top+107),'数据',font=font(24),fill=MUTED,anchor='ra')
            d.text((right-28,top+107),'群内排名',font=font(24),fill=MUTED,anchor='ra')
            for i,(label,value,metric) in enumerate(items):
                y=top+151+i*step
                d.line((left+25,y-10,right-25,y-10),fill='#304050',width=1)
                line(left+30,y+19,label,25,160,color='#B5C1CF',anchor='lm')
                line(left+332,y+19,value,29,132,True,TEAL,'rm')
                line(right-28,y+19,rank(metric),24,220,True,GOLD,'rm')
            if len(items)<row_count:
                y=top+151+(row_count-1)*step
                d.rounded_rectangle((left+23,y-5,right-23,y+49),radius=10,fill='#233442')
                d.text((left+36,y+20),'结算原图',font=font(23),fill='#B5C1CF',anchor='lm')
                line(right-35,y+20,f'最高 #{p["max_points_source"]}   /   最低 #{p["min_points_source"]}',23,420,color=TEAL,anchor='rm')
        d.rounded_rectangle((64,1062,1376,1142),radius=16,fill='#233442')
        d.text((94,1101),'查看结算原图',font=font(25,True),fill='#B5C1CF',anchor='lm')
        line(1344,1101,f'/群数据 原图 {p["max_points_source"]}       /群数据 原图 {p["min_points_source"]}',25,1040,color=TEAL,anchor='rm')
    line(72,1192,f'排名：同赛季、同模式、同筛选条件；共 {len(stats)} 位玩家，至少 {minimum} 场。',23,1296,color=MUTED)
    line(72,1230,'平均顺位、最低持点由低到高，其余由高到低；同值并列跳位。',23,1296,color=MUTED)
    if score_data is not None:
        note=(f'综合评分有效{score_row["games"]}场，正式排名共{score_data["formal_count"]}人' if score_row else '所选范围暂无可评分标准终局')
        line(72,1267,'综合分：成绩90%＋场次10%；满10场入榜。'+note+'。',22,1296,color=MUTED)
    line(72,1300,'仅统计本群精华中的已确认结算，三麻 / 四麻独立统计。',23,1040,color=MUTED)
    line(1368,1300,f'群 {group}',23,270,color=MUTED,anchor='ra')
    im.save(path);return path


def score_board_text(data,period_name,days=0):
    official=[r for r in data['rows'] if r['qualified']];provisional=[r for r in data['rows'] if not r['qualified']]
    lines=[f'群数据 · {period_name} · {data["mode"]}麻 · 综合表现榜'+(f' · 近{days}天收录' if days else ''),
           f'正式{len(official)}人，暂定{len(provisional)}人；满10场入榜，筛选至少{data["minimum"]}场。',
           *[f'{r["position"]}. {r["name"]} · {score_display(r["score"])}分 · {r["games"]}场' for r in official[:10]]]
    if not official:lines.append('暂无满10场的正式玩家。')
    if provisional:
        lines.append('暂定分（不占正式名次）：')
        lines.extend(f'{r["name"]} · {score_display(r["score"])}分 · {r["games"]}/10场' for r in provisional[:5])
    lines.extend(['综合分=成绩分×90%＋10×场次÷（场次＋20）；成绩分=顺位65%＋持点35%。',
                  '仅反映本群精华样本，三麻四麻、S0与S1独立统计。'])
    return '\n'.join(lines)

def score_board(path,group,data,days=0,period_name='当前周期'):
    official=[r for r in data['rows'] if r['qualified']];provisional=[r for r in data['rows'] if not r['qualified']]
    shown=official[:10];pending=provisional[:5 if official else 10]
    height=650+len(shown)*82+(len(pending)*82+95 if pending else 0)
    if not shown:height+=82
    im,d=base(f'群数据 · {period_name} · {data["mode"]}麻 · 综合表现榜',
              f'成绩90%＋场次10%   /   满10场正式入榜   /   正式{len(official)}人 · 暂定{len(provisional)}人',height)
    columns=[(90,'名次'),(190,'游戏昵称'),(805,'场次'),(975,'成绩分'),(1135,'场次项'),(1335,'综合分')]
    def header(y):
        for x,label in columns:d.text((x,y),label,font=font(25),fill=MUTED,anchor='ra' if x>=805 else None)
    def row(r,y,qualified):
        d.rounded_rectangle((45,y,1395,y+72),radius=14,fill=PANEL)
        d.text((78,y+19),str(r['position']) if qualified else '—',font=font(28,True),fill=TEAL if qualified else MUTED)
        d.text((190,y+19),fit(d,r['name'],28,500,True),font=font(28,True),fill=INK)
        for x,value,color,bold in [(805,str(r['games']),MUTED,False),(975,score_display(r['performance']),MUTED,False),
                                    (1135,score_display(r['attendance']),MUTED,False),(1335,score_display(r['score']),GOLD,True)]:
            d.text((x,y+19),value,font=font(28,bold),fill=color,anchor='ra')
    header(240);y=292
    if not shown:
        d.text((65,y),'暂无满10场的正式玩家；下方仅展示暂定分。' if pending else '所选范围暂无可评分的标准终局。',font=font(29),fill=MUTED)
        y+=82
    for r in shown:row(r,y,True);y+=82
    if pending:
        d.text((65,y+20),'暂定区 · 不足10场，不占正式名次',font=font(27,True),fill=TEAL);y+=76
        for r in pending:row(r,y,False);y+=82
    notes=[f'正式共{len(official)}人，展示前10；暂定共{len(provisional)}人，展示前{len(pending)}。同分并列，不以场次破同分。',
           '综合分=成绩分×90%＋10×场次÷（场次＋20）；成绩分=顺位65%＋持点35%。',
           f'评分纳入标准终局{data["games"]}场；本次筛选至少{data["minimum"]}场。'+(f'近{days}天按精华收录时间筛选。' if days else ''),
           f'本模式另有{len(data["excluded"])}场不符合评分条件；/群数据 评分 昵称 查看分解。']
    for i,note in enumerate(notes):d.text((58,height-306+i*42),fit(d,note,23,1320),font=font(23),fill=MUTED)
    footer(d,height-117,group,days,period_name);im.save(path);return path

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
