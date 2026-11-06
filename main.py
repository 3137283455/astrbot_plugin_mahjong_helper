from __future__ import annotations
import asyncio
import json
import shlex
import re
import time
import uuid
from copy import deepcopy
from contextlib import suppress
from datetime import datetime,timezone,timedelta
from pathlib import Path
from aiocqhttp.exceptions import ActionFailed
import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent,filter
from astrbot.api.star import Star,Context,StarTools
from astrbot.core.star.filter.command import GreedyStr
from . import cards
from .core import ALIASES,METRICS,aggregate,normalize_name,parse_json,validate,ranking
from .store import Store
from .source import EssenceSource,unwrap
from .recognizer import Recognizer
from . import periods
from .nicknames import SelectionCache,candidates,choice
from .scoring import score_matches,personal_lines,rules_text
from .pagination import page_number,page_rows,navigation

HELP=[
 '/群数据                         三麻 / 四麻总览',
 '/群数据 被飞榜 四麻          按被飞次数排行（默认）',
 '/群数据 被飞率 四麻          明确查询被飞比例，参数顺序可换',
 '/群数据 一位 三麻             一位次数榜',
 '/群数据 二位率 四麻 30天 5场   近30天，至少5场',
 '/群数据 个人 "游戏昵称" 三麻   玩家档案',
 '/群数据 综合榜 四麻 S0         综合表现榜；默认S1，满10场正式入榜',
 '/群数据 综合榜 四麻 S0 第2页   每页10人；各榜单页码与筛选顺序可换',
 '/群数据 暂定榜 四麻             不足10场的综合暂定分，单独分页',
 '/群数据 下一页 / 上一页 / 第3页  沿用本人最近榜单；群聊也可@慧慧翻页',
 '/群数据 评分 "游戏昵称" 三麻 S1  综合评分分解；个人卡片也显示总分和排名',
 '/群数据 评分规则               查看成绩90%＋场次10%的计算规则',
 '昵称可只填一部分，或省略特殊字符；完整匹配直接查，否则发送文字候选。',
 '候选3分钟有效：本人回复序号 / 第2个，或 /群数据 选 2；/群数据 取消。',
 '/群数据 最高 四麻             最高持点榜 + 第一条记录原图',
 '/群数据 最低 三麻             最低持点榜 + 第一条记录原图',
 '/群数据 最高 "游戏昵称" 三麻 S0   直接发送该玩家最高持点的结算图',
 '/群数据 最低 "游戏昵称" 四麻 S0   直接发送该玩家最低持点的结算图',
 '/群数据 极值 "游戏昵称" S0        分别发送三麻、四麻的最高和最低图',
 '/群数据 原图 编号             查看记录对应的精华截图',
 '/群数据 玩家 / 记录 [页码] / 状态 / 帮助',
 '/群数据 赛季                  查看赛季列表；默认查询 S1赛季',
 '/群数据 被飞榜 四麻 S0       查询 S0赛季；加 S1 查当前赛季',
 '/群数据 最近 5 四麻           最近5场完整结算（最多20场）',
 '/群数据 对比 "甲" "乙" 四麻   比较场次、顺位和被飞',
 '/群数据 同桌 "甲" "乙" 三麻   只统计两人同时出场的对局',
 '/群数据 表格                  在线明细和玩家统计',
 '榜单：被飞、被飞率、一位、二位、三位、四位、末位及各自比率；',
 '还有场次、平均顺位、平均持点、最高、最低。模式不填则分别发三麻和四麻。',
 '被飞=最终持点小于0；比例分母是该玩家在本群精华中的出场次数。',
 '文字和非终局图片跳过；昵称不等于QQ；时间范围按加入精华时间。',
 '相同图片 / 相同完整结果去重；率榜显示次数/场次，小样本请谨慎解读。',
 '管理员：开启、关闭、更新、待确认、核对 编号、确认 编号、忽略 编号、重识 编号',
 '人工修正：/群数据 修正 编号 三麻 "甲=65000" "乙=42000" "丙=-2000"',
 '按顺位从高到低填写所有人；只在核对原图后确认，不确定就保留待确认。',
 '别名：/群数据 合并 "旧昵称" "新昵称"；撤销别名：/群数据 取消合并 "旧昵称"',
 '新增/取消精华会立即唤醒同步，并每约2分钟兜底检查；识别异步进行。',
 '管理员 /群数据 时间：查看数据截至日期、各模式最新被飞和程序检查时间。',
 '管理员 /群数据 同步：查看在线同步状态；/群数据 同步更新 立即同步。',
 '管理员 /群数据 字库：查看已确认昵称；字库 录入 编号 顺位 完整昵称；字库 删除 完整昵称。',
 '正常终局、被飞终局都录入；S0赛季已封存，与S1赛季独立统计。',
 '群聊也可@慧慧说“看看本群三麻被飞榜”；管理操作仅接受明确命令。',
]
WRITES={'开启','关闭','更新','确认','忽略','修正','重识','合并','取消合并','同步更新','字库'}
ADMIN_READS={'待确认','核对','时间','同步'}

def parse_query(arguments):
    try:tokens=shlex.split(str(arguments))
    except ValueError:raise ValueError('引号未闭合，含空格的昵称请用一对英文双引号包住')
    options={'mode':None,'days':0,'minimum':1,'action':'总览','rest':[],'period':None,'page':1,'page_set':False}
    for token in tokens:
        number=page_number(token)
        if number is not None:
            if options['page_set'] and options['page']!=number:raise ValueError('一次只能选择一个页码')
            options['page']=number;options['page_set']=True
        elif token in ('历史','上一期','当前') or token.startswith(('周期=','赛季=')) or re.fullmatch(r'(?i)S\d+(?:赛季)?',token):
            choice=token.removeprefix('周期=').removeprefix('赛季=')
            if re.fullmatch(r'(?i)S\d+(?:赛季)?',choice):choice=choice.upper().removesuffix('赛季')
            if options['period'] and options['period']!=choice:raise ValueError('一次只能选择一个统计周期')
            options['period']=choice
        elif token in ALIASES and token not in ('3','4'):
            mode=ALIASES[token]
            if options['mode'] and options['mode']!=mode:raise ValueError('一次选择一种模式；不填则分别显示三麻和四麻')
            options['mode']=mode
        elif token.endswith('天') and token[:-1].isdigit():
            options['days']=int(token[:-1])
            if not 1<=options['days']<=3650:raise ValueError('天数范围为1～3650')
        elif token.endswith('场') and token.removeprefix('至少')[:-1].isdigit():
            options['minimum']=int(token.removeprefix('至少')[:-1])
            if not 1<=options['minimum']<=10000:raise ValueError('最少场次范围为1～10000')
        else:options['rest'].append(token)
    rest=options['rest']
    if rest:options['action']=rest.pop(0)
    elif options['page_set']:options['action']='翻页'
    alias={'菜单':'帮助','排行榜':'被飞','榜单':'被飞','最高点数':'最高','最低点数':'最低','最高分':'最高','最低分':'最低','一位最多':'一位','二位最多':'二位','被飞最多':'被飞','开启统计':'开启','更新数据':'更新','玩家列表':'玩家','被飞榜':'被飞','被飞排行榜':'被飞','被飞排行':'被飞','被飞次数':'被飞','被飞次数榜':'被飞','被飞率榜':'被飞率','被飞率排行':'被飞率','被飞率排行榜':'被飞率','被飞率排名':'被飞率','统计时间':'时间','更新时间':'时间','同步时间':'时间'}
    alias.update({'综合排名':'综合榜','综合排行':'综合榜','综合排行榜':'综合榜','综合评分榜':'综合榜','综合分榜单':'综合榜','实力榜':'综合榜','暂定分榜':'暂定榜','综合暂定榜':'暂定榜',
                  '综合分析':'评分','综合评分':'评分','个人评分':'评分','综合分':'评分','综合规则':'评分规则'})
    options['action']=alias.get(options['action'],options['action'])
    if options['action']=='赛季':options['action']='周期'
    if options['action'] in ('最高最低','最高最低对局','极值对局'):options['action']='极值'
    return options

def player_name(value,aliases):
    name=normalize_name(value);seen=set()
    while name in aliases and name not in seen:seen.add(name);name=aliases[name]
    return name

class GroupDataPlugin(Star):
    def __init__(self,context:Context,config=None):
        super().__init__(context);self.config=config or {};self.tasks=[];self.jobs=set()
    async def initialize(self):
        self.folder=StarTools.get_data_dir('astrbot_plugin_group_data')
        self.store=Store(self.folder/'group_data.db')
        self.card_dir=self.folder/'cards';self.card_dir.mkdir(exist_ok=True)
        self.source=EssenceSource(self.store,self.folder/'images')
        self.recognizer=Recognizer(self.context,self.config,self.store)
        self.poll_wake=asyncio.Event()
        self.tasks=[asyncio.create_task(self._poll()),asyncio.create_task(self.recognizer.loop()),asyncio.create_task(self.recognizer.llm_loop())]
        self.online=None
        target=self.folder/'feishu_target.json'
        if target.is_file():
            try:
                settings=json.loads(target.read_text(encoding='utf-8'))
                if settings.get('enabled'):
                    from .online import OnlineSync
                    self.online=OnlineSync(self.store,self.folder,settings)
                    self.tasks.append(asyncio.create_task(self.online.loop()))
            except (ValueError,KeyError,OSError):
                logger.warning('群数据飞书配置无效，已暂停在线同步；本地命令正常保留')
        logger.info('群数据已加载：只录入完整雀魂终局结算截图')
    async def terminate(self):
        tasks=[*self.tasks,*self.jobs]
        for task in tasks:task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError,Exception):await task
        self.store.close()
    def spawn(self,coro):
        task=asyncio.create_task(coro);self.jobs.add(task);task.add_done_callback(self.jobs.discard)
        return task
    async def _poll(self):
        while True:
            self.poll_wake.clear();started=time.monotonic()
            try:
                for platform in self.context.platform_manager.get_insts():
                    if platform.meta().name!='aiocqhttp':continue
                    bot=platform.get_client()
                    try:
                        login=unwrap(await asyncio.wait_for(bot.call_action(action='get_login_info'),10));self_id=str(login['user_id'])
                    except Exception:continue
                    for gid in self.config.get('initial_groups',[]):
                        gid=str(gid)
                        if not gid.isdigit():continue
                        scope=self_id+':'+gid
                        if not self.store.group(scope):self.store.enable(scope,self_id,gid)
                    for group in self.store.groups():
                        if group['bot']==self_id:
                            await self.source.sync(group['scope'],bot,group['gid'])
                # Old generated cards are disposable, originals/recognitions are retained.
                for path in self.card_dir.glob('*.png'):
                    if path.stat().st_mtime<time.time()-86400:path.unlink(missing_ok=True)
            except Exception as exc:logger.warning('群数据后台检查失败：%s',type(exc).__name__)
            interval=max(60,int(self.config.get('poll_seconds',120)))
            try:await asyncio.wait_for(self.poll_wake.wait(),max(1,interval-(time.monotonic()-started)))
            except asyncio.TimeoutError:pass

    @filter.event_message_type(filter.EventMessageType.ALL,priority=131)
    async def essence_notice(self,event:AstrMessageEvent):
        """Wake the pull loop immediately after QQ adds/removes an essence."""
        raw=getattr(getattr(event,'message_obj',None),'raw_message',None)
        if not isinstance(raw,dict) or raw.get('post_type')!='notice' or raw.get('notice_type')!='essence':return
        gid=str(raw.get('group_id') or '')
        if not gid.isdigit():return
        scope=str(event.get_self_id())+':'+gid
        group=self.store.group(scope)
        if group and group['enabled']:self.poll_wake.set()
    async def admin(self,event):
        if event.is_admin():return True
        try:
            data=unwrap(await asyncio.wait_for(event.bot.call_action(action='get_group_member_info',group_id=int(event.get_group_id()),user_id=int(event.get_sender_id()),no_cache=True),15))
            return data.get('role') in ('owner','admin')
        except Exception:return False
    async def card(self,event,title,lines):
        path=self.card_dir/(uuid.uuid4().hex+'.png')
        await asyncio.to_thread(cards.text_card,path,title,lines)
        await self.send_result(event,event.chain_result([Comp.Image.fromFileSystem(str(path))]),title+'\n'+'\n'.join(str(line) for line in lines))
    async def send_result(self,event,result,fallback=None):
        try:
            await event.send(result)
            return True
        except (ActionFailed,TimeoutError) as exc:
            if hasattr(event,'set_extra'):event.set_extra('group_data_delivery_error',True)
            logger.warning('群数据QQ发送异常 group=%s user=%s error=%s detail=%s',event.get_group_id(),event.get_sender_id(),type(exc).__name__,str(exc)[:350])
            # A timed-out send may already have reached QQ. Never retry the
            # image blindly; keep the selection and offer one text response.
            if fallback:
                try:
                    await event.send(event.plain_result('QQ图片发送回执异常，先提供文字结果：\n'+fallback[:3500]))
                    return True
                except (ActionFailed,TimeoutError) as text_exc:
                    logger.warning('群数据文字结果发送也失败 group=%s error=%s；未重发图片，候选保留',event.get_group_id(),type(text_exc).__name__)
            return False
    def selection_key(self,event):
        return (str(event.get_self_id()),str(event.get_group_id()),str(event.get_sender_id()))
    def selection_cache(self):
        if not hasattr(self,'_nickname_choices'):
            self._nickname_choices=SelectionCache();self._nickname_context=SelectionCache(ttl=3600);self._nickname_seen=SelectionCache();self._nickname_progress=SelectionCache()
        return self._nickname_choices
    def selection_context(self,event):
        self.selection_cache()
        return self._nickname_context.get(self.selection_key(event))
    def remember_context(self,event,query):
        cache=self.selection_cache();key=self.selection_key(event)
        if not cache.get(key) and isinstance(query,str) and len(query)<=500:
            self._nickname_context.put(key,{'query':query})
    def board_cache(self):
        if not hasattr(self,'_board_context'):self._board_context=SelectionCache(ttl=600)
        return self._board_context
    def paging_options(self,event,opt):
        previous=self.board_cache().get(self.selection_key(event))
        if not previous:raise ValueError('没有最近的榜单，或已超过10分钟；请先发送 /群数据 综合榜 四麻。')
        selected_mode=opt['mode'] or previous['options']['mode']
        modes=previous['modes']
        if not selected_mode:
            active=[m for m,info in modes.items() if info['total']]
            if len(active)==1:selected_mode=active[0]
            else:raise ValueError('最近同时查询了三麻和四麻，请指定模式，例如 /群数据 下一页 四麻。')
        if selected_mode not in modes:raise ValueError('最近没有这个模式的榜单，请先查询对应榜单。')
        for field in ('period','days','minimum'):
            supplied=opt[field]
            default={'period':None,'days':0,'minimum':1}[field]
            if supplied!=default and supplied!=previous['options'][field]:raise ValueError('翻页沿用原筛选；修改条件请重新发送完整榜单命令。')
        result=deepcopy(previous['options']);result['mode']=selected_mode
        result['action']=modes[selected_mode].get('action',result['action'])
        current=modes[selected_mode]['page']
        result['page']=current+1 if opt['action']=='下一页' else current-1 if opt['action']=='上一页' else opt['page']
        if result['page']<1:raise ValueError('已经是第1页。')
        if result['page']>modes[selected_mode]['pages']:raise ValueError(f'已经到最后一页，共{modes[selected_mode]["pages"]}页。')
        result['page_set']=True
        return result
    def remember_board(self,event,opt,period_name,views):
        saved=deepcopy(opt);saved['rest']=[]
        if re.fullmatch(r'S\d+赛季',period_name):saved['period']=period_name.removesuffix('赛季')
        self.board_cache().put(self.selection_key(event),{'options':saved,'modes':views})
        query=self.query_text(saved,period_name)
        self.remember_context(event,query)
        if hasattr(event,'set_extra'):event.set_extra('group_data_resolved_query',query)
    @staticmethod
    def query_text(opt,period_name):
        tokens=[opt['action'],*opt['rest']]
        if opt['mode']:tokens.append('三麻' if opt['mode']==3 else '四麻')
        if opt['days']:tokens.append(str(opt['days'])+'天')
        if opt['minimum']>1:tokens.append(str(opt['minimum'])+'场')
        if opt.get('page_set'):tokens.append(f'第{opt["page"]}页')
        if re.fullmatch(r'S\d+赛季',period_name):tokens.append(period_name.removesuffix('赛季'))
        elif opt['period']:tokens.append('周期='+opt['period'])
        return ' '.join(json.dumps(token,ensure_ascii=False) if any(c.isspace() or c in "'\"\\" for c in token) else token for token in tokens)
    async def resolve_query_names(self,event,opt,scope,period_id,period_name):
        action=opt['action'];paired=action in ('对比','同桌')
        if action not in ('个人','评分','极值','对比','同桌') and not (action in ('最高','最低') and opt['rest']):return True
        if paired:
            if len(opt['rest'])!=2:raise ValueError('用法：/群数据 '+action+' "甲昵称或片段" "乙昵称或片段"')
            values=list(opt['rest'])
        else:
            if not opt['rest']:raise ValueError('请在 '+action+' 后填写游戏昵称或昵称片段')
            values=[' '.join(opt['rest'])]
        # Find identities within this season. Time/mode/minimum still apply to
        # the eventual result, without making an existing identity disappear.
        matches=self.store.matches(scope,period=period_id);aliases=self.store.aliases(scope,period_id)
        names={s['name'] for mode in (3,4) for s in aggregate(matches,mode,aliases)}
        for index,value in enumerate(values):
            exact,options=candidates(value,names,aliases)
            if exact:values[index]=exact;continue
            if not options:
                hints=[]
                for historical in periods.all_periods(self.store,scope):
                    if historical['id']==period_id or historical['state']!='archived':continue
                    old_matches=self.store.matches(scope,period=historical['id'])
                    old_names={p['name'] for mode in (3,4) for p in aggregate(old_matches,mode,self.store.aliases(scope,historical['id']))}
                    old_exact,old_options=candidates(value,old_names,self.store.aliases(scope,historical['id']))
                    found=[old_exact] if old_exact else old_options[:3]
                    if found:
                        hints.append(historical['name']+' 中找到：'+ '、'.join(found))
                        for found_name in found:
                            hint={**opt,'action':'个人','rest':[found_name],'days':0,'minimum':1,'period':historical['id']}
                            hints.append('查看历史：/群数据 '+self.query_text(hint,historical['name']))
                await self.card(event,'群数据 · 当前赛季无匹配',[
                    f'{period_name}：没有匹配“{value}”的已确认玩家。',
                    *hints,
                    '以上历史命令需明确发送；当前查询没有自动切换赛季。' if hints else '可换更短的昵称片段；/群数据 玩家 查看昵称，旧赛季请加 S0。']);return False
            saved=deepcopy(opt);saved['rest']=values;saved['period']=period_id
            self.selection_cache().put(self.selection_key(event),{'options':options[:20],'query':saved,'index':index,'paired':paired})
            logger.info('群数据昵称候选 group=%s user=%s count=%s season=%s',scope,event.get_sender_id(),len(options),period_name)
            conditions=[period_name,'三麻' if opt['mode']==3 else '四麻' if opt['mode']==4 else '三麻 / 四麻']
            if opt['days']:conditions.append(f'近{opt["days"]}天')
            if opt['minimum']>1:conditions.append(f'至少{opt["minimum"]}场')
            example=min(2,len(options))
            lines=[
                ' · '.join(conditions)+f' · {action}',f'查找“{value}”，找到 {len(options)} 个候选：',
                *[f'{i}. {name}' for i,name in enumerate(options[:20],1)],
                *(['仅列前20项；请补充昵称关键词缩小范围。'] if len(options)>20 else []),
                f'本人3分钟内回复序号或“第{example}个”，也可 /群数据 选 {example}。',
                '仅当前群、当前发起人有效；/群数据 取消 可结束。']
            await self.send_result(event,event.plain_result('群数据 · 选择昵称\n'+'\n'.join(lines)));return False
        opt['rest']=values
        query=self.query_text(opt,period_name)
        if hasattr(event,'set_extra'):event.set_extra('group_data_resolved_query',query)
        return True
    async def acknowledge_selection(self,event,value):
        recent=self._nickname_progress.get(self.selection_key(event))
        if not recent or value!=recent['value']:return False
        context=self.selection_context(event)
        if context:recent['resolved_query']=context['query']
        if hasattr(event,'set_extra') and recent.get('resolved_query'):
            event.set_extra('group_data_resolved_query',recent['resolved_query'])
        if not recent.get('acknowledged'):
            recent['acknowledged']=True
            text=(f'已选择“{recent["name"]}”，正在生成查询结果，请稍候。' if recent['phase']=='running'
                  else f'已查询“{recent["name"]}”，但QQ发送回执异常；需要重看请重新发送群数据查询。' if recent.get('delivery_error')
                  else f'刚才已按“{recent["name"]}”完成查询，无需再次选择。需要重看请重新发送群数据查询。')
            await self.send_result(event,event.plain_result(text))
        logger.info('群数据重复选择已拦截 group=%s user=%s phase=%s',event.get_group_id(),event.get_sender_id(),recent['phase'])
        return True
    async def select_nickname(self,event,value):
        cache=self.selection_cache();key=self.selection_key(event);pending=cache.get(key)
        if value=='cancel':
            running=self._nickname_progress.get(key)
            if running and running['phase']=='running' and not pending:
                await self.send_result(event,event.plain_result('选择已提交，正在生成结果；当前已没有待选昵称。'));return
            if pending or self._nickname_progress.get(key):
                cache.pop(key);self._nickname_progress.pop(key)
                await self.send_result(event,event.plain_result('已取消本次昵称选择。'));return
        progress=self._nickname_progress.get(key)
        if progress and progress['phase']=='running':
            if not await self.acknowledge_selection(event,value):await self.send_result(event,event.plain_result('上一项选择正在处理中，请稍候。'))
            return
        if not pending:
            if await self.acknowledge_selection(event,value):return
            await self.send_result(event,event.plain_result('没有有效的昵称候选，可能已过期；请重新发送群数据查询。'));return
        if value=='cancel':
            cache.pop(key);await self.send_result(event,event.plain_result('已取消本次昵称选择。'));return
        if not isinstance(value,int) or not 1<=value<=len(pending['options']):
            await self.send_result(event,event.plain_result(f'请选择 1～{len(pending["options"])}，例如 /群数据 选 1。'));return
        # Consume before any await, so duplicate replies cannot replay a query.
        cache.pop(key);opt=deepcopy(pending['query']);selected=pending['options'][value-1]
        opt['rest'][pending['index']]=selected
        opt['_selected']=True
        self._nickname_progress.put(key,{'value':value,'name':selected,'phase':'running','acknowledged':False})
        progress=self._nickname_progress.get(key)
        logger.info('群数据选择开始 group=%s user=%s index=%s',event.get_group_id(),event.get_sender_id(),value)
        try:
            await self.handle(event,allow_write=False,_options=opt)
        finally:
            progress['phase']='done';progress['finished']=time.monotonic()
            progress['delivery_error']=getattr(event,'get_extra',lambda *args:False)('group_data_delivery_error',False)
            context=self.selection_context(event)
            if context:progress['resolved_query']=context['query']
            logger.info('群数据选择结束 group=%s user=%s next_candidates=%s',event.get_group_id(),event.get_sender_id(),bool(cache.get(key)))
    @filter.event_message_type(filter.EventMessageType.ALL,priority=130)
    async def nickname_followup(self,event:AstrMessageEvent):
        if event.get_platform_name()!='aiocqhttp' or not str(event.get_group_id() or '').isdigit():return
        cache=self.selection_cache();key=self.selection_key(event)
        message_id=str(getattr(getattr(event,'message_obj',None),'message_id','') or '')
        if message_id and self._nickname_seen.get((*key,message_id)):
            event.should_call_llm(True);event.stop_event();return
        pending=cache.get(key);progress=self._nickname_progress.get(key)
        if not pending and not progress:return
        parts=getattr(event,'get_messages',lambda:[])()
        if any(isinstance(p,Comp.At) and str(p.qq)!=str(event.get_self_id()) for p in parts):return
        if any(not isinstance(p,(Comp.Plain,Comp.At,Comp.Reply)) for p in parts):return
        text=''.join(p.text for p in parts if isinstance(p,Comp.Plain)) if parts else getattr(event,'message_str','')
        value=choice(text)
        if value is None:
            if not pending and progress and progress['phase']=='done':self._nickname_progress.pop(key)
            return
        if not pending and (not progress or value!=progress['value'] or
                (progress['phase']=='done' and time.monotonic()-progress.get('finished',0)>15)):return
        if message_id:self._nickname_seen.put((*key,message_id),{})
        event.should_call_llm(True);event.stop_event()
        await self.select_nickname(event,value)
    async def original(self,event,scope,id):
        row=self.store.original_source(scope,id)
        if not row or not row['path'] or not Path(row['path']).is_file():
            await self.send_result(event,event.plain_result('本群没有这个有效图片记录，或原图暂不可用。'));return
        await self.send_result(event,event.chain_result([Comp.Plain(f'精华原图 #{id} · 消息 {row["msg"]}\n'),Comp.Image.fromFileSystem(row['path'])]),f'结算原图 #{id} 的发送回执异常；需要重看可发送 /群数据 原图 {id}。')
    async def player_extremes(self,event,matches,aliases,name,modes,action,period_name,days,minimum):
        metrics=('最高','最低') if action=='极值' else (action,)
        for mode in modes:
            mode_name='三麻' if mode==3 else '四麻'
            stats=aggregate(matches,mode,aliases)
            player=next((p for p in stats if p['name']==name),None)
            if not player or player['games']<minimum:
                await self.card(event,f'群数据 · {period_name} · {mode_name} · {name}',[
                    '没有符合条件的已确认对局。',f'查询范围：{period_name}，'+(f'近{days}天，' if days else '')+f'至少{minimum}场。',
                    '旧赛季请在命令后加 S0；可用 /群数据 玩家 查看对应赛季的昵称。']);continue
            grouped={}
            for metric in metrics:
                key='max_points' if metric=='最高' else 'min_points'
                grouped.setdefault(player[key+'_source'],[]).append(metric)
            for source_id,labels in grouped.items():
                match=next((m for m in matches if m['id']==source_id and m['result']['mode']==mode),None)
                seat=next((p for p in match['result']['players'] if player_name(p['name'],aliases)==name),None) if match else None
                if not seat:
                    await self.send_result(event,event.plain_result(f'{name} · {period_name} · {mode_name}：对应结算记录暂不可用。'));continue
                title=' / '.join(labels)+'持点'
                caption=f'{name} · {period_name} · {mode_name}\n{title}：{seat["points"]:,} 点 · 本局{seat["place"]}位\n结算原图 #{source_id}'
                if days:caption+=f' · 近{days}天'
                path=match.get('path')
                if path and Path(path).is_file():
                    await self.send_result(event,event.chain_result([Comp.Plain(caption+'\n'),Comp.Image.fromFileSystem(path)]),caption+f'\n需要重看可发送 /群数据 原图 {source_id}。')
                else:await self.send_result(event,event.plain_result(caption+'\n原图缓存暂不可用，请联系管理员。'))
    @filter.command('群数据')
    async def group_data(self,event:AstrMessageEvent,arguments:GreedyStr):
        await self.handle(event,arguments)
        event.stop_event()
    async def handle(self,event,arguments='',allow_write=True,*,_options=None):
        if event.get_platform_name()!='aiocqhttp' or not str(event.get_group_id() or '').isdigit():
            await self.send_result(event,event.plain_result('请在要统计的 QQ 群中使用 /群数据；私聊不读取群精华。'));return
        scope=str(event.get_self_id())+':'+str(event.get_group_id());gid=str(event.get_group_id())
        try:
            opt=deepcopy(_options) if _options is not None else parse_query(arguments)
            if opt['action'] in ('下一页','上一页','翻页'):opt=self.paging_options(event,opt)
            action=opt['action'];rest=opt['rest'];days=opt['days'];minimum=opt['minimum']
            if opt.get('page_set') and action not in set(METRICS)|{'综合榜','暂定榜'}:
                raise ValueError('页码只用于榜单；个人数据、原图等查询不需要页码。')
            if action in ('选','选择','取消','取消选择'):
                if action in ('取消','取消选择'):value='cancel'
                elif len(rest)==1:value=choice(rest[0])
                else:raise ValueError('用法：/群数据 选 序号；或 /群数据 取消')
                await self.select_nickname(event,value);return
            if _options is None:
                self.selection_cache().pop(self.selection_key(event))
                self._nickname_context.pop(self.selection_key(event))
                self._nickname_progress.pop(self.selection_key(event))
            if action in WRITES|ADMIN_READS:
                if not allow_write and action in WRITES:
                    await self.send_result(event,event.plain_result('管理操作请群管理员明确发送 /群数据 对应命令。'));return
                if not await self.admin(event):
                    await self.send_result(event,event.plain_result('这项操作仅限本群群主、管理员或机器人管理员。'));return
            if action=='帮助':await self.card(event,'群数据 · 使用帮助',HELP);return
            if opt['period'] and action in WRITES:
                target_period=periods.select(self.store,scope,opt['period'])
                if target_period and target_period['state']=='archived':raise ValueError('封存周期只读；修改当前数据请去掉“历史”参数')
            if action in ('开启','关闭'):
                self.store.enable(scope,event.get_self_id(),gid,action=='开启')
                self.store.audit(scope,event.get_sender_id(),action,{})
                if action=='开启':self.spawn(self.source.sync(scope,event.bot,gid))
                await self.send_result(event,event.plain_result('已开启本群精华统计，正在异步检查结算截图。' if action=='开启' else '已暂停本群自动更新，历史统计保留。'));return
            group=self.store.group(scope)
            if not group:
                await self.card(event,'群数据 · 尚未开启',['请本群管理员发送 /群数据 开启。','开启后读取本群精华图片，只把完整雀魂终局结算录入。','使用 /群数据 帮助 查看规则。']);return
            selected=periods.select(self.store,scope,opt['period'])
            period_id=selected['id'] if selected else None
            period_name=selected['name'] if selected else '当前数据'
            if selected and selected['state']=='archived' and action in WRITES:
                raise ValueError('封存周期只读；修改当前数据请去掉“历史”参数')
            if action=='评分规则':
                await self.send_result(event,event.plain_result(rules_text()));return
            if action=='字库':
                bank=self.recognizer.verified_names
                if not rest or rest==['列表']:
                    names=bank.summary(scope)
                    lines=[f'本群昵称字形库：{len(names)} 个昵称，{sum(names.values())} 份字形样本。']
                    lines.extend(f'{name}：{count}份' for name,count in sorted(names.items()))
                    lines.extend(['录入：/群数据 字库 录入 图片编号 顺位 完整昵称',
                                  '溯源：/群数据 字库 查看 完整昵称',
                                  '删除：/群数据 字库 删除 完整昵称',
                                  '按原图登记；只匹配本群字形，不全局合并玩家。新样本会自动复核待确认图片。'])
                    await self.send_result(event,event.plain_result('\n'.join(lines)));return
                if rest[0]=='查看' and len(rest)==2:
                    items=[r for r in bank.templates(scope) if r['name']==rest[1]]
                    lines=[f'字形标签：{rest[1]}；{len(items)}份样本。']
                    lines.extend(f'原图 #{r["source"]} · '+('明确登记' if r.get('explicit') else '封存确认样本' if r.get('origin')=='archived-confirmed' else '历史人工确认') for r in items)
                    await self.send_result(event,event.plain_result('\n'.join(lines)));return
                if rest[0]=='录入' and len(rest)==4:
                    row=self.store.original_source(scope,int(rest[1]))
                    if not row:raise ValueError('未找到本群原始图片记录')
                    entry=bank.enroll(scope,row,int(rest[2]),rest[3])
                    self.store.audit(scope,event.get_sender_id(),'字库录入',entry)
                    self.spawn(self.recognizer.recheck_reviews())
                    await self.send_result(event,event.plain_result(f'已登记“{entry["name"]}”的字形样本 #{entry["source"]}；后台将自动复核待确认图片。'));return
                if rest[0]=='删除' and len(rest)==2:
                    bank.remove(scope,rest[1]);self.store.audit(scope,event.get_sender_id(),'字库删除',{'name':rest[1]})
                    await self.send_result(event,event.plain_result('已停用该昵称的字形匹配；已有对局与原始昵称保留。'));return
                raise ValueError('用法：/群数据 字库；字库 录入 图片编号 顺位 "完整昵称"；字库 删除 "完整昵称"')
            if action=='周期':
                items=periods.all_periods(self.store,scope)
                await self.card(event,'群数据 · 赛季列表',[
                    *[f'{p["name"]}（{"当前" if p["state"]=="current" else "已封存"}） · {len(self.store.matches(scope,period=p["id"]))} 场 · {p["id"]}' for p in items],
                    '默认查询 S1赛季；查询后加 S0 查旧赛季，加 S1 查当前赛季。',
                    '例如 /群数据 被飞榜 四麻 S0；赛季、模式等参数顺序可调换。',
                    '正常结算和被飞结算均统计；比例只代表本群精华样本。']);return
            if action in ('同步','同步更新','表格'):
                online=getattr(self,'online',None)
                if not online or online.scope!=scope:raise ValueError('本群尚未配置在线表格同步')
                if action=='表格':
                    await self.send_result(event,event.plain_result('群数据在线表格：'+online.target['url']+'\n查看与编辑权限由飞书控制；修改对局明细后自动重算统计。'));return
                if action=='同步更新':
                    if online.lock.locked():raise ValueError('正在同步，请稍后通过 /群数据 同步 查看结果')
                    async def synchronize():
                        try:
                            info=await online.once()
                            await self.send_result(event,event.plain_result(f'在线同步完成；当前周期 {info["games"]} 场，待处理项 {len(info["errors"])}。'))
                        except Exception as exc:
                            logger.warning('群数据在线同步失败：%s',type(exc).__name__)
                            await self.send_result(event,event.plain_result('在线同步暂时失败，本地统计保留；稍后自动重试。'))
                    self.spawn(synchronize());return
                info=online.get('last_sync',{});error=online.get('last_sync_error',{})
                lines=['群数据 · 在线同步',f'自动同步间隔：{online.interval} 秒',
                       '本地对局变化后主动同步；在线修改按定期间隔检查。',
                       '最近完成：'+(datetime.fromtimestamp(info['stamp'],timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S') if info.get('stamp') else '尚未完成'),
                       *info.get('errors',[])]
                if error.get('stamp',0)>info.get('stamp',0):lines.append('最近异常：'+error.get('message',error.get('type','未知')))
                await self.send_result(event,event.plain_result('\n'.join(lines)));return
            if action=='时间':
                info=self.store.freshness(scope,period_id);s=self.store.summary(scope,period_id)
                tz=timezone(timedelta(hours=8))
                def stamp(value,missing):
                    return datetime.fromtimestamp(float(value),tz).strftime('%Y-%m-%d %H:%M:%S') if value else missing
                def record(value):
                    return stamp(value['stamp'],'暂无已确认记录')+(f'（记录 #{value["id"]}）' if value['id'] is not None else '')
                lines=[
                    '群数据 · 更新时间（北京时间）',
                    '统计周期：'+period_name+(f' · {period_id}' if period_id else ''),
                    '查询时间：'+stamp(time.time(),''),
                    '数据截至（加入精华时间）：'+record(info['data_until']),
                    '三麻最新被飞精华：'+record(info['latest_fly'][3]),
                    '四麻最新被飞精华：'+record(info['latest_fly'][4]),
                    '最近成功检查精华：'+stamp(info['last_scan'],'尚未成功检查'),
                    '最近结果入库（程序处理时间）：'+stamp(info['last_import'],'本周期尚无新结果入库' if selected and selected['state']=='current' else '历史处理时间未记录；不代表没有历史数据'),
                    f'自动检查：{"开启" if group["enabled"] else "暂停"}；检查间隔至少 {max(60,int(self.config.get("poll_seconds",120)))} 秒',
                    f'已录入：{s["games"]} 场；待识别：{s.get("new",0)} 张；待辅助识别：{s.get("llm_pending",0)} 张',
                    f'待确认：{s.get("review",0)} 张；本地异常：{s.get("local_error",0)} 张；读取失败：{s.get("download_error",0)} 张',
                    '最近检查异常：'+group['error'] if group['error'] else '最近检查：正常',
                    '数据截至与最新被飞按已确认记录的加入精华时间计算，不推断实际对局时间。',
                    '成功检查只代表精华列表已同步；识别或人工确认完成后才会入库。',
                ]
                await self.send_result(event,event.plain_result('\n'.join(lines)));return
            if action=='更新':
                if not group['enabled']:raise ValueError('本群已暂停，请先 /群数据 开启')
                self.spawn(self.source.sync(scope,event.bot,gid))
                await self.send_result(event,event.plain_result('已安排检查精华变更；图片识别在后台进行，发送 /群数据 状态 查看进度。'));return
            if action=='状态':
                s=self.store.summary(scope);stamp=time.strftime('%m-%d %H:%M',time.localtime(group['last_scan'])) if group['last_scan'] else '尚未成功扫描'
                try:
                    await self.recognizer.provider();model=self.recognizer.model_error or '默认DeepSeek（仅本地识别不清时辅助）'
                except RuntimeError as exc:model=str(exc)
                await self.card(event,'群数据 · 导入状态',[f'自动更新：{"开启" if group["enabled"] else "暂停"}；最近检查：{stamp}',f'有效精华图片：{s["images"]} 张；已缓存唯一图片：{s["unique_images"]} 张',f'已录入去重对局：{s["games"]} 场；待识别：{s.get("new",0)} 张',f'跳过非结算图：{s.get("ignored",0)} 张；待人工确认：{s.get("review",0)} 张',f'待默认DeepSeek辅助：{s.get("llm_pending",0)} 张；本地识别异常：{s.get("local_error",0)} 张',f'图片读取失败：{s.get("download_error",0)} 张；本地OCR优先，不调用魔搭',f'图像模型：{model}',group['error'] or '扫描正常；同图只识别一次，相同完整结果只统计一场。','图片已入队不代表已计入；确认后的对局才进入榜单。']);return
            if action=='原图':
                if len(rest)!=1:raise ValueError('用法：/群数据 原图 编号')
                await self.original(event,scope,int(rest[0]));return
            if action in ('待确认','记录'):
                rows=[r for r in self.store.rows(scope,period_id) if action=='记录' or (r['override_status'] or r['status']) in ('review','error','llm_pending','local_error') or not r['hash']]
                page=int(rest[0]) if rest else 1
                pages=max(1,(len(rows)+19)//20)
                if not 1<=page<=pages:raise ValueError(f'页码范围1～{pages}')
                labels={'accepted':'已录入','ignored':'已跳过','review':'待确认','llm_pending':'待辅助识别','new':'待本地识别','local_error':'本地识别异常'}
                lines=[f'#{r["id"]}  {labels.get(r["override_status"] or r["status"],"读取失败")}  {r["override_reason"] or r["reason"] or r["error"] or ""}' for r in rows[(page-1)*20:page*20]]
                await self.card(event,'群数据 · '+('图片记录' if action=='记录' else '待核对图片'),[f'共 {len(rows)} 条，第{page}/{pages}页；下一页 /群数据 {action} {min(page+1,pages)}',*(lines or ['当前没有待核对项。']),'查看候选值 /群数据 核对 编号；查看原图 /群数据 原图 编号']);return
            if action in ('核对','确认','忽略','重识','修正'):
                if not rest:raise ValueError('请在操作后填写图片记录编号')
                row=self.store.get_source(scope,int(rest[0]),period_id)
                if not row:raise ValueError('本群没有这个有效精华图片记录')
                if action=='忽略' and not row['hash']:
                    self.store.exclude_source(scope,row,'管理员忽略无法读取的精华图片')
                    self.store.audit(scope,event.get_sender_id(),action,{'id':row['id'],'source_excluded':True})
                    await self.send_result(event,event.plain_result(f'记录 #{row["id"]} 已排除，后续扫描不再读取该图。'));return
                if not row['hash']:raise ValueError('这张原图尚未成功下载，可先忽略或待图片恢复后重试')
                if action=='核对':
                    raw=json.loads(row['raw'] or '[]');lines=[f'记录 #{row["id"]}：{row["override_status"] or row["status"]}',row['override_reason'] or row['reason'] or '']
                    for i,candidate in enumerate(item for item in raw if 'is_result' in item):
                        lines.append(f'第{i+1}次：{candidate.get("reason","")}')
                        lines.extend(f'{p.get("place")}位  {p.get("name")}  {p.get("points")} 点' for p in candidate.get('players',[]) if isinstance(p,dict))
                    await self.card(event,'群数据 · 识别核对',lines);return
                if action=='重识':
                    if row['hash'] in self.recognizer.running:raise ValueError('这张图片正在识别，请完成后再重识')
                    self.store.db.execute('DELETE FROM overrides WHERE scope=? AND hash=?',(scope,row['hash']));self.store.db.commit()
                    self.store.save(row['hash'],'new',reason='管理员要求重新识别')
                elif action=='忽略':self.store.override(scope,row,'ignored',reason='管理员忽略')
                else:
                    if action=='修正':
                        if not opt['mode'] or len(rest)!=opt['mode']+1:raise ValueError('用法：/群数据 修正 编号 三麻 "甲=65000" "乙=42000" "丙=-2000"，按顺位填写完整名单')
                        players=[]
                        for place,value in enumerate(rest[1:],1):
                            name,points=value.rsplit('=',1);players.append({'name':name,'place':place,'points':int(points.replace(',',''))})
                        obj={'is_result':True,'mode':opt['mode'],'players':players}
                    else:
                        raw=json.loads(row['raw'] or '[]')
                        if not raw:raise ValueError('没有可确认的识别候选，请核对原图后用“修正”完整录入')
                        obj=raw[0]
                    result=validate(obj,approve=True)
                    if result is None:raise ValueError('该图片未识别为终局，不能直接确认；请核对原图后完整修正')
                    self.store.override(scope,row,'accepted',result,'管理员已核对原图')
                self.store.audit(scope,event.get_sender_id(),action,{'id':row['id'],'args':rest[1:]})
                await self.send_result(event,event.plain_result(f'记录 #{row["id"]} 已{action}，统计已更新。'));return
            if action in ('合并','取消合并'):
                if action=='合并' and len(rest)==2:self.store.merge(scope,*rest)
                elif action=='取消合并' and len(rest)==1:self.store.unmerge(scope,rest[0])
                else:raise ValueError('用法：/群数据 合并 "旧昵称" "新昵称" 或 /群数据 取消合并 "旧昵称"')
                self.store.audit(scope,event.get_sender_id(),action,rest)
                await self.send_result(event,event.plain_result('昵称别名已更新；原始截图记录保留。'));return
            matches=self.store.matches(scope,days,period_id);aliases=self.store.aliases(scope,period_id)
            if not await self.resolve_query_names(event,opt,scope,period_id,period_name):return
            rest=opt['rest']
            if action in ('个人','评分','极值','最高','最低') and rest and opt['mode'] is None:
                selected_name=player_name(' '.join(rest),aliases)
                available=[mode for mode in (3,4) if any(p['name']==selected_name and p['games']>=minimum for p in aggregate(matches,mode,aliases))]
                if len(available)==1:opt['mode']=available[0]
                if not available:
                    if opt.get('_selected'):self._nickname_context.put(self.selection_key(event),{'query':self.query_text(opt,period_name)})
                    await self.card(event,'群数据 · '+period_name+' · 查询范围暂无对局',[
                        selected_name+'：没有满足所选时间和最少场次条件的已确认对局。',
                        '可调整天数或明确指定赛季；不会自动混用其他赛季。']);return
            if hasattr(event,'set_extra') and action in ('个人','评分','极值','最高','最低') and rest:
                event.set_extra('group_data_resolved_query',self.query_text(opt,period_name))
            if opt.get('_selected'):
                self._nickname_context.put(self.selection_key(event),{'query':self.query_text(opt,period_name)})
            if action=='极值' or (action in ('最高','最低') and rest):
                if not rest:raise ValueError('用法：/群数据 极值 "完整游戏昵称" [三麻/四麻] [S0/S1]')
                name=player_name(' '.join(rest),aliases)
                await self.player_extremes(event,matches,aliases,name,[opt['mode']] if opt['mode'] else [3,4],action,period_name,days,minimum)
                return
            if action=='最近':
                count=int(rest[0]) if rest else 5
                if not 1<=count<=20:raise ValueError('最近场数范围1～20')
                rows=sorted([m for m in matches if not opt['mode'] or m['result']['mode']==opt['mode']],key=lambda m:(m['stamp'],m['id']),reverse=True)[:count]
                await self.card(event,'群数据 · 最近对局 · '+period_name,[
                    *[f'#{m["id"]} · {m["result"]["mode"]}麻 · '+ ' / '.join(f'{p["place"]}位 {p["name"]} {p["points"]:,}' for p in m['result']['players']) for m in rows],
                    '用 /群数据 原图 编号 查看结算；没有记录表示本周期尚无对应对局。']);return
            if action in ('对比','同桌'):
                if len(rest)!=2:raise ValueError('用法：/群数据 '+action+' "甲完整昵称" "乙完整昵称" [三麻/四麻]')
                def resolved(name):
                    name=normalize_name(name);seen=set()
                    while name in aliases and name not in seen:seen.add(name);name=aliases[name]
                    return name
                names=[resolved(n) for n in rest]
                if len(set(names))!=2:raise ValueError('请填写两个不同的玩家')
                lines=[period_name+'；只计算本群已确认的完整结算。']
                for mode in ([opt['mode']] if opt['mode'] else [3,4]):
                    selected_matches=[m for m in matches if m['result']['mode']==mode]
                    if action=='同桌':selected_matches=[m for m in selected_matches if all(n in [resolved(p['name']) for p in m['result']['players']] for n in names)]
                    stats=aggregate(selected_matches,mode,aliases);lines.append(f'{mode}麻 · 样本 {len(selected_matches)} 场')
                    for name in names:
                        player=next((s for s in stats if s['name']==name),None)
                        if player:
                            lines.extend([f'{name}：{player["games"]}场 · 平均顺位{float(player["avg_place"]):.2f}',
                                f'一位{player["rank1"]}次（{player["rank1"]/player["games"]:.1%}） · 二位{player["rank2"]}次（{player["rank2"]/player["games"]:.1%}） · 被飞{player["fly"]}次（{player["fly"]/player["games"]:.1%}）'])
                        else:lines.append(name+'：无对应样本')
                await self.card(event,'群数据 · '+action,lines);return
            if action=='总览':
                path=self.card_dir/(uuid.uuid4().hex+'.png')
                await asyncio.to_thread(cards.overview,path,gid,matches,aliases,self.store.summary(scope,period_id),days,opt['mode'],period_name=period_name)
                await self.send_result(event,event.chain_result([Comp.Image.fromFileSystem(str(path))]),f'{period_name} · 群数据总览\n当前筛选已确认 {len(matches)} 场。\n可发送 /群数据 最近 5 查看结算，或 /群数据 个人 昵称 查询玩家。');return
            modes=[opt['mode']] if opt['mode'] else [3,4]
            if action in ('综合榜','暂定榜','评分'):
                if action=='评分' and not rest:raise ValueError('用法：/群数据 评分 昵称 [三麻/四麻] [S0/S1]；查看榜单用 /群数据 综合榜')
                if action!='评分' and rest:raise ValueError('榜单格式：/群数据 综合榜 [三麻/四麻] [S0/S1] [第2页]')
                views={};prepared=[]
                for mode in modes:
                    data=score_matches(matches,mode,aliases,minimum)
                    provisional=action=='暂定榜' or (action=='综合榜' and not data['formal_count'])
                    info=page_rows([r for r in data['rows'] if r['qualified']!=provisional],opt.get('page',1))
                    prepared.append((mode,data,provisional,info))
                for mode,data,provisional,info in prepared:
                    if action=='评分':
                        name=player_name(' '.join(rest),aliases)
                        lines=[f'{period_name} · {mode}麻'+(f' · 近{days}天收录' if days else ''),*personal_lines(data,name)[:6]]
                        await self.card(event,'群数据 · 综合评分分析',lines)
                    else:
                        path=self.card_dir/(uuid.uuid4().hex+'.png')
                        render_opt={**opt,'action':'暂定榜' if provisional else '综合榜','mode':mode,'page_set':False}
                        nav=navigation(info,self.query_text(render_opt,period_name))
                        await asyncio.to_thread(cards.score_board,path,gid,data,days,period_name,page=opt.get('page',1),provisional=provisional,nav=nav)
                        await self.send_result(event,event.chain_result([Comp.Image.fromFileSystem(str(path))]),cards.score_board_text(data,period_name,days,page=opt.get('page',1),provisional=provisional,nav=nav))
                        views[mode]={**{k:info[k] for k in ('page','pages','total')},'action':render_opt['action']}
                if views:self.remember_board(event,opt,period_name,views)
                return
            if action=='玩家':
                names=sorted({s['name'] for m in modes for s in aggregate(matches,m,aliases)})
                await self.card(event,'群数据 · 玩家昵称',[f'共 {len(names)} 位；仅列出已录入对局中的昵称。',*names[:60],*(['超过60位，请用个人查询完整昵称。'] if len(names)>60 else [])]);return
            if action not in METRICS and action!='个人':raise ValueError('未识别这个选项，发送 /群数据 帮助 查看完整用法')
            if action=='个人' and not rest:raise ValueError('用法：/群数据 个人 "完整游戏昵称" [三麻/四麻]')
            if action!='个人' and rest:raise ValueError('榜单参数请使用 三麻/四麻、S0/S1、30天、5场、第2页 等格式。')
            views={}
            if action!='个人':
                # Validate every mode before sending any card, avoiding partial replies.
                for mode in modes:
                    if mode==3 and action in ('四位','四位率'):continue
                    page_rows(ranking(aggregate(matches,mode,aliases),action,minimum),opt.get('page',1))
            for mode in modes:
                if mode==3 and action in ('四位','四位率'):continue
                stats=aggregate(matches,mode,aliases);path=self.card_dir/(uuid.uuid4().hex+'.png')
                if action=='个人':
                    name=player_name(' '.join(rest),aliases)
                    stats=[s for s in stats if s['games']>=minimum]
                    score_data=score_matches(matches,mode,aliases,minimum)
                    await asyncio.to_thread(cards.profile,path,gid,mode,stats,name,days,period_name=period_name,minimum=minimum,score_data=score_data)
                else:
                    info=page_rows(ranking(stats,action,minimum),opt.get('page',1))
                    render_opt={**opt,'mode':mode,'page_set':False}
                    nav=navigation(info,self.query_text(render_opt,period_name))
                    await asyncio.to_thread(cards.leaderboard,path,gid,mode,action,stats,days,minimum,period_name=period_name,page=opt.get('page',1),nav=nav)
                    views[mode]={k:info[k] for k in ('page','pages','total')}
                fallback=(cards.profile_text(gid,mode,stats,name,days,period_name,minimum,score_data=score_data) if action=='个人'
                          else f'{period_name} · {mode}麻 · {action}榜\n'+'\n'.join([*[f'{r["position"]}. {r["name"]} · {float(r["value"]):.2%}' if r['kind']=='rate' else f'{r["position"]}. {r["name"]} · {float(r["value"]):g}' for r in info['rows']],*nav]))
                await self.send_result(event,event.chain_result([Comp.Image.fromFileSystem(str(path))]),fallback)
                if action in ('最高','最低'):
                    rows=ranking(stats,action,minimum)
                    if rows and opt.get('page',1)==1:await self.original(event,scope,rows[0][('max_points' if action=='最高' else 'min_points')+'_source'])
            if views:self.remember_board(event,opt,period_name,views)
        except (ValueError,IndexError) as exc:await self.send_result(event,event.plain_result(str(exc)))
        except Exception as exc:
            logger.exception('群数据请求失败')
            await self.send_result(event,event.plain_result('群数据处理暂时失败，请稍后重试或联系管理员查看日志。'))
