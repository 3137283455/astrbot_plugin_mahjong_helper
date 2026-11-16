from __future__ import annotations
import asyncio
import hashlib
import json
import time
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent,MessageChain,filter
from astrbot.api.star import Star,Context,StarTools
from astrbot.core.star.filter.command import GreedyStr
from .client import Client,RadarError
from .core import Query,State,parse,select,key,page_number
from .cards import render,stamp

INSTRUCTIONS='''【优惠雷达】用户查询优惠、限免、Epic免费游戏、软件折扣时使用perkradar_query，query填写/优惠后的参数，例如游戏、Epic、搜索 云服务器、第2页。查询只读已有抓取数据，不要改用娱乐API。管理员明确要求订阅、取消订阅或立即抓取时使用perkradar_manage；权限由代码检查。线索不等于已核实可领取，不编造价格或领取资格。工具会直接发送图片与原文链接，不重复解释执行结果。'''

class PerkRadarPlugin(Star):
    def __init__(self,context:Context,config=None):
        super().__init__(context);self.config=config or {}
        self.folder=Path(StarTools.get_data_dir());self.folder.mkdir(parents=True,exist_ok=True)
        self.cards=self.folder/'cards';self.cards.mkdir(exist_ok=True)
        self.state=State(self.folder/'subscriptions.db');self.client=None
        self.jobs=set();self.contexts={};self.primed=set();self.started=time.time()*1000
        self.refresh_task=None;self.refresh_at=0;self.poll_error_at=0
    async def initialize(self):
        try:self.client=Client(self.config.get('credentials_file','/bot/perkradar/deploy/client.json'))
        except (OSError,ValueError,KeyError):logger.error('优惠雷达本机凭据未就绪；可用 /优惠 帮助。')
        if self.config.get('enabled',True):self.spawn(self.poll())
    def spawn(self,coroutine):
        task=asyncio.create_task(coroutine);self.jobs.add(task);task.add_done_callback(self.jobs.discard);return task
    async def terminate(self):
        tasks=list(self.jobs)
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        self.state.close()
    def _client(self):
        if not self.config.get('enabled',True):raise RadarError('优惠功能暂未开启。')
        if self.client is None:raise RadarError('优惠服务凭据尚未配置。')
        return self.client
    def _scope(self,event):return (event.unified_msg_origin,str(event.get_sender_id()))
    async def admin(self,event):
        if callable(getattr(event,'is_admin',None)) and event.is_admin():return True
        if event.get_group_id() and str(event.get_sender_id()).isdigit() and hasattr(event,'bot'):
            try:
                obj=await asyncio.wait_for(event.bot.call_action('get_group_member_info',group_id=int(event.get_group_id()),user_id=int(event.get_sender_id()),no_cache=False),5)
                return isinstance(obj,dict) and obj.get('role') in ('admin','owner')
            except Exception:pass
        return False
    def claim(self,event):
        if event.get_platform_name()!='aiocqhttp':return False
        if event.get_group_id() and not any(isinstance(p,Comp.At) and str(p.qq)==str(event.get_self_id()) for p in event.get_messages()):return False
        if not event.get_extra('mahjong_ai_request') or event.get_extra('mahjong_ai_action_used'):return False
        event.set_extra('mahjong_ai_action_used',True);event.set_extra('mahjong_ai_tool_used',True);return True
    async def image(self,title,subtitle,items=None,lines=None,footer=''):
        digest=hashlib.sha256(json.dumps([title,subtitle,items,lines,footer],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        path=self.cards/(digest+'.png')
        if not path.exists():await asyncio.to_thread(render,path,title,subtitle,items,lines,footer)
        # Old cache files are expendable; retain recent output while sends are in flight.
        for old in self.cards.glob('*.png'):
            if old.stat().st_mtime<time.time()-7*86400:
                with suppress(OSError):old.unlink()
        return path
    def chain(self,path,items,bot_id):
        picture=Comp.Image.fromFileSystem(str(path))
        if not items:return MessageChain(chain=[picture])
        links='\n\n'.join(str(i)+'. '+item['title']+'\n'+item['url'] for i,item in enumerate(items,1))
        nodes=[Comp.Node(content=[picture],uin=str(bot_id),name='慧慧 · 优惠雷达'),Comp.Node(content=[Comp.Plain(links)],uin=str(bot_id),name='原文链接')]
        return MessageChain(chain=[Comp.Nodes(nodes)])
    async def send(self,event,chain):
        # Retry the unchanged full image/forward only, never replace it with text.
        for attempt in range(2):
            try:
                await event.send(chain);return True
            except Exception as exc:
                logger.warning('优惠发送未确认 attempt=%s type=%s',attempt+1,type(exc).__name__)
                if attempt==0:await asyncio.sleep(2)
        return False
    async def notice(self,event,title,lines):
        path=await self.image(title,'慧慧 · PerkRadar',lines=lines)
        return await self.send(event,self.chain(path,[],event.get_self_id()))
    async def show(self,event,text):
        q=parse(text);scope=self._scope(event);now=time.monotonic()
        for k,(when,_) in list(self.contexts.items()):
            if now-when>600:self.contexts.pop(k,None)
        if text.strip() in ('下一页','上一页') or page_number(text.strip()):
            previous=self.contexts.get(scope)
            if previous is None:raise ValueError('请先查询优惠列表，再使用翻页。')
            q=replace(previous[1],page=max(1,previous[1].page+(1 if text.strip()=='下一页' else -1)) if text.strip() in ('下一页','上一页') else page_number(text.strip()))
        board=await self._client().board();items=select(board['deals'],q);pages=max(1,(len(items)+5)//6)
        if q.page>pages:raise ValueError('当前筛选共'+str(pages)+'页。')
        current=items[(q.page-1)*6:q.page*6]
        health=board.get('sourceHealth') or {};status=f"抓取 {stamp(board.get('lastUpdatedAt'))}  ·  来源 {health.get('ok',0)}/{health.get('total',7)}"
        if (board.get('freshness') or {}).get('stale'):status+='  ·  数据较旧'
        lines=[]
        if not current:lines=['来源抓取失败，暂时无法取得本次优惠线索。' if board.get('roundStatus')=='failed' else '当前筛选没有匹配的在架线索。']
        path=await self.image('优惠雷达',status,items=current,lines=lines,footer=f'第{q.page}/{pages}页 · 共{len(items)}条   /优惠 下一页   /优惠 帮助')
        if await self.send(event,self.chain(path,current,event.get_self_id())):
            if len(self.contexts)>1024:self.contexts.pop(next(iter(self.contexts)))
            self.contexts[scope]=(time.monotonic(),q)
    async def status(self,event):
        board=await self._client().board();h=board.get('sourceHealth') or {}
        lines=[f"最近抓取：{stamp(board.get('lastUpdatedAt'))}",f"成功来源：{h.get('ok',0)}/{h.get('total',7)}    在架线索：{board.get('activeCount',0)}",'上游自动抓取约每2小时；群推送只在明确订阅后开启。']
        for s in board.get('sources',[]):
            label='正常' if s.get('lastOk') else '尚未抓取' if s.get('lastOk') is None else '暂不可用'
            lines.append(str(s.get('name',''))+'：'+label)
        subscriptions=self.state.filters(event.unified_msg_origin)
        lines.append('本会话订阅：'+('、'.join(x or '全部' for x in subscriptions) if subscriptions else '未开启'))
        await self.notice(event,'优惠雷达 · 状态',lines)
    async def refresh(self,event):
        try:
            await self._client().refresh();await self.status(event)
        except Exception as exc:
            logger.warning('优惠后台刷新失败 type=%s',type(exc).__name__)
            await self.notice(event,'优惠刷新未完成',[str(exc) if isinstance(exc,RadarError) else '抓取失败，可用 /优惠 状态 查看。'])
    async def handle(self,event,text):
        if event.get_platform_name()!='aiocqhttp':return
        text=str(text).strip();parts=text.split(maxsplit=1);action=parts[0] if parts else '';tail=parts[1] if len(parts)>1 else ''
        try:
            if action in ('帮助','菜单','help'):
                await self.notice(event,'优惠雷达 · 菜单',['/优惠                        最新优惠线索','/优惠 Epic                  Epic 当前限免','/优惠 搜索 云服务器        搜索标题关键词','/优惠 游戏 第2页            分类筛选、翻页','/优惠 状态                   抓取时间与来源状态','/优惠 订阅 游戏              管理员开启本群推送','/优惠 取消订阅 游戏         取消；不填分类则全取消','/优惠 刷新                   管理员后台刷新']);return
            if action in ('状态','版本'):await self.status(event);return
            if action in ('订阅','取消订阅','刷新'):
                if not await self.admin(event):raise ValueError('此操作仅限群主、群管理员或机器人管理员。')
                if action=='刷新':
                    if self.refresh_task is not None and not self.refresh_task.done():raise ValueError('已有抓取任务正在运行。')
                    if time.monotonic()-self.refresh_at<60:raise ValueError('刷新正在冷却，请稍后再试。')
                    self._client();self.refresh_at=time.monotonic();self.refresh_task=self.spawn(self.refresh(event))
                    await self.notice(event,'优惠后台刷新',['正在抓取，完成后发送来源状态；其他命令可正常使用。']);return
                q=parse(tail)
                if q.term or q.page!=1:raise ValueError('订阅只接受来源或分类，例如 /优惠 订阅 游戏。')
                query=' '.join(x for x in (q.source,q.category) if x);origin=event.unified_msg_origin
                if action=='取消订阅':
                    self.state.unsubscribe(origin,query);await self.notice(event,'优惠订阅已取消',[query or '本会话全部优惠订阅']);return
                if len(self.state.targets())>=20 and not self.state.filters(origin):raise ValueError('订阅会话数量已达上限。')
                if len(self.state.filters(origin))>=5:raise ValueError('本会话最多5个订阅条件。')
                board=await self._client().board();items=select(board['deals'],Query())
                self.state.subscribe(origin,str(event.get_self_id()),str(event.get_sender_id()),query,items);self.primed.add(origin)
                await self.notice(event,'优惠订阅已开启',[query or '全部分类','从后续新增线索开始推送，不补发历史。']);return
            await self.show(event,text)
        except (ValueError,RadarError) as exc:await self.notice(event,'优惠雷达',[str(exc)])
        except Exception as exc:
            logger.exception('优惠命令处理失败 type=%s',type(exc).__name__)
            await self.notice(event,'优惠暂时不可用',['请稍后重试，或由管理员查看 /优惠 状态。'])
    @filter.command('优惠')
    async def command(self,event:AstrMessageEvent,query:GreedyStr=''):
        event.stop_event();await self.handle(event,query)
    @filter.llm_tool(name='perkradar_query')
    async def query_ai(self,event:AstrMessageEvent,query:str=''):
        """查询优惠雷达并发送图片和原文链接，不执行订阅或刷新。

        Args:
            query(string): 来源、分类、搜索和页码，例如Epic、游戏、第2页、搜索 云服务器；帮助或状态也可。
        """
        if not self.claim(event):return
        if query.split(maxsplit=1)[:1] and query.split(maxsplit=1)[0] in ('订阅','取消订阅','刷新'):
            await self.notice(event,'优惠管理',['管理操作请使用原生命令，或明确请求优惠管理工具。']);return
        await self.handle(event,query)
    @filter.llm_tool(name='perkradar_manage')
    async def manage_ai(self,event:AstrMessageEvent,action:str,query:str=''):
        """管理员明确要求时管理当前会话优惠订阅或刷新；代码验证权限。

        Args:
            action(string): subscribe订阅、unsubscribe取消订阅、refresh刷新。
            query(string): 来源或分类，例如游戏、Epic；取消时留空为全取消。
        """
        if not self.claim(event):return
        commands={'subscribe':'订阅','unsubscribe':'取消订阅','refresh':'刷新'}
        if action not in commands:await self.notice(event,'优惠管理',['不支持的管理操作。']);return
        await self.handle(event,commands[action]+' '+query)
    async def poll_once(self):
        targets=self.state.targets()
        if not targets:return  # no subscriptions, no fetch and no background messages
        board=await self._client().board(force=True);items=select(board['deals'],Query())
        if board.get('roundStatus')=='failed':return
        for target in targets:
            origin=target['origin']
            if origin not in self.primed:
                self.state.baseline(origin,items);self.primed.add(origin);continue
            fresh=self.state.unseen(origin,items)
            # A restart never replays offers from before this plugin session.
            fresh=[i for i in fresh if i['publishedAt'] is None or i['publishedAt']>=self.started]
            selected={key(i):i for f in self.state.filters(origin) for i in select(fresh,parse(f))}
            if not selected:continue
            rows=list(selected.values());current=rows[:12]
            path=await self.image('优惠雷达 · 新增线索',f'本轮匹配{len(rows)}条 · 更多使用 /优惠 查询',items=current)
            chain=self.chain(path,current,target['bot']);status='unknown'
            for attempt in range(2):
                if not self.state.filters(origin):break
                try:
                    matched=await self.context.send_message(origin,chain)
                    if matched is False:raise RuntimeError('目标平台未连接')
                    status='sent';break
                except Exception as exc:
                    logger.warning('优惠订阅发送未确认 attempt=%s type=%s',attempt+1,type(exc).__name__)
                    if attempt==0:await asyncio.sleep(2)
            self.state.delivered(origin,rows,status)
    async def poll(self):
        while True:
            try:await self.poll_once()
            except asyncio.CancelledError:raise
            except Exception as exc:
                if time.monotonic()-self.poll_error_at>300:
                    logger.warning('优惠轮询暂不可用 type=%s',type(exc).__name__);self.poll_error_at=time.monotonic()
            await asyncio.sleep(max(30,int(self.config.get('poll_seconds',60))))
