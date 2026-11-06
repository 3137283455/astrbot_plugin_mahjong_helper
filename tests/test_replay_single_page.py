import json,re,tempfile,unittest,urllib.request
from pathlib import Path
import astrbot.api.message_components as Comp
from astrbot_plugin_mahjong_helper.replay_core import parse_url
from astrbot_plugin_mahjong_helper.replay_delivery import split_result
from astrbot_plugin_mahjong_helper.replay_bridge import ReplayService
from astrbot_plugin_mahjong_helper.replay_pages import ReplayPages
from test_replay import fixture
from test_replay_queue import Context,Request


def forward_text(chain):
    return '\n'.join(p.text for node in chain.chain[0].nodes for p in node.content if isinstance(p,Comp.Plain))


class SinglePageTests(unittest.IsolatedAsyncioTestCase):
    def service(self,folder):
        service=ReplayService(Context(),folder)
        event=Request('10001',None)
        source=parse_url('https://game.maj-soul.com/1/?paipu=abcdefghijklmnop');source['seat']=1
        job,_=service.store.create(event.unified_msg_origin,event.actor,source,'private')
        service.store.complete(job,fixture())
        service.store.update(job['id'],remote_task='953bc4e73316d17b')
        job=service.store.get(event.unified_msg_origin,event.actor,job['id'])
        pages=ReplayPages(service.store,service.batches,'http://127.0.0.1:6192',0,'127.0.0.1')
        pages.start();service.pages=pages
        return service,event,job

    async def test_single_views_send_one_forward_and_one_stable_page_url(self):
        with tempfile.TemporaryDirectory() as folder:
            service,event,job=self.service(folder)
            try:
                links=[]
                for view,hand in [('result',None),('differences',None),('hand',0)]:
                    chain=service.result_chain(job,view,hand)
                    self.assertEqual(len(split_result(chain)),1)
                    self.assertIsInstance(chain.chain[0],Comp.Nodes)
                    text=forward_text(chain);urls=re.findall(r'https?://\S+',text)
                    self.assertEqual(len(urls),1);self.assertIn('/r/',urls[0]);links+=urls
                    self.assertNotIn('输入链接',text)
                    self.assertNotIn('killerducky',text)
                    self.assertTrue(any(isinstance(p,Comp.Image) for n in chain.chain[0].nodes for p in n.content))
                self.assertEqual(len(set(links)),1)
                await service.notify(job)
                self.assertEqual(len(service.context.sent),1)
                await service.handle(event,'重发 '+job['id'])
                self.assertEqual(len(event.sent),1)
                self.assertEqual(re.findall(r'https?://\S+',forward_text(event.sent[0])),links[:1])
                await service.handle(event,'链接 '+job['id'])
                self.assertEqual(event.sent[-1].get_plain_text(),links[0])
                with service.store.db() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM replay_batches').fetchone()[0],0)
                    self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],1)
            finally:
                service.pages.close()

    async def test_single_page_contains_details_copy_and_owned_job_without_new_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            service,event,job=self.service(folder)
            try:
                url=service.pages.single_link(job)
                token=url.rsplit('/',1)[1]
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open('http://127.0.0.1:'+str(service.pages.server.server_port)+'/r/'+token) as response:
                    html=response.read().decode()
                self.assertIn('单牌谱复盘 · 乙',html)
                self.assertIn('任务 '+job['id'],html)
                self.assertIn('<details open>',html)
                self.assertIn('data-copy-replay="'+job['url']+'"',html)
                self.assertIn('打开本场逐巡复盘',html)
                self.assertIn('第2巡',html.replace(' ',''))
                self.assertNotIn('逐场一致率趋势',html)
                self.assertNotIn('原始牌谱 ↗',html)
                self.assertEqual(url,service.pages.single_link(job))
                with service.store.db() as db:
                    db.execute('UPDATE replay_pages SET actor=? WHERE token=?',('someone-else',token))
                service.pages.cache.clear()
                from astrbot_plugin_mahjong_helper.replay_core import ReplayError
                with self.assertRaises(ReplayError):service.pages.page(token)
            finally:
                service.pages.close()

    async def test_web_unavailable_still_keeps_one_forward_and_no_link_flood(self):
        with tempfile.TemporaryDirectory() as folder:
            service,event,job=self.service(folder)
            try:
                pages=service.pages;service.pages=None
                chain=service.result_chain(job)
                self.assertEqual(len(split_result(chain)),1)
                text=forward_text(chain)
                self.assertEqual(len(re.findall(r'https?://\S+',text)),1)
                self.assertIn('完整逐巡',text)
                self.assertNotIn('输入链接',text)
            finally:
                pages.close()


if __name__=='__main__':unittest.main()
