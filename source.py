"""OneBot essence source; attachments only, with bounded trusted QQ downloads."""
from __future__ import annotations
import asyncio
import hashlib
import io
import ipaddress
from pathlib import Path
from urllib.parse import urlparse
import aiohttp
from PIL import Image

MAX_BYTES=20*1024*1024

def unwrap(value):
    if isinstance(value,dict) and 'retcode' in value:
        if value.get('retcode')!=0:raise RuntimeError('OneBot 接口返回失败')
        return value.get('data')
    return value

def extract_entries(payload):
    payload=unwrap(payload)
    if not isinstance(payload,list):
        raise ValueError('精华列表格式异常，本次不修改已有记录')
    result=[];text_count=0
    for e in payload:
        if not isinstance(e,dict) or not e.get('message_id'):
            raise ValueError('精华列表缺少消息编号，本次不修改已有记录')
        parts=e.get('content',e.get('message'))
        if not isinstance(parts,list):
            raise ValueError('精华列表缺少完整消息内容，本次不修改已有记录')
        found=False
        for i,x in enumerate(parts):
            if not isinstance(x,dict):raise ValueError('精华消息内容格式异常')
            if x.get('type')!='image':continue
            data=x.get('data')
            if not isinstance(data,dict):raise ValueError('图片字段异常')
            found=True
            result.append({'msg':str(e['message_id']),'idx':i,'stamp':int(e.get('operator_time') or e.get('sender_time') or 0),'url':str(data.get('url') or ''),'file':str(data.get('file') or '')})
        if not found:text_count+=1
    return result,{'messages':len(payload),'text':text_count}

def trusted_url(url):
    p=urlparse(url)
    host=(p.hostname or '').lower()
    suffixes=('qq.com','qq.com.cn','qpic.cn','gtimg.cn','myqcloud.com')
    if p.scheme not in ('http','https') or p.username or p.password or p.port not in (None,80,443):return False
    return any(host==s or host.endswith('.'+s) for s in suffixes)

async def download(url,path):
    if not trusted_url(url):raise ValueError('图片地址不是受支持的 QQ 附件地址')
    timeout=aiohttp.ClientTimeout(total=35)
    async with aiohttp.ClientSession(timeout=timeout,trust_env=False) as session:
        for _ in range(4):
            if not trusted_url(url):raise ValueError('图片重定向到不受支持的地址')
            async with session.get(url,allow_redirects=False,headers={'User-Agent':'Mozilla/5.0'}) as resp:
                if resp.status in (301,302,303,307,308):
                    from urllib.parse import urljoin
                    url=urljoin(url,resp.headers.get('Location',''));continue
                resp.raise_for_status()
                if int(resp.headers.get('Content-Length',0))>MAX_BYTES:raise ValueError('图片超过20MB')
                chunks=[];total=0
                async for block in resp.content.iter_chunked(65536):
                    total+=len(block)
                    if total>MAX_BYTES:raise ValueError('图片超过20MB')
                    chunks.append(block)
                data=b''.join(chunks);break
        else:raise ValueError('图片重定向次数过多')
    with Image.open(io.BytesIO(data)) as im:
        if im.width*im.height>40_000_000:raise ValueError('图片像素过大')
        if getattr(im,'is_animated',False):raise ValueError('动态图不属于结算截图')
        im.verify()
    digest=hashlib.sha256(data).hexdigest();dest=Path(path)/(digest+'.img')
    if not dest.exists():
        tmp=dest.with_suffix('.tmp');tmp.write_bytes(data);tmp.replace(dest)
    return digest,dest

class EssenceSource:
    def __init__(self,store,folder):
        self.store=store;self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True)
        self.locks={};self.download_lock=asyncio.Semaphore(4)
    async def sync(self,scope,bot,gid):
        lock=self.locks.setdefault(scope,asyncio.Lock())
        if lock.locked():return False
        async with lock:
            try:
                data=await asyncio.wait_for(bot.call_action(action='get_essence_msg_list',group_id=int(gid)),45)
                entries,counts=extract_entries(data)
                self.store.reconcile(scope,entries)
                self.store.audit(scope,'system','scan',counts)
                async def fetch(entry):
                    row=self.store.source(scope,entry['msg'],entry['idx'])
                    if row['hash'] and self.store.image(row['hash']) and Path(self.store.image(row['hash'])['path']).is_file():return
                    async with self.download_lock:
                        try:
                            url=entry['url']
                            if not url and entry['file']:
                                image=unwrap(await bot.call_action(action='get_image',file=entry['file']))
                                url=str(image.get('url',''))
                            try: digest,path=await download(url,self.folder)
                            except Exception:
                                # Signed URL may expire; get_image can refresh it.
                                if not entry['file']:raise
                                image=unwrap(await bot.call_action(action='get_image',file=entry['file']))
                                try:
                                    digest,path=await download(str(image.get('url','')),self.folder)
                                except Exception:
                                    original=unwrap(await bot.call_action(action='get_msg',message_id=int(entry['msg'])))
                                    parts=original.get('message',[]) if isinstance(original,dict) else []
                                    pictures=[x.get('data',{}) for x in parts if isinstance(x,dict) and x.get('type')=='image']
                                    match=next((x for x in pictures if x.get('file')==entry['file']),None)
                                    if match is None and len(pictures)==1:match=pictures[0]
                                    if match is None:raise ValueError('无法安全对应原消息中的图片')
                                    digest,path=await download(str(match.get('url','')),self.folder)
                            self.store.attach(row['id'],digest,path)
                        except Exception as exc:
                            self.store.source_error(row['id'],'图片读取失败：'+type(exc).__name__)
                await asyncio.gather(*(fetch(e) for e in entries))
                return True
            except Exception as exc:
                self.store.scan_error(scope,'精华同步失败：'+type(exc).__name__)
                return False
