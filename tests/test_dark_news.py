import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_bilibili.core.presentation import dark_context, notification_text, plain_text
from astrbot_plugin_bilibili.core.models import RenderPayload
from astrbot_plugin_bilibili.services.renderer import Renderer
from astrbot_plugin_bilibili.services.listener import DynamicListener
from astrbot_plugin_bilibili.services.dispatcher import SubscriptionNotificationDispatcher
from astrbot.api.message_components import AtAll, Image, Plain


class PresentationTests(unittest.TestCase):
    def test_real_comic_body_produces_short_title_and_chapter(self):
        p=RenderPayload(name='雀魂麻将majsoul',type='DYNAMIC_TYPE_DRAW',
            text='#雀魂# #雀魂漫画#<br>☆漫画第一百五十六话☆<br>不给糖就捣蛋<br>作者：@藤川フレ',
            image_urls=['https://i0.hdslb.com/comic.jpg'])
        d=dark_context(p)
        self.assertEqual((d['news_title'],d['news_subtitle']),('漫画更新','第156话'))
        self.assertEqual(d['news_excerpt'],'不给糖就捣蛋')
        self.assertEqual(d['news_count'],1)
        self.assertEqual(notification_text(p),'🔔 B站动态更新\n雀魂麻将majsoul\n漫画更新 · 第156话')

    def test_video_title_and_cover_are_preserved(self):
        p=RenderPayload(name='视频UP',type='DYNAMIC_TYPE_AV',title='很长的视频标题'*30,image_urls=['https://i0.hdslb.com/cover.jpg'])
        d=dark_context(p)
        self.assertEqual(d['news_label'],'视频投稿')
        self.assertEqual(d['news_cover'],p.image_urls[0])
        self.assertEqual(d['news_title'],p.title[:100])
        self.assertLess(d['news_title_size'],66)
        self.assertIn(p.title[:100],notification_text(p))

    def test_text_only_has_readable_fallback_without_fake_image(self):
        d=dark_context(RenderPayload(name='UP',type='DYNAMIC_TYPE_WORD',text='第一句更新<br>第二句正文'))
        self.assertEqual(d['news_title'],'第一句更新')
        self.assertEqual(d['news_excerpt'],'第二句正文')
        self.assertEqual(d['news_cover'],'')

    def test_forward_uses_original_cover_without_changing_comment(self):
        p=RenderPayload(name='转发UP',type='DYNAMIC_TYPE_FORWARD',text='转发评论')
        p.forward=SimpleNamespace(image_urls=['https://i0.hdslb.com/original.jpg'])
        d=dark_context(p)
        self.assertEqual(d['news_label'],'转发动态')
        self.assertEqual(d['news_title'],'转发评论')
        self.assertEqual(d['news_count'],1)

    def test_live_title_and_author_are_taken_from_real_notice(self):
        p=RenderPayload(name='AstrBot',url='https://live.bilibili.com/123',title='今天来打麻将',text='📣 你订阅的UP 「直播UP」 开播了！')
        d=dark_context(p)
        self.assertEqual((d['news_tag'],d['news_name'],d['news_title']),('开播提醒','直播UP','今天来打麻将'))
        self.assertEqual(notification_text(p,'live'),'🔔 开播提醒\n直播UP\n今天来打麻将')
        p.text='📣 你订阅的UP 「直播UP」 下播了！'
        self.assertEqual(dark_context(p)['news_tag'],'下播提醒')

    def test_rich_text_is_plain_and_unsafe_image_schemes_are_not_rendered(self):
        p=RenderPayload(name='UP',text='<script>alert(1)</script><p>正文 &amp; 原文</p>',image_urls=['file:///etc/passwd','javascript:alert(1)'])
        d=dark_context(p)
        self.assertEqual(d['news_title'],'正文 & 原文')
        self.assertEqual(d['news_cover'],'')
        self.assertNotIn('alert',plain_text(p.text))


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    def listener(self, style='dark_news'):
        context=SimpleNamespace(send_message=AsyncMock())
        listener=DynamicListener.__new__(DynamicListener)
        listener.context=context;listener.node=False
        listener.renderer=Renderer(SimpleNamespace(),True,style)
        listener.dispatcher=SubscriptionNotificationDispatcher(context)
        listener._send_ai_summary=AsyncMock()
        return listener,context

    async def test_automatic_cached_push_gets_one_header_and_preserves_mention_and_link(self):
        listener,context=self.listener()
        p=RenderPayload(name='UP')
        parts=[AtAll(),Image.fromFileSystem('/tmp/example.png'),Plain('\nhttps://t.bilibili.com/123')]
        for _ in range(2):
            await listener._send_dynamic('test:GroupMessage:1',parts,summary_payload=p)
        self.assertEqual(len(parts),3)
        self.assertEqual(context.send_message.await_count,2)
        for call in context.send_message.await_args_list:
            chain=call.args[1].chain
            self.assertEqual(sum(isinstance(x,Plain) and 'B站动态更新' in x.text for x in chain),1)
            self.assertIsInstance(chain[1],AtAll)
            self.assertTrue(chain[-1].text.endswith('/123'))

    async def test_legacy_style_keeps_original_message_chain(self):
        listener,context=self.listener('template_2')
        parts=[Plain('原来消息')]
        await listener._send_dynamic('test:FriendMessage:1',parts,summary_payload=RenderPayload(name='UP'))
        self.assertEqual(context.send_message.await_args.args[1].chain,parts)

    async def test_silent_recovery_and_success_callback_still_belong_to_dispatcher(self):
        listener,context=self.listener()
        listener.dispatcher.set_silent_until_ts(9999999999)
        await listener._send_dynamic('test:FriendMessage:1',[Plain('原文')],summary_payload=RenderPayload(name='UP'))
        context.send_message.assert_not_awaited()
        listener._send_ai_summary.assert_not_awaited()

    async def test_renderer_uses_short_context_and_legacy_context_is_unchanged(self):
        from PIL import Image as PILImage
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'mock.png';PILImage.effect_noise((128,128),80).save(path)
            star=SimpleNamespace(html_render=AsyncMock(return_value=str(path)))
            p=RenderPayload(name='UP',text='正文',banner='huge old decoration',qrcode='old QR')
            r=Renderer(star,True,'dark_news')
            self.assertEqual(await r.render_dynamic(p),str(path))
            data=star.html_render.await_args.kwargs['data']
            self.assertIn('news_title',data)
            self.assertNotIn('qrcode',data)
            self.assertNotIn('banner',data)
            await r.render_dynamic(p,style='template_2')
            self.assertEqual(star.html_render.await_args.kwargs['data'],p.to_template_context())


if __name__=='__main__':
    unittest.main()
