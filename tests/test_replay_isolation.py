import asyncio,hashlib,tempfile,time,unittest
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from PIL import Image
import astrbot.api.message_components as C
from astrbot.api.event import MessageChain
from astrbot_plugin_mahjong_helper.replay_delivery import private_history_receipt
from astrbot_plugin_mahjong_helper.replay_bridge import ReplayService
from test_replay_queue import Context,Request,ControlledService,until
from test_replay_delivery import timeout

class IsolationTests(unittest.IsolatedAsyncioTestCase):
 async def test_retry_preserves_image_and_forward_types(self):
  with tempfile.TemporaryDirectory() as folder:
   s=ReplayService(Context(),folder)
   path=Path(folder)/'card.png';Image.new('RGB',(16,16),'red').save(path)
   for i,chain in enumerate([MessageChain().file_image(str(path)),MessageChain(chain=[C.Nodes([C.Node(content=MessageChain().file_image(str(path)).chain,uin='1',name='慧慧')])])]):
    sent=[]
    async def send(part):
     sent.append(part)
     if len(sent)==1:raise timeout()
    with patch.object(s,'verify_private',new=AsyncMock(return_value=None)),patch('astrbot_plugin_mahjong_helper.replay_delivery.asyncio.sleep',new_callable=AsyncMock):
     self.assertTrue(await s.deliver_with_receipts('original'+str(i),{'scope':'napcat:FriendMessage:10001','actor':'10001'},chain,send))
    self.assertEqual(len(sent),2);self.assertIs(sent[0],sent[1]);self.assertIs(type(sent[1].chain[0]),type(chain.chain[0]))
    self.assertFalse(any(isinstance(p,C.Plain) for msg in sent for p in msg.chain))
 async def test_image_history_checks_md5(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'card.png';Image.new('RGB',(16,16),'red').save(path);digest=hashlib.md5(path.read_bytes()).hexdigest()
   chain=MessageChain().file_image(str(path));row={'user_id':3113357165,'time':105,'real_seq':'3','message_id':22,'message':[{'type':'image','data':{'file':digest.upper()+'.png'}}]}
   bot=SimpleNamespace(call_action=AsyncMock(return_value={'messages':[row]}));job={'scope':'napcat:GroupMessage:1036516712','actor':'3439740900'}
   self.assertEqual((await private_history_receipt(bot,job,chain,100,'3113357165'))['real_seq'],3)
   row['message'][0]['data']['file']='0'*32+'.png'
   self.assertIsNone(await private_history_receipt(bot,job,chain,100,'3113357165'))
 async def test_forward_history_compares_original_node_content(self):
  chain=MessageChain(chain=[C.Nodes([C.Node(content=[C.Plain('结果 #unique')],uin='3113357165',name='慧慧')])])
  row={'user_id':3113357165,'time':105,'real_seq':'4','message_id':23,'message':[{'type':'forward','data':{'id':'resource'}}]}
  bot=SimpleNamespace(call_action=AsyncMock(side_effect=[{'messages':[row]},{'messages':[{'content':[{'type':'text','data':{'text':'结果 #unique'}}]}]}]))
  result=await private_history_receipt(bot,{'scope':'napcat:FriendMessage:10001','actor':'10001'},chain,100,'3113357165')
  self.assertEqual(result['real_seq'],4);self.assertEqual(bot.call_action.call_args.args[0],'get_forward_msg')
 async def test_stuck_notification_does_not_stop_second_analysis_or_old_commands(self):
  from test_ai_entry import plugin
  with tempfile.TemporaryDirectory() as folder:
   s=ControlledService(Context(),folder);first=Request('10001');second=Request('10002');s.release.set()
   await s.handle(first,'https://game.maj-soul.com/1/?paipu=abcdefghijklmnop 南家')
   await s.handle(second,'https://game.maj-soul.com/1/?paipu=abcdefghijklmnop1 南家')
   entered=asyncio.Event();never=asyncio.Event()
   async def stuck(job,chain):entered.set();await never.wait()
   s.send_private=stuck;task=asyncio.create_task(s.run())
   try:
    await asyncio.wait_for(entered.wait(),3)
    await until(lambda:len(s.order)==2)
    self.assertEqual(s.peak,1);self.assertLessEqual(len(s.notice_tasks),2)
    p=plugin(str(Path(folder)/'old'));started=time.monotonic()
    quiz=[r async for r in p.nanikiru(Request(),'150')]
    menu=[r async for r in p.koromo_menu(Request(),'文')]
    self.assertTrue(any(isinstance(c,C.Image) for r in quiz for c in r.chain));self.assertIn('雀魂快捷查询',menu[0].get_plain_text())
    self.assertLess(time.monotonic()-started,1)
   finally:
    task.cancel()
    with suppress(asyncio.CancelledError):await task
