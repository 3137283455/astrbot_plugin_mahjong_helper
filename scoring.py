"""Versioned result-only rating. No model, network or persistent score state."""
from __future__ import annotations
from fractions import Fraction as F
from .core import signature,validate

VERSION='result90-attendance10-v1'
OFFICIAL_MINIMUM=10
SCORE_NUMBERS={'评分有效场次','顺位评分','持点评分','成绩评分','场次评分项','综合评分','综合评分排名'}
SCORE_FIELDS=[*sorted(SCORE_NUMBERS),'综合评分状态','综合评分版本']

def cents(value):
    """Round an exact nonnegative fraction half up, consistently across views."""
    q,r=divmod(value.numerator*100,value.denominator)
    return q+int(2*r>=value.denominator)

def display(value):return f'{cents(value)/100:.2f}'

def score_matches(matches,mode,aliases=None,minimum=1):
    if mode not in (3,4):raise ValueError('评分模式只能为三麻或四麻')
    aliases=aliases or {};players={};excluded=[];seen=set();games=0
    utility=(100,50,0) if mode==3 else (100,65,35,0)
    for match in matches:
        if match['result'].get('mode')!=mode:continue
        try:
            data=validate({'is_result':True,**match['result']},approve=True)
            if data is None:raise ValueError('非终局')
            total=sum(p['points'] for p in data['players'])
            if total!={3:105000,4:100000}[mode]:raise ValueError('非标准总持点')
            canonical=[]
            for p in data['players']:
                name=p['name'];visited=set()
                while name in aliases and name not in visited:visited.add(name);name=aliases[name]
                canonical.append({**p,'name':name})
            if len({p['name'] for p in canonical})!=mode:raise ValueError('同桌昵称重复')
        except (ValueError,KeyError,TypeError) as exc:
            excluded.append({'id':match.get('id'),'reason':str(exc)});continue
        key=signature({**data,'players':canonical})
        if key in seen:continue
        seen.add(key);games+=1
        for p in canonical:
            s=players.setdefault(p['name'],{'name':p['name'],'games':0,'rank_sum':F(0),'point_sum':F(0)})
            s['games']+=1;s['rank_sum']+=utility[p['place']-1]
            s['point_sum']+=max(F(0),min(F(100),F(50*mode*p['points'],total)))
    rows=[]
    for s in players.values():
        n=s['games']
        if n<minimum:continue
        s['R']=s.pop('rank_sum')/n;s['P']=s.pop('point_sum')/n
        s['performance']=F(65,100)*s['R']+F(35,100)*s['P']
        s['attendance']=F(10*n,n+20)
        s['score']=F(90,100)*s['performance']+s['attendance']
        s['qualified']=n>=OFFICIAL_MINIMUM;s['position']=None;s['tied']=False
        rows.append(s)
    rows.sort(key=lambda r:(not r['qualified'],-cents(r['score']),r['name']))
    official=[r for r in rows if r['qualified']];last=None;position=0
    counts={}
    for r in official:counts[cents(r['score'])]=counts.get(cents(r['score']),0)+1
    for i,r in enumerate(official,1):
        value=cents(r['score'])
        if value!=last:position=i;last=value
        r['position']=position;r['tied']=counts[value]>1
    return {'version':VERSION,'mode':mode,'games':games,'excluded':excluded,'rows':rows,
            'formal_count':len(official),'provisional_count':len(rows)-len(official),'minimum':minimum}

def rank_label(row,data):
    if row['position'] is None:return f'暂定 · {row["games"]}/10场'
    return ('并列' if row['tied'] else '')+f'第{row["position"]}/{data["formal_count"]}名'

def personal_lines(data,name):
    row=next((r for r in data['rows'] if r['name']==name),None)
    if row is None:return [name+'：暂无有效评分']
    return [name+' · '+('正式' if row['qualified'] else '暂定'),
            f'综合评分：{display(row["score"])} / 100 · {rank_label(row,data)}',f'评分有效对局：{row["games"]}场',
            f'顺位评分：{display(row["R"])} · 持点评分：{display(row["P"])}',
            f'成绩分：{display(row["performance"])}',
            f'成绩贡献：{display(F(90,100)*row["performance"])} · 场次项：{display(row["attendance"])} / 10']

def rules_text():
    return '\n'.join(['群数据 · 综合评分规则',
        '满10场正式入榜，1～9场显示暂定分，不占正式名次；无有效对局不生成分数。',
        '四麻顺位分：一位100、二位65、三位35、四位0；三麻：100、50、0。取逐场平均。',
        '每场持点分 = 50 × 最终持点 ÷ 本桌平均持点，先限制为0～100，再取平均。',
        '成绩分 = 顺位分 × 65% + 持点分 × 35%。',
        '综合分 = 成绩分 × 90% + 10 × 场次 ÷（场次 + 20）。',
        '场次项上限10分且增长递减；总分两位小数同分并列，不再以场次破同分。',
        '评分仅纳入标准总持点的完整已确认结算，重复、取消精华和忽略的记录不计入当前赛季。',
        'S0为封存快照，只读查询；默认S1。模式、赛季及天数可放后面，顺序不限。',
        '仅反映本群精华样本的结算表现，不是雀魂官方评级；没有和牌率、放铳率等攻防评分。'])

def online_fields(row):
    if row is None:return {'评分有效场次':0,'综合评分状态':'无有效评分','综合评分版本':VERSION}
    fields={'评分有效场次':row['games'],'顺位评分':cents(row['R'])/100,'持点评分':cents(row['P'])/100,
            '成绩评分':cents(row['performance'])/100,'场次评分项':cents(row['attendance'])/100,
            '综合评分':cents(row['score'])/100,'综合评分状态':'正式' if row['qualified'] else '暂定','综合评分版本':VERSION}
    if row['position'] is not None:fields['综合评分排名']=row['position']
    return fields
