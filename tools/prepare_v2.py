"""Read-only preparation report for period archives and online-table integration.

This tool never changes groups, periods, recognition results or the current schema.
It is deliberately separate from the running plugin until a reviewed cutover.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_group_data.store import Store


def inspect_database(database: Path, scope: str) -> dict:
    database=Path(database).resolve()
    if not database.is_file():
        raise ValueError('群数据数据库不存在')
    connection=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    try:
        connection.execute('BEGIN')
        # Reuse the exact production query/deduplication rules without invoking
        # Store.__init__, which performs schema migrations and is not read-only.
        store=Store.__new__(Store);store.path=database;store.db=connection
        group=store.group(scope)
        if not group:
            raise ValueError('该群尚无统计记录')
        matches=store.matches(scope)
        sources=[dict(r) for r in connection.execute(
            'SELECT id,msg,idx,hash,active,stamp FROM sources WHERE scope=? ORDER BY id',(scope,))]
        exclusions=[dict(r) for r in connection.execute(
            'SELECT msg,idx,reason,stamp FROM source_exclusions WHERE scope=? ORDER BY msg,idx',(scope,))]
        aliases=store.aliases(scope)
        effective=[{'source_id':m['id'],'stamp':m['stamp'],'result':m['result']} for m in matches]
        fingerprint=hashlib.sha256(json.dumps(
            {'matches':effective,'sources':sources,'exclusions':exclusions,'aliases':aliases},
            sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        accepted_rows=[r for r in store.rows(scope)
                       if (r['override_status'] or r['status'])=='accepted']
        missing=[r['id'] for r in accepted_rows
                 if not r['path'] or not Path(r['path']).is_file()]
        freshness=store.freshness(scope)
        return {
            'format':'group-data-v2-preparation/1','preview_only':True,
            'generated_at':datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
            'scope':scope,'existing_enabled':bool(group['enabled']),
            'summary':store.summary(scope),
            'games_by_mode':{str(mode):sum(m['result']['mode']==mode for m in matches) for mode in (3,4)},
            'latest_confirmed':freshness['data_until'],'latest_fly':freshness['latest_fly'],
            'known_sources':sources,'source_exclusions':exclusions,
            'aliases_at_preparation':aliases,'effective_data_sha256':fingerprint,
            'accepted_sources_with_missing_originals':missing,
            'cutover':{'performed':False,'new_period_started_at':None,
                       'strategy':'archive the complete reviewed baseline; preserve existing source and image identities'},
            'online_sync':{'enabled':False,'provider':'feishu','credentials_included':False},
        }
    finally:
        connection.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True,type=Path)
    parser.add_argument('--scope',required=True)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.resolve()==args.db.resolve():
        parser.error('报告路径不能覆盖数据库')
    report=inspect_database(args.db,args.scope)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    # This report contains group-specific metadata and belongs with private backups,
    # never in the public plugin repository.
    with args.output.open('x',encoding='utf-8') as output:
        output.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    args.output.chmod(0o600)
    print(json.dumps({'report':str(args.output),'preview_only':True,
                      'games':report['summary']['games'],'by_mode':report['games_by_mode'],
                      'missing_originals':len(report['accepted_sources_with_missing_originals'])},ensure_ascii=False))


if __name__=='__main__':
    main()
