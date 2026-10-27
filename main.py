from __future__ import annotations
import asyncio
import json
import shlex
import time
import uuid
from contextlib import suppress
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

HELP=[
 '/群数据                         三麻 / 四麻总览',
 '/群数据 被飞率 四麻          查看分榜，参数顺序可换',
 '/群数据 一位 三麻             一位次数榜',
 '/群数据 二位率 四麻 30天 5场   近30天，至少5场',
 '/群数据 个人 "游戏昵称" 三麻   玩家档案',
 '/群数据 最高 四麻             最高持点榜 + 第一条记录原图',
 '/群数据 最低 三麻             最低持点榜 + 第一条记录原图',
 '/群数据 原图 编号             查看记录对应的精华截图',
 '/群数据 玩家 / 记录 [页码] / 状态 / 帮助',
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
 '群聊也可@慧慧说“看看本群三麻被飞榜”；管理操作仅接受明确命令。',
]
WRITES={'开启','关闭','更新','确认','忽略','修正','重识','合并','取消合并'}
ADMIN_READS={'待确认','核对'}

def parse_query(arguments):
    try:tokens=shlex.split(str(arguments))
    except ValueError:raise ValueError('引号未闭合，含空格的昵称请用一对英文双引号包住')
    options={'mode':None,'days':0,'minimum':1,'action':'总览','rest':[]}
    for token in tokens:
        if token in ALIASES and token not in ('3','4'):
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
    alias={'菜单':'帮助','排行榜':'被飞','榜单':'被飞','最高点数':'最高','最低点数':'最低','最高分':'最高','最低分':'最低','一位最多':'一位','二位最多':'二位','被飞最多':'被飞','开启统计':'开启','更新数据':'更新','玩家列表':'玩家'}
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
        row=self.store.get_source(scope,id)
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
            if action in ('开启','关闭'):
                self.store.enable(scope,event.get_self_id(),gid,action=='开启')
                self.store.audit(scope,event.get_sender_id(),action,{})
                if action=='开启':self.spawn(self.source.sync(scope,event.bot,gid))
                await event.send(event.plain_result('已开启本群精华统计，正在异步检查结算截图。' if action=='开启' else '已暂停本群自动更新，历史统计保留。'));return
            group=self.store.group(scope)
            if not group:
                await self.card(event,'群数据 · 尚未开启',['请本群管理员发送 /群数据 开启。','开启后读取本群精华图片，只把完整雀魂终局结算录入。','使用 /群数据 帮助 查看规则。']);return
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
                rows=[r for r in self.store.rows(scope) if action=='记录' or (r['override_status'] or r['status']) in ('review','error','llm_pending','local_error') or not r['hash']]
                page=int(rest[0]) if rest else 1
                pages=max(1,(len(rows)+19)//20)
                if not 1<=page<=pages:raise ValueError(f'页码范围1～{pages}')
                labels={'accepted':'已录入','ignored':'已跳过','review':'待确认','llm_pending':'待辅助识别','new':'待本地识别','local_error':'本地识别异常'}
                lines=[f'#{r["id"]}  {labels.get(r["override_status"] or r["status"],"读取失败")}  {r["override_reason"] or r["reason"] or r["error"] or ""}' for r in rows[(page-1)*20:page*20]]
                await self.card(event,'群数据 · '+('图片记录' if action=='记录' else '待核对图片'),[f'共 {len(rows)} 条，第{page}/{pages}页；下一页 /群数据 {action} {min(page+1,pages)}',*(lines or ['当前没有待核对项。']),'查看候选值 /群数据 核对 编号；查看原图 /群数据 原图 编号']);return
            if action in ('核对','确认','忽略','重识','修正'):
                if not rest:raise ValueError('请在操作后填写图片记录编号')
                row=self.store.get_source(scope,int(rest[0]))
                if not row or not row['hash']:raise ValueError('本群没有这个已下载的有效精华图片记录')
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
            matches=self.store.matches(scope,days);aliases=self.store.aliases(scope)
            if action=='总览':
                path=self.card_dir/(uuid.uuid4().hex+'.png')
                await asyncio.to_thread(cards.overview,path,gid,matches,aliases,self.store.summary(scope),days,opt['mode'])
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
                    await asyncio.to_thread(cards.profile,path,gid,mode,stats,name,days)
                else:await asyncio.to_thread(cards.leaderboard,path,gid,mode,action,stats,days,minimum)
                await event.send(event.chain_result([Comp.Image.fromFileSystem(str(path))]))
                if action in ('最高','最低'):
                    rows=ranking(stats,action,minimum)
                    if rows:await self.original(event,scope,rows[0][('max_points' if action=='最高' else 'min_points')+'_source'])
        except (ValueError,IndexError) as exc:await event.send(event.plain_result(str(exc)))
        except Exception as exc:
            logger.warning('群数据请求失败：%s',type(exc).__name__)
            await event.send(event.plain_result('群数据处理暂时失败，请稍后重试或联系管理员查看日志。'))
