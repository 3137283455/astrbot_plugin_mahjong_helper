"""Collect only responses produced by the normal Amae-Koromo player page."""
import asyncio,re,time
from urllib.parse import urlsplit

CHART='https://amae-koromo.sapk.ch'
API='https://5-data.amae-koromo.com/api/v2/pl4'

async def fetch_recent(worker):
    request=worker.current['request'];player=request['player'];want=request['count'];modes=request['modes']
    if not isinstance(player,str) or not 1<=len(player)<=100 or type(want) is not int or not 1<=want<=10 or not modes or any(type(m) is not int or m not in (9,12,16) for m in modes):raise ValueError('最近对局任务参数无效')
    await worker.progress('resolving');page=await worker.get_page();await worker.ensure_valid()
    nickname=player
    if player.isdigit():uid=int(player)
    else:
        await page.goto(CHART,wait_until='domcontentloaded',timeout=60000)
        deadline=time.monotonic()+600;players=None
        while time.monotonic()<deadline:
            await worker.ensure_valid()
            if page.is_closed():raise RuntimeError('牌谱屋页面已关闭，请 /复盘 重试')
            response=await page.evaluate('''async ([api, term]) => {
                try {const r=await fetch(`${api}/search_player/${encodeURIComponent(term)}`, {cache:'no-store',signal:AbortSignal.timeout(12000)});
                return {status:r.status,data:r.ok ? await r.json() : null};} catch(e) {return {status:0};}
            }''',[API,player])
            if response.get('status')==200:
                players=response.get('data');break
            if worker.current.get('phase')!='verification':await worker.progress('verification')
            await asyncio.sleep(5)
        if players is None:raise RuntimeError('牌谱屋昵称搜索尚未恢复，请在电脑专用浏览器检查网络或正常完成验证，再 /复盘 重试')
        if not isinstance(players,list):raise ValueError('牌谱屋昵称搜索返回格式不正确')
        exact=[p for p in players if isinstance(p,dict) and p.get('nickname')==player]
        candidates=exact or [p for p in players if isinstance(p,dict)]
        if not candidates:raise ValueError('牌谱屋没有找到该昵称，仅收录金之间及以上玩家')
        if len(candidates)!=1:
            names='；'.join(str(p.get('nickname',''))+'（UID '+str(p.get('id',''))+'）' for p in candidates[:5])
            raise ValueError('昵称存在多个候选，请改用UID提交：'+names)
        uid=int(candidates[0]['id']);nickname=str(candidates[0].get('nickname') or uid)
    if not 0<uid<10**15:raise ValueError('玩家UID无效')
    collected={};pending=set();observed={'response':False,'cap':False}

    async def receive(response):
        try:
            parsed=urlsplit(response.url)
            if parsed.scheme!='https' or not re.fullmatch(r'(?:[a-z0-9-]+\.)+(?:amae-koromo\.com)',parsed.hostname or ''):return
            if not re.match(r'/api/v2/pl4/player_records/'+str(uid)+r'/',parsed.path):return
            if response.status in (403,429):observed['cap']=True;return
            if response.status!=200:return
            data=await response.json()
            if not isinstance(data,list):return
            observed['response']=True
            for row in data[:450]:
                if not isinstance(row,dict) or row.get('modeId') not in modes or not row.get('uuid'):continue
                if len(collected)>=450 and row['uuid'] not in collected:continue
                # Only public game data needed by the server; no tokens or browser storage.
                collected[row['uuid']]={k:row.get(k) for k in ('uuid','modeId','startTime','players')}
        except Exception:pass

    def schedule(response):
        task=asyncio.create_task(receive(response));pending.add(task);task.add_done_callback(pending.discard)

    page.on('response',schedule)
    try:
        for mode in modes:
            observed.update(response=False,cap=False);await worker.ensure_valid();await worker.progress('records')
            await page.goto(f'{CHART}/player/{uid}/{mode}',wait_until='domcontentloaded',timeout=60000)
            begin=time.monotonic();last=-1;idle=0
            while time.monotonic()-begin<600:
                await worker.ensure_valid()
                if page.is_closed():raise RuntimeError('牌谱屋页面已关闭，请 /复盘 重试')
                count=sum(r['modeId']==mode for r in collected.values())
                if count>=want:break
                idle=idle+1 if count==last else 0;last=count
                if observed['response'] and idle>=3:break
                if time.monotonic()-begin>20 and observed['cap'] and worker.current.get('phase')!='verification':await worker.progress('verification')
                if time.monotonic()-begin>60 and not observed['cap']:raise RuntimeError('牌谱屋页面未加载对局列表，请检查电脑网络后 /复盘 重试')
                await asyncio.sleep(1)
            else:raise RuntimeError('等待牌谱屋验证超过10分钟，任务已结束；正常完成验证后可 /复盘 重试')
        records=sorted(collected.values(),key=lambda r:r.get('startTime') or 0,reverse=True)[:want]
        if not records:raise ValueError('网页未取得可分析的四麻东南对局，仅收录金之间及以上')
        return {'uid':str(uid),'nickname':nickname,'records':records}
    finally:
        page.remove_listener('response',schedule)
        for task in list(pending):task.cancel()
        if pending:await asyncio.gather(*list(pending),return_exceptions=True)
