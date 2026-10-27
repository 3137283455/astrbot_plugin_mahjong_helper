import unittest
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from astrbot_plugin_group_data.recognizer import Recognizer
from astrbot_plugin_group_data.store import Store
from test_core import result
class RecognizerTests(unittest.IsolatedAsyncioTestCase):
 def setup_recognizer(self,folder,provider=None):
  store=Store(Path(folder)/'data.db');self.addCleanup(store.close)
  ctx=SimpleNamespace(get_using_provider_async=AsyncMock(return_value=provider))
  rec=Recognizer(ctx,{},store);store.enable('b:g','b','g');store.reconcile('b:g',[{'msg':'1','idx':0,'stamp':1}]);row=store.source('b:g','1',0);store.attach(row['id'],'h','image.png')
  return rec,store,store.get_source('b:g',row['id'])
 async def test_uses_only_default_deepseek_and_rejects_modelscope(self):
  with tempfile.TemporaryDirectory() as d:
   provider=SimpleNamespace(provider_config={'id':'default','model':'deepseek-v4.1-flash','api_base':'http://127.0.0.1:8788/v1'})
   rec,store,row=self.setup_recognizer(d,provider);self.assertIs((await rec.provider())[0],provider)
   provider.provider_config['api_base']='https://api-inference.modelscope.cn/v1'
   with self.assertRaises(RuntimeError):await rec.provider()
 async def test_local_result_calls_no_llm(self):
  with tempfile.TemporaryDirectory() as d:
   rec,store,row=self.setup_recognizer(d)
   with patch('astrbot_plugin_group_data.recognizer.run_ocr',AsyncMock(return_value={})),patch('astrbot_plugin_group_data.recognizer.interpret',return_value={'decision':'accepted','result':result(),'raw':[],'reason':'local'}):
    await rec.local(row)
   self.assertEqual(store.image('h')['status'],'accepted');rec.context.get_using_provider_async.assert_not_awaited()
 async def test_403_circuit_keeps_local_queue_running(self):
  class Forbidden(Exception):status_code=403
  with tempfile.TemporaryDirectory() as d:
   provider=SimpleNamespace(provider_config={'model':'deepseek-v4','api_base':'http://127.0.0.1:8788/v1'})
   rec,store,row=self.setup_recognizer(d,provider);store.save('h','llm_pending',raw=[result()])
   rec.read=AsyncMock(side_effect=Forbidden())
   await rec.llm(row);await rec.llm(row)
   self.assertEqual(rec.read.await_count,1);self.assertEqual(store.image('h')['status'],'llm_pending');self.assertIn('403',rec.model_error)
 async def test_inconsistent_model_readings_never_counted(self):
  with tempfile.TemporaryDirectory() as d:
   provider=SimpleNamespace(provider_config={'model':'deepseek-v4','api_base':'http://127.0.0.1:8788/v1'})
   rec,store,row=self.setup_recognizer(d,provider)
   rec.read=AsyncMock(side_effect=[result(),result(names=['另一人','乙','丙']),result(names=['第三个不同的人','乙','丙'])]);await rec.llm(row)
   self.assertEqual(store.image('h')['status'],'review');self.assertFalse(store.matches('b:g'))
 async def test_visible_final_marker_prevents_silent_skip(self):
  with tempfile.TemporaryDirectory() as d:
   provider=SimpleNamespace(provider_config={'model':'deepseek-v4','api_base':'http://127.0.0.1:8788/v1'})
   rec,store,row=self.setup_recognizer(d,provider)
   store.save('h','llm_pending',raw=[{'local_ocr':{'full':[{'text':'对局终结'}]}}])
   row=store.get_source('b:g',row['id']);rec.read=AsyncMock(return_value={'is_result':False,'reason':'东2局仍在进行'})
   await rec.llm(row)
   self.assertEqual(store.image('h')['status'],'review');self.assertEqual(rec.read.await_count,1)
 async def test_third_read_resolves_one_spelling_without_guessing(self):
  with tempfile.TemporaryDirectory() as d:
   provider=SimpleNamespace(provider_config={'model':'deepseek-v4','api_base':'http://127.0.0.1:8788/v1'})
   rec,store,row=self.setup_recognizer(d,provider)
   rec.read=AsyncMock(side_effect=[result(),result(names=['另一人','乙','丙']),result()]);await rec.llm(row)
   self.assertEqual(store.image('h')['status'],'accepted');self.assertEqual(rec.read.await_count,3)
 async def test_startup_waits_for_default_provider_without_marking_image_failure(self):
  with tempfile.TemporaryDirectory() as d:
   provider=SimpleNamespace(provider_config={'model':'deepseek-v4','api_base':'http://127.0.0.1:8788/v1'})
   rec,store,row=self.setup_recognizer(d,provider)
   rec.context.get_using_provider_async=AsyncMock(side_effect=[None,provider])
   rec.pending=lambda _: [row]
   rec.llm=AsyncMock(side_effect=asyncio.CancelledError())
   with patch('astrbot_plugin_group_data.recognizer.asyncio.sleep',AsyncMock()):
    with self.assertRaises(asyncio.CancelledError):await rec.llm_loop()
   self.assertEqual(rec.context.get_using_provider_async.await_count,2)
   self.assertEqual(store.image('h')['status'],'new');self.assertEqual(rec.llm.await_count,1)
if __name__=='__main__':unittest.main()
