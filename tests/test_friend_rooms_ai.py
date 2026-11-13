import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
import astrbot.api.message_components as C
from astrbot.core.provider.entities import ProviderRequest
from astrbot_plugin_mahjong_helper.friend_rooms import room_request,current_room
from astrbot_plugin_mahjong_helper.ai_entry import AI_TOOLS
from test_ai_entry import Event,plugin,tools
from test_room_broadcast import _Bot


class FriendRoomParserTests(unittest.TestCase):
 def test_compact_and_explicit_requests_use_current_payload(self):
  url='https://game.maj-soul.com/1/?room=77182&foo=bar'
  cases={'友77182':'77182','友 77182':'77182','友人房77182':'77182',
         '帮我艾特友人房':'','帮我艾特一下友人房77182':'77182',
         '广播友人房 77182':'77182','帮我艾特全体，友人房77182':'77182',
         '友人房77182，帮我艾特全体':'77182',
         '帮我广播友人房 '+url:url,'帮我广播友人房 这个':'',
         '帮我广播友人房 room_abc':'room_abc'}
  for text,wanted in cases.items():self.assertEqual(room_request(text),wanted,text)
 def test_other_chat_questions_and_bare_numbers_do_not_auto_broadcast(self):
  for text in ['你好朋友77182','77182','不要广播友人房77182','友人房怎么用',
               '怎么广播友人房77182','我不想广播友人房77182','帮我艾特友人房 谢谢']:
   self.assertIsNone(room_request(text),text)
 def test_tool_cannot_use_old_room_wrong_substring_or_negated_room(self):
  for room,text in [('77182','帮我艾特友人房'),('77182','广播友人房 99888'),
                    ('77182','帮我广播友人房771823'),('77182','不要广播友人房77182'),
                    ('77182','为什么不能广播友人房77182'),('77182','不是77182，帮我广播友人房'),
                    ('77182','77182')]:
   self.assertIsNone(current_room(room,text),(room,text))
  self.assertEqual(current_room('77182','帮我把77182这个友人房艾特全体'),'77182')
  url='https://game.maj-soul.com/1/?room=77182'
  self.assertEqual(current_room(url,'帮我广播友人房 '+url),url)
  self.assertIsNone(current_room(url,'帮我广播友人房 https://example.com/room=99888'))


class FriendRoomAITests(unittest.IsolatedAsyncioTestCase):
 async def test_fast_compact_request_calls_existing_business_once_without_model(self):
  with tempfile.TemporaryDirectory() as d:
   p=plugin(d);p._broadcast_room=AsyncMock(return_value=None)
   p.context.get_provider_by_id=lambda _:self.fail('Must not request a model for an explicit room')
   e=Event(text='友77182');await p.ai_before_model(e)
   p._broadcast_room.assert_awaited_once_with(e,'77182');self.assertTrue(e.stopped);self.assertEqual(e.sent,[])
   await p.ai_before_model(Event(text=e.message_str))
   self.assertEqual(p._broadcast_room.await_count,1)
 async def test_missing_room_only_mentions_requester_even_with_old_chat_and_quote(self):
  with tempfile.TemporaryDirectory() as d:
   p=plugin(d);p._broadcast_room=AsyncMock()
   p._ai_chats={'old':(1,[{'role':'user','content':'友77182'}])}
   e=Event(text='帮我艾特友人房');e.parts.insert(0,C.Reply(id='123',chain=[C.Plain('友77182')],sender_id='someone-else'))
   await p.ai_before_model(e);p._broadcast_room.assert_not_awaited();self.assertTrue(e.stopped)
   self.assertEqual(len(e.sent),1)
   self.assertEqual([(type(x).__name__,getattr(x,'qq',getattr(x,'text',None))) for x in e.sent[0].chain],
                    [('At','user'),('Plain',' 请提供房间号或链接。')])
 async def test_model_cannot_broadcast_room_from_history_or_repeat_action(self):
  with tempfile.TemporaryDirectory() as d:
   p=plugin(d);p._broadcast_room=AsyncMock(return_value=None)
   e=Event(text='帮我艾特友人房');e.set_extra('mahjong_ai_request',True)
   await p.friend_room_ai(e,'77182');p._broadcast_room.assert_not_awaited()
   self.assertEqual(e.sent[0].chain[0].qq,'user');self.assertIn('提供房间号',e.sent[0].chain[1].text)
   fresh=Event(text='帮我把77182这个友人房艾特全体',message_id='2');fresh.set_extra('mahjong_ai_request',True)
   await p.friend_room_ai(fresh,'77182');await p.friend_room_ai(fresh,'77182')
   p._broadcast_room.assert_awaited_once_with(fresh,'77182')
 async def test_no_self_mention_other_member_or_private_chat_can_broadcast(self):
  with tempfile.TemporaryDirectory() as d:
   p=plugin(d);p._broadcast_room=AsyncMock()
   for i,mention in enumerate((None,'another-member')):
    e=Event(text='友77182',mention=mention,message_id=str(i));await p.ai_before_model(e)
   private=Event(text='帮我广播友人房77182',group=None,mention=None,message_id='p');private.set_extra('mahjong_ai_request',True)
   await p.friend_room_ai(private,'77182');p._broadcast_room.assert_not_awaited()
   self.assertIn('QQ 群',private.sent[0].chain[0].text)
 async def test_tool_is_enabled_with_current_message_only_instructions(self):
  with tempfile.TemporaryDirectory() as d:
   p=plugin(d);e=Event(text='请把77182这个友人房广播给大家');e.set_extra('mahjong_ai_started',True)
   req=ProviderRequest(prompt=e.message_str,func_tool=tools('broadcast_friend_room','execute_code'))
   await p.ai_limit_request(e,req)
   self.assertEqual(req.func_tool.names(),['broadcast_friend_room'])
   self.assertIn('broadcast_friend_room',AI_TOOLS);self.assertIn('不读取聊天历史',req.system_prompt)
 async def test_natural_request_keeps_permission_check_and_cooldown(self):
  with tempfile.TemporaryDirectory() as d:
   p=plugin(d);p._room_broadcast_lock=asyncio.Lock()
   def event(text,id,bot):
    e=Event(text=text,group='1036516712',message_id=id);e.bot=bot;e.message_obj.self_id='3113357165'
    return e
   denied=event('友77182','1',_Bot(role='member'));await p.ai_before_model(denied)
   self.assertEqual(denied.bot.sent,[]);self.assertIn('管理员',denied.sent[0].chain[1].text)
   bot=_Bot();first=event('帮我艾特友人房77182','2',bot);await p.ai_before_model(first)
   self.assertEqual(len(bot.sent),1);self.assertEqual(bot.sent[0]['message'][0],{'type':'at','data':{'qq':'all'}})
   again=event('广播友人房 99888','3',bot);await p.ai_before_model(again)
   self.assertEqual(len(bot.sent),1);self.assertIn('冷却',again.sent[0].chain[1].text)
 def test_five_and_fifteen_minute_cooldowns_are_unchanged(self):
  with tempfile.TemporaryDirectory() as d:
   p=plugin(d);p.db.record_room_broadcast('actor',now=1000)
   self.assertEqual(p.db.room_broadcast_wait('actor',now=1000),300)
   p.db.record_room_broadcast('actor',now=1300)
   self.assertEqual(p.db.room_broadcast_wait('actor',now=1300),900)


if __name__=='__main__':unittest.main()
