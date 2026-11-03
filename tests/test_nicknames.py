import asyncio,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import astrbot.api.message_components as Comp
from astrbot_plugin_group_data import periods
from astrbot_plugin_group_data.core import validate
from astrbot_plugin_group_data.nicknames import candidates,choice,SelectionCache
import test_commands
BaseEvent=test_commands.Event

class Event(BaseEvent):
    def __init__(self,text='',sender='56789',group='987654321',bot='12345',message_id=''):
        super().__init__(group);self.text=text;self.sender=sender;self.self_id=bot
        self.parts=[Comp.Plain(text)];self.message_str=text;self.stopped=False;self.extras={};self.llm=None
        self.message_obj=SimpleNamespace(message_id=message_id)
    def get_sender_id(self):return self.sender
    def get_self_id(self):return self.self_id
    def get_messages(self):return self.parts
    def set_extra(self,key,value):self.extras[key]=value
    def get_extra(self,key,default=None):return self.extras.get(key,default)
    def stop_event(self):self.stopped=True
    def should_call_llm(self,value):self.llm=value

class MatchingTests(unittest.TestCase):
    def test_exact_partial_punctuation_case_alias_and_symbol_only(self):
        names=['今天吃啥好呢=','清风难胡+）（','清风难胡呀','A B_C','其他']
        self.assertEqual(candidates('今天吃啥好呢=',names),('今天吃啥好呢=',[]))
        self.assertEqual(candidates('今天吃啥',names),(None,['今天吃啥好呢=']))
        self.assertEqual(set(candidates('清风难胡',names)[1]),set(names[1:3]))
        self.assertEqual(candidates('abc',names)[1],['A B_C'])
        self.assertEqual(candidates('旧名',names,{'旧名称':'其他'})[1],['其他'])
        self.assertEqual(candidates('未找到',names),(None,[]))
        self.assertEqual(candidates('',names),(None,[]))
        self.assertEqual(candidates('不存在+',names),(None,[]))
        self.assertEqual(candidates('清风难胡',['清风难胡','清风难胡+'])[0],'清风难胡')
    def test_only_selection_language_is_consumed(self):
        for text in ['2','第2个','选择第二个','就第二位','选 2号']:self.assertEqual(choice(text),2)
        self.assertEqual(choice('第二十个'),20)
        self.assertEqual(choice('２'),2)
        for text in ['播放第二首','把第二首加入歌单','今天2个人','/群数据 选 2','来道题']:self.assertIsNone(choice(text))
    def test_expiry_replacement_and_memory_bound(self):
        clock=[0];cache=SelectionCache(limit=2,clock=lambda:clock[0]);cache.put('a',{'query':1});cache.put('a',{'query':2})
        self.assertEqual(cache.get('a')['query'],2)
        cache.put('b',{});cache.put('c',{});self.assertIsNone(cache.get('a'))
        clock[0]=180;self.assertIsNone(cache.get('c'))

class NicknameFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_inflight_retry_and_completed_retry_do_not_repeat_query_or_enter_llm(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.seed(folder);await p.handle(Event(),'极值 今天吃啥 四麻')
            started=asyncio.Event();release=asyncio.Event();calls=[]
            async def delayed(*args):calls.append(args);started.set();await release.wait()
            p.player_extremes=delayed
            first=Event('第一个',message_id='quoted-first');task=asyncio.create_task(p.nickname_followup(first))
            await asyncio.wait_for(started.wait(),2)
            retry=Event('1',message_id='numeric-retry');await p.nickname_followup(retry)
            self.assertTrue(retry.stopped);self.assertIn('正在生成',retry.sent[0]);self.assertEqual(len(calls),1)
            self.assertIn('今天吃啥好呢=',retry.get_extra('group_data_resolved_query'))
            release.set();await task
            again=Event('1',message_id='third');await p.nickname_followup(again)
            self.assertTrue(again.stopped);self.assertFalse(again.sent);self.assertEqual(len(calls),1)
            tool_retry=Event();await p.handle(tool_retry,'选 1 S1',allow_write=False)
            self.assertFalse(any(isinstance(x,str) and '过期' in x for x in tool_retry.sent))
    async def test_completed_retry_ack_and_new_message_does_not_steal_music_followup(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.seed(folder);await p.handle(Event(),'个人 今天吃啥 四麻')
            with patch('astrbot_plugin_group_data.main.cards.profile') as render:
                await p.nickname_followup(Event('1',message_id='first'))
                again=Event('1',message_id='next');await p.nickname_followup(again)
                self.assertTrue(again.stopped);self.assertIn('完成查询',again.sent[0]);render.assert_called_once()
                await p.nickname_followup(Event('播放第二首',message_id='music'))
                music_number=Event('1',message_id='music-number');await p.nickname_followup(music_number)
                self.assertFalse(music_number.stopped)
    async def test_unspecified_mode_omits_empty_profile_and_explicit_mode_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.seed(folder)
            with patch('astrbot_plugin_group_data.main.cards.profile') as render:
                await p.handle(Event(),'个人 今天吃啥好呢=');render.assert_called_once();self.assertEqual(render.call_args.args[2],4)
                render.reset_mock();await p.handle(Event(),'个人 今天吃啥好呢= 三麻')
                render.assert_called_once();self.assertEqual(render.call_args.args[2],3)
            await p.handle(Event(),'个人 今天吃啥好呢= 至少5场')
            self.assertIn('最少场次',p.card.call_args.args[-1][0])
    async def test_history_hint_discovers_real_name_without_switching_or_creating_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.seed(folder,archive=True)
            await p.handle(Event(),'个人 今天吃啥')
            lines=p.card.call_args.args[-1]
            self.assertIn('S1',lines[0]);self.assertTrue(any('S0' in x and '今天吃啥好呢=' in x for x in lines))
            self.assertTrue(any('个人' in x and 'S0' in x for x in lines))
            self.assertIsNone(p.selection_cache().get(p.selection_key(Event())))
    def seed(self,folder,archive=False):
        p=test_commands.CommandTests.setup_plugin(self,folder);scope='12345:987654321'
        names=['今天吃啥好呢=','清风难胡+）（','清风难胡呀',"Q'昵称"]
        p.store.reconcile(scope,[{'msg':'1','idx':0,'stamp':time.time()}]);r=p.store.source(scope,'1',0)
        p.store.attach(r['id'],'h',Path(folder)/'original.png')
        p.store.save('h','accepted',validate({'is_result':True,'mode':4,'confidence':1,'players':[{'name':n,'place':i,'points':score} for i,(n,score) in enumerate(zip(names,[40000,30000,20000,10000]),1)]}))
        if archive:periods.cutover(p.store,scope)
        p.card=AsyncMock();return p
    async def test_single_partial_prompts_and_exact_goes_direct_without_database_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.seed(folder);before=p.store.db.total_changes
            with patch('astrbot_plugin_group_data.main.cards.profile') as profile:
                query=Event();await p.handle(query,'个人 今天吃啥 四麻');profile.assert_not_called()
                self.assertEqual(len(query.sent),1);self.assertIn('今天吃啥好呢=',query.sent[0])
                self.assertIn('第1个',query.sent[0]);p.card.assert_not_called()
                await p.handle(Event(),'选 1');self.assertEqual(profile.call_args.args[4],'今天吃啥好呢=')
                profile.reset_mock();p.card.reset_mock()
                await p.handle(Event(),'个人 今天吃啥好呢= 四麻');profile.assert_called_once();p.card.assert_not_called()
            self.assertEqual(p.store.db.total_changes,before)
    async def test_choices_preserve_season_mode_time_minimum_and_extreme_action(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.seed(folder,archive=True)
            await p.handle(Event(),'最低 清风难胡 四麻 S0 30天 至少2场')
            p.player_extremes=AsyncMock();await p.handle(Event(),'选 1')
            call=p.player_extremes.call_args.args
            self.assertIn(call[3],['清风难胡+)(','清风难胡呀']);self.assertEqual(call[4:],([4],'最低','S0赛季',30,2))
            self.assertIn('S0',p.selection_context(Event())['query'])
    async def test_group_sender_and_bot_isolation_no_llm_and_other_mentions_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.seed(folder);await p.handle(Event(),'个人 今天吃啥 四麻')
            for e in [Event('1',sender='another'),Event('1',group='other'),Event('1',bot='other'),Event('播放第二首')]:
                await p.nickname_followup(e);self.assertFalse(e.stopped);self.assertFalse(e.sent)
            other=Event('1');other.parts.insert(0,Comp.At(qq='someone-else'));await p.nickname_followup(other);self.assertFalse(other.stopped)
            with patch('astrbot_plugin_group_data.main.cards.profile') as profile:
                e=Event('第1个',message_id='reply');await p.nickname_followup(e)
                self.assertTrue(e.stopped);self.assertTrue(e.llm);profile.assert_called_once()
                repeat=Event('第1个',message_id='reply');await p.nickname_followup(repeat)
                self.assertTrue(repeat.stopped);self.assertFalse(repeat.sent);profile.assert_called_once()
    async def test_invalid_selection_does_not_consume_and_cancel_expiry_and_new_query(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.seed(folder);await p.handle(Event(),'个人 今天吃啥 四麻')
            invalid=Event();await p.handle(invalid,'选 9');self.assertIn('请选择',invalid.sent[0]);self.assertIsNotNone(p.selection_cache().get(p.selection_key(invalid)))
            await p.handle(Event(),'取消');self.assertIsNone(p.selection_cache().get(p.selection_key(Event())))
            expired=Event();await p.handle(expired,'选 1');self.assertIn('过期',expired.sent[0])
            await p.handle(Event(),'个人 今天吃啥 四麻');await p.handle(Event(),'帮助')
            self.assertIsNone(p.selection_cache().get(p.selection_key(Event())))
    async def test_apostrophe_name_is_not_reparsed_and_pair_queries_select_each_person(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.seed(folder)
            with patch('astrbot_plugin_group_data.main.cards.profile') as profile:
                await p.handle(Event(),'个人 Q 四麻');await p.handle(Event(),'选 1')
                self.assertEqual(profile.call_args.args[4],"Q'昵称")
                from astrbot_plugin_group_data.main import parse_query
                self.assertEqual(parse_query(p.selection_context(Event())['query'])['rest'],["Q'昵称"])
            p.card.reset_mock();await p.handle(Event(),'对比 今天吃啥 清风难胡 四麻')
            await p.handle(Event(),'选 1');self.assertEqual(len(p.selection_cache().get(p.selection_key(Event()))['options']),2)
            await p.handle(Event(),'选 1')
            lines=p.card.call_args.args[-1];self.assertTrue(any('今天吃啥好呢=' in line for line in lines));self.assertTrue(any('清风难胡' in line for line in lines))
            self.assertIsNone(p.selection_cache().get(p.selection_key(Event())))
    async def test_no_cross_season_search_and_expired_bare_numbers_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.seed(folder,archive=True);await p.handle(Event(),'个人 今天吃啥 四麻')
            self.assertIsNone(p.selection_cache().get(p.selection_key(Event())))
            await p.handle(Event(),'个人 今天吃啥 S0 四麻')
            p.selection_cache().clock=lambda:time.monotonic()+181
            e=Event('1');await p.nickname_followup(e);self.assertFalse(e.stopped);self.assertFalse(e.sent)
            await p.handle(e,'选 1');self.assertIn('过期',e.sent[0])

if __name__=='__main__':unittest.main()
