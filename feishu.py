"""Small fixed-origin Feishu client. Credentials stay in the plugin data folder."""
from __future__ import annotations
import asyncio
import hashlib
import io
import json
import re
import time
from pathlib import Path
import aiohttp
from PIL import Image

ORIGIN='https://open.feishu.cn/open-apis/'


class FeishuError(RuntimeError):pass


class Feishu:
    def __init__(self,credentials,app_token):
        self.credentials=Path(credentials);self.app=app_token
        self.token='';self.expires=0;self.lock=asyncio.Lock();self.calls=0

    async def auth(self):
        async with self.lock:
            if time.time()<self.expires:return self.token
            private=json.loads(self.credentials.read_text(encoding='utf-8'))
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35)) as session:
                async with session.post(ORIGIN+'auth/v3/tenant_access_token/internal',json=private,allow_redirects=False) as response:
                    obj=await response.json(content_type=None)
                    if response.status!=200 or obj.get('code')!=0:raise FeishuError('飞书认证失败，错误码 '+str(obj.get('code',response.status)))
            self.token=obj['tenant_access_token'];self.expires=time.time()+int(obj['expire'])-120
            return self.token

    async def request(self,method,path,body=None,params=None,form=None):
        if path.startswith('/') or '://' in path:raise ValueError('仅允许飞书官方接口路径')
        headers={'Authorization':'Bearer '+await self.auth()}
        self.calls+=1
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.request(method,ORIGIN+path,json=body,params=params,data=form,headers=headers,allow_redirects=False) as response:
                chunks=[];size=0
                async for chunk in response.content.iter_chunked(65536):
                    size+=len(chunk)
                    if size>8*1024*1024:raise FeishuError('飞书响应过大')
                    chunks.append(chunk)
                data=b''.join(chunks)
                try:obj=json.loads(data)
                except ValueError:raise FeishuError('飞书返回格式异常') from None
                if response.status!=200 or obj.get('code')!=0:
                    if obj.get('code') in (99991663,99991668):self.expires=0
                    raise FeishuError('飞书接口失败：'+str(obj.get('code',response.status)))
                return obj.get('data') or {}

    def table_path(self,table=''):
        return 'bitable/v1/apps/'+self.app+'/tables'+('/'+table if table else '')

    async def listing(self,path,params=None):
        items=[];seen=set();query={'page_size':100,**(params or {})}
        for _ in range(1000):
            obj=await self.request('GET',path,params=query);items.extend(obj.get('items') or [])
            if not obj.get('has_more'):return items
            token=obj.get('page_token')
            if not token or token in seen:raise FeishuError('飞书分页异常；本次同步取消')
            seen.add(token);query['page_token']=token
        raise FeishuError('飞书分页超限；本次同步取消')

    async def records(self,table):
        return await self.listing(self.table_path(table)+'/records',{'automatic_fields':'true','user_id_type':'open_id'})

    async def create(self,table,fields):
        data=await self.request('POST',self.table_path(table)+'/records',{'fields':fields})
        return data['record']

    async def update(self,table,record,fields):
        return await self.request('PUT',self.table_path(table)+'/records/'+record,{'fields':fields})

    async def upload(self,path):
        path=Path(path)
        if not path.is_file() or not 0<path.stat().st_size<=20*1024*1024:raise FeishuError('原图缺失或超过20MB')
        with path.open('rb') as file:
            form=aiohttp.FormData()
            for key,value in {'file_name':path.name,'parent_type':'bitable_image','parent_node':self.app,'size':str(path.stat().st_size)}.items():form.add_field(key,value)
            form.add_field('file',file,filename=path.name,content_type='application/octet-stream')
            obj=await self.request('POST','drive/v1/medias/upload_all',form=form)
        return obj['file_token']

    async def download(self,token,folder):
        if not re.fullmatch(r'[A-Za-z0-9_-]{10,200}',str(token)):raise ValueError('原图附件标识无效')
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.get(ORIGIN+'drive/v1/medias/'+token+'/download',headers={'Authorization':'Bearer '+await self.auth()},allow_redirects=False) as response:
                if response.status!=200:raise FeishuError('原图附件读取失败：'+str(response.status))
                chunks=[];size=0
                async for chunk in response.content.iter_chunked(65536):
                    size+=len(chunk)
                    if size>20*1024*1024:raise ValueError('原图附件超过20MB')
                    chunks.append(chunk)
        data=b''.join(chunks)
        with Image.open(io.BytesIO(data)) as image:
            if image.width*image.height>40_000_000 or getattr(image,'n_frames',1)!=1:raise ValueError('附件必须是单张结算截图')
            image.verify()
        digest=hashlib.sha256(data).hexdigest();folder=Path(folder);folder.mkdir(exist_ok=True)
        path=folder/(digest+'.png')
        if not path.exists():path.write_bytes(data)
        return digest,path


# Use text status fields so free-tier tables need no paid advanced permissions.
NUMBERS={'模式','精华来源编号','入库时间','一位持点','二位持点','三位持点','四位持点（三麻留空）',
         '场次','一位次数','二位次数','三位次数','四位次数','一位率','二位率','三位率','四位率',
         '被飞次数','被飞率','平均顺位','平均终局持点','最高终局持点','最低终局持点','重试次数'}
DATES={'开始时间','封存时间','精华时间','对局时间（可空）','入库时间','更新时间','最近成功时间'}


async def bootstrap(client,blueprint,initial_table=None):
    existing=await client.listing(client.table_path());tables={r['name']:r['table_id'] for r in existing};result={}
    for spec in blueprint['tables']:
        names=list(spec['fields'])
        if spec['key']=='matches':names.append('冲突处理')
        fields=[{'field_name':n,'type':17 if n=='原图附件' else 5 if n in DATES else 2 if n in NUMBERS else 1} for n in names]
        table=tables.get(spec['name'])
        if not table:
            obj=await client.request('POST',client.table_path(),{'table':{'name':spec['name'],'fields':fields}})
            table=obj['table_id']
        current=await client.listing(client.table_path(table)+'/fields')
        by_name={f['field_name']:f for f in current}
        # Rename the primary text column of the user's empty starter table.
        if table==initial_table and '文本' in by_name and names[0] not in by_name:
            await client.request('PUT',client.table_path(table)+'/fields/'+by_name['文本']['field_id'],fields[0])
            by_name[names[0]]=fields[0]
        for field in fields:
            if field['field_name'] not in by_name:
                await client.request('POST',client.table_path(table)+'/fields',field)
            elif by_name[field['field_name']]['type']!=field['type']:
                raise FeishuError('字段类型不匹配：'+field['field_name'])
        result[spec['key']]=table
    return result
