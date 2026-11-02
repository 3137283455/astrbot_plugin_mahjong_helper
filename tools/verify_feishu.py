"""Live API round-trip using isolated synthetic rows, removed in finally.

Never opens the production SQLite database or changes production editor settings.
"""
import argparse
import asyncio
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path
from PIL import Image,ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_group_data.feishu import Feishu
from astrbot_plugin_group_data.online import OnlineSync,normalize
from astrbot_plugin_group_data.store import Store
from astrbot_plugin_group_data import periods
from astrbot_plugin_group_data.core import validate


async def verify(args):
    target=json.loads((args.data_dir/'feishu_target.json').read_text(encoding='utf-8'))
    client=Feishu(args.data_dir/'feishu_credentials.json',target['app_token'])
    scope='verify:'+uuid.uuid4().hex;target={**target,'scope':scope,'editor_ids':[target['bot_open_id']]}
    checks=[]
    try:
        with tempfile.TemporaryDirectory(prefix='feishu-check-',dir=args.work_dir) as temp:
            folder=Path(temp);store=Store(folder/'test.db');store.enable(scope,'verify',scope)
            image=Image.new('RGB',(800,320),'white');ImageDraw.Draw(image).text((40,100),'SYNTHETIC SYNC TEST - NOT A REAL GAME',fill='black');image.save(folder/'test.png')
            def seed(msg):
                entries=[{'msg':r['msg'],'idx':0,'stamp':r['stamp']} for r in store.db.execute('SELECT * FROM sources')]
                store.reconcile(scope,[*entries,{'msg':str(msg),'idx':0,'stamp':int(time.time())}]);row=store.source(scope,str(msg),0)
                data=validate({'is_result':True,'mode':4,'confidence':1,'players':[
                    {'name':'同步验证'+str(msg)+name,'place':i+1,'points':points}
                    for i,(name,points) in enumerate(zip('甲乙丙丁',[50000,30000,15000,5000]))]})
                store.attach(row['id'],'test'+str(msg),folder/'test.png');store.save('test'+str(msg),'accepted',data)
                return store.get_source(scope,row['id'])
            seed(1);periods.cutover(store,scope);source=seed(2)
            online=OnlineSync(store,folder,target,client)
            try:
                await online.once();checks.append('real_api_export_and_attachment');print('export passed',flush=True)
                key=scope+':'+periods.select(store,scope)['id']+':'+str(source['id']);rid=online.links()[key]['record_id']
                table=target['tables']['matches']
                initial=next(r for r in await client.records(table) if r['record_id']==rid)
                actor=(initial.get('last_modified_by') or {}).get('id')
                if not actor:raise ValueError('真实接口未返回编辑者身份')
                # API edits are attributed to the application identity, which may
                # differ from its chat bot open_id. Grant it only in this test object.
                online.allowed.add(actor)
                await client.update(table,rid,{'一位昵称':'同步验证_在线改名','一位持点':51000,'二位持点':29000})
                info=await online.once();print('online_edit_check',json.dumps(info,ensure_ascii=False),flush=True)
                assert store.matches(scope)[0]['result']['players'][0]['name']=='同步验证_在线改名'
                checks.append('real_api_online_to_local')
                data=store.matches(scope)[0]['result'];data['players'][0]['name']='同步验证_本地改名'
                store.override(scope,source,'accepted',data)
                await online.once();rows=await client.records(table)
                assert normalize(next(r for r in rows if r['record_id']==rid)['fields'])['一位昵称']=='同步验证_本地改名'
                checks.append('real_api_local_to_online')
                await client.update(table,rid,{'确认状态':'停用'});await online.once();assert not store.matches(scope)
                await client.update(table,rid,{'确认状态':'已确认'});await online.once();assert len(store.matches(scope))==1
                checks.append('real_api_soft_disable_and_restore')
                historic=next(k for k in online.links() if k!=key);hrid=online.links()[historic]['record_id']
                await client.update(table,hrid,{'一位昵称':'同步验证_禁止改历史'});await online.once()
                assert store.matches(scope,period='历史')[0]['result']['players'][0]['name']=='同步验证1甲'
                checks.append('real_api_archive_immutable')
            finally:store.close()
    finally:
        removed=0
        for table in target['tables'].values():
            rows=await client.records(table)
            ids=[r['record_id'] for r in rows if normalize(r.get('fields') or {}).get('群标识')==scope]
            for i in range(0,len(ids),100):
                await client.request('POST',client.table_path(table)+'/records/batch_delete',{'records':ids[i:i+100]});removed+=len(ids[i:i+100])
        print(json.dumps({'checks':checks,'synthetic_rows_removed':removed,'production_database_changed':False},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-dir',required=True,type=Path);p.add_argument('--work-dir',required=True,type=Path)
    asyncio.run(verify(p.parse_args()))
