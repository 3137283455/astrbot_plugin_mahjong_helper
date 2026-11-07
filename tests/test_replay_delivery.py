import asyncio,json,re,tempfile,unittest,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import AsyncMock,patch
from aiocqhttp.exceptions import ActionFailed
import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain
from astrbot_plugin_mahjong_helper.replay_core import parse_request,ReplayError,normalize_control_text,URL_RE
from astrbot_plugin_mahjong_helper.replay_bridge import ReplayService
from astrbot_plugin_mahjong_helper.replay_delivery import DeliveryStore,split_result,forward_text,GroupPrivateBot,private_history_receipt
from astrbot_plugin_mahjong_helper.replay_pages import ReplayPages
from test_replay import fixture
from test_replay_queue import Request,Context
from test_replay_recent import record
from astrbot_plugin_mahjong_helper.replay_recent import recent_sources,recent_request

def timeout():return ActionFailed({'status':'failed','retcode':1200,'message':'Timeout: NTEvent sendMsg'})

class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def service(self,folder):return ReplayService(Context(),folder)
    def batch(self,s):
        sources=recent_sources(recent_request('12105509 2 玉'),{'uid':'12105509','records':[record(1),record(2)]})
        b=s.batches.create('napcat:FriendMessage:10001','10001',sources,'private','最近对局 · 测试玩家','time')
        for _ in range(2):j=s.store.next();s.store.complete(j,fixture(2))
        return s.batches.get(b['scope'],b['actor'],b['id'])
    async def test_late_history_receipt_prevents_duplicate_retry(self):
        with tempfile.TemporaryDirectory() as f:
            s=self.service(f);send=AsyncMock(side_effect=timeout());verify=AsyncMock(return_value={'message_id':42,'real_seq':2})
            chain=MessageChain().message('已保存的结果 #example')
            self.assertTrue(await s.delivery_store.deliver('late',[chain],send,verify=verify,text_retries=2))
            self.assertEqual(send.await_count,1);self.assertEqual(verify.await_count,1)
            self.assertEqual(s.delivery_store.states('late')[0]['status'],'history_acknowledged')
    async def test_missing_history_retries_text_and_stops_after_success(self):
        with tempfile.TemporaryDirectory() as f:
            s=self.service(f);send=AsyncMock(side_effect=[timeout(),None]);verify=AsyncMock(return_value=None)
            with patch('astrbot_plugin_mahjong_helper.replay_delivery.asyncio.sleep',new_callable=AsyncMock):
                ok=await s.delivery_store.deliver('retry',[MessageChain().message('结果')],send,verify=verify,text_retries=2)
            self.assertTrue(ok);self.assertEqual(send.await_count,2);self.assertGreaterEqual(verify.await_count,1)
            self.assertEqual(s.delivery_store.states('retry')[0]['status'],'acknowledged')
    async def test_retries_are_bounded_and_terminal_state_survives_restart(self):
        with tempfile.TemporaryDirectory() as f:
            s=self.service(f);send=AsyncMock(side_effect=timeout());verify=AsyncMock(return_value=None);parts=[MessageChain().message('结果')]
            with patch('astrbot_plugin_mahjong_helper.replay_delivery.asyncio.sleep',new_callable=AsyncMock):
                self.assertFalse(await s.delivery_store.deliver('bounded',parts,send,verify=verify,text_retries=2))
                recovered=ReplayService(Context(),f)
                self.assertFalse(await recovered.delivery_store.deliver('bounded',parts,send,verify=verify,text_retries=2))
            self.assertEqual(send.await_count,3);self.assertGreaterEqual(verify.await_count,3)
    async def test_history_requires_recipient_scope_sender_content_time_and_real_sequence(self):
        from types import SimpleNamespace
        chain=MessageChain().message('复盘 #example\n75.15%');base={'user_id':3113357165,'time':105,'real_seq':'2','message_id':861620715,'message':[{'type':'text','data':{'text':'复盘 #example\n75.15%'}}]}
        rows=[{**base,'user_id':3439740900},{**base,'time':90},{**base,'real_seq':'0'},
              {**base,'message':[{'type':'text','data':{'text':'另一份报告'}}]}]
        bot=SimpleNamespace(call_action=AsyncMock(return_value={'messages':rows}))
        job={'actor':'3439740900','scope':'napcat:GroupMessage:1036516712'}
        self.assertIsNone(await private_history_receipt(bot,job,chain,100,'3113357165'))
        rows.append(base)
        receipt=await private_history_receipt(bot,job,chain,100,'3113357165')
        self.assertEqual(receipt['real_seq'],2);self.assertEqual(receipt['message_id'],861620715)
        self.assertEqual(bot.call_action.call_args.kwargs,{'user_id':3439740900,'group_id':1036516712,'count':40})
    async def test_rejected_forward_sends_text_link_once_without_reanalysis(self):
        with tempfile.TemporaryDirectory() as f:
            s=self.service(f);sent=[]
            chain=MessageChain(chain=[Comp.Nodes([Comp.Node(content=[Comp.Plain('复盘结果\nhttp://127.0.0.1:6192/r/example')],uin='1',name='慧慧')])])
            async def send(part):
                sent.append(part)
                if isinstance(part.chain[0],Comp.Nodes):raise timeout()
            self.assertTrue(await s.delivery_store.deliver('test-forward',split_result(chain),send,fallback=forward_text))
            self.assertEqual(len(sent),2);self.assertIn('/r/example',sent[1].get_plain_text())
            self.assertEqual(s.delivery_store.states('test-forward')[0]['status'],'fallback_acknowledged')
            self.assertIn('历史',s.delivery_store.description('test-forward'))
            await s.delivery_store.deliver('test-forward',split_result(chain),send,fallback=forward_text)
            recovered=ReplayService(Context(),f)
            await recovered.delivery_store.deliver('test-forward',split_result(chain),send,fallback=forward_text)
            self.assertEqual(len(sent),2)
    async def test_failed_text_fallback_retains_unconfirmed_receipts(self):
        with tempfile.TemporaryDirectory() as f:
            s=self.service(f);send=AsyncMock(side_effect=timeout())
            chain=MessageChain(chain=[Comp.Nodes([Comp.Node(content=[Comp.Plain('复盘结果')],uin='1',name='慧慧')])])
            self.assertFalse(await s.delivery_store.deliver('both-failed',[chain],send,fallback=forward_text))
            self.assertEqual(send.await_count,2)
            self.assertEqual(s.delivery_store.states('both-failed')[0]['status'],'unknown')
            self.assertEqual(s.delivery_store.states('both-failed:fallback:0')[0]['status'],'unknown')
    async def test_private_route_retains_source_group_for_plain_and_forward(self):
        from types import SimpleNamespace
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
        with tempfile.TemporaryDirectory() as f:
            s=self.service(f);client=SimpleNamespace(call_action=AsyncMock())
            platform=SimpleNamespace(meta=lambda:SimpleNamespace(name='aiocqhttp'),get_client=lambda:client)
            s.context.get_platform_inst=lambda platform_id:platform
            async def serialize(bot,message_chain,is_group,session_id):
                self.assertFalse(is_group);self.assertEqual(session_id,'10001')
                await bot.send_private_msg(user_id=int(session_id),message='result')
                await bot.call_action('send_private_forward_msg',user_id=session_id,messages=[])
            with patch.object(AiocqhttpMessageEvent,'send_message',side_effect=serialize):
                await s.send_private({'scope':'napcat:GroupMessage:12345','actor':'10001','delivery':'group'},MessageChain().message('result'))
            self.assertEqual([c.args[0] for c in client.call_action.await_args_list],['send_private_msg','send_private_forward_msg'])
            self.assertTrue(all(c.kwargs['group_id']==12345 and str(c.kwargs['user_id'])=='10001' for c in client.call_action.await_args_list))
            await s.send_private({'scope':'napcat:FriendMessage:10001','actor':'10001','delivery':'private'},MessageChain().message('result'))
            self.assertEqual(len(s.context.sent),1)
    async def test_copied_status_description_and_batch_id(self):
        for text in ('状态 查看进度','状态 查进度','进度 查看进度','查看进度'):
            self.assertEqual(parse_request(text)['action'],'status')
        with self.assertRaises(ReplayError):parse_request('状态 乱填')
        text='批量\nhttps://game.maj-soul.com/1/?paipu=abcdefghijklmnop 西家\nhttps://game.maj-soul.com/1/?paipu=abcdefghijklmnop1 南家'
        self.assertEqual(normalize_control_text(text),text)
        with tempfile.TemporaryDirectory() as f:
            s=self.service(f);b=self.batch(s);e=Request('10001',None)
            await s.handle(e,'状态 查看进度 '+b['id']);self.assertIn('成功2',e.sent[-1].get_plain_text())
    async def test_send_timeout_never_generates_false_business_error(self):
        with tempfile.TemporaryDirectory() as f:
            s=self.service(f);self.batch(s);e=Request('10001',None);e.send=AsyncMock(side_effect=timeout())
            await s.handle(e,'状态 查看进度')
            self.assertEqual(e.send.await_count,1)
            self.assertNotIn('处理失败',e.send.call_args.args[0].get_plain_text())
    async def test_partial_receipt_retains_data_no_automatic_duplicate_on_restart(self):
        with tempfile.TemporaryDirectory() as f:
            s=self.service(f);b=self.batch(s);sent=[]
            async def send(scope,chain):
                sent.append(chain)
                if len(sent)==1:raise timeout()
            s.context.send_message=send
            await s.notify_batches();self.assertEqual(len(sent),3)
            rows=s.delivery_store.states(b['id']);self.assertEqual([r['status'] for r in rows],['acknowledged','acknowledged'])
            self.assertEqual(s.batches.get(b['scope'],b['actor'],b['id'])['done'],2)
            await s.notify_batches();self.assertEqual(len(sent),3)
            recovered=ReplayService(s.context,f);await recovered.notify_batches();self.assertEqual(len(sent),3)
            e=Request('10001',None);await recovered.handle(e,'状态');self.assertIn('确认',e.sent[-1].get_plain_text())
            await recovered.handle(e,'重发');self.assertEqual(len(e.sent),3)
            self.assertTrue(all(j['status']=='done' for j in recovered.batches.get(b['scope'],b['actor'],b['id'])['items']))
    async def test_crash_after_send_claim_does_not_resend_ambiguous_part(self):
        with tempfile.TemporaryDirectory() as f:
            s=self.service(f);b=self.batch(s);self.assertTrue(s.delivery_store.claim(b['id'],0))
            with s.store.db() as db:db.execute('UPDATE replay_batches SET notified=-1 WHERE id=?',(b['id'],))
            new=ReplayService(Context(),f);await new.notify_batches()
            self.assertEqual(len(new.context.sent),2)
            self.assertTrue(any(isinstance(c,Comp.Image) for c in new.context.sent[0][1].chain))
            self.assertEqual(new.delivery_store.states(b['id'])[0]['status'],'acknowledged')
    async def test_forward_contains_one_page_url_and_survives_send_splitter(self):
        with tempfile.TemporaryDirectory() as f:
            s=self.service(f);b=self.batch(s);pages=ReplayPages(s.store,s.batches,'http://127.0.0.1:6192',0,'127.0.0.1');pages.start();s.pages=pages
            try:
                chain=s.batch_chain(b);parts=split_result(chain);self.assertEqual(len(parts),1)
                forward=chain.chain[0];self.assertIsInstance(forward,Comp.Nodes)
                text=''.join(c.text for n in forward.nodes for c in n.content if isinstance(c,Comp.Plain))
                self.assertEqual(len(re.findall(r'https?://\S+',text)),1);self.assertIn('/r/',text)
                self.assertTrue(any(isinstance(c,Comp.Image) for n in forward.nodes for c in n.content))
                await s.notify_batches();self.assertEqual(len(s.context.sent),1)
            finally:pages.close()

class PageTests(unittest.TestCase):
    def test_private_random_link_real_game_mapping_escaping_and_no_queue_changes(self):
        with tempfile.TemporaryDirectory() as f:
            helper=DeliveryTests();s=helper.service(f);b=helper.batch(s)
            with s.store.db() as db:db.execute('UPDATE replay_batches SET label=? WHERE id=?',('<script>alert(1)</script>',b['id']))
            pages=ReplayPages(s.store,s.batches,'http://127.0.0.1:6192',0,'127.0.0.1');pages.start()
            try:
                url=pages.link(b);self.assertEqual(url,pages.link(b));self.assertEqual(len(url.rsplit('/',1)[1]),40)
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));port=pages.server.server_port
                with opener.open('http://127.0.0.1:'+str(port)+urlsplit_path(url)) as response:
                    html=response.read().decode();policy=response.headers['Content-Security-Policy'];self.assertIn('frame-ancestors',policy)
                    from astrbot_plugin_mahjong_helper.replay_pages import COPY_SCRIPT_HASH
                    self.assertIn("script-src 'sha256-"+COPY_SCRIPT_HASH+"'",policy)
                self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html)
                self.assertIn('data-copy-replay=',html);self.assertIn('复制牌谱</button>',html)
                self.assertNotIn('原始牌谱 ↗',html)
                self.assertNotRegex(html,r'<a[^>]+href="https://game\.maj-soul\.com/')
                self.assertIn('第 1 场',html);self.assertIn('第 2 场',html)
                self.assertIn('mnop1_a12105509',html);self.assertIn('mnop2_a12105509',html);self.assertIn('开局西家',html)
                self.assertIn('分局数据',html);self.assertIn('viewport',html)
                with self.assertRaises(urllib.error.HTTPError) as err:opener.open(f'http://127.0.0.1:{port}/r/'+('0'*40))
                self.assertEqual(err.exception.code,404)
                with self.assertRaises(urllib.error.HTTPError):opener.open(f'http://127.0.0.1:{port}/')
                self.assertEqual(s.batches.get(b['scope'],b['actor'],b['id'])['done'],2)
            finally:pages.close()

def urlsplit_path(url):
    from urllib.parse import urlsplit
    return urlsplit(url).path

if __name__=='__main__':unittest.main()
