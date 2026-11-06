"""AstrBot adapter for persistent, serial, single-log review jobs."""
from __future__ import annotations
import asyncio,json,os,re,signal,sys,time
from datetime import datetime,timezone,timedelta
from contextlib import suppress
from pathlib import Path
from astrbot.api import logger
from astrbot.api.event import MessageChain
import astrbot.api.message_components as Comp
from .replay_core import ReplayError,ReplayStore,parse_request,summarize,FORM_URL,TASK_RE,URL_RE,hand_name,report_url,normalize_control_text
from .replay_card import render_replay_card,render_batch_card
from .replay_batch import ReplayBatches,parse_sources
from .replay_recent import create_recent
from .replay_delivery import DeliveryStore,split_result

REPLAY_INSTRUCTIONS='''电脑自动取谱V3。单牌谱复盘工具 mahjong_replay：用户提供雀魂牌谱链接、要求分析某一场或查看AI一致率时使用。
query使用“完整链接 开局南家”等参数；缺少分析席位时不要猜测，由工具询问。
“复盘状态、复盘结果、复盘记录、分歧、分局南二、取消复盘”等也交给该工具。
当前仅四麻东南战。聚合牌风锐评仍使用mahjong_review；这两个工具用途不同。
复盘是异步任务，工具会直接发送进度和结果卡片。不要编造一致率、失误、点数损失或重复提交。
遇到上游等待状态时只说明任务仍在处理中，不提示群管理员执行验证；不要重复提交或声称分析已成功。'''
REPLAY_INSTRUCTIONS+='\n用户提供多条链接时，query填“批量\\n链接1 开局西家\\n链接2 开局南家”（实际换行），一次调用，不要逐个重复调用。可用汇总、状态、取消加批次编号。最近N场填“最近 玩家UID或完整昵称 5”，是否能取得列表由工具核实，不编造链接。'

HELP='''🀄 单牌谱复盘（四麻东南战）
/复盘 牌谱链接 开局南家
开局席位可选：东家、南家、西家、北家；参数可换序。
/复盘 状态　/复盘 结果　/复盘 记录
/复盘 分歧　/复盘 分局 南2
/复盘 取消　/复盘 重试
/复盘 重发：重新发送已保存结果，不重新分析。
/复盘 链接　/复盘 原谱：分别查看总览网页、原始牌谱。
单场和批量结果均合并转发；逐巡入口集中在网页，原谱可点击复制。
以上默认本人在当前会话的最近任务，也可在末尾加任务编号。
/复盘 Mortal报告链接：导入在线站生成的报告。
/复盘 批量：下一行起每行“牌谱链接 开局西家”；最多10份。
/复盘 汇总　/复盘 状态：查看最近批次汇总或进度。
/复盘 最近 玩家 5：电脑浏览器获取最近四麻东南牌谱，自动识别席位并生成趋势；数量1～10，可加金/玉/王。
后台按提交顺序处理；无论从群聊还是私聊发起，进度和结果都私聊发送，不占用其他命令。
同样支持 /雀 复盘；不支持三麻、东风战。
一致率只是与模型决策的吻合程度，不等于实战胜率。'''
STATUS={'queued':'排队中','running':'正在分析','done':'已完成','manual':'未完成','failed':'未完成','cancelled':'已取消'}

class ReplayService:
    def __init__(self,context,folder,config=None):
        self.context=context;self.store=ReplayStore(Path(folder)/'replays');self.config=config or {}
        self.batches=ReplayBatches(self.store);self.delivery_store=DeliveryStore(self.store);self.recent_loader=None
        self.process=None;self.current_id='';self.wake=asyncio.Event();self.pc_gateway=None;self.pc_error='';self.pages=None
        identity=self.store.folder/'bot_identity.json'
        self.bot_uin=json.loads(identity.read_text()).get('uin','0') if identity.is_file() else '0'
        if self.config.get('replay_backend','server')!='pc':self.store.recover()

    async def db_call(self,name,*args,**kwargs):
        return await asyncio.to_thread(getattr(self.store,name),*args,**kwargs)

    async def reply(self,event,chain):
        ok=True
        parts=split_result(chain)
        for index,part in enumerate(parts):
            try:await event.send(part)
            except Exception:
                ok=False;logger.warning('复盘消息发送未确认，业务结果保留；不重复发送“处理失败”')
            if index+1<len(parts):await asyncio.sleep(.6)
        return ok

    async def deliver_result(self,event,job,chain):
        owner=f"query:{job['id']}:{time.time_ns()}"
        async def send(part):
            if self.is_group_job(job):await self.context.send_message(self.private_scope(job),part)
            else:await event.send(part)
        ok=await self.delivery_store.deliver(owner,split_result(chain),send)
        if not ok:await self.reply(event,event.plain_result('分析结果已保存，部分发送未确认。可用 /复盘 重发 再发一次，无需重新分析。'))

    @staticmethod
    def delivery(event):
        if hasattr(event,'is_private_chat'):return 'private' if event.is_private_chat() else 'group'
        return 'group' if ':GroupMessage:' in event.unified_msg_origin else 'private'

    @staticmethod
    def is_group_job(job):
        return job.get('delivery')=='group' or (not job.get('delivery') and ':GroupMessage:' in job['scope'])

    @classmethod
    def private_scope(cls,job):
        if not cls.is_group_job(job):return job['scope']
        platform=str(job['scope']).split(':',1)[0] or 'napcat'
        return f"{platform}:FriendMessage:{job['actor']}"

    async def describe(self,job):
        info=await self.db_call('queue_info',job['id']) if job['status']=='queued' else None
        text=self.status_text(job,info)
        if self.config.get('replay_backend','server')=='pc' and job['status'] in ('queued','running'):
            online=await asyncio.to_thread(self.pc_gateway.queue.status) if self.pc_gateway else {'online':False}
            if not online['online']:text+='\n电脑工作端当前离线，任务已保留，连接恢复后继续。'
        return text

    async def handle(self,event,text):
        event.should_call_llm(True)
        try:
            uin=str(event.get_self_id()) if hasattr(event,'get_self_id') else '0'
            if uin.isdigit() and uin!=self.bot_uin:
                self.bot_uin=uin
                await asyncio.to_thread((self.store.folder/'bot_identity.json').write_text,json.dumps({'uin':uin}),encoding='utf-8')
            text=normalize_control_text(text)
            if len(text)>6000:raise ReplayError('复盘参数太长，每批最多10条完整链接。')
            if await self.handle_batches(event,text):return
            request=parse_request(text);scope=event.unified_msg_origin;actor=str(event.get_sender_id())
            action=request['action'];source=request['source']
            if not text.strip() or action=='help':
                await self.reply(event,event.plain_result(HELP));return
            if action=='comment':
                await self.reply(event,event.plain_result('首版先提供实际决策对照与一致率卡片。请用 /复盘 分歧 或 /复盘 分局 南2 查看。'));return
            if action=='history':
                jobs=await self.db_call('history',scope,actor)
                await self.reply(event,event.plain_result('你的最近复盘任务：\n'+'\n'.join(f"#{j['id']} · {STATUS[j['status']]}" for j in jobs) if jobs else '当前会话还没有你的复盘任务。'));return
            if source is None and action in ('submit','import'):
                if request['seat'] is None:raise ReplayError('请发送牌谱链接和开局席位，例如 /复盘 链接 开局南家。')
                source=await self.db_call('get_pending',scope,actor)
                if not source:raise ReplayError('没有待选择席位的牌谱，或已超过5分钟，请重新发送完整链接。')
                source['seat']=request['seat']
            if source:
                if source['kind']=='replay' and source.get('seat') is None:
                    await self.db_call('save_pending',scope,actor,source)
                    await self.reply(event,event.plain_result('请指定分析谁的操作：开局东家、南家、西家或北家。\n例如 /复盘 南家（5分钟内有效）。这里指开局席位，不是当前局庄家。'));return
                job,created=await self.db_call('create',scope,actor,source,self.delivery(event))
                if job['status']=='done':await self.send_result(event,job);return
                await self.reply(event,event.plain_result(await self.describe(job)));self.wake.set();return
            job=await self.db_call('get',scope,actor,request['job'])
            if action=='cancel':
                if job['status'] in ('done','cancelled'):raise ReplayError('任务已经结束，无需取消。')
                await self.db_call('update',job['id'],status='cancelled',notified=1)
                if self.current_id==job['id']:self.stop_process()
                await self.reply(event,event.plain_result(f"复盘 #{job['id']} 已取消。"));return
            if action=='retry':
                if job['status'] not in ('failed','manual','cancelled'):raise ReplayError('只有未完成或已取消的任务可以重试。')
                await self.db_call('retry',scope,actor,job['id'])
                job=await self.db_call('get',scope,actor,job['id'])
                self.wake.set();await self.reply(event,event.plain_result(await self.describe(job)));return
            if action in ('result','differences','hand','resend','links','source'):
                if action=='source' and job.get('kind')!='recent':
                    await self.reply(event,event.plain_result('原始牌谱：'+job['url']));return
                if job['status']!='done':await self.reply(event,event.plain_result(await self.describe(job)));return
                if job.get('kind')=='recent':
                    result=json.loads(job['result_json']);batch=await asyncio.to_thread(self.batches.get,scope,actor,result['batch_id'])
                    await self.deliver_result(event,batch,await asyncio.to_thread(self.batch_chain,batch));return
                if action=='links':
                    url=await asyncio.to_thread(self.pages.single_link,job) if self.pages else report_url(job) or job['url']
                    await self.reply(event,event.plain_result(url));return
                if action=='hand' and request['hand'] is None:raise ReplayError('请指定局数，例如 /复盘 分局 南2。')
                await self.send_result(event,job,'result' if action=='resend' else action,request['hand']);return
            await self.reply(event,event.plain_result(await self.describe(job)))
        except ReplayError as exc:await self.reply(event,event.plain_result(str(exc)))
        except Exception:
            logger.exception('单牌谱复盘入口失败')
            await self.reply(event,event.plain_result('复盘处理失败，请稍后重试；原有雀魂查询仍可使用。'))

    def status_text(self,job,queue_info=None):
        result=f"{'最近对局' if job.get('kind')=='recent' else '复盘'} #{job['id']} · {STATUS[job['status']]}"
        if job.get('kind')=='recent' and job['status']=='running' and job.get('phase')!='verification':result+='\n电脑正在查找玩家、加载网页对局列表；取得后自动加入复盘批次。'
        if job['status'] in ('queued','running'):
            if queue_info is not None:result+=f"\n前方还有 {queue_info['ahead']} 个任务（含正在分析），按提交顺序处理。"
            return result+'\n结果将私聊发送。'
        if job['status']=='done':return result+'\n/复盘 结果 可重新查看。'
        if job['error']:
            error=job['error']
            if '验证' in error or 'captcha' in error.lower() or 'turnstile' in error.lower():
                error='本次未能取得上游数据，可稍后重试，或直接提供完整牌谱链接。'
            result+='\n'+error
        if job['status']=='manual':result+='\n请打开 '+FORM_URL+' 手动提交同一牌谱和席位，再发送 /复盘 报告页面链接。'
        return result

    def result_chain(self,job,view='result',hand=None):
        report=json.loads(Path(job['report_path']).read_text(encoding='utf-8'))
        data=summarize(report,job['seat'])
        if hand is not None and not any(h['kyoku']==hand for h in data['hands']):raise ReplayError('这份牌谱没有该局，请先查看总览里的分局列表。')
        card=render_replay_card(data,self.store.folder/'cards'/f"{job['id']}-{view}-{hand}.png",job['id'],view,hand)
        message=f"🀄 单牌谱复盘 #{job['id']}\nMortal {data['model']} · 一致率 {data['accordance']:.2%}（{data['matches']}/{data['total']}）"
        if self.pages:
            message+='\n\n'+self.pages.single_link(job)
        else:
            url=report_url(job)
            message+='\n\n'+('完整逐巡复盘：\n'+url if url else '总览网页暂不可用，分析结果已保存。')
        nodes=[Comp.Node(content=[Comp.Plain(message)],uin=self.bot_uin,name='慧慧 · 单牌谱复盘'),
               Comp.Node(content=MessageChain().file_image(str(card)).chain,uin=self.bot_uin,name='慧慧 · 复盘结果')]
        return MessageChain(chain=[Comp.Nodes(nodes)])

    def batch_chain(self,batch):
        data=self.batches.summary(batch);path=render_batch_card(data,self.store.folder/'cards'/(batch['id']+'.png'))
        if self.pages:
            url=self.pages.link(batch)
            rate=f"{data['accordance']:.2%}" if data['accordance'] is not None else '等待报告'
            message=f"{data.get('label') or '多牌谱复盘'} #{batch['id']}\nMortal 4.1b · 完成 {data['done']}/{len(data['games'])}\n汇总一致率 {rate}（{data['matches']}/{data['total']}）\n\n{url}"
            nodes=[Comp.Node(content=[Comp.Plain(message)],uin=self.bot_uin,name='慧慧 · 复盘汇总'),Comp.Node(content=MessageChain().file_image(str(path)).chain,uin=self.bot_uin,name='慧慧 · 一致率趋势')]
            return MessageChain(chain=[Comp.Nodes(nodes)])
        chain=MessageChain().message('🀄 多牌谱复盘 '+batch['id']+'\n').file_image(str(path))
        chain.message(f"Mortal {data['model']} · 成功{data['done']}/{len(data['games'])} · 汇总一致率 "+(f"{data['accordance']:.2%}" if data['accordance'] is not None else '暂无成功报告')+'\n')
        for game in data['games']:
            link=report_url(game) or game['url'];status=f"{game['accordance']:.2%}" if 'accordance' in game else STATUS.get(game['status'],game['status'])
            detail=''
            if game.get('start_time'):
                stamp=datetime.fromtimestamp(game['start_time'],timezone(timedelta(hours=8))).strftime('%m-%d %H:%M')
                detail=f"{stamp} · 第{game['rank']}位 · {game['score']:,}点 · 开局{'东南西北'[game['seat']]}家\n"
            chain.message(f"\n{game['position']}. {detail}{status} · 单场 #{game['job_id']}\n"+link)
        return chain

    async def handle_batches(self,event,text):
        scope=event.unified_msg_origin;actor=str(event.get_sender_id());tokens=text.strip().split();first=tokens[0] if tokens else ''
        if first=='最近':
            if not self.recent_loader:raise ReplayError('最近对局入口尚未就绪，请先用 /复盘 批量 提供完整链接。')
            if self.config.get('replay_backend')!='pc':await self.reply(event,event.plain_result('正在查询最近四麻东南对局。'))
            try:sources=await self.recent_loader(event,' '.join(tokens[1:]))
            except Exception as exc:raise ReplayError(str(exc)+'\n暂未提交复盘任务；可先用 /复盘 批量 提供链接。') from exc
            if isinstance(sources,dict) and sources.get('kind')=='recent':
                job,_=await asyncio.to_thread(create_recent,self.store,scope,actor,sources,self.delivery(event))
                self.wake.set();await self.reply(event,event.plain_result(await self.describe(job)));return True
            batch=await asyncio.to_thread(self.batches.create,scope,actor,sources,self.delivery(event),'最近对局 · '+' '.join(tokens[1:]),'time')
            self.wake.set();await self.reply(event,event.plain_result(f"最近对局已加入批次 {batch['id']}，按时间从早到晚分析，共{len(batch['items'])}场。完成后发送趋势汇总。"))
            if not batch['pending']:await self.deliver_result(event,batch,await asyncio.to_thread(self.batch_chain,batch))
            return True
        if first=='批量' or len(URL_RE.findall(text))>1:
            sources=parse_sources(text);batch=await asyncio.to_thread(self.batches.create,scope,actor,sources,self.delivery(event));self.wake.set()
            if not batch['pending']:await self.deliver_result(event,batch,await asyncio.to_thread(self.batch_chain,batch))
            else:await self.reply(event,event.plain_result(f"批次 {batch['id']} 已排队，共{len(batch['items'])}份。\n结果将私聊发送。"))
            return True
        match=re.search(r'\b[Bb][0-9a-fA-F]{12}\b',text);batch_id='B'+match[0][1:].lower() if match else ''
        actions={'汇总':'结果','进度':'状态','批量状态':'状态','批量结果':'结果','批量取消':'取消'};action=actions.get(first,first)
        if action not in ('结果','状态','取消','重发','链接','原谱'):return False
        if not batch_id and len(tokens)>1:return False
        if not batch_id and action!='取消' and not await asyncio.to_thread(self.batches.latest_is_batch,scope,actor):return False
        if action=='取消' and not batch_id:
            try:batch=await asyncio.to_thread(self.batches.active,scope,actor)
            except ReplayError:return False  # A standalone/recent job may be the active target.
        else:batch=await asyncio.to_thread(self.batches.get,scope,actor,batch_id)
        if action=='取消':
            await asyncio.to_thread(self.batches.cancel,scope,actor,batch['id'])
            if self.current_id in {j['id'] for j in batch['items']}:self.stop_process()
            await self.reply(event,event.plain_result(f"批次 {batch['id']} 的未结束任务已取消；已完成报告仍可用 /复盘 汇总 查看。"));return True
        if action=='状态':
            message=f"批次 {batch['id']}：成功{batch['done']}，等待/分析{batch['pending']}，未完成{batch['failed']}，共{len(batch['items'])}份。"
            if self.config.get('replay_backend')=='pc' and batch['pending']:
                status=await asyncio.to_thread(self.pc_gateway.queue.status) if self.pc_gateway else {'online':False}
                if not status['online']:message+='\n电脑工作端离线；任务已保留，连接恢复后自动继续。'
            receipt=await asyncio.to_thread(self.delivery_store.description,batch['id'])
            if receipt:message+='\n'+receipt
            await self.reply(event,event.plain_result(message));return True
        if action in ('链接','原谱'):
            if self.pages:
                url=await asyncio.to_thread(self.pages.link,batch)
                await self.deliver_result(event,batch,MessageChain().message('本批逐场复盘与原谱总览：\n'+url));return True
            message='\n\n'.join(f"{j['position']}. "+((report_url(j) or '报告尚未生成') if action=='链接' else j['url']) for j in batch['items'])
            await self.deliver_result(event,batch,MessageChain().message(message));return True
        await self.deliver_result(event,batch,await asyncio.to_thread(self.batch_chain,batch));return True

    async def notify_batches(self):
        for batch in await asyncio.to_thread(self.batches.ready):
            try:
                chain=await asyncio.to_thread(self.batch_chain,batch)
                async def send(part):await self.context.send_message(self.private_scope(batch),part)
                ok=await self.delivery_store.deliver(batch['id'],split_result(chain),send)
                with self.store.db() as db:db.execute('UPDATE replay_batches SET notified=? WHERE id=?',(1 if ok else 2,batch['id']))
                if not ok:logger.warning('批次 %s 的分析已完成，部分发送未确认；结果保留，可 /复盘 重发',batch['id'])
            except Exception:
                with self.store.db() as db:db.execute('UPDATE replay_batches SET notified=2 WHERE id=?',(batch['id'],))
                logger.exception('批量结果发送未确认，结果保留，可通过 /复盘 重发 读取')

    async def send_result(self,event,job,view='result',hand=None):
        chain=await asyncio.to_thread(self.result_chain,job,view,hand)
        await self.deliver_result(event,job,chain)

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
        await self.db_call('update',job['id'],notified=-1)
        try:
            if job.get('kind')=='recent' and job['status']=='done':
                result=json.loads(job['result_json']);batch=await asyncio.to_thread(self.batches.get,job['scope'],job['actor'],result['batch_id'])
                # A cached batch is already notified by notify_batches, without a second progress message.
                if not batch['pending']:
                    await self.db_call('update',job['id'],notified=1);return
                requested=result.get('requested',result['count'])
                message=f"已取得 {result['nickname']} 最近{result['count']}场四麻东南对局"+(f"（请求{requested}场，网页仅返回{result['count']}场）" if requested>result['count'] else '')+f"，已加入批次 {batch['id']}。\n按实际席位、时间从早到晚复盘，完成后发送对局列表和趋势图。\n/复盘 状态 查看进度；/复盘 取消 取消本批。"
                chain=MessageChain().message(message)
            else:chain=await asyncio.to_thread(self.result_chain,job) if job['status']=='done' else MessageChain().message(await self.describe(job))
            async def send(part):await self.context.send_message(self.private_scope(job),part)
            ok=await self.delivery_store.deliver(job['id'],split_result(chain),send)
            await self.db_call('update',job['id'],notified=1 if ok else 2)
            if not ok:logger.warning('复盘 %s 已保存，发送回执未确认，可 /复盘 重发',job['id'])
        except Exception:
            await self.db_call('update',job['id'],notified=2)
            logger.exception('复盘通知发送未确认，结果保留，可 /复盘 重发')

    async def start_pages(self):
        url=self.config.get('replay_public_url','')
        if not url:return
        try:
            from .replay_pages import ReplayPages
            self.pages=await asyncio.to_thread(ReplayPages,self.store,self.batches,url,int(self.config.get('replay_web_port',6192)))
            self.pages.start();logger.info('复盘总览网页已启动，端口 %s',self.config.get('replay_web_port',6192))
        except Exception:
            self.pages=None;logger.exception('复盘总览网页启动失败，复盘队列继续运行')

    async def close_pages(self):
        if self.pages:
            await asyncio.to_thread(self.pages.close);self.pages=None

    async def run(self):
        await self.start_pages()
        if self.config.get('replay_backend','server')=='pc':
            try:await self.run_pc()
            finally:await self.close_pages()
            return
        try:
            while True:
                await self.notify_batches()
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
        finally:
            self.stop_process();await self.close_pages()

    async def run_pc(self):
        from .replay_pc import PCGateway
        try:
            while True:
                await self.notify_batches()
                if self.pc_gateway is None:
                    try:
                        gateway=await asyncio.to_thread(PCGateway,self.store,self.store.folder/'pc_worker.json')
                        gateway.start();self.pc_gateway=gateway;self.pc_error=''
                        logger.info('Mortal电脑工作端网关已启动，仅监听本机')
                    except Exception:
                        if not self.pc_error:logger.exception('Mortal电脑工作端网关暂不可用')
                        self.pc_error='电脑工作端网关暂不可用'
                        await asyncio.sleep(5);continue
                for job in await self.db_call('unnotified'):await self.notify(job)
                await asyncio.sleep(1)
        finally:
            if self.pc_gateway is not None:
                await asyncio.to_thread(self.pc_gateway.close);self.pc_gateway=None
