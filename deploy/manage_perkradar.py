#!/usr/bin/env python3
"""Administrator CLI for the standalone service. Never called by chat or the LLM."""
import argparse
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

BASE=Path('/bot/perkradar')
NODE=Path('/root/.nvm/versions/node/v22.22.1/bin')
ENV={**os.environ,'PATH':str(NODE)+':'+os.environ.get('PATH','')}
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def run(args,**kwargs):
    return subprocess.run(args,env=ENV,capture_output=True,text=True,check=True,timeout=kwargs.pop('timeout',30),**kwargs)
def private(path,obj):
    path.write_text(json.dumps(obj,indent=2));path.chmod(0o600)
def fetch(url):
    with OPENER.open(urllib.request.Request(url,headers={'User-Agent':'Huihui-PerkRadar-Maintenance'}),timeout=25) as response:
        body=response.read(32*1024*1024+1)
        if len(body)>32*1024*1024:raise RuntimeError('下载内容超出大小限制')
        return body
def status():
    version=json.loads((BASE/'deploy/version.json').read_text())
    processes=json.loads(run([str(NODE/'pm2'),'jlist']).stdout)
    process=next((p for p in processes if p['name']=='perkradar'),None)
    print(json.dumps({'version':version,'process':None if not process else {'pid':process['pid'],'status':process['pm2_env']['status'],'rss_bytes':process.get('monit',{}).get('memory')}},ensure_ascii=False))
def check():
    current=json.loads((BASE/'deploy/version.json').read_text())
    latest=json.loads(fetch('https://api.github.com/repos/luobinger/PerkRadar/commits/main'))
    print(json.dumps({'installed':current['commit'],'upstream_main':latest['sha'],'update_available':current['commit']!=latest['sha'],'policy':'先审核变更和AGENTS，再stage固定提交；不自动切换生产'},ensure_ascii=False))
def stage(sha):
    if not re.fullmatch('[a-f0-9]{40}',sha):raise ValueError('需要完整40位提交SHA')
    target=BASE/'releases'/sha
    if target.exists():raise ValueError('候选目录已存在，请先检查其构建结果；不覆盖现有目录')
    body=fetch('https://codeload.github.com/luobinger/PerkRadar/tar.gz/'+sha)
    target.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(body),mode='r:gz') as archive:
        for m in archive.getmembers():
            parts=Path(m.name).parts[1:]
            if not parts:continue
            if '..' in parts or m.issym() or m.islnk() or not (m.isfile() or m.isdir()):raise ValueError('不支持的源码归档条目')
            dest=target.joinpath(*parts)
            if not dest.resolve().is_relative_to(target.resolve()):raise ValueError('非法归档路径')
            if m.isdir():dest.mkdir(parents=True,exist_ok=True)
            else:
                dest.parent.mkdir(parents=True,exist_ok=True)
                with archive.extractfile(m) as source:dest.write_bytes(source.read())
    # The upstream workflow checker requires LF, including when YAML was committed as CRLF.
    for p in (target/'.github/workflows').glob('*.yml'):p.write_bytes(p.read_bytes().replace(b'\r\n',b'\n'))
    for p in [target/'AGENTS.md',target/'docs/OPERATIONS.md']:
        if not p.exists():raise ValueError('候选缺少维护说明，停止自动构建')
    script=BASE/'deploy'/('build-'+sha+'.sh')
    script.write_text('''#!/bin/bash
set -euo pipefail
export PATH=/root/.nvm/versions/node/v22.22.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export NODE_OPTIONS=--max-old-space-size=640 NEXT_TELEMETRY_DISABLED=1
export DATA_DIR=/bot/perkradar/deploy/candidate-data-SHA
export DISABLE_SCHEDULER=1 DEALS_SKIP_BOOT=1
npm ci --no-audit --no-fund
npm test
npm run lint
npx --no-install tsc --noEmit
PERKRADAR_OFFLINE=1 DISABLE_SCHEDULER=1 DEALS_SKIP_BOOT=1 npm run build
date -u +%FT%TZ > READY
'''.replace('candidate-data-SHA','candidate-data-'+sha));script.chmod(0o700)
    log=BASE/'deploy'/('build-'+sha+'.log')
    unit='perkradar-stage-'+sha[:12]
    run(['systemd-run','--unit='+unit,'--collect','--property=MemoryMax=1000M','--property=MemorySwapMax=1500M','--property=CPUQuota=100%','--property=Nice=15','--working-directory='+str(target),'--property=StandardOutput=append:'+str(log),'--property=StandardError=append:'+str(log),'/bin/bash',str(script)])
    print('候选已开始隔离验证；生产未切换。日志：'+str(log))
def backup():
    dest=BASE/'backups'/('upgrade-'+time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns()%1000000));dest.mkdir(mode=0o700)
    with sqlite3.connect((BASE/'data/deals.db').as_uri()+'?mode=ro',uri=True) as live:
        with sqlite3.connect(dest/'deals.db') as saved:live.backup(saved)
    for name in ['version.json','ecosystem.json','client.json']:shutil.copy2(BASE/'deploy'/name,dest/name);(dest/name).chmod(0o600)
    print('一致性备份：'+str(dest));return dest
def health():
    cfg=json.loads((BASE/'deploy/client.json').read_text())
    for attempt in range(20):
        try:
            for endpoint in ['/api/health','/api/deals?limit=1']:
                req=urllib.request.Request(cfg['url']+endpoint,headers={'Authorization':'Bearer '+cfg['token']})
                with OPENER.open(req,timeout=3) as response:obj=json.load(response)
                if endpoint.endswith('health') and obj.get('ok') is not True:raise RuntimeError('数据库不可读')
                if 'deals?' in endpoint and not isinstance(obj.get('deals'),list):raise RuntimeError('API不兼容')
            return
        except Exception:
            if attempt==19:raise RuntimeError('启动验证未通过') from None
            time.sleep(1)
def stop():
    process=next((p for p in json.loads(run([str(NODE/'pm2'),'jlist']).stdout) if p['name']=='perkradar'),None)
    if process is None:return
    run([str(NODE/'pm2'),'stop','perkradar'])
    time.sleep(1)
    process=next((p for p in json.loads(run([str(NODE/'pm2'),'jlist']).stdout) if p['name']=='perkradar'),None)
    if process and process.get('pid'):raise RuntimeError('旧进程尚未停止，不能切换数据库')
def point(release):
    pending=BASE/'current.next'
    if pending.is_symlink():pending.unlink()
    pending.symlink_to(release,target_is_directory=True);os.replace(pending,BASE/'current')
def start():
    if any(p['name']=='perkradar' for p in json.loads(run([str(NODE/'pm2'),'jlist']).stdout)):
        run([str(NODE/'pm2'),'delete','perkradar'])
    run([str(NODE/'pm2'),'start',str(BASE/'deploy/ecosystem.json'),'--only','perkradar'])
    health();run([str(NODE/'pm2'),'save'])
    for p in [Path('/root/.pm2/dump.pm2'),Path('/root/.pm2/dump.pm2.bak')]:
        if p.exists():p.chmod(0o600)
def restore(saved):
    saved=Path(saved).resolve()
    if not saved.is_relative_to((BASE/'backups').resolve()) or not all((saved/name).is_file() for name in ['deals.db','version.json','ecosystem.json','client.json']):raise ValueError('只能恢复本服务的完整备份目录')
    with sqlite3.connect((saved/'deals.db').as_uri()+'?mode=ro',uri=True) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('备份数据库校验失败')
    if not re.fullmatch('[a-f0-9]{40}',json.loads((saved/'version.json').read_text())['commit']):raise ValueError('备份版本无效')
    version=json.loads((saved/'version.json').read_text());release=BASE/'releases'/version['commit']
    if not (release/'.next/BUILD_ID').exists():raise ValueError('缺少匹配备份的旧程序')
    stop()
    aside=BASE/'backups'/('before-restore-'+time.strftime('%Y%m%d-%H%M%S'));aside.mkdir(mode=0o700)
    for name in ['deals.db','deals.db-wal','deals.db-shm']:
        path=BASE/'data'/name
        if path.exists():shutil.move(str(path),str(aside/name))
    shutil.copy2(saved/'deals.db',BASE/'data/deals.db')
    for name in ['version.json','ecosystem.json','client.json']:shutil.copy2(saved/name,BASE/'deploy'/name);(BASE/'deploy'/name).chmod(0o600)
    point(release);start()
    print('程序与数据库已共同恢复；优惠插件订阅库未改动。')
def activate(sha):
    if not re.fullmatch('[a-f0-9]{40}',sha):raise ValueError('需要完整提交SHA')
    release=BASE/'releases'/sha
    if not (release/'READY').exists() or not (release/'.next/BUILD_ID').exists():raise ValueError('候选尚未通过构建检查')
    stop()
    try:saved=backup()
    except Exception:
        start();raise
    try:
        cfg=json.loads((BASE/'deploy/ecosystem.json').read_text());cfg['apps'][0].update(script=str(release/'node_modules/next/dist/bin/next'),cwd=str(release))
        private(BASE/'deploy/ecosystem.json',cfg);point(release)
        private(BASE/'deploy/version.json',{'repository':'https://github.com/luobinger/PerkRadar','commit':sha,'installed':time.time(),'source_version':json.loads((release/'package.json').read_text())['version']})
        start();print('升级通过，备份保留于 '+str(saved))
    except Exception:
        restore(saved);raise RuntimeError('升级失败，已恢复旧程序与升级前数据库') from None
def selftest():
    with tempfile.TemporaryDirectory(dir=BASE/'deploy',prefix='rollback-selftest-') as directory:
        root=Path(directory);db=sqlite3.connect(root/'source.db');db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE sample(value TEXT)');db.execute("INSERT INTO sample VALUES('before-upgrade')");db.commit()
        with sqlite3.connect(root/'backup.db') as saved:db.backup(saved)
        db.execute("UPDATE sample SET value='after-upgrade'");db.commit();db.close()
        aside=root/'aside';aside.mkdir()
        for p in root.glob('source.db*'):shutil.move(str(p),str(aside/p.name))
        shutil.copy2(root/'backup.db',root/'source.db')
        with sqlite3.connect(root/'source.db') as db:assert db.execute('SELECT value FROM sample').fetchone()[0]=='before-upgrade'
    print('WAL一致性备份与数据库副本恢复检查通过；生产库和现有服务未改动。')
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['status','check','backup','stage','activate','rollback','selftest']);parser.add_argument('target',nargs='?');args=parser.parse_args()
    {'status':status,'check':check,'backup':backup,'stage':lambda:stage(args.target or ''),'activate':lambda:activate(args.target or ''),'rollback':lambda:restore(args.target or ''),'selftest':selftest}[args.action]()
