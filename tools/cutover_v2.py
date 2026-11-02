"""One-time explicit cutover. Run with the bot paused, after round-trip tests pass."""
import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from datetime import datetime,timezone,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_group_data.store import Store
from astrbot_plugin_group_data import periods


def run(args):
    data=args.data_dir.resolve();database=data/'group_data.db'
    if not database.is_file():raise ValueError('数据库不存在')
    backup=args.backups.resolve()/('group-data-v2-cutover-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700);(backup/'images').mkdir(mode=0o700)
    # SQLite backup includes committed WAL pages; do this before additive migrations.
    with sqlite3.connect(database) as source,sqlite3.connect(backup/'group_data.db') as copy:
        source.backup(copy)
        originals=source.execute('SELECT hash,path FROM images').fetchall()
    manifest=[]
    for image,path in originals:
        path=Path(path).resolve()
        if not path.is_relative_to(data/'images'):raise ValueError('原图路径超出数据目录，停止切换')
        if not path.is_file():continue
        target=backup/'images'/path.name;shutil.copy2(path,target);target.chmod(0o600)
        manifest.append({'hash':image,'file':path.name,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'bytes':target.stat().st_size})
    (backup/'group_data.db').chmod(0o600)
    (backup/'originals.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8');(backup/'originals.json').chmod(0o600)
    store=Store(database)
    try:
        if periods.select(store,args.scope):raise ValueError('已有当前周期，拒绝再次切换')
        matches=store.matches(args.scope)
        if args.baseline_source_max is not None:matches=[m for m in matches if m['id']<=args.baseline_source_max]
        if len(matches)!=args.expected_games:raise ValueError('对局数与核对基线不符，未切换')
        for row in matches:
            if not row['path'] or not Path(row['path']).is_file():raise ValueError('已确认对局缺少原图，未切换')
        latest={}
        for mode in (3,4):
            row=max((m for m in matches if m['result']['mode']==mode and any(p['points']<0 for p in m['result']['players'])),key=lambda m:(m['stamp'],m['id']),default=None)
            latest[mode]={'id':row['id'],'stamp':row['stamp']} if row else {'id':None,'stamp':0}
        if {3:latest[3]['id'],4:latest[4]['id']}!={3:args.three_latest,4:args.four_latest}:raise ValueError('最新被飞与核对基线不符，未切换')
        result=periods.cutover(store,args.scope,baseline_source_max=args.baseline_source_max)
        result['new_period_games']=store.summary(args.scope)['games']
        assert len(store.matches(args.scope,period='历史'))==args.expected_games
        result.update({'backup':str(backup),'originals_backed_up':len(manifest),'scope':args.scope,
                       'started_beijing':datetime.fromtimestamp(result['started'],timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
                       'latest_fly_at_archive':latest})
        report=backup/'cutover.json';report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');report.chmod(0o600)
        print(json.dumps(result,ensure_ascii=False))
    finally:store.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-dir',required=True,type=Path);p.add_argument('--backups',required=True,type=Path)
    p.add_argument('--scope',required=True);p.add_argument('--expected-games',required=True,type=int)
    p.add_argument('--three-latest',required=True,type=int);p.add_argument('--four-latest',required=True,type=int)
    p.add_argument('--baseline-source-max',type=int)
    run(p.parse_args())
