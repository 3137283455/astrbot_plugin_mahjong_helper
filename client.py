import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlsplit
import aiohttp

class RadarError(RuntimeError):pass

class Client:
    def __init__(self,credentials):
        private=json.loads(Path(credentials).read_text(encoding='utf8'))
        self.url=private['url'].rstrip('/');self.token=private['token']
        p=urlsplit(self.url)
        if p.scheme!='http' or p.hostname not in ('127.0.0.1','localhost') or p.username or p.password or p.path:
            raise ValueError('PerkRadar仅允许本机服务地址。')
        if len(self.token)<32:raise ValueError('PerkRadar凭据长度不足。')
        self.lock=asyncio.Lock();self.cache=None;self.stamp=0
    async def request(self,path,method='GET',timeout=8):
        try:
            async with aiohttp.ClientSession(trust_env=False,timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                async with session.request(method,self.url+path,headers={'Authorization':'Bearer '+self.token},allow_redirects=False) as response:
                    if response.status==409:raise RadarError('已有抓取任务正在运行。')
                    if response.status==429:raise RadarError('刷新正在冷却，请稍后再试。')
                    if response.status!=200:raise RadarError('优惠服务暂时不可用（HTTP '+str(response.status)+'）。')
                    chunks=[];size=0
                    async for chunk in response.content.iter_chunked(65536):
                        size+=len(chunk)
                        if size>8*1024*1024:raise RadarError('优惠接口响应过大。')
                        chunks.append(chunk)
                    obj=json.loads(b''.join(chunks))
                    if not isinstance(obj,dict):raise RadarError('优惠接口格式异常。')
                    return obj
        except (aiohttp.ClientError,TimeoutError,json.JSONDecodeError,UnicodeError):
            raise RadarError('优惠服务连接超时或返回异常，请稍后重试。') from None
    async def board(self,force=False):
        async with self.lock:
            if self.cache is not None and not force and time.monotonic()-self.stamp<45:return self.cache
            items=[];offset=0
            for page in range(10):
                data=await self.request('/api/deals?limit=500&offset='+str(offset))
                if not isinstance(data.get('deals'),list):raise RadarError('优惠列表结构异常。')
                items.extend(data['deals']);paging=data.get('pagination') or {}
                if not paging.get('hasMore'):break
                if not data['deals']:raise RadarError('优惠分页返回异常。')
                offset+=len(data['deals'])
            else:raise RadarError('优惠条目超出读取上限，请联系管理员。')
            data['deals']=items;self.cache=data;self.stamp=time.monotonic()
            return data
    async def refresh(self):
        await self.request('/api/refresh','POST',timeout=60)
        self.stamp=0
        return await self.board(force=True)
