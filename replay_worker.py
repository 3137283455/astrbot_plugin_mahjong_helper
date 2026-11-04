"""Isolated browser worker. Normal website interactions, no CAPTCHA solver."""
from __future__ import annotations
import asyncio,json,os,re,sys,time
from pathlib import Path
from urllib.parse import parse_qs,urlsplit

BASE='https://mjai.ekyu.moe'
TASK=re.compile(r'^[A-Za-z0-9_-]{8,100}$')
MAX_REPORT=20*1024*1024

def emit(**data):print(json.dumps(data,ensure_ascii=False),flush=True)

async def fetch_report(context,task,seconds=150):
    if not TASK.fullmatch(task):raise ValueError('无效的上游任务编号')
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        response=await context.request.get(f'{BASE}/report/{task}.json',timeout=20000,max_redirects=0)
        if response.status==200:
            if int(response.headers.get('content-length','0'))>MAX_REPORT:raise ValueError('报告超过大小限制')
            data=await response.body()
            if len(data)>MAX_REPORT:raise ValueError('报告超过大小限制')
            return json.loads(data)
        if response.status==403:raise PermissionError('上游拒绝服务器读取报告，可能需要人工验证。')
        if response.status not in (404,202,429,503):raise ValueError(f'报告服务返回 HTTP {response.status}')
        await asyncio.sleep(4 if response.status!=429 else 15)
    raise TimeoutError('上游尚未生成报告。任务编号已保存，稍后可用“复盘 重试”继续读取。')

async def run(request):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        cached=sorted(Path(os.environ.get('PLAYWRIGHT_BROWSERS_PATH','/bot/mahjong-replay-worker/browsers')).glob('chromium-*/chrome-linux64/chrome'),reverse=True)
        browser=await p.chromium.launch(headless=True,**({'executable_path':str(cached[0])} if cached else {}))
        try:
            context=await browser.new_context(locale='zh-CN')
            task=request.get('remote_task','')
            if task:
                report=await fetch_report(context,task)
            else:
                page=await context.new_page()
                response=await page.goto(BASE+'/zh-cn.html',wait_until='domcontentloaded',timeout=45000)
                # Let the site's own validation run. Do not interact with challenge frames.
                try:await page.locator('input[name="log-url"]').wait_for(timeout=15000)
                except Exception:raise PermissionError('在线复盘站点要求人工验证，服务器暂时无法自动提交。')
                await page.locator('input[name="log-url"]').fill(request['url'])
                await page.locator('select[name="player-id"]').select_option(str(request['seat']))
                await page.locator('select[name="engine"]').select_option('mortal')
                await page.locator('select[name="mortal-model-tag"]').select_option('4.1b')
                await page.get_by_text('高级选项',exact=True).click()
                await page.locator('input[name="temperature"]').fill('0.1')
                await page.locator('input[name="show-rating"]').check()
                button=page.locator('form[name="reviewForm"] button[name="submitBtn"]')
                try:await button.wait_for(state='visible',timeout=5000)
                except Exception:raise PermissionError('上游提交表单不可用。')
                deadline=time.monotonic()+30
                while not await button.is_enabled() and time.monotonic()<deadline:await asyncio.sleep(1)
                if not await button.is_enabled():raise PermissionError('在线站点的人机验证未完成，需在浏览器手动提交后导入报告链接。')
                await button.click()
                deadline=time.monotonic()+75
                while time.monotonic()<deadline:
                    task=(parse_qs(urlsplit(page.url).query).get('task') or [''])[0]
                    if TASK.fullmatch(task):break
                    if '/report/' in page.url:
                        match=re.search(r'/report/([A-Za-z0-9_-]{8,100})',urlsplit(page.url).path)
                        if match:task=match[1];break
                    await asyncio.sleep(1)
                if not TASK.fullmatch(task):
                    raise ValueError('上游未返回任务编号；请确认是四麻东南战的完整牌谱，或手动提交后导入报告。')
                emit(type='submitted',task_id=task)
                report=await fetch_report(context,task)
            emit(type='result',report=report)
        finally:await browser.close()

if __name__=='__main__':
    try:asyncio.run(run(json.loads(sys.stdin.readline())))
    except PermissionError as exc:emit(type='error',code='manual',message=str(exc))
    except TimeoutError as exc:emit(type='error',code='failed',message=str(exc))
    except Exception as exc:
        # Never expose response HTML, environment variables, or browser internals to QQ.
        emit(type='error',code='failed',message=('复盘服务请求失败：'+str(exc).split('Call log:')[0].strip())[:250])
