import tempfile
import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace
from test_ai_entry import Event,plugin,tools
from astrbot_plugin_mahjong_helper.ai_entry import AI_TOOLS
from astrbot.core.provider.entities import ProviderRequest
class GroupDataBridgeTests(unittest.IsolatedAsyncioTestCase):
 async def test_global_extremes_need_no_player_and_keep_all_filters(self):
  import shlex
  from astrbot_plugin_mahjong_helper.group_data_bridge import quick_group_query
  cases={'s0最低持点':['最低','S0'],'我要s0最低持点':['最低','S0'],
         '查一下S0最低持点':['最低','S0'],'S0三麻最低持点':['最低','S0','三麻'],
         '四麻最高点数S0':['最高','四麻','S0'],'看看全群最低持点':['最低'],
         '最高持点 三麻 S1':['最高','三麻','S1'],'最低持点 四麻 S0 第2页':['最低','四麻','S0','第2页']}
  for text,wanted in cases.items():self.assertEqual(shlex.split(quick_group_query(text)),wanted,text)
  for text in ['那最低那张呢','最高那局','看看清风难胡S0的最低持点','为什么S0最低持点这么低','/群数据 最低 S0']:
   self.assertIsNone(quick_group_query(text),text)

 async def test_observed_global_request_calls_native_query_without_model(self):
  with tempfile.TemporaryDirectory() as d:
   p=plugin(d);calls=[]
   p.context.llm_generate=AsyncMock()
   async def handle(event,query,allow_write):
    calls.append((query,allow_write));await event.send(event.plain_result('captured card'))
   p.context.get_registered_star=lambda name:SimpleNamespace(activated=True,star_cls=SimpleNamespace(handle=handle)) if name=='astrbot_plugin_group_data' else None
   event=Event(text='s0最低持点')
   await p.ai_before_model(event)
   self.assertEqual(calls,[('最低 S0',False)]);self.assertTrue(event.stopped)
   p.context.llm_generate.assert_not_awaited()

 async def test_fast_query_parser_handles_explicit_requests_and_preserves_names(self):
  import shlex
  from astrbot_plugin_mahjong_helper.group_data_bridge import quick_group_query
  cases={'查一下高贵名门的群数据':['个人','高贵名门'],
         '查一下群数据综合分榜单':['综合榜'],
         '看看S0三麻综合榜 第2页':['综合榜','S0','三麻','第2页'],
         '看看下一页':['下一页'],
         '上一页 四麻':['上一页','四麻'],
         '第二页':['第二页'],
         '看看暂定榜 四麻 第2页':['暂定榜','四麻','第2页'],
         '看看S0三麻综合表现榜':['综合榜','S0','三麻'],
         '查清风难胡+）（的群数据 四麻 S0':['个人','清风难胡+）（','四麻','S0'],
         '看看被飞率榜 四麻':['被飞率','四麻'],
         '查高贵名门的综合评分 近30天 S0':['评分','高贵名门','30天','S0']}
  for text,wanted in cases.items():self.assertEqual(shlex.split(quick_group_query(text)),wanted)
  for text in ['你好','为什么高贵名门的群数据','查一下怎么计算群数据','查高贵名门的群数据并删除它','/群数据 个人 高贵名门','看看这个人的表现如何']:
   self.assertIsNone(quick_group_query(text),text)
 async def test_fast_group_query_bypasses_provider_and_deduplicates_before_send(self):
  with tempfile.TemporaryDirectory() as d:
   p=plugin(d);calls=[];remembered=[]
   async def handle(event,query,allow_write):calls.append((query,allow_write));await event.send(event.plain_result('群数据卡片'))
   instance=SimpleNamespace(handle=handle,remember_context=lambda event,query:remembered.append(query))
   p.context.get_registered_star=lambda name:SimpleNamespace(activated=True,star_cls=instance) if name=='astrbot_plugin_group_data' else None
   p.context.get_provider_by_id=lambda _:self.fail('Explicit query must not touch model provider')
   first=Event(text='查一下高贵名门的群数据');await p.ai_before_model(first)
   self.assertTrue(first.stopped);self.assertEqual(len(first.sent),1);self.assertTrue(first.call_llm)
   repeat=Event(text=first.message_str);await p.ai_before_model(repeat);self.assertTrue(repeat.stopped);self.assertEqual(repeat.sent,[])
   self.assertEqual(calls,[('个人 "高贵名门"',False)]);self.assertEqual(remembered,['个人 "高贵名门"'])
 async def test_fast_group_lane_keeps_scope_and_image_requests_on_original_path(self):
  import astrbot.api.message_components as Comp
  with tempfile.TemporaryDirectory() as d:
   p=plugin(d);calls=[]
   async def handle(*args,**kwargs):calls.append(args)
   p.context.get_registered_star=lambda name:SimpleNamespace(activated=True,star_cls=SimpleNamespace(handle=handle))
   for i,event in enumerate([Event(text='查高贵名门的群数据',mention=None),Event(text='查高贵名门的群数据',mention='other')]):
    event.message_obj.message_id=str(i);await p.ai_before_model(event);self.assertTrue(event.stopped)
   private=Event(text='查高贵名门的群数据',group=None,mention=None,message_id='private');await p.ai_before_model(private);self.assertFalse(private.stopped)
   image=Event(text='查高贵名门的群数据',message_id='image');image.parts.append(Comp.Image.fromFileSystem('/tmp/query.png'));await p.ai_before_model(image);self.assertFalse(image.stopped)
   normal=Event(text='你好',message_id='chat');await p.ai_before_model(normal);self.assertFalse(normal.stopped)
   self.assertEqual(calls,[])
 async def test_fast_group_lane_inactive_plugin_falls_back_to_llm(self):
  with tempfile.TemporaryDirectory() as d:
   p=plugin(d);p.context.get_registered_star=lambda name:SimpleNamespace(activated=False,star_cls=object())
   event=Event(text='查一下高贵名门的群数据');await p.ai_before_model(event)
   self.assertFalse(event.stopped);self.assertEqual(event.sent,[]);self.assertTrue(event.get_extra('mahjong_ai_started'))
 async def test_native_selection_is_injected_once_for_next_ai_followup(self):
  import time
  from collections import OrderedDict
  from astrbot.core.agent.tool import FunctionTool
  with tempfile.TemporaryDirectory() as d:
   p=plugin(d);stamp=time.monotonic()
   meta=SimpleNamespace(activated=True,star_cls=SimpleNamespace(selection_context=lambda event:{'query':"个人 '清风难胡+）（' 四麻 S0",'stamp':stamp}))
   p.context.get_registered_star=lambda name:meta if name=='astrbot_plugin_group_data' else None
   e=Event(text='那最低那张呢');e.set_extra('mahjong_ai_started',True)
   req=ProviderRequest(prompt=e.message_str,func_tool=tools('group_data'));await p.ai_limit_request(e,req)
   self.assertEqual(len(req.contexts),1);self.assertIn('清风难胡+）（',req.contexts[0]['content'])
   e._has_send_oper=True;e.set_extra('group_data_resolved_query',"最低 '清风难胡+）（' 四麻 S0")
   await p.ai_tool_finished(e,FunctionTool(name='group_data',description='',parameters={}),{'query':'选 1'},None)
   next_event=Event(text='再看最高');next_event.set_extra('mahjong_ai_started',True)
   next_req=ProviderRequest(prompt=next_event.message_str,func_tool=tools('group_data'));await p.ai_limit_request(next_event,next_req)
   self.assertEqual(len(next_req.contexts),3);self.assertIn('最低',next_req.contexts[-1]['content'])
   self.assertNotIn('选 1',next_req.contexts[-1]['content'])
 async def test_sent_group_query_is_retained_for_followups_without_replaying_tool_calls(self):
  from astrbot.core.agent.tool import FunctionTool
  with tempfile.TemporaryDirectory() as d:
   p=plugin(d);p.context.get_registered_star=lambda name:None
   e=Event(text='看看清风难胡S0三麻最高那局');e.set_extra('mahjong_ai_started',True)
   req=ProviderRequest(prompt=e.message_str,func_tool=tools('group_data'))
   await p.ai_limit_request(e,req)
   e._has_send_oper=True;tool=FunctionTool(name='group_data',description='',parameters={})
   await p.ai_tool_finished(e,tool,{'query':'最高 清风难胡 三麻 S0'},None)
   await p.ai_tool_finished(e,tool,{'query':'最高 清风难胡 三麻 S0'},None)
   follow=Event(text='那四麻最低呢',message_id='2');follow.set_extra('mahjong_ai_started',True)
   next_req=ProviderRequest(prompt=follow.message_str,func_tool=tools('group_data'))
   await p.ai_limit_request(follow,next_req)
   self.assertEqual(len(next_req.contexts),2)
   self.assertIn('最高 清风难胡 三麻 S0',next_req.contexts[-1]['content'])
   self.assertEqual([r['role'] for r in next_req.contexts],['user','assistant'])
   self.assertTrue(all('tool_calls' not in r for r in next_req.contexts))
   self.assertEqual(e.sent,[])
 async def test_group_query_context_does_not_cross_senders_or_groups(self):
  from astrbot.core.agent.tool import FunctionTool
  with tempfile.TemporaryDirectory() as d:
   p=plugin(d);p.context.get_registered_star=lambda name:None
   e=Event(text='清风难胡S0资料');e.set_extra('mahjong_ai_started',True)
   req=ProviderRequest(prompt=e.message_str,func_tool=tools('group_data'));await p.ai_limit_request(e,req)
   e._has_send_oper=True
   await p.ai_tool_finished(e,FunctionTool(name='group_data',description='',parameters={}),{'query':'个人 清风难胡 S0'},None)
   for other in [Event(text='那最高呢',group='other'),Event(text='那最高呢')]:
    if other.group=='group':other.get_sender_id=lambda:'another-user'
    other.set_extra('mahjong_ai_started',True)
    other_req=ProviderRequest(prompt=other.message_str,func_tool=tools('group_data'))
    await p.ai_limit_request(other,other_req)
    self.assertEqual(other_req.contexts,[])
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
   self.assertIn('综合榜 三麻 S0',req.system_prompt);self.assertIn('评分 清风难胡 三麻 S0',req.system_prompt)
   self.assertIn('满10场正式入榜',req.system_prompt)
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
