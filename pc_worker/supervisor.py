"""Per-user, hidden supervisor; restart only its own desktop worker."""
import argparse,logging,logging.handlers,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from worker import ChildJob,CREATE_NO_WINDOW

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    root=Path(__file__).resolve().parent;(root/'logs').mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO,handlers=[logging.handlers.RotatingFileHandler(root/'logs/supervisor.log',maxBytes=512*1024,backupCount=2,encoding='utf-8')],format='%(asctime)s %(levelname)s %(message)s')
    children=ChildJob();delay=3
    try:
        while True:
            start=time.monotonic()
            process=subprocess.Popen([sys.executable,'-I',str(root/'worker.py'),'--config',str(Path(args.config).resolve())],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=CREATE_NO_WINDOW)
            children.add(process);logging.info('工作进程已启动 PID=%s',process.pid)
            code=process.wait();logging.warning('工作进程已退出 code=%s，将自动恢复',code)
            delay=3 if time.monotonic()-start>30 else min(30,delay*2)
            time.sleep(delay)
    finally:children.close()

if __name__=='__main__':main()
