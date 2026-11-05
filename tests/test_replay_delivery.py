import asyncio,json,re,tempfile,unittest,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import AsyncMock,patch
from aiocqhttp.exceptions import ActionFailed
import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain
from astrbot_plugin_mahjong_helper.replay_core import parse_request,ReplayError,normalize_control_text,URL_RE
from astrbot_plugin_mahjong_helper.replay_bridge import ReplayService
from astrbot_plugin_mahjong_helper.replay_delivery import DeliveryStore,split_result
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
            await s.notify_batches();self.assertEqual(len(sent),2)
            rows=s.delivery_store.states(b['id']);self.assertEqual([r['status'] for r in rows],['unknown','acknowledged'])
            self.assertEqual(s.batches.get(b['scope'],b['actor'],b['id'])['done'],2)
            await s.notify_batches();self.assertEqual(len(sent),2)
            recovered=ReplayService(s.context,f);await recovered.notify_batches();self.assertEqual(len(sent),2)
            e=Request('10001',None);await recovered.handle(e,'状态');self.assertIn('未确认',e.sent[-1].get_plain_text())
            await recovered.handle(e,'重发');self.assertEqual(len(e.sent),3)
            self.assertTrue(all(j['status']=='done' for j in recovered.batches.get(b['scope'],b['actor'],b['id'])['items']))
    async def test_crash_after_send_claim_does_not_resend_ambiguous_part(self):
        with tempfile.TemporaryDirectory() as f:
            s=self.service(f);b=self.batch(s);self.assertTrue(s.delivery_store.claim(b['id'],0))
            with s.store.db() as db:db.execute('UPDATE replay_batches SET notified=-1 WHERE id=?',(b['id'],))
            new=ReplayService(Context(),f);await new.notify_batches()
            self.assertEqual(len(new.context.sent),1)
            self.assertFalse(any(isinstance(c,Comp.Image) for c in new.context.sent[0][1].chain))
            self.assertEqual(new.delivery_store.states(b['id'])[0]['status'],'unknown')
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
                    html=response.read().decode();self.assertIn('frame-ancestors',response.headers['Content-Security-Policy'])
                self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html)
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
