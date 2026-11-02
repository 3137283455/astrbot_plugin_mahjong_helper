"""Create table structure using private runtime credentials; does not archive data."""
import argparse
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_group_data.feishu import Feishu,bootstrap


async def main(args):
    file=args.data_dir/'feishu_target.json';target=json.loads(file.read_text(encoding='utf-8'))
    client=Feishu(args.data_dir/'feishu_credentials.json',target['app_token'])
    owner_ids=[r['owner_id'] for r in target.get('drive_meta',{}).get('metas',[]) if r.get('owner_id')]
    if not owner_ids:raise ValueError('缺少已核实的文档管理员身份，不自动启用写回')
    spec=json.loads((Path(__file__).resolve().parents[1]/'docs'/'V2表结构草案.json').read_text(encoding='utf-8'))
    tables=await bootstrap(client,spec,target.get('initial_table_id'))
    target.update({'enabled':False,'tables':tables,'scope':args.scope,'editor_ids':owner_ids,'poll_seconds':600})
    file.write_text(json.dumps(target,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');file.chmod(0o600)
    print(json.dumps({'tables':tables,'read_write':'ok','sync_enabled':False,'archive_performed':False},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data-dir',type=Path,required=True);parser.add_argument('--scope',required=True)
    asyncio.run(main(parser.parse_args()))
