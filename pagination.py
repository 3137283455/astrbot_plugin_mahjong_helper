"""Read-only leaderboard paging. Slice only after global ranks are assigned."""
import re
import unicodedata

PAGE_SIZE=10

def page_number(token):
    token=unicodedata.normalize('NFKC',str(token))
    match=re.fullmatch(r'第?([0-9]+|[零一二三四五六七八九十百两]+)页',token)
    if not match:return None
    value=match[1]
    if value.isdigit():n=int(value)
    else:
        digits=dict(zip('零一二三四五六七八九两',(0,1,2,3,4,5,6,7,8,9,2)))
        n=0;part=0
        for ch in value:
            if ch in digits:part=digits[ch]
            else:n+=(part or 1)*{'十':10,'百':100}[ch];part=0
        n+=part
    if not 1<=n<=10000:raise ValueError('页码范围为1～10000')
    return n

def page_rows(rows,page=1):
    total=len(rows);pages=max(1,(total+PAGE_SIZE-1)//PAGE_SIZE)
    if not 1<=page<=pages:raise ValueError(f'共{total}人、{pages}页，请选择第1～{pages}页。')
    return {'rows':rows[(page-1)*PAGE_SIZE:page*PAGE_SIZE], 'page':page,'pages':pages,'total':total}

def navigation(info,query):
    lines=[f'第{info["page"]}/{info["pages"]}页 · 共{info["total"]}人 · 每页10人']
    if info['page']<info['pages']:lines.append(f'下一页：/群数据 {query} 第{info["page"]+1}页')
    elif info['page']>1:lines.append(f'上一页：/群数据 {query} 第{info["page"]-1}页')
    else:lines.append('当前已展示完整榜单。')
    return lines
