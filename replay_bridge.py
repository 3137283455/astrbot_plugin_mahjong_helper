"""AstrBot adapter for persistent, serial, single-log review jobs."""
from __future__ import annotations
import asyncio,json,os,re,signal,sys
from contextlib import suppress
from pathlib import Path
from astrbot.api import logger
from astrbot.api.event import MessageChain
import astrbot.api.message_components as Comp
from .replay_core import ReplayError,ReplayStore,parse_request,summarize,FORM_URL,TASK_RE
from .replay_card import render_replay_card

REPLAY_INSTRUCTIONS='''单牌谱复盘工具 mahjong_replay：用户提供雀魂牌谱链接、要求分析某一场或查看AI一致率时使用。
query使用“完整链接 开局南家”等参数；缺少分析席位时不要猜测，由工具询问。
“复盘状态、复盘结果、复盘记录、分歧、分局南二、取消复盘”等也交给该工具。
当前仅四麻东南战。聚合牌风锐评仍使用mahjong_review；这两个工具用途不同。
复盘是异步任务，工具会直接发送进度和结果卡片。不要编造一致率、失误、点数损失或重复提交。
若上游要求人工验证，说明需要用户手动生成报告再导入，不声称分析已成功。'''

HELP='''🀄 单牌谱复盘（四麻东南战）
/复盘 牌谱链接 开局南家
开局席位可选：东家、南家、西家、北家；参数可换序。
/复盘 状态　/复盘 结果　/复盘 记录
/复盘 分歧　/复盘 分局 南2
/复盘 取消　/复盘 重试
以上默认本人在当前会话的最近任务，也可在末尾加任务编号。
/复盘 Mortal报告链接：导入在线站生成的报告。
后台按提交顺序处理；完成后群聊@发起人、私聊直接发送，不占用其他命令。
同样支持 /雀 复盘；不支持三麻、东风战。
一致率只是与模型决策的吻合程度，不等于实战胜率。'''
STATUS={'queued':'排队中','running':'正在分析','done':'已完成','manual':'需要人工验证','failed':'未完成','cancelled':'已取消'}

class ReplayService:
    def __init__(self,context,folder,config=None):
        self.context=context;self.store=ReplayStore(Path(folder)/'replays');self.config=config or {}
        self.process=None;self.current_id='';self.wake=asyncio.Event();self.store.recover()

    async def db_call(self,name,*args,**kwargs):
        return await asyncio.to_thread(getattr(self.store,name),*args,**kwargs)

    @staticmethod
    def delivery(event):
        if hasattr(event,'is_private_chat'):return 'private' if event.is_private_chat() else 'group'
        return 'group' if ':GroupMessage:' in event.unified_msg_origin else 'private'

    @staticmethod
    def addressed(job,chain):
        group=job.get('delivery')=='group' or (not job.get('delivery') and ':GroupMessage:' in job['scope'])
        actor=str(job['actor'])
        if group and re.fullmatch(r'[1-9][0-9]{4,19}',actor):
            chain.chain[:0]=[Comp.At(qq=actor),Comp.Plain(' ')]
        return chain

    async def describe(self,job):
        info=await self.db_call('queue_info',job['id']) if job['status']=='queued' else None
        return self.status_text(job,info)

    async def handle(self,event,text):
        event.should_call_llm(True)
        try:
            if len(text)>1800:raise ReplayError('复盘参数太长，请只发完整链接和开局席位。')
            request=parse_request(text);scope=event.unified_msg_origin;actor=str(event.get_sender_id())
            action=request['action'];source=request['source']
            if not text.strip() or action=='help':
                await event.send(event.plain_result(HELP));return
            if action=='comment':
                await event.send(event.plain_result('首版先提供实际决策对照与一致率卡片。请用 /复盘 分歧 或 /复盘 分局 南2 查看。'));return
            if action=='history':
                jobs=await self.db_call('history',scope,actor)
                await event.send(event.plain_result('你的最近复盘任务：\n'+'\n'.join(f"#{j['id']} · {STATUS[j['status']]}" for j in jobs) if jobs else '当前会话还没有你的复盘任务。'));return
            if source is None and action in ('submit','import'):
                if request['seat'] is None:raise ReplayError('请发送牌谱链接和开局席位，例如 /复盘 链接 开局南家。')
                source=await self.db_call('get_pending',scope,actor)
                if not source:raise ReplayError('没有待选择席位的牌谱，或已超过5分钟，请重新发送完整链接。')
                source['seat']=request['seat']
            if source:
                if source['kind']=='replay' and source.get('seat') is None:
                    await self.db_call('save_pending',scope,actor,source)
                    await event.send(event.plain_result('请指定分析谁的操作：开局东家、南家、西家或北家。\n例如 /复盘 南家（5分钟内有效）。这里指开局席位，不是当前局庄家。'));return
                job,created=await self.db_call('create',scope,actor,source,self.delivery(event))
                if job['status']=='done':await self.send_result(event,job);return
                await event.send(event.plain_result(await self.describe(job)));self.wake.set();return
            job=await self.db_call('get',scope,actor,request['job'])
            if action=='cancel':
                if job['status'] in ('done','cancelled'):raise ReplayError('任务已经结束，无需取消。')
                await self.db_call('update',job['id'],status='cancelled',notified=1)
                if self.current_id==job['id']:self.stop_process()
                await event.send(event.plain_result(f"复盘 #{job['id']} 已取消。"));return
            if action=='retry':
                if job['status'] not in ('failed','manual','cancelled'):raise ReplayError('只有未完成、人工验证或取消的任务可以重试。')
                await self.db_call('retry',scope,actor,job['id'])
                job=await self.db_call('get',scope,actor,job['id'])
                self.wake.set();await event.send(event.plain_result(await self.describe(job)));return
            if action in ('result','differences','hand'):
                if job['status']!='done':await event.send(event.plain_result(await self.describe(job)));return
                if action=='hand' and request['hand'] is None:raise ReplayError('请指定局数，例如 /复盘 分局 南2。')
                await self.send_result(event,job,action,request['hand']);return
            await event.send(event.plain_result(await self.describe(job)))
        except ReplayError as exc:await event.send(event.plain_result(str(exc)))
        except Exception:
            logger.exception('单牌谱复盘入口失败')
            await event.send(event.plain_result('复盘处理失败，请稍后重试；原有雀魂查询仍可使用。'))

    def status_text(self,job,queue_info=None):
        result=f"复盘 #{job['id']} · {STATUS[job['status']]}"
        if job['status'] in ('queued','running'):
            if queue_info is not None:result+=f"\n前方还有 {queue_info['ahead']} 个任务（含正在分析），按提交顺序处理。"
            group=job.get('delivery')=='group' or (not job.get('delivery') and ':GroupMessage:' in job['scope'])
            return result+('\n完成后在本群@你发送结果。' if group else '\n完成后在本私聊发送结果。')+'等待期间其他命令照常使用。\n/复盘 状态 查进度；/复盘 取消 取消。'
        if job['status']=='done':return result+'\n/复盘 结果 可重新查看。'
        if job['error']:result+='\n'+job['error']
        if job['status']=='manual':result+='\n请打开 '+FORM_URL+' 手动提交同一牌谱和席位，再发送 /复盘 报告页面链接。'
        return result

    def result_chain(self,job,view='result',hand=None):
        report=json.loads(Path(job['report_path']).read_text(encoding='utf-8'))
        data=summarize(report,job['seat'],job['model'])
        if hand is not None and not any(h['kyoku']==hand for h in data['hands']):raise ReplayError('这份牌谱没有该局，请先查看总览里的分局列表。')
        card=render_replay_card(data,self.store.folder/'cards'/f"{job['id']}-{view}-{hand}.png",job['id'],view,hand)
        chain=MessageChain().message(f"🀄 复盘 #{job['id']}\n").file_image(str(card))
        if TASK_RE.fullmatch(str(job.get('remote_task',''))):
            chain.message('\n完整逐巡复盘：https://mjai.ekyu.moe/killerducky/?data=/report/'+job['remote_task']+'.json')
        return chain

    async def send_result(self,event,job,view='result',hand=None):
        chain=await asyncio.to_thread(self.result_chain,job,view,hand)
        await event.send(self.addressed(job,chain))

    def stop_process(self):
        process=self.process
        if process is None or process.returncode is not None:return
        with suppress(ProcessLookupError):
            if os.name=='posix':os.killpg(process.pid,signal.SIGTERM)
            else:process.terminate()

    async def run_job(self,job):
        runtime=Path(self.config.get('replay_runtime') or '/bot/mahjong-replay-worker')
        python=runtime/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
        if not python.is_file():raise ReplayError('复盘运行环境尚未安装，请管理员检查独立复盘服务。')
        env={**os.environ,'PLAYWRIGHT_BROWSERS_PATH':str(runtime/'browsers'),'HOME':'/tmp' if os.name=='posix' else os.environ.get('TEMP',''),'PYTHONUNBUFFERED':'1'}
        options={}
        if os.name=='posix':
            options['start_new_session']=True
            if os.geteuid()==0:
                import pwd,grp
                options.update(user=pwd.getpwnam('nobody').pw_uid,group=grp.getgrnam('nogroup').gr_gid)
        worker=runtime/'replay_worker.py'
        # Plugin updates carry their worker; keep the isolated runner in sync.
        content=Path(__file__).with_name('replay_worker.py').read_bytes()
        if not worker.is_file() or worker.read_bytes()!=content:
            try:
                temporary=runtime/'.replay-worker.tmp';temporary.write_bytes(content);temporary.chmod(0o644);temporary.replace(worker)
            except OSError as exc:raise ReplayError('复盘运行环境无法更新执行文件，请管理员检查目录权限。') from exc
        self.process=await asyncio.create_subprocess_exec(str(python),'-u',str(worker),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,limit=25*1024*1024,env=env,**options)
        if os.name=='posix':
            with suppress(OSError):os.setpriority(os.PRIO_PROCESS,self.process.pid,10)
        try:
            self.process.stdin.write((json.dumps({'url':job['url'],'seat':job['seat'],'remote_task':job['remote_task']})+'\n').encode());await self.process.stdin.drain();self.process.stdin.close()
            async with asyncio.timeout(330):
                while line:=await self.process.stdout.readline():
                    data=await asyncio.to_thread(json.loads,line)
                    if (await self.db_call('get',job['scope'],job['actor'],job['id']))['status']=='cancelled':return
                    if data.get('type')=='submitted' and TASK_RE.fullmatch(str(data.get('task_id',''))):
                        job['remote_task']=data['task_id'];await self.db_call('update',job['id'],remote_task=job['remote_task'])
                    elif data.get('type')=='result':
                        await self.db_call('complete',job,data.get('report'));return
                    elif data.get('type')=='error':
                        await self.db_call('finish_error',job['id'],'manual' if data.get('code')=='manual' else 'failed',str(data.get('message','上游分析失败'))[:400]);return
            raise ReplayError('复盘进程未返回报告，请稍后重试。')
        finally:
            self.stop_process()
            try:await asyncio.wait_for(self.process.wait(),5)
            except asyncio.TimeoutError:
                if os.name=='posix':
                    with suppress(ProcessLookupError):os.killpg(self.process.pid,signal.SIGKILL)
                else:self.process.kill()
                await self.process.wait()
            self.process=None

    async def notify(self,job):
        # Claim before sending: restart must not repeatedly post the same result.
        await self.db_call('update',job['id'],notified=1)
        try:
            chain=await asyncio.to_thread(self.result_chain,job) if job['status']=='done' else MessageChain().message(await self.describe(job))
            await self.context.send_message(job['scope'],self.addressed(job,chain))
        except Exception:logger.exception('复盘通知发送失败，可通过 /复盘 结果 手动读取')

    async def run(self):
        try:
            while True:
                job=await self.db_call('next')
                if not job:
                    pending=await self.db_call('unnotified')
                    for item in pending:await self.notify(item)
                    self.wake.clear()
                    try:await asyncio.wait_for(self.wake.wait(),3)
                    except asyncio.TimeoutError:pass
                    continue
                self.current_id=job['id']
                try:await self.run_job(job)
                except asyncio.CancelledError:raise
                except Exception as exc:
                    current=await self.db_call('get',job['scope'],job['actor'],job['id'])
                    if current['status']!='cancelled':await self.db_call('finish_error',job['id'],'failed',str(exc)[:350] or '复盘等待超时，可用 /复盘 重试 继续。')
                    logger.warning('复盘任务 %s 未完成：%s',job['id'],type(exc).__name__)
                finally:self.current_id=''
                completed=await self.db_call('get',job['scope'],job['actor'],job['id'])
                if completed['status'] in ('done','failed','manual') and not completed['notified']:
                    await self.notify(completed)
        finally:self.stop_process()
