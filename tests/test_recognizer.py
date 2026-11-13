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
 async def test_confirmed_aliases_resolve_model_and_local_spellings_without_third_request(self):
  import json
  with tempfile.TemporaryDirectory() as d:
   provider=SimpleNamespace(provider_config={'model':'deepseek-v4','api_base':'http://127.0.0.1:8788/v1'})
   rec,store,row=self.setup_recognizer(d,provider)
   store.merge('b:g','旧写法一','确认昵称');store.merge('b:g','旧写法二','确认昵称')
   originals=[result(names=[name,'乙','丙']) for name in ('旧写法一','旧写法二')]
   rec.read=AsyncMock(side_effect=originals)
   with patch('astrbot_plugin_group_data.recognizer.trusted_names',return_value={1:'旧写法二'}):await rec.llm(row)
   saved=store.image('h');self.assertEqual(saved['status'],'accepted')
   self.assertEqual(json.loads(saved['result'])['players'][0]['name'],'确认昵称')
   self.assertEqual(json.loads(saved['raw'])[:2],originals);self.assertEqual(rec.read.await_count,2)

 async def test_retained_readings_use_confirmed_aliases_without_glyph_or_model(self):
  import json
  with tempfile.TemporaryDirectory() as d:
   rec,store,row=self.setup_recognizer(d);store.merge('b:g','旧写法','确认昵称')
   originals=[result(names=[name,'乙','丙']) for name in ('旧写法','确认昵称')]
   store.save('h','review',raw=originals);rec._match_verified=lambda *args:[];rec.read=AsyncMock()
   with patch('astrbot_plugin_group_data.recognizer.trusted_names',return_value={1:'旧写法'}):
    self.assertEqual(await rec.recheck_reviews(),[row['id']])
   self.assertEqual(json.loads(store.image('h')['result'])['players'][0]['name'],'确认昵称')
   self.assertEqual(json.loads(store.image('h')['raw'])[:2],originals);rec.read.assert_not_awaited()

 async def test_aliases_do_not_settle_disagreeing_points(self):
  with tempfile.TemporaryDirectory() as d:
   rec,store,row=self.setup_recognizer(d);store.merge('b:g','旧写法','确认昵称')
   originals=[result(names=['旧写法','乙','丙']),result(names=['确认昵称','乙','丙'],points=[64000,43000,-2000])]
   store.save('h','review',raw=originals);rec._match_verified=lambda *args:[]
   self.assertEqual(await rec.recheck_reviews(),[]);self.assertEqual(store.image('h')['status'],'review')

 async def test_alias_cannot_collapse_two_seats_into_one_player(self):
  with tempfile.TemporaryDirectory() as d:
   rec,store,row=self.setup_recognizer(d);store.merge('b:g','甲','乙')
   store.save('h','review',raw=[result(),result()]);rec._match_verified=lambda *args:[]
   self.assertEqual(await rec.recheck_reviews(),[]);self.assertFalse(store.matches('b:g'))

 async def test_aliases_from_other_group_do_not_relabel_this_group(self):
  with tempfile.TemporaryDirectory() as d:
   rec,store,row=self.setup_recognizer(d);store.merge('other:group','甲','其他人')
   store.save('h','review',raw=[result(),result()]);rec._match_verified=lambda *args:[]
   self.assertEqual(await rec.recheck_reviews(),[])

 async def test_local_admission_resolves_confirmed_alias_without_model(self):
  import json
  with tempfile.TemporaryDirectory() as d:
   rec,store,row=self.setup_recognizer(d);store.merge('b:g','甲','确认昵称')
   with patch('astrbot_plugin_group_data.recognizer.run_ocr',AsyncMock(return_value={})),patch('astrbot_plugin_group_data.recognizer.interpret',return_value={'decision':'accepted','result':result(),'raw':[result()],'reason':'local'}):
    await rec.local(row)
   self.assertEqual(json.loads(store.image('h')['result'])['players'][0]['name'],'确认昵称')
   self.assertEqual(json.loads(store.image('h')['raw'])[0]['players'][0]['name'],'甲')
   rec.context.get_using_provider_async.assert_not_awaited()

 async def test_new_glyphs_recheck_retained_reads_without_model_requests(self):
  import json
  with tempfile.TemporaryDirectory() as d:
   rec,store,row=self.setup_recognizer(d)
   originals=[result(names=[name,'乙','丙']) for name in ('错字一','错字二','错字三')]
   store.save('h','review',raw=originals,reason='三次识别仍存在无法确认的字段')
   rec._match_verified=lambda r,ocr:[{'mode':3,'place':1,'points':65000,'name':'慪龢','score':1,'sources':[1]}]
   rec.read=AsyncMock()
   accepted=await rec.recheck_reviews()
   self.assertEqual(accepted,[row['id']]);rec.read.assert_not_awaited()
   saved=store.image('h');self.assertEqual(saved['status'],'accepted')
   self.assertEqual(json.loads(saved['result'])['players'][0]['name'],'慪龢')
   self.assertEqual(json.loads(saved['raw'])[:3],originals)
   self.assertEqual(await rec.recheck_reviews(),[])

 async def test_dictionary_does_not_override_points_or_uncertain_unmatched_name(self):
  with tempfile.TemporaryDirectory() as d:
   rec,store,row=self.setup_recognizer(d)
   readings=[result(names=[name,'乙','丙']) for name in ('错字一','错字二','错字三')]
   store.save('h','review',raw=readings)
   rec._match_verified=lambda r,ocr:[{'mode':3,'place':1,'points':64000,'name':'慪龢'}]
   self.assertEqual(await rec.recheck_reviews(),[])
   self.assertEqual(store.image('h')['status'],'review');self.assertFalse(store.matches('b:g'))

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
 async def test_verified_glyphs_correct_model_names_and_preserve_original_reads(self):
  import json
  with tempfile.TemporaryDirectory() as d:
   provider=SimpleNamespace(provider_config={'model':'deepseek-v4','api_base':'http://127.0.0.1:8788/v1'})
   rec,store,row=self.setup_recognizer(d,provider)
   rec._match_verified=lambda row,ocr:[{'mode':3,'place':1,'points':65000,'name':'慪龢','score':.99,'sources':[2,3]}]
   rec.read=AsyncMock(side_effect=[result(names=['榧酥','乙','丙']),result(names=['偃稣','乙','丙'])])
   await rec.llm(row)
   saved=store.image('h');self.assertEqual(saved['status'],'accepted')
   self.assertEqual(json.loads(saved['result'])['players'][0]['name'],'慪龢')
   self.assertEqual(json.loads(saved['raw'])[0]['players'][0]['name'],'榧酥')
   self.assertEqual(rec.read.await_count,2)
 async def test_local_admission_also_uses_verified_glyphs(self):
  import json
  with tempfile.TemporaryDirectory() as d:
   rec,store,row=self.setup_recognizer(d)
   rec._match_verified=lambda row,ocr:[{'mode':3,'place':1,'points':65000,'name':'慪龢'}]
   with patch('astrbot_plugin_group_data.recognizer.run_ocr',AsyncMock(return_value={})),patch('astrbot_plugin_group_data.recognizer.interpret',return_value={'decision':'accepted','result':result(),'raw':[],'reason':'local'}):
    await rec.local(row)
   self.assertEqual(json.loads(store.image('h')['result'])['players'][0]['name'],'慪龢')
   rec.context.get_using_provider_async.assert_not_awaited()
 async def test_model_invented_space_uses_two_trusted_local_reads(self):
  import json
  with tempfile.TemporaryDirectory() as d:
   provider=SimpleNamespace(provider_config={'model':'deepseek-v4','api_base':'http://127.0.0.1:8788/v1'})
   rec,store,row=self.setup_recognizer(d,provider)
   rec.read=AsyncMock(side_effect=[result(names=['shao jue','乙','丙']),result(names=['shaojue','乙','丙'])])
   with patch('astrbot_plugin_group_data.recognizer.trusted_names',return_value={1:'shaojue'}):await rec.llm(row)
   self.assertEqual(store.image('h')['status'],'accepted')
   self.assertEqual(json.loads(store.image('h')['result'])['players'][0]['name'],'shaojue')
   self.assertEqual(json.loads(store.image('h')['raw'])[0]['players'][0]['name'],'shao jue')
   self.assertEqual(rec.read.await_count,2)
 async def test_real_character_conflict_still_requires_review(self):
  with tempfile.TemporaryDirectory() as d:
   provider=SimpleNamespace(provider_config={'model':'deepseek-v4','api_base':'http://127.0.0.1:8788/v1'})
   rec,store,row=self.setup_recognizer(d,provider);rec.read=AsyncMock(return_value=result(names=['慪龢','乙','丙']))
   with patch('astrbot_plugin_group_data.recognizer.trusted_names',return_value={1:'鱈灥'}):await rec.llm(row)
   self.assertEqual(store.image('h')['status'],'review');self.assertFalse(store.matches('b:g'))
 def test_unverified_spaces_are_not_globally_normalized(self):
  with patch('astrbot_plugin_group_data.recognizer.trusted_names',return_value={}):
   self.assertEqual(Recognizer.checked(result(names=['shao jue','乙','丙']),None,[])['players'][0]['name'],'shao jue')
if __name__=='__main__':unittest.main()
