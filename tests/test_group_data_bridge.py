import tempfile
import unittest
from types import SimpleNamespace
from test_ai_entry import Event,plugin,tools
from astrbot_plugin_mahjong_helper.ai_entry import AI_TOOLS
from astrbot.core.provider.entities import ProviderRequest
class GroupDataBridgeTests(unittest.IsolatedAsyncioTestCase):
 async def test_unqualified_fly_board_is_count_even_if_model_chooses_rate(self):
  from astrbot_plugin_mahjong_helper.group_data_bridge import run_group_data
  calls=[]
  async def handle(event,query,allow_write):calls.append(query)
  context=SimpleNamespace(get_registered_star=lambda name:SimpleNamespace(activated=True,star_cls=SimpleNamespace(handle=handle)))
  await run_group_data(context,Event(text='看看本群四麻被飞榜'),'四麻 被飞率 30天')
  await run_group_data(context,Event(text='谁被飞最多'),'被飞率榜 三麻')
  await run_group_data(context,Event(text='看看被飞次数榜'),'30天 被飞率 四麻')
  self.assertEqual(calls,['四麻 被飞 30天','被飞 三麻','30天 被飞 四麻'])
 async def test_explicit_rates_and_follow_up_filters_remain_rate(self):
  from astrbot_plugin_mahjong_helper.group_data_bridge import run_group_data
  calls=[]
  async def handle(event,query,allow_write):calls.append(query)
  context=SimpleNamespace(get_registered_star=lambda name:SimpleNamespace(activated=True,star_cls=SimpleNamespace(handle=handle)))
  for text in ['看看被飞率榜','被飞占比最高的是谁','换成四麻']:
   await run_group_data(context,Event(text=text),'四麻 被飞率')
  self.assertEqual(calls,['四麻 被飞率']*3)
 async def test_read_only_native_reuse_and_no_repeated_messages(self):
  with tempfile.TemporaryDirectory() as d:
   p=plugin(d);calls=[]
   async def handle(event,query,allow_write):
    calls.append((event.group,query,allow_write));await event.send(event.plain_result('群数据卡片'))
   meta=SimpleNamespace(activated=True,star_cls=SimpleNamespace(handle=handle))
   p.context.get_registered_star=lambda name:meta if name=='astrbot_plugin_group_data' else None
   e=Event(text='看看本群四麻谁被飞最多');e.set_extra('mahjong_ai_request',True)
   await p.group_data_ai(e,'四麻 被飞');await p.group_data_ai(e,'四麻 被飞')
   self.assertEqual(calls,[('group','四麻 被飞',False)]);self.assertEqual(len(e.sent),1)
 async def test_tool_allowlist_and_runtime_instructions(self):
  self.assertIn('group_data',AI_TOOLS)
  with tempfile.TemporaryDirectory() as d:
   p=plugin(d);meta=SimpleNamespace(activated=True,star_cls=object())
   p.context.get_registered_star=lambda name:meta if name=='astrbot_plugin_group_data' else None
   e=Event();e.set_extra('mahjong_ai_started',True)
   req=ProviderRequest(prompt='群数据',func_tool=tools('group_data','execute_code'))
   await p.ai_limit_request(e,req)
   self.assertEqual([t.name for t in req.func_tool.tools],['group_data']);self.assertIn('群数据',req.system_prompt)
 async def test_current_turn_images_are_preserved_and_group_scope_still_applies(self):
  import astrbot.api.message_components as Comp
  from astrbot_plugin_mahjong_helper.ai_entry import eligible,ai_provider_allowed
  with tempfile.TemporaryDirectory() as d:
   p=plugin(d);e=Event(text='识别这张图片');e.set_extra('mahjong_ai_started',True)
   req=ProviderRequest(prompt='识别这张图片',image_urls=['/tmp/current-image.png'])
   await p.ai_limit_request(e,req)
   self.assertEqual(req.image_urls,['/tmp/current-image.png'])
   e.message_str='';e.parts.append(Comp.Image.fromFileSystem('/tmp/current-image.png'))
   self.assertTrue(eligible(e))
   other=Event(text='',mention='someone_else');other.parts.append(Comp.Image.fromFileSystem('/tmp/current-image.png'))
   self.assertFalse(eligible(other))
  provider=SimpleNamespace(provider_config={'enable':True,'model':'deepseek-v4.1-flash','api_base':'http://127.0.0.1:8788/v1','custom_headers':{'X-Realm':'cn'}})
  self.assertTrue(ai_provider_allowed(provider))
  provider.provider_config['custom_headers']={}
  self.assertFalse(ai_provider_allowed(provider))
if __name__=='__main__':unittest.main()
