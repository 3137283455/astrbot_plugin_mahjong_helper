"""Compact, factual Mortal report cards."""
from pathlib import Path
from PIL import Image,ImageDraw
from .stat_card import _font,_fit
from .replay_core import hand_name,SEAT_NAMES

BG='#101821';PANEL='#1d2b38';TEXT='#edf2f7';MUTED='#a1b3c5';TEAL='#53d6cb';GOLD='#ffcc7a'

def render_replay_card(summary,output,job_id,view='result',hand=None):
    if hand is not None:
        summary=dict(summary)
        summary['hands']=[h for h in summary['hands'] if h['kyoku']==hand]
        summary['total']=sum(h['total'] for h in summary['hands'])
        summary['matches']=sum(h['matches'] for h in summary['hands'])
        summary['accordance']=summary['matches']/summary['total'] if summary['total'] else 0
        summary['difference_count']=summary['total']-summary['matches']
    differences=summary['differences']
    if hand is not None:differences=[d for d in differences if d['kyoku']==hand]
    shown=differences[:(8 if view!='result' else 3)]
    extra=40 if len(summary['hands'])>12 else 0
    height=850+len(shown)*98+extra
    image=Image.new('RGB',(1400,height),BG);draw=ImageDraw.Draw(image)
    def text(x,y,value,size=30,color=TEXT,bold=False,width=1250):
        font=_font(size,bold);draw.text((x,y),_fit(draw,str(value),font,width),font=font,fill=color)
    draw.rounded_rectangle((40,36,1360,215),radius=24,fill=PANEL)
    draw.rounded_rectangle((65,65,76,173),radius=5,fill=TEAL)
    text(100,59,'单牌谱复盘 · '+('分歧详情' if view=='differences' else hand_name(hand)+'分局' if hand is not None else 'Mortal'),43,bold=True)
    identity=(f"开局{'东南西北'[summary['seat']]}家（报告昵称匿名）" if summary.get('anonymous') else f"{summary['nickname']}  /  开局{'东南西北'[summary['seat']]}家")
    text(100,126,f"{identity}  /  模型 {summary['model']}",30,MUTED,width=1210)
    for x,label,value,color in ((45,'AI 一致率',f"{summary['accordance']:.1%}",TEAL),(495,'一致 / 评估决策',f"{summary['matches']} / {summary['total']}",TEXT),(945,'分歧决策',str(summary['difference_count']),GOLD)):
        draw.rounded_rectangle((x,244,x+410,411),radius=20,fill=PANEL)
        text(x+24,263,label,26,MUTED,width=360);text(x+24,312,value,48,color,True,width=360)
    rating='未提供' if summary['rating'] is None else f"{summary['rating']:.2f}"
    text(60,438,f'全场参考 Rating：{rating}  ·  任务 #{job_id}',25,MUTED)
    hands=[h for h in summary['hands'] if hand is None or h['kyoku']==hand]
    text(60,489,'分局一致率',30,bold=True)
    chunks=[f"{hand_name(h['kyoku'],h['honba'])}  {h['matches']/h['total']:.0%}" if h['total'] else f"{hand_name(h['kyoku'],h['honba'])}  —" for h in hands]
    for row in range(4 if extra else 3):text(60,535+row*38,'    '.join(chunks[row*4:(row+1)*4]),25,TEAL)
    if len(chunks)>16:text(60,689,f'另有 {len(chunks)-16} 个本场，可用“复盘 分局 东1”等命令查看。',23,MUTED)
    text(60,691+extra,'值得回看的分歧（按模型选项排序）',30,bold=True)
    if not shown:text(60,744+extra,'此范围内没有分歧决策。',28,TEAL)
    for i,item in enumerate(shown):
        y=741+extra+i*98
        draw.rounded_rectangle((50,y,1350,y+86),radius=12,fill=PANEL)
        rank=f" · 实际选项第{item['choice_rank']}" if item['choice_rank'] else ''
        text(70,y+8,f"{hand_name(item['kyoku'],item['honba'])} · 第{item['turn']}巡{rank}",23,MUTED)
        text(70,y+42,f"实际：{item['actual']}       Mortal：{item['expected']}",29,TEAL,width=1230)
    text(60,height-78,'一致率反映模型决策吻合程度；分歧不等于失误，Rating 仅供参考。',24,MUTED)
    text(60,height-42,'详单：/复盘 分歧    分局：/复盘 分局 南2    记录：/复盘 记录',23,MUTED)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);image.save(output)
    return output
