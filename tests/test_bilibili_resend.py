import copy
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import astrbot.api.message_components as Comp
from astrbot_plugin_mahjong_helper.bilibili_bridge import resend_latest
from test_ai_entry import Event, plugin


class ResendTests(unittest.IsolatedAsyncioTestCase):
    def source(self, *, record=None, filtered=False, image=None):
        if record is None:
            record=SimpleNamespace(uid=353240497,last='new',recent_ids=['new','old'],filter_types=[],filter_regex=[])
        payload=SimpleNamespace(url='https://t.bilibili.com/new',title='最新动态')
        def parse(dynamic, preview):
            if preview.last=='new' or 'new' in preview.recent_ids or filtered:
                return []
            self.assertEqual(preview.filter_types,record.filter_types)
            self.assertEqual(preview.filter_regex,record.filter_regex)
            return [SimpleNamespace(payload=payload,has_payload=lambda:True)]
        obj=SimpleNamespace(
            rai=bool(image),
            data_manager=SimpleNamespace(get_subscription=lambda umo,uid:record),
            bili_client=SimpleNamespace(credential=SimpleNamespace(sessdata='test'),get_latest_dynamics=AsyncMock(return_value={'items':[{'id_str':'new'}]})),
            dynamic_listener=SimpleNamespace(_parse_and_filter_dynamics=parse,_compose_plain_push=lambda p,render_fail=False:[Comp.Plain(p.title+' '+p.url)]),
            renderer=SimpleNamespace(render_dynamic=AsyncMock(return_value=image)),
            dispatcher=SimpleNamespace(send=AsyncMock(side_effect=AssertionError('MANUAL_REPLY_MUST_NOT_USE_BACKGROUND_DISPATCHER'))))
        return obj,record

    async def test_previously_seen_latest_can_be_resent_twice_without_mutating_cursor_or_filters(self):
        record=SimpleNamespace(uid=353240497,last='new',recent_ids=['new','old'],filter_types=['forward'],filter_regex=['抽奖'])
        obj,record=self.source(record=record)
        before=copy.deepcopy(vars(record))
        for i in range(2):
            e=Event(message_id=str(i));await resend_latest(obj,e,'353240497')
            self.assertEqual(len(e.sent),1)
            self.assertIn('https://t.bilibili.com/new',e.sent[0].get_plain_text())
        self.assertEqual(vars(record),before)
        self.assertEqual(obj.bili_client.get_latest_dynamics.await_count,2)
        obj.dispatcher.send.assert_not_awaited()

    async def test_content_filters_still_apply(self):
        obj,record=self.source(filtered=True)
        e=Event();await resend_latest(obj,e,'353240497')
        self.assertIn('过滤规则排除',e.sent[0].get_plain_text())
        self.assertNotIn('没有新动态',e.sent[0].get_plain_text())

    async def test_empty_api_result_returns_visible_failure(self):
        obj,record=self.source();obj.bili_client.get_latest_dynamics.return_value=None
        e=Event();await resend_latest(obj,e,'353240497')
        self.assertIn('未获取到',e.sent[0].get_plain_text())

    async def test_image_preview_is_sent_directly_once(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'preview.png';Image.new('RGB',(20,20),'white').save(path)
            obj,record=self.source(image=str(path))
            e=Event();await resend_latest(obj,e,'353240497')
            self.assertEqual(len(e.sent),1)
            self.assertTrue(any(isinstance(p,Comp.Image) for p in e.sent[0].chain))
            obj.renderer.render_dynamic.assert_awaited_once()
            obj.dispatcher.send.assert_not_awaited()

    async def test_status_with_no_subscription_handles_none(self):
        with tempfile.TemporaryDirectory() as d:
            p=plugin(d);obj,record=self.source()
            obj.data_manager.get_subscriptions_by_user=lambda umo:None
            obj.dynamic_sub=AsyncMock()
            p.context.get_registered_star=lambda name:SimpleNamespace(activated=True,star_cls=obj)
            e=Event();e.set_extra('mahjong_ai_request',True)
            await p.bilibili_subscription_ai(e,'status')
            self.assertIn('0 个UP主',e.sent[0].get_plain_text())

    async def test_framework_command_parser_keeps_uid_and_filters(self):
        from astrbot.core.star.star_handler import star_handlers_registry
        from astrbot.core.star.filter.command import CommandFilter
        handler=next(h for h in star_handlers_registry._handlers if h.handler.__name__=='bilibili_command')
        command=next(f for f in handler.event_filters if isinstance(f,CommandFilter))
        command.init_handler_md(handler)
        self.assertIn('b站',command.alias)
        with tempfile.TemporaryDirectory() as d:
            p=plugin(d);p._run_bilibili=AsyncMock()
            for text,expected in [('B站 测试 353240497',('latest','353240497','')),
                                  ('b站 订阅 353240497 lottery forward',('add','353240497','lottery forward')),
                                  ('B站',('menu','',''))]:
                e=Event(text=text,mention=None)
                e.is_at_or_wake_command=True;e.get_message_str=lambda:text
                self.assertTrue(command.filter(e,{}))
                await p.bilibili_command(e,**e.get_extra('parsed_params'))
                p._run_bilibili.assert_awaited_with(e,*expected)
                self.assertTrue(e.stopped)


if __name__=='__main__':
    unittest.main()
