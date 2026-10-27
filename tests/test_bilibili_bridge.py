import asyncio
import tempfile
import unittest
from types import SimpleNamespace

from astrbot.core.provider.entities import ProviderRequest
from astrbot_plugin_mahjong_helper.bilibili_bridge import BILIBILI_HELP, normalize_uid
from test_ai_entry import Event, plugin, tools


class BilibiliBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setup_bili(self, p, *, login=True, direct=False):
        calls = []
        async def add(event, uid, filters):
            calls.append(('add', event.unified_msg_origin, uid, filters))
            if direct:
                await event.send(event.plain_result('原插件订阅卡片'))
                return
            return event.plain_result('原插件订阅成功')
        async def delete(event, uid):
            calls.append(('remove', event.unified_msg_origin, uid))
            return event.plain_result('原插件删除成功')
        async def listing(event):
            calls.append(('list', event.unified_msg_origin))
            return event.plain_result('当前聊天原版订阅列表')
        async def latest(event, uid):
            calls.append(('latest', event.unified_msg_origin, uid))
            event.stop_event()
        obj = SimpleNamespace(dynamic_sub=add, sub_del=delete, sub_list=listing, sub_test=latest,
            bili_client=SimpleNamespace(credential=SimpleNamespace(sessdata='secret') if login else None),
            data_manager=SimpleNamespace(get_subscriptions_by_user=lambda umo: [umo]),
            dynamic_listener_task=SimpleNamespace(done=lambda: False))
        p.context.get_registered_star = lambda name: SimpleNamespace(activated=True, star_cls=obj) if name == 'astrbot_plugin_bilibili' else None
        return obj, calls

    def event(self, **kw):
        e = Event(text='帮我订阅B站UID 123456，不看抽奖', **kw)
        e.set_extra('mahjong_ai_request', True)
        return e

    async def test_native_and_ai_reuse_same_service_and_do_not_repeat(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d); obj, calls = self.setup_bili(p)
            e = self.event()
            await p.bilibili_subscription_ai(e, 'add', 'https://space.bilibili.com/123456?spm_id_from=test', 'lottery forward')
            await p.bilibili_subscription_ai(e, 'add', '123456')
            self.assertEqual(calls, [('add', e.unified_msg_origin, '123456', 'lottery forward')])
            self.assertEqual(len(e.sent), 1)
            native = Event(mention=None)
            await p.bilibili_command(native, '订阅 123456 lottery forward')
            self.assertEqual(calls[-1], calls[0])
            self.assertTrue(native.stopped)

    async def test_direct_card_is_not_sent_twice(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d); self.setup_bili(p, direct=True)
            e = self.event(); await p.bilibili_subscription_ai(e, 'add', '123456')
            self.assertEqual(len(e.sent), 1)
            self.assertEqual(e.sent[0].get_plain_text(), '原插件订阅卡片')

    async def test_missing_login_reports_login_not_missing_api_and_does_not_subscribe(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d); obj, calls = self.setup_bili(p, login=False)
            e = self.event(); await p.bilibili_subscription_ai(e, 'add', '123456')
            self.assertEqual(calls, [])
            self.assertIn('/bili_login', e.sent[0].get_plain_text())
            self.assertNotIn('没有匹配', e.sent[0].get_plain_text())

    async def test_invalid_uid_and_injected_url_do_not_mutate_subscriptions(self):
        for uid in ['某个UP主', '0', '-1', '123456 群号', 'https://example.com/123456', '9223372036854775808']:
            with tempfile.TemporaryDirectory() as d:
                p = plugin(d); obj, calls = self.setup_bili(p)
                e = self.event(); await p.bilibili_subscription_ai(e, 'add', uid)
                self.assertEqual(calls, [], uid)
                self.assertIn('UID', e.sent[0].get_plain_text())
        self.assertEqual(normalize_uid('3540000000000001'), '3540000000000001')

    async def test_list_remove_and_status_use_current_chat_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d); obj, calls = self.setup_bili(p, login=False)
            for i, action in enumerate(['list','remove','status']):
                e = self.event(group=None, mention=None, message_id=str(i))
                await p.bilibili_subscription_ai(e, action, '123456')
                self.assertEqual(len(e.sent), 1)
            self.assertEqual(calls, [('list','napcat:FriendMessage:user'),('remove','napcat:FriendMessage:user','123456')])
            self.assertNotIn('secret', e.sent[0].get_plain_text())
            self.assertIn('尚未登录', e.sent[0].get_plain_text())

    async def test_group_without_self_mention_cannot_execute_ai_action(self):
        with tempfile.TemporaryDirectory() as d:
            p=plugin(d); obj, calls=self.setup_bili(p)
            for mention in [None,'other','all']:
                e=self.event(mention=mention)
                await p.bilibili_subscription_ai(e,'add','123456')
                self.assertEqual(e.sent,[])
            self.assertEqual(calls,[])

    async def test_llm_sees_real_bili_tool_and_specific_instructions(self):
        with tempfile.TemporaryDirectory() as d:
            p=plugin(d);self.setup_bili(p)
            e=self.event();await p.ai_before_model(e)
            req=ProviderRequest(prompt=e.message_str,func_tool=tools('bilibili_subscription','query_available_apis','dangerous_tool'))
            await p.ai_limit_request(e,req)
            self.assertIn('bilibili_subscription', req.func_tool.names())
            self.assertNotIn('dangerous_tool',req.func_tool.names())
            self.assertIn('这不是娱乐 API',req.system_prompt)
            self.assertIn('lottery不看抽奖',req.system_prompt)

    async def test_help_includes_full_native_commands_and_menu_entry(self):
        with tempfile.TemporaryDirectory() as d:
            p=plugin(d);self.setup_bili(p)
            e=self.event();await p.menu_ai(e,section='bilibili')
            self.assertIn('/bili_global_sub',e.sent[0].get_plain_text())
            self.assertIn('/bili_logout',BILIBILI_HELP)
            e=self.event(message_id='2');await p.bilibili_command(e,'')
            self.assertEqual(e.sent[0].get_plain_text(),BILIBILI_HELP)
            self.assertTrue(e.stopped)


if __name__ == '__main__':
    unittest.main()
