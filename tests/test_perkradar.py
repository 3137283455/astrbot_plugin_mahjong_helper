import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain
from astrbot_plugin_perkradar.core import Query,State,parse,select,key
from astrbot_plugin_perkradar.client import Client,RadarError
from astrbot_plugin_perkradar.main import PerkRadarPlugin

def item(id=1,title='限免游戏',source='epic',expires=None):
    return {'id':id,'title':title,'summary':'原文摘要','url':'https://store.epicgames.com/p/'+str(id),'canonicalUrl':'https://store.epicgames.com/p/'+str(id),'sourceId':source,'sourceName':'Epic','category':'游戏','publishedAt':time.time()*1000,'expiresAt':expires}

class CoreTests(unittest.TestCase):
    def test_parameter_order_and_keywords(self):
        self.assertEqual(parse('Epic 游戏 第2页'),parse('第2页 游戏 Epic'))
        self.assertEqual(parse('搜索 云服务器 第三页').term,'云服务器')
        self.assertEqual(parse('搜索 云服务器 第三页').page,3)
        with self.assertRaises(ValueError):parse('第0页')
        with self.assertRaises(ValueError):parse('第101页')
    def test_expiry_url_safety_and_duplicate_canonical_urls(self):
        data=[item(1),item(1),item(2,expires=1),{**item(3),'url':'javascript:alert(1)','canonicalUrl':'javascript:alert(1)'},{**item(4),'url':'https://x:y@example.com','canonicalUrl':'https://x:y@example.com'}]
        self.assertEqual([x['id'] for x in select(data,Query())],[1])
    def test_filter_does_not_claim_other_sources(self):
        data=[item(1),item(2,'软件促销','ghxi')]
        self.assertEqual(len(select(data,parse('Epic'))),1)
        self.assertEqual(len(select(data,parse('软件'))),1)
        self.assertEqual(select(data,parse('搜索 不存在')),[])
    def test_persistent_baseline_no_history_and_scopes(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'state.db';s=State(path);old=select([item(1)],Query());new=select([item(1),item(2)],Query())
            s.subscribe('group:a','bot','user','游戏',old)
            self.assertEqual([x['id'] for x in s.unseen('group:a',new)],[2])
            self.assertEqual(s.unseen('group:a',new),[]);s.close();s=State(path)
            self.assertEqual(s.unseen('group:a',new),[])
            s.subscribe('group:b','bot','user','游戏',old)
            self.assertEqual([x['id'] for x in s.unseen('group:b',new)],[2])
            s.unsubscribe('group:a');self.assertEqual(len(s.targets()),1);s.close()

class Event:
    def __init__(self,user='1',group='2',admin=False,mention=True):
        self.user=user;self.group=group;self.admin_value=admin;self.sent=[];self.extras={};self.mention=mention
        self.unified_msg_origin='napcat:GroupMessage:'+group if group else 'napcat:FriendMessage:'+user
        self.bot=SimpleNamespace(call_action=AsyncMock(return_value={'role':'member'}))
    def get_platform_name(self):return 'aiocqhttp'
    def get_sender_id(self):return self.user
    def get_group_id(self):return self.group
    def get_self_id(self):return '3113357165'
    def get_messages(self):return [Comp.At(qq=self.get_self_id())] if self.mention else []
    def get_extra(self,k,default=None):return self.extras.get(k,default)
    def set_extra(self,k,v):self.extras[k]=v
    def is_admin(self):return self.admin_value
    async def send(self,chain):self.sent.append(chain)

class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        p=PerkRadarPlugin.__new__(PerkRadarPlugin);p.config={};p.folder=Path(self.directory.name);p.cards=p.folder
        p.state=State(p.folder/'state.db');self.addCleanup(p.state.close)
        p.client=SimpleNamespace(board=AsyncMock(return_value={'deals':[item(1)],'roundStatus':'success','sourceHealth':{'ok':1,'total':7},'lastUpdatedAt':time.time()*1000}),refresh=AsyncMock())
        p.context=SimpleNamespace(send_message=AsyncMock(return_value=True));p.contexts={};p.primed=set();p.jobs=set();p.started=0;p.refresh_task=None;p.refresh_at=0
        self.plugin=p
    async def test_real_card_and_links_preserved_in_one_forward(self):
        event=Event();await self.plugin.show(event,'Epic')
        self.assertEqual(len(event.sent),1);self.assertIsInstance(event.sent[0].chain[0],Comp.Nodes)
        nodes=event.sent[0].chain[0].nodes
        self.assertTrue(any(isinstance(x,Comp.Image) for x in nodes[0].content))
        self.assertIn('https://store.epicgames.com/p/1',nodes[1].content[0].text)
        self.assertEqual(len(list(self.plugin.cards.glob('*.png'))),1)
    async def test_admin_and_scope_checks_for_subscription(self):
        self.plugin.notice=AsyncMock()
        user=Event();await self.plugin.handle(user,'订阅 游戏')
        self.assertEqual(self.plugin.state.targets(),[])
        user.bot.call_action.return_value={'role':'admin'}
        await self.plugin.handle(user,'订阅 游戏');self.assertEqual(len(self.plugin.state.targets()),1)
        self.assertEqual(self.plugin.context.send_message.await_count,0)
    async def test_ai_only_mentions_or_private_and_no_repeat(self):
        p=self.plugin;p.handle=AsyncMock()
        ordinary=Event(mention=False);ordinary.set_extra('mahjong_ai_request',True)
        await p.query_ai(ordinary,'Epic');p.handle.assert_not_awaited()
        private=Event(group='',mention=False);private.set_extra('mahjong_ai_request',True)
        await p.query_ai(private,'Epic');await p.query_ai(private,'Epic')
        self.assertEqual(p.handle.await_count,1)
    async def test_pagination_isolated_by_sender_and_group(self):
        p=self.plugin;event=Event();await p.show(event,'Epic')
        with self.assertRaises(ValueError):await p.show(Event(user='3'),'第2页')
        with self.assertRaises(ValueError):await p.show(Event(group='4'),'下一页')
    async def test_no_subscribers_means_no_background_fetch(self):
        await self.plugin.poll_once();self.plugin.client.board.assert_not_awaited()
    async def test_restart_baseline_and_new_only_forward(self):
        p=self.plugin;origin='napcat:GroupMessage:2';old=select([item(1)],Query())
        p.state.subscribe(origin,'3113357165','1','游戏',old)
        await p.poll_once();p.context.send_message.assert_not_awaited()
        p.client.board.return_value['deals']=[item(1),item(2)]
        await p.poll_once();self.assertEqual(p.context.send_message.await_count,1)
        self.assertIsInstance(p.context.send_message.call_args.args[1].chain[0],Comp.Nodes)
        await p.poll_once();self.assertEqual(p.context.send_message.await_count,1)
        p.primed.clear();p.client.board.return_value['deals'].append(item(3))
        await p.poll_once();self.assertEqual(p.context.send_message.await_count,1)
    async def test_retry_keeps_entire_original_forward_no_text_fallback(self):
        p=self.plugin;event=Event();chain=MessageChain(chain=[Comp.Nodes([Comp.Node(content=[Comp.Plain('完整结果')],uin='1',name='慧慧')])])
        event.send=AsyncMock(side_effect=[TimeoutError(),None])
        with patch('astrbot_plugin_perkradar.main.asyncio.sleep',new=AsyncMock()):self.assertTrue(await p.send(event,chain))
        self.assertEqual(event.send.await_count,2)
        self.assertTrue(all(x.args[0] is chain for x in event.send.call_args_list))
    async def test_refresh_is_background_and_query_remains_available(self):
        p=self.plugin;p.notice=AsyncMock();gate=asyncio.Event()
        async def refresh(event):await gate.wait()
        p.refresh=refresh
        await p.handle(Event(admin=True),'刷新')
        self.assertFalse(p.refresh_task.done());await p.show(Event(),'Epic')
        self.assertEqual(p.client.board.await_count,1)
        gate.set();await p.refresh_task

class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_pagination_cache_and_malformed_board(self):
        p=Client.__new__(Client);p.lock=asyncio.Lock();p.cache=None;p.stamp=0
        p.request=AsyncMock(side_effect=[{'deals':[item(1)],'pagination':{'hasMore':True}},{'deals':[item(2)],'pagination':{'hasMore':False}}])
        board=await p.board();self.assertEqual(len(board['deals']),2)
        self.assertIs(await p.board(),board);self.assertEqual(p.request.await_count,2)
        p.request=AsyncMock(return_value={'deals':None})
        with self.assertRaises(RadarError):await p.board(force=True)
    def test_credentials_never_accept_remote_or_embedded_secret_url(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'secret.json'
            for url in ['https://example.com','http://token:secret@127.0.0.1:38471']:
                f.write_text(json.dumps({'url':url,'token':'x'*48}))
                with self.assertRaises(ValueError):Client(f)
