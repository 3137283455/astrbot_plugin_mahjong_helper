import tempfile,unittest
from pathlib import Path
from aiocqhttp.exceptions import ActionFailed
from astrbot_plugin_group_data.core import validate
from test_core import result
import test_commands,test_nicknames

class Event(test_nicknames.Event):
 def __init__(self,failures=1):super().__init__();self.failures=failures;self.attempts=[]
 async def send(self,value):
  self.attempts.append(value)
  if len(self.attempts)<=self.failures:raise ActionFailed({'status':'failed','retcode':1200,'message':'Timeout: NodeIKernelMsgService/sendMsg'})
  await super().send(value)
class DeliveryTests(unittest.IsolatedAsyncioTestCase):
 def seed(self,folder):
  p=test_commands.CommandTests.setup_plugin(self,folder);scope='12345:987654321'
  p.store.reconcile(scope,[{'msg':'1','idx':0,'stamp':1}]);row=p.store.source(scope,'1',0);p.store.attach(row['id'],'h',Path(folder)/'original.png')
  p.store.save('h','accepted',validate(result(4,names=['甲','乙','丙','音弦离歌'])))
  return p
 async def test_candidate_image_timeout_falls_back_to_selectable_text(self):
  with tempfile.TemporaryDirectory() as folder:
   p=self.seed(folder);e=Event();await p.handle(e,'个人 离歌 四麻')
   self.assertEqual(len(e.attempts),2);self.assertEqual(len(e.sent),1)
   self.assertIn('音弦离歌',e.sent[0]);self.assertIn('选 1',e.sent[0]);self.assertNotIn('群数据处理暂时失败',e.sent[0])
   self.assertIsNotNone(p.selection_cache().get(p.selection_key(e)))
   selected=test_nicknames.Event('1');await p.nickname_followup(selected)
   self.assertTrue(selected.stopped);self.assertEqual(len(selected.sent),1)
 async def test_both_send_timeouts_do_not_escape_or_retry_image_and_keep_candidate(self):
  with tempfile.TemporaryDirectory() as folder:
   p=self.seed(folder);e=Event(failures=2);await p.handle(e,'个人 离歌 四麻')
   self.assertEqual(len(e.attempts),2);self.assertEqual(e.sent,[])
   self.assertIsNotNone(p.selection_cache().get(p.selection_key(e)));self.assertTrue(e.get_extra('group_data_delivery_error'))
 async def test_profile_image_timeout_returns_ranked_text_without_reexecuting_query(self):
  with tempfile.TemporaryDirectory() as folder:
   p=self.seed(folder);e=Event();changes=p.store.db.total_changes
   await p.handle(e,'个人 音弦离歌 四麻')
   self.assertEqual(len(e.attempts),2);self.assertEqual(len(e.sent),1)
   self.assertIn('平均顺位',e.sent[0]);self.assertIn('原图 #1',e.sent[0]);self.assertIn('第',e.sent[0])
   self.assertEqual(p.store.db.total_changes,changes)
if __name__=='__main__':unittest.main()
