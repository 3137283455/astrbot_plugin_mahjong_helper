"""Windows desktop worker for official Mortal 4.1b. No CAPTCHA solver."""
from __future__ import annotations
import argparse,asyncio,ctypes,json,logging,logging.handlers,os,re,signal,socket,subprocess,sys,time,urllib.error,urllib.request
from contextlib import suppress
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parent))

BASE='https://mjai.ekyu.moe';MODEL='4.1b';TASK=re.compile(r'^[A-Za-z0-9_-]{8,100}$')
CREATE_NO_WINDOW=0x08000000 if os.name=='nt' else 0

class Offline(Exception):pass
class StaleJob(Exception):pass
class RPCError(Exception):
    def __init__(self,status,message):self.status=status;super().__init__(message)

def write_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)

class ChildJob:
    """Kill only this worker's children when Windows closes the worker process."""
    def __init__(self):
        self.handle=None
        if os.name!='nt':return
        from ctypes import wintypes
        class BASIC(ctypes.Structure):
            _fields_=[('PerProcessUserTimeLimit',ctypes.c_int64),('PerJobUserTimeLimit',ctypes.c_int64),('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',wintypes.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]
        class IO(ctypes.Structure):_fields_=[(n,ctypes.c_uint64) for n in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
        class EXT(ctypes.Structure):_fields_=[('BasicLimitInformation',BASIC),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateJobObjectW.restype=wintypes.HANDLE;kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
        kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.CreateJobObjectW(None,None);info=EXT();info.BasicLimitInformation.LimitFlags=0x2000
        if not handle or not kernel.SetInformationJobObject(handle,9,ctypes.byref(info),ctypes.sizeof(info)):raise OSError('无法建立工作端子进程保护')
        self.handle=handle;self.kernel=kernel
    def add(self,process):
        if self.handle and not self.kernel.AssignProcessToJobObject(self.handle,int(process._handle)):
            process.terminate();raise OSError('无法保护工作端子进程')
    def close(self):
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None

class Worker:
    def __init__(self,config_path):
        self.config_path=Path(config_path).resolve();self.config=json.loads(self.config_path.read_text(encoding='utf-8'))
        self.root=self.config_path.parent.parent;self.private=self.config_path.parent;self.state_path=self.private/'current.json'
        self.current=json.loads(self.state_path.read_text(encoding='utf-8')) if self.state_path.exists() else None
        self.valid=False;self.cancelled=False;self.connected=False;self.tunnel=None;self.tunnel_lock=asyncio.Lock();self.children=ChildJob()
        self.playwright=None;self.browser=None;self.page=None;self.browser_process=None;self.ssh_log=None
        self.status='正在启动';self.last_error='';self.active_phase='offline';self.stopping=False
        logs=self.root/'logs';logs.mkdir(exist_ok=True)
        handler=logging.handlers.RotatingFileHandler(logs/'worker.log',maxBytes=1024*1024,backupCount=3,encoding='utf-8')
        logging.basicConfig(level=logging.INFO,handlers=[handler],format='%(asctime)s %(levelname)s %(message)s')

    def persist(self):
        if self.current:write_json(self.state_path,self.current)
        else:self.state_path.unlink(missing_ok=True)
        self.write_status()
    def write_status(self):
        write_json(self.root/'status.json',{'status':self.status,'connected':self.connected,'phase':self.active_phase,'job':self.current.get('id') if self.current else None,'model':MODEL,'ssh_pid':self.tunnel.pid if self.tunnel and self.tunnel.poll() is None else None,'worker_pid':os.getpid(),'updated':time.strftime('%Y-%m-%d %H:%M:%S'),'error':self.last_error})

    async def ensure_tunnel(self):
        async with self.tunnel_lock:
            if self.tunnel and self.tunnel.poll() is None:return
            if self.ssh_log:self.ssh_log.close()
            self.ssh_log=(self.root/'logs/ssh.log').open('w',encoding='utf-8')
            c=self.config
            args=[c.get('ssh_exe','ssh.exe'),'-N','-T','-i',c['key_path'],'-p',str(c.get('ssh_port',22)),'-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ExitOnForwardFailure=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+c['known_hosts'],'-L',f"127.0.0.1:{c.get('local_port',18769)}:127.0.0.1:{c.get('remote_port',18769)}",c['ssh_user']+'@'+c['ssh_host']]
            self.tunnel=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=self.ssh_log,creationflags=CREATE_NO_WINDOW)
            self.children.add(self.tunnel);logging.info('已启动专用SSH连接')

    def request(self,action,payload):
        data=json.dumps(payload,ensure_ascii=False).encode();url=f"http://127.0.0.1:{self.config.get('local_port',18769)}/worker/{action}"
        request=urllib.request.Request(url,data=data,headers={'Authorization':'Bearer '+self.config['token'],'Content-Type':'application/json'})
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request,timeout=120 if action=='result' else 12) as response:return json.loads(response.read(1024*1024))
        except urllib.error.HTTPError as exc:
            try:message=json.loads(exc.read(4096)).get('error','工作端请求失败')
            except Exception:message='工作端请求失败'
            raise RPCError(exc.code,message) from None
        except (OSError,ValueError):raise Offline('与服务器的连接暂时中断') from None

    async def rpc(self,action,payload):
        await self.ensure_tunnel()
        return await asyncio.to_thread(self.request,action,payload)

    def payload(self):
        return {k:self.current.get(k,'') for k in ('id','lease_token','remote_task','phase')} if self.current else {}

    async def heartbeat(self):
        delay=2
        while True:
            payload=self.payload()
            try:
                reply=await self.rpc('resume' if payload else 'heartbeat',payload)
                self.connected=True;self.last_error='';delay=2
                if not self.current:self.active_phase='online'
                if self.current and self.current['id']==payload.get('id') and self.current['lease_token']==payload.get('lease_token'):
                    self.valid=bool(reply.get('keep'));self.cancelled=not self.valid
                self.status='等待人工验证' if self.current and self.current.get('phase')=='verification' else '处理复盘任务' if self.current else '在线，等待任务'
                self.write_status();await asyncio.sleep(10)
            except RPCError as exc:
                if exc.status==409 and self.current and self.current['id']==payload.get('id'):self.cancelled=True
                self.valid=False;self.connected=False;self.last_error=str(exc);self.status='连接异常，自动重试';self.write_status();await asyncio.sleep(delay);delay=min(30,delay*2)
            except (Offline,OSError):
                self.valid=False;self.connected=False;self.status='断线，自动重连中';self.write_status();await asyncio.sleep(delay);delay=min(30,delay*2)

    async def ensure_valid(self):
        while not self.valid:
            if self.cancelled:raise StaleJob('任务已结束或被重新分配')
            await asyncio.sleep(.5)
        if self.cancelled:raise StaleJob('任务已取消')

    async def progress(self,phase,**fields):
        self.current.update(fields,phase=phase);self.active_phase=phase;self.status='等待人工验证' if phase=='verification' else '处理复盘任务';self.persist()
        try:
            reply=await self.rpc('progress',self.payload())
            self.valid=bool(reply.get('keep'));self.cancelled=not self.valid
        except Offline:self.valid=False;self.connected=False

    async def ensure_browser(self):
        if self.browser and self.browser.is_connected():return
        from playwright.async_api import async_playwright
        if self.playwright is None:self.playwright=await async_playwright().start()
        port=int(self.config.get('browser_port',9223));endpoint=f'http://127.0.0.1:{port}'
        if self.browser_process and self.browser_process.poll() is None:
            self.browser=await self.playwright.chromium.connect_over_cdp(endpoint);return
        # A dedicated profile; never attach to a personal browser or expose CDP over SSH.
        browser=Path(self.config['browser_exe']);profile=self.private/'browser-profile';profile.mkdir(exist_ok=True)
        args=[str(browser),f'--remote-debugging-port={port}','--remote-debugging-address=127.0.0.1',f'--user-data-dir={profile}','--no-first-run','--no-default-browser-check','about:blank']
        self.browser_process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.children.add(self.browser_process)
        for _ in range(45):
            try:
                self.browser=await self.playwright.chromium.connect_over_cdp(endpoint,timeout=2000)
                logging.info('专用浏览器已连接');return
            except Exception:await asyncio.sleep(1)
        raise RuntimeError('无法启动专用浏览器，请检查Edge或Chrome是否可用')

    async def get_page(self):
        await self.ensure_browser()
        if self.page is None or self.page.is_closed():
            context=self.browser.contexts[0];self.page=await context.new_page()
        return self.page

    async def fetch_report(self,task):
        if not TASK.fullmatch(task):raise ValueError('报告编号无效')
        await self.progress('fetching',remote_task=task);page=await self.get_page();context=self.browser.contexts[0]
        deadline=time.monotonic()+300
        while time.monotonic()<deadline:
            await self.ensure_valid()
            response=await context.request.get(f'{BASE}/report/{task}.json',timeout=25000,max_redirects=0)
            if response.status==200:
                data=await response.body()
                if len(data)>20*1024*1024:raise ValueError('报告超过大小限制')
                return json.loads(data)
            if response.status==403:
                await page.goto(f'{BASE}/report/{task}.json',wait_until='domcontentloaded',timeout=45000)
                await self.progress('verification')
                while True:
                    await self.ensure_valid()
                    try:
                        report=json.loads(await page.locator('pre').inner_text(timeout=1000))
                        if isinstance(report,dict) and isinstance(report.get('review'),dict):return report
                    except Exception:pass
                    if page.is_closed():raise RuntimeError('浏览器页面已关闭，请重新提交任务')
                    await asyncio.sleep(3)
            if response.status not in (404,202,429,503):raise RuntimeError(f'报告服务返回HTTP {response.status}')
            await asyncio.sleep(10 if response.status==429 else 4)
        raise RuntimeError('报告尚未生成，任务号已保存，可在机器人中使用 /复盘 重试')

    async def analyze(self):
        job=self.current
        if job.get('model')!=MODEL:raise ValueError('工作端只接受官方Mortal4.1b任务')
        if job.get('remote_task'):return await self.fetch_report(job['remote_task'])
        if job.get('submitting'):raise RuntimeError('提交时工作端发生中断，暂不能确认上游任务号；请检查浏览器后 /复盘 重试，避免重复提交')
        await self.progress('browser');page=await self.get_page()
        await page.goto(BASE+'/zh-cn.html',wait_until='domcontentloaded',timeout=45000)
        await page.locator('input[name="log-url"]').fill(job['url'],timeout=30000)
        await page.locator('select[name="player-id"]').select_option(str(job['seat']))
        await page.locator('select[name="engine"]').select_option('mortal')
        await page.locator('select[name="mortal-model-tag"]').select_option(MODEL)
        await page.get_by_text('高级选项',exact=True).click()
        await page.locator('input[name="temperature"]').fill('0.1')
        await page.locator('input[name="show-rating"]').check()
        button=page.locator('form[name="reviewForm"] button[name="submitBtn"]');begin=time.monotonic()
        while not await button.is_enabled():
            await self.ensure_valid()
            if time.monotonic()-begin>20 and self.current.get('phase')!='verification':await self.progress('verification')
            if page.is_closed():raise RuntimeError('专用复盘页面已关闭')
            await asyncio.sleep(1)
        await self.ensure_valid();self.current['submitting']=True;self.persist();await button.click()
        for _ in range(100):
            task=(parse_qs(urlsplit(page.url).query).get('task') or [''])[0]
            if not task:
                match=re.search(r'/report/([A-Za-z0-9_-]{8,100})',page.url);task=match[1] if match else ''
            if TASK.fullmatch(task):
                await self.progress('submitted',remote_task=task);return await self.fetch_report(task)
            await self.ensure_valid();await asyncio.sleep(1)
        raise RuntimeError('上游没有返回任务号，请检查是否为四麻东南战的完整牌谱')

    async def run_job(self):
        try:
            await self.ensure_valid()
            recent=self.current.get('kind')=='recent'
            result_file=self.private/('report-'+self.current['id']+'.json')
            if not result_file.exists():
                if recent:
                    from amae import fetch_recent
                    report=await fetch_recent(self)
                else:
                    report=await self.analyze()
                    if str(report.get('review',{}).get('model_tag'))!=MODEL:raise ValueError('返回的报告不是Mortal4.1b，未采用该结果')
                write_json(result_file,report)
            await self.progress('uploading')
            while True:
                await self.ensure_valid()
                try:
                    report=json.loads(result_file.read_text(encoding='utf-8'))
                    reply=await self.rpc('result',{**self.payload(),('records_result' if recent else 'report'):report})
                    if reply.get('status')=='done':break
                    raise RuntimeError('服务器未确认报告接收')
                except Offline:self.valid=False;self.connected=False
            logging.info('复盘任务 %s 已回传',self.current['id'])
        except StaleJob:logging.info('任务已取消或租约结束')
        except RPCError as exc:
            if exc.status!=409:
                with suppress(StaleJob):await self.fail(str(exc))
        except Exception as exc:
            # Only concise exception messages; never log configuration or tokens.
            with suppress(StaleJob):await self.fail((str(exc).split('Call log:')[0] or type(exc).__name__)[:300])
        self.current=None;self.valid=False;self.cancelled=False;self.active_phase='online';self.persist()

    async def fail(self,message):
        logging.warning('任务暂未完成：%s',message)
        while True:
            await self.ensure_valid()
            try:await self.rpc('fail',{**self.payload(),'message':message});return
            except Offline:self.valid=False;self.connected=False
            except RPCError as exc:
                if exc.status==409:return
                await asyncio.sleep(5)

    async def run(self):
        heartbeat=asyncio.create_task(self.heartbeat())
        try:
            while True:
                if self.current:
                    await self.run_job();continue
                if not self.connected:await asyncio.sleep(1);continue
                try:
                    reply=await self.rpc('claim',{})
                    if reply.get('job'):
                        self.current=reply['job'];self.valid=True;self.cancelled=False;self.persist();continue
                except (Offline,RPCError,OSError):self.connected=False
                await asyncio.sleep(3)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):await heartbeat
            self.children.close()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    lock=Path(args.config).parent/'worker.lock';lock.parent.mkdir(parents=True,exist_ok=True)
    with lock.open('a+b') as stream:
        if os.name=='nt':
            import msvcrt
            stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0)
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:return
        asyncio.run(Worker(args.config).run())

if __name__=='__main__':main()
