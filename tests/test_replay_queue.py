import asyncio,json,sqlite3,sys,tempfile,time,unittest
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain
from astrbot_plugin_mahjong_helper.replay_bridge import ReplayService
from astrbot_plugin_mahjong_helper.replay_core import ReplayStore,parse_url
from test_ai_entry import Event,plugin
from test_replay import fixture


class Request(Event):
    def __init__(self,actor='10001',group='12345'):
        super().__init__(group=group,mention=None);self.actor=actor
        if not group:self.unified_msg_origin='napcat:FriendMessage:'+actor
    def get_sender_id(self):return self.actor


class Context:
    def __init__(self):self.sent=[]
    async def send_message(self,scope,chain):self.sent.append((scope,chain))


class ControlledService(ReplayService):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.started=asyncio.Event();self.release=asyncio.Event();self.order=[];self.active=0;self.peak=0
    async def run_job(self,job):
        self.active+=1;self.peak=max(self.peak,self.active);self.order.append(job['id']);self.started.set()
        try:
            await self.release.wait()
            await self.db_call('complete',job,fixture(job['seat']))
        finally:self.active-=1


async def until(predicate,seconds=4):
    async with asyncio.timeout(seconds):
        while not predicate():await asyncio.sleep(.01)


class ReplayQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_fifo_group_mention_private_delivery_and_no_repeat(self):
        with tempfile.TemporaryDirectory() as folder:
            context=Context();service=ControlledService(context,folder)
            group=Request();private=Request('10002',None);other_group=Request('10003','67890')
            events=(group,private,other_group);ids=[]
            for index,event in enumerate(events):
                await service.handle(event,f'https://game.maj-soul.com/1/?paipu=abcdefghijklmnop{index} 南家')
                ids.append(service.store.get(event.unified_msg_origin,event.actor)['id'])
            self.assertEqual(service.store.queue_info(ids[1])['ahead'],1)
            self.assertIn('前方还有 1 个任务',private.sent[0].get_plain_text())
            task=asyncio.create_task(service.run())
            try:
                await service.started.wait();self.assertEqual(service.store.queue_info(ids[1])['ahead'],1)
                self.assertEqual(context.sent,[]);service.release.set()
                await until(lambda:len(context.sent)==6)
                self.assertEqual(service.order,ids);self.assertEqual(service.peak,1)
                self.assertEqual([scope for scope,_ in context.sent],[event.unified_msg_origin for event in events for _ in range(2)])
                for (_,chain),event in zip(context.sent[::2],events):
                    mentions=[str(p.qq) for p in chain.chain if isinstance(p,Comp.At)]
                    self.assertEqual(mentions,[event.actor] if event.group else [])
                    self.assertTrue(any(isinstance(p,Comp.Image) for p in chain.chain))
                await asyncio.sleep(.1);self.assertEqual(len(context.sent),6)
                # Immediate cached results use the same group/private addressing rule.
                await service.handle(group,'https://game.maj-soul.com/1/?paipu=abcdefghijklmnop0 南家')
                self.assertEqual(str(group.sent[-2].chain[0].qq),group.actor)
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):await task

    async def test_waiting_in_real_subprocess_does_not_hold_other_handlers(self):
        with tempfile.TemporaryDirectory() as folder:
            context=Context();runtime=Path(folder)/'runtime';binary=runtime/('.venv/Scripts/python.exe' if sys.platform=='win32' else '.venv/bin/python');binary.parent.mkdir(parents=True);binary.write_text('test placeholder')
            service=ReplayService(context,folder,{'replay_runtime':str(runtime)})
            event=Request();await service.handle(event,'https://game.maj-soul.com/1/?paipu=abcdefghijklmnop 南家')
            started=asyncio.Event();original=asyncio.create_subprocess_exec
            report=json.dumps(fixture())
            script='import sys,time,json\nsys.stdin.readline()\ntime.sleep(0.7)\nprint(json.dumps({"type":"result","report":json.loads('+repr(report)+')}),flush=True)'
            async def launch(*args,**kwargs):
                process=await original(sys._base_executable,'-u','-c',script,**kwargs);started.set();return process
            p=plugin(str(Path(folder)/'normal'));p.state_dir.mkdir(exist_ok=True)
            async def music(e,query):yield e.plain_result('原点歌搜索入口可用：'+query)
            p.context.get_registered_star=lambda name:SimpleNamespace(activated=True,star_cls=SimpleNamespace(search_song=music))
            with patch('astrbot_plugin_mahjong_helper.replay_bridge.asyncio.create_subprocess_exec',side_effect=launch):
                task=asyncio.create_task(service.run())
                try:
                    await asyncio.wait_for(started.wait(),2)
                    begin=time.monotonic()
                    quiz=[result async for result in p.nanikiru(Request(),'150')]
                    menu=[result async for result in p.koromo_menu(Request(),'文')]
                    song=Request();song.parts.insert(0,Comp.At(qq='bot'));song.set_extra('mahjong_ai_request',True);await p.music_search_ai(song,'晴天')
                    self.assertTrue(any(isinstance(part,Comp.Image) for result in quiz for part in result.chain))
                    self.assertIn('雀魂快捷查询',menu[0].get_plain_text());self.assertIn('晴天',song.sent[0].get_plain_text())
                    self.assertLess(time.monotonic()-begin,.5)
                    self.assertIsNotNone(service.process);self.assertIsNone(service.process.returncode)
                    await until(lambda:len(context.sent)==2)
                    self.assertEqual(str(context.sent[0][1].chain[0].qq),event.actor)
                finally:
                    task.cancel()
                    with suppress(asyncio.CancelledError):await task

    async def test_old_jobs_and_failed_notifications_preserve_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            context=Context();service=ReplayService(context,folder)
            for actor,scope,delivery in (('10001','napcat:GroupMessage:123',''),('10002','napcat:FriendMessage:10002','private')):
                source=parse_url('https://game.maj-soul.com/1/?paipu=abcdefghijklmnop');source['seat']=1
                job,_=service.store.create(scope,actor,source,delivery)
                service.store.finish_error(job['id'],'manual','在线站点需要人工验证')
                await service.notify(service.store.get(scope,actor))
            self.assertEqual([str(p.qq) for p in context.sent[0][1].chain if isinstance(p,Comp.At)],['10001'])
            self.assertFalse(any(isinstance(p,Comp.At) for p in context.sent[1][1].chain))
            self.assertIn('人工验证',context.sent[0][1].get_plain_text())


class ReplayMigrationTests(unittest.TestCase):
    def test_old_database_upgrade_keeps_jobs_and_retry_goes_to_queue_end(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ReplayStore(folder);source=parse_url('https://game.maj-soul.com/1/?paipu=abcdefghijklmnop');source['seat']=1
            first,_=store.create('napcat:GroupMessage:123','10001',source)
            with store.db() as db:db.execute('ALTER TABLE jobs DROP COLUMN delivery')
            store=ReplayStore(folder);self.assertEqual(store.get(first['scope'],first['actor'])['id'],first['id'])
            second,_=store.create('napcat:GroupMessage:123','10002',source,'group')
            store.finish_error(first['id'],'failed','测试失败');store.retry(first['scope'],first['actor'],first['id'])
            self.assertEqual(store.next()['id'],second['id'])
            store.update(first['id'],status='cancelled');store.finish_error(first['id'],'manual','晚到的错误')
            self.assertEqual(store.get(first['scope'],first['actor'])['status'],'cancelled')


if __name__=='__main__':unittest.main()
