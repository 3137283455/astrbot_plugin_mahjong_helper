"""Authenticated, current-season manual review. Uses the command's store boundary."""
import base64
import hashlib
import json
from pathlib import Path

from . import periods
from .core import validate


class ManualReview:
    def __init__(self, actions):
        self.actions=actions;self.plugin=actions.plugin

    @staticmethod
    def status(row):return row['override_status'] or row['status'] or 'download_error'

    def current(self,scope):
        if not self.plugin.store.group(scope):raise ValueError('该群尚未接入群数据')
        period=periods.select(self.plugin.store,scope,None)
        if period is None:raise ValueError('请先配置当前赛季，人工复核不读取封存记录')
        return period

    def row(self,scope,ident):
        self.current(scope)
        if type(ident) is not int:raise ValueError('图片编号不正确')
        row=self.plugin.store.get_source(scope,ident)
        if not row:raise ValueError('图片已移除或不属于当前赛季，请刷新列表')
        periods.assert_current(self.plugin.store,scope,row)
        return row

    def version(self,row):
        current=periods.current_ids(self.plugin.store,row['scope'])
        data={k:row.get(k) for k in ('id','scope','hash','active','status','result','raw','reason','error','override_status','override_result','override_reason')}
        data['current']=sorted(current) if current is not None else None
        return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    def image_path(self,row):
        if not row.get('path'):return None
        path=Path(row['path']).resolve();root=(Path(self.plugin.folder)/'images').resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size>12*1024*1024:return None
        return path

    def brief(self,row):
        return {'id':row['id'],'status':self.status(row),'stamp':row['stamp'],
                'reason':row['override_reason'] or row['reason'] or row['error'] or '',
                'has_image':self.image_path(row) is not None,
                'busy':row['hash'] in getattr(self.plugin.recognizer,'running',set())}

    def listing(self,scope,kind='pending'):
        period=self.current(scope)
        if kind not in ('pending','all'):raise ValueError('筛选条件不正确')
        rows=self.plugin.store.rows(scope)
        if kind=='pending':rows=[r for r in rows if self.status(r) in ('review','error','local_error','download_error')]
        if kind=='pending':rows.sort(key=lambda r:(self.status(r)!='review',-r['stamp'],-r['id']))
        return {'items':[self.brief(r) for r in rows],'period':period['name']}

    def detail(self,scope,ident):
        row=self.row(scope,ident);candidates=[]
        for value in (row['override_result'],row['result']):
            if value:
                try:
                    obj=json.loads(value)
                    if isinstance(obj,dict) and obj.get('players'):candidates.append(obj)
                except (ValueError,TypeError):pass
        try:
            raw=json.loads(row['raw'] or '[]')
            if isinstance(raw,list):
                for obj in raw:
                    if isinstance(obj,dict) and isinstance(obj.get('players'),list) and obj.get('mode') in (3,4):
                        if obj not in candidates:candidates.append(obj)
        except (ValueError,TypeError):pass
        # Only expose editable fields, not OCR logs, request metadata or local paths.
        candidates=[{'mode':v.get('mode'),'players':[{'name':p.get('name',''),'place':p.get('place',i+1),'points':p.get('points','')} for i,p in enumerate(v['players']) if isinstance(p,dict)]} for v in candidates]
        names=sorted({p['name'] for m in self.plugin.store.matches(scope) for p in m['result']['players']})
        return {**self.brief(row),'version':self.version(row),'candidates':candidates,'names':names}

    def image(self,scope,ident):
        path=self.image_path(self.row(scope,ident))
        if path is None:raise ValueError('原图尚未缓存或不可用，请先同步QQ精华')
        data=path.read_bytes()
        if data.startswith(b'\x89PNG\r\n\x1a\n'):mime='image/png'
        elif data.startswith(b'\xff\xd8\xff'):mime='image/jpeg'
        elif data[:6] in (b'GIF87a',b'GIF89a'):mime='image/gif'
        elif data.startswith(b'RIFF') and data[8:12]==b'WEBP':mime='image/webp'
        else:raise ValueError('原图格式暂不支持预览')
        return {'mime':mime,'image':base64.b64encode(data).decode()}

    def apply(self,scope,ident,body,actor):
        row=self.row(scope,ident)
        if body.get('version')!=self.version(row):raise ValueError('这张图片的识别结果已变化，请重新打开后核对')
        if row['hash'] in getattr(self.plugin.recognizer,'running',set()):raise ValueError('这张图片正在识别，请稍后再核对')
        action=body.get('action')
        if action not in ('confirm','ignore'):raise ValueError('不支持这个复核操作')
        if action=='confirm':
            if body.get('checked') is not True:raise ValueError('请先核对完整终局结算原图')
            if not self.image_path(row):raise ValueError('没有可核对的原图，不能确认入库')
            obj=body.get('result')
            if not isinstance(obj,dict):raise ValueError('请填写完整结算数据')
            result=validate({**obj,'is_result':True},approve=True)
            if sum(p['points'] for p in result['players'])!={3:105000,4:100000}[result['mode']] and body.get('nonstandard') is not True:
                raise ValueError('总持点不是标准总额，请核对并明确确认自定义规则')
            self.plugin.store.override(scope,row,'accepted',result,'网页人工核对原图')
        elif row['hash']:self.plugin.store.override(scope,row,'ignored',reason='网页人工忽略')
        else:self.plugin.store.exclude_source(scope,row,'网页人工忽略无法读取的精华图片')
        self.plugin.store.audit(scope,actor,'网页确认' if action=='confirm' else '网页忽略',{'id':ident,'before':self.status(row),'result':result if action=='confirm' else None})
        if action=='confirm':
            # Overrides are already the verified glyph library's training boundary.
            bank=getattr(self.plugin.recognizer,'verified_names',None)
            if bank is not None:bank.cache.pop(scope,None)
            recheck=getattr(self.plugin.recognizer,'recheck_reviews',None)
            if recheck:self.plugin.spawn(recheck())
        return {'ok':True,'id':ident,'action':action}

    async def api(self):
        from astrbot.api.web import request,json_response,error_response
        if not request.username:return error_response('仅限管理员访问',status_code=403)
        if self.actions.closed:return error_response('插件正在重新加载',status_code=503)
        body=await request.json(default={})
        if not isinstance(body,dict):return error_response('请求格式不正确')
        if body.get('period') not in (None,'current'):return error_response('封存赛季只读，仅复核当前赛季')
        try:
            scope=str(body.get('scope',''));action=body.get('action')
            if action=='list':data=self.listing(scope,body.get('filter','pending'))
            elif action=='get':data=self.detail(scope,body.get('id'))
            elif action=='image':data=self.image(scope,body.get('id'))
            else:data=self.apply(scope,body.get('id'),body,request.username)
            return json_response({'review':data})
        except (ValueError,TypeError,OSError) as exc:
            return error_response(str(exc) if isinstance(exc,ValueError) else '图片暂不可用，请刷新重试')
