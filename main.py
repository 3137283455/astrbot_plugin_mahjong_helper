from __future__ import annotations
import asyncio
import json
import shlex
import time
import uuid
from contextlib import suppress
from datetime import datetime,timezone,timedelta
from pathlib import Path
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

HELP=[
 '/群数据                         三麻 / 四麻总览',
 '/群数据 被飞榜 四麻          按被飞次数排行（默认）',
 '/群数据 被飞率 四麻          明确查询被飞比例，参数顺序可换',
 '/群数据 一位 三麻             一位次数榜',
 '/群数据 二位率 四麻 30天 5场   近30天，至少5场',
 '/群数据 个人 "游戏昵称" 三麻   玩家档案',
 '/群数据 最高 四麻             最高持点榜 + 第一条记录原图',
 '/群数据 最低 三麻             最低持点榜 + 第一条记录原图',
 '/群数据 原图 编号             查看记录对应的精华截图',
 '/群数据 玩家 / 记录 [页码] / 状态 / 帮助',
 '/群数据 周期                  查看当前周期与封存周期',
 '/群数据 被飞榜 四麻 历史     查询封存数据；默认只查新周期',
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
 '每约2分钟检查精华变更，识别异步进行；取消精华后不再计入。',
 '管理员 /群数据 时间：查看数据截至日期、各模式最新被飞和程序检查时间。',
 '管理员 /群数据 同步：查看在线同步状态；/群数据 同步更新 立即同步。',
 '正常终局、被飞终局都录入；历史数据已封存，不与新周期混算。',
 '群聊也可@慧慧说“看看本群三麻被飞榜”；管理操作仅接受明确命令。',
]
WRITES={'开启','关闭','更新','确认','忽略','修正','重识','合并','取消合并','同步更新'}
ADMIN_READS={'待确认','核对','时间','同步'}

def parse_query(arguments):
    try:tokens=shlex.split(str(arguments))
    except ValueError:raise ValueError('引号未闭合，含空格的昵称请用一对英文双引号包住')
    options={'mode':None,'days':0,'minimum':1,'action':'总览','rest':[],'period':None}
    for token in tokens:
        if token in ('历史','上一期','当前') or token.startswith('周期='):
            choice=token.removeprefix('周期=')
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
    alias={'菜单':'帮助','排行榜':'被飞','榜单':'被飞','最高点数':'最高','最低点数':'最低','最高分':'最高','最低分':'最低','一位最多':'一位','二位最多':'二位','被飞最多':'被飞','开启统计':'开启','更新数据':'更新','玩家列表':'玩家','被飞榜':'被飞','被飞排行榜':'被飞','被飞排行':'被飞','被飞次数':'被飞','被飞次数榜':'被飞','被飞率榜':'被飞率','被飞率排行':'被飞率','被飞率排行榜':'被飞率','被飞率排名':'被飞率','统计时间':'时间','更新时间':'时间','同步时间':'时间'}
    options['action']=alias.get(options['action'],options['action'])
    return options

class GroupDataPlugin(Star):
    def __init__(self,context:Context,config=None):
        super().__init__(context);self.config=config or {};self.tasks=[];self.jobs=set()
    async def initialize(self):
        self.folder=StarTools.get_data_dir('astrbot_plugin_group_data')
        self.store=Store(self.folder/'group_data.db')
        self.card_dir=self.folder/'cards';self.card_dir.mkdir(exist_ok=True)
        self.source=EssenceSource(self.store,self.folder/'images')
        self.recognizer=Recognizer(self.context,self.config,self.store)
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
            await asyncio.sleep(max(60,int(self.config.get('poll_seconds',120))))
    async def admin(self,event):
        if event.is_admin():return True
        try:
            data=unwrap(await asyncio.wait_for(event.bot.call_action(action='get_group_member_info',group_id=int(event.get_group_id()),user_id=int(event.get_sender_id()),no_cache=True),15))
            return data.get('role') in ('owner','admin')
        except Exception:return False
    async def card(self,event,title,lines):
        path=self.card_dir/(uuid.uuid4().hex+'.png')
        await asyncio.to_thread(cards.text_card,path,title,lines)
        await event.send(event.chain_result([Comp.Image.fromFileSystem(str(path))]))
    async def original(self,event,scope,id):
        row=self.store.original_source(scope,id)
        if not row or not row['path'] or not Path(row['path']).is_file():
            await event.send(event.plain_result('本群没有这个有效图片记录，或原图暂不可用。'));return
        await event.send(event.chain_result([Comp.Plain(f'精华原图 #{id} · 消息 {row["msg"]}\n'),Comp.Image.fromFileSystem(row['path'])]))
    @filter.command('群数据')
    async def group_data(self,event:AstrMessageEvent,arguments:GreedyStr):
        await self.handle(event,arguments)
        event.stop_event()
    async def handle(self,event,arguments='',allow_write=True):
        if event.get_platform_name()!='aiocqhttp' or not str(event.get_group_id() or '').isdigit():
            await event.send(event.plain_result('请在要统计的 QQ 群中使用 /群数据；私聊不读取群精华。'));return
        scope=str(event.get_self_id())+':'+str(event.get_group_id());gid=str(event.get_group_id())
        try:
            opt=parse_query(arguments);action=opt['action'];rest=opt['rest'];days=opt['days'];minimum=opt['minimum']
            if action in WRITES|ADMIN_READS:
                if not allow_write and action in WRITES:
                    await event.send(event.plain_result('管理操作请群管理员明确发送 /群数据 对应命令。'));return
                if not await self.admin(event):
                    await event.send(event.plain_result('这项操作仅限本群群主、管理员或机器人管理员。'));return
            if action=='帮助':await self.card(event,'群数据 · 使用帮助',HELP);return
            if opt['period'] and action in WRITES:
                target_period=periods.select(self.store,scope,opt['period'])
                if target_period and target_period['state']=='archived':raise ValueError('封存周期只读；修改当前数据请去掉“历史”参数')
            if action in ('开启','关闭'):
                self.store.enable(scope,event.get_self_id(),gid,action=='开启')
                self.store.audit(scope,event.get_sender_id(),action,{})
                if action=='开启':self.spawn(self.source.sync(scope,event.bot,gid))
                await event.send(event.plain_result('已开启本群精华统计，正在异步检查结算截图。' if action=='开启' else '已暂停本群自动更新，历史统计保留。'));return
            group=self.store.group(scope)
            if not group:
                await self.card(event,'群数据 · 尚未开启',['请本群管理员发送 /群数据 开启。','开启后读取本群精华图片，只把完整雀魂终局结算录入。','使用 /群数据 帮助 查看规则。']);return
            selected=periods.select(self.store,scope,opt['period'])
            period_id=selected['id'] if selected else None
            period_name=selected['name'] if selected else '当前数据'
            if selected and selected['state']=='archived' and action in WRITES:
                raise ValueError('封存周期只读；修改当前数据请去掉“历史”参数')
            if action=='周期':
                items=periods.all_periods(self.store,scope)
                await self.card(event,'群数据 · 统计周期',[
                    *[f'{p["name"]}（{"当前" if p["state"]=="current" else "已封存"}） · {len(self.store.matches(scope,period=p["id"]))} 场 · {p["id"]}' for p in items],
                    '默认查询当前周期；在任意查询后加“历史”可查封存数据。',
                    '指定其他周期：在查询后加 周期=周期ID；所有参数可调换顺序。',
                    '正常结算和被飞结算均统计；比例只代表本群精华样本。']);return
            if action in ('同步','同步更新','表格'):
                online=getattr(self,'online',None)
                if not online or online.scope!=scope:raise ValueError('本群尚未配置在线表格同步')
                if action=='表格':
                    await event.send(event.plain_result('群数据在线表格：'+online.target['url']+'\n查看与编辑权限由飞书控制；修改对局明细后自动重算统计。'));return
                if action=='同步更新':
                    if online.lock.locked():raise ValueError('正在同步，请稍后通过 /群数据 同步 查看结果')
                    async def synchronize():
                        try:
                            info=await online.once()
                            await event.send(event.plain_result(f'在线同步完成；当前周期 {info["games"]} 场，待处理项 {len(info["errors"])}。'))
                        except Exception as exc:
                            logger.warning('群数据在线同步失败：%s',type(exc).__name__)
                            await event.send(event.plain_result('在线同步暂时失败，本地统计保留；稍后自动重试。'))
                    self.spawn(synchronize());return
                info=online.get('last_sync',{});error=online.get('last_sync_error',{})
                lines=['群数据 · 在线同步',f'自动同步间隔：{online.interval} 秒',
                       '最近完成：'+(datetime.fromtimestamp(info['stamp'],timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S') if info.get('stamp') else '尚未完成'),
                       *info.get('errors',[])]
                if error.get('stamp',0)>info.get('stamp',0):lines.append('最近异常：'+error.get('message',error.get('type','未知')))
                await event.send(event.plain_result('\n'.join(lines)));return
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
                    '查询时间：'+stamp(info['generated_at'],''),
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
                await event.send(event.plain_result('\n'.join(lines)));return
            if action=='更新':
                if not group['enabled']:raise ValueError('本群已暂停，请先 /群数据 开启')
                self.spawn(self.source.sync(scope,event.bot,gid))
                await event.send(event.plain_result('已安排检查精华变更；图片识别在后台进行，发送 /群数据 状态 查看进度。'));return
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
                    await event.send(event.plain_result(f'记录 #{row["id"]} 已排除，后续扫描不再读取该图。'));return
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
                await event.send(event.plain_result(f'记录 #{row["id"]} 已{action}，统计已更新。'));return
            if action in ('合并','取消合并'):
                if action=='合并' and len(rest)==2:self.store.merge(scope,*rest)
                elif action=='取消合并' and len(rest)==1:self.store.unmerge(scope,rest[0])
                else:raise ValueError('用法：/群数据 合并 "旧昵称" "新昵称" 或 /群数据 取消合并 "旧昵称"')
                self.store.audit(scope,event.get_sender_id(),action,rest)
                await event.send(event.plain_result('昵称别名已更新；原始截图记录保留。'));return
            matches=self.store.matches(scope,days,period_id);aliases=self.store.aliases(scope,period_id)
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
                await event.send(event.chain_result([Comp.Image.fromFileSystem(str(path))]));return
            modes=[opt['mode']] if opt['mode'] else [3,4]
            if action=='玩家':
                names=sorted({s['name'] for m in modes for s in aggregate(matches,m,aliases)})
                await self.card(event,'群数据 · 玩家昵称',[f'共 {len(names)} 位；仅列出已录入对局中的昵称。',*names[:60],*(['超过60位，请用个人查询完整昵称。'] if len(names)>60 else [])]);return
            if action not in METRICS and action!='个人':raise ValueError('未识别这个选项，发送 /群数据 帮助 查看完整用法')
            if action=='个人' and not rest:raise ValueError('用法：/群数据 个人 "完整游戏昵称" [三麻/四麻]')
            for mode in modes:
                if mode==3 and action in ('四位','四位率'):continue
                stats=aggregate(matches,mode,aliases);path=self.card_dir/(uuid.uuid4().hex+'.png')
                if action=='个人':
                    name=normalize_name(' '.join(rest));seen=set()
                    while name in aliases and name not in seen:seen.add(name);name=aliases[name]
                    await asyncio.to_thread(cards.profile,path,gid,mode,stats,name,days,period_name=period_name)
                else:await asyncio.to_thread(cards.leaderboard,path,gid,mode,action,stats,days,minimum,period_name=period_name)
                await event.send(event.chain_result([Comp.Image.fromFileSystem(str(path))]))
                if action in ('最高','最低'):
                    rows=ranking(stats,action,minimum)
                    if rows:await self.original(event,scope,rows[0][('max_points' if action=='最高' else 'min_points')+'_source'])
        except (ValueError,IndexError) as exc:await event.send(event.plain_result(str(exc)))
        except Exception as exc:
            logger.warning('群数据请求失败：%s',type(exc).__name__)
            await event.send(event.plain_result('群数据处理暂时失败，请稍后重试或联系管理员查看日志。'))
