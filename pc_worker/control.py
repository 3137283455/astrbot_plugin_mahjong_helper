import json,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parent;action=sys.argv[1] if len(sys.argv)>1 else 'status'
if action in ('start','stop'):
    command=('Start' if action=='start' else 'Stop')+"-ScheduledTask -TaskName 'Mortal4.1b电脑工作端'"
    completed=subprocess.run(['powershell.exe','-NoProfile','-Command',command],capture_output=True,text=True,encoding='utf-8',errors='replace')
    print('工作端已启动。' if action=='start' and completed.returncode==0 else '工作端已停止；下次登录仍会自启。' if completed.returncode==0 else '操作失败，请在任务计划程序中检查。')
else:
    path=root/'status.json'
    if path.exists():
        data=json.loads(path.read_text(encoding='utf-8'));print('Mortal 4.1b 电脑工作端')
        for key,label in (('status','状态'),('connected','服务器连接'),('job','当前任务'),('updated','更新时间'),('error','提示')):print(label+'：'+str(data.get(key) or '—'))
    else:print('工作端尚未启动。')
if sys.stdin.isatty():input('\n按回车关闭。')
