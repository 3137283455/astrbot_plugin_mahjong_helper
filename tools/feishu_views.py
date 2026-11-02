"""Set up filtered views after cutover; never duplicates editable detail records."""
import argparse
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_group_data import periods
from astrbot_plugin_group_data.store import Store
from astrbot_plugin_group_data.feishu import Feishu


async def configure(args):
    target=json.loads((args.data_dir/'feishu_target.json').read_text(encoding='utf-8'))
    store=Store(args.data_dir/'group_data.db')
    try:current=periods.select(store,target['scope'])['id'];historic=periods.select(store,target['scope'],'历史')['id']
    finally:store.close()
    client=Feishu(args.data_dir/'feishu_credentials.json',target['app_token'])
    specs={
        'periods':[('当前周期',[('状态','进行中')]),('历史周期',[('状态','已封存')])],
        'matches':[('当前周期全部',[('周期ID',current),('确认状态','已确认')]),
                   ('当前周期三麻',[('周期ID',current),('模式','3'),('确认状态','已确认')]),
                   ('当前周期四麻',[('周期ID',current),('模式','4'),('确认状态','已确认')]),
                   ('待核对',[('周期ID',current),('确认状态','待核对')]),
                   ('已停用',[('周期ID',current),('确认状态','停用')]),
                   ('历史归档',[('周期ID',historic)])],
        'players':[('当前三麻统计',[('周期ID',current),('模式','3')]),
                   ('当前四麻统计',[('周期ID',current),('模式','4')]),
                   ('历史三麻统计',[('周期ID',historic),('模式','3')]),
                   ('历史四麻统计',[('周期ID',historic),('模式','4')])]}
    configured={}
    for key,views in specs.items():
        table=target['tables'][key];base=client.table_path(table)
        fields={r['field_name']:r for r in await client.listing(base+'/fields')}
        existing={r['view_name']:r['view_id'] for r in await client.listing(base+'/views')}
        hidden=[fields[n]['field_id'] for n in ('对局ID','群标识','周期ID','数据版本','统计ID','玩家标识') if n in fields and not fields[n].get('is_primary')]
        for name,conditions in views:
            vid=existing.get(name)
            if not vid:
                result=await client.request('POST',base+'/views',{'view_name':name,'view_type':'grid'})
                vid=result['view']['view_id']
            body={'property':{'hidden_fields':hidden,'filter_info':{'conjunction':'and','conditions':[
                {'field_id':fields[field]['field_id'],'field_type':fields[field]['type'],'operator':'is','value':json.dumps([value],ensure_ascii=False)} for field,value in conditions]}}}
            await client.request('PATCH',base+'/views/'+vid,body)
            # Verify that the server really applies each filter, not merely its name.
            rows=await client.listing(base+'/records',{'view_id':vid})
            from astrbot_plugin_group_data.online import normalize
            assert all(all(str(normalize(r['fields']).get(f))==v for f,v in conditions) for r in rows),(key,name)
            configured[key+':'+name]={'view_id':vid,'records':len(rows)}
    target['views']=configured
    from urllib.parse import urlsplit,urlunsplit,urlencode
    original=urlsplit(target['url']);target.setdefault('original_url',target['url'])
    target['url']=urlunsplit((original.scheme,original.netloc,original.path,urlencode({'table':target['tables']['matches'],'view':configured['matches:当前周期全部']['view_id']}),''))
    path=args.data_dir/'feishu_target.json';path.write_text(json.dumps(target,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');path.chmod(0o600)
    print(json.dumps(configured,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-dir',required=True,type=Path)
    asyncio.run(configure(p.parse_args()))
