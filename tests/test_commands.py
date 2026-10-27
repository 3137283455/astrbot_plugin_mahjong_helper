import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from astrbot_plugin_group_data.main import GroupDataPlugin,parse_query
from astrbot_plugin_group_data.store import Store
from astrbot_plugin_group_data import cards
from test_core import result
from astrbot_plugin_group_data.core import validate,aggregate

class Event:
    def __init__(self,gid='987654321',admin=False):self.gid=gid;self.sent=[];self.owner=admin;self.calls=[];self.bot=self
    def get_platform_name(self):return 'aiocqhttp'
    def get_group_id(self):return self.gid
    def get_self_id(self):return '12345'
    def get_sender_id(self):return '56789'
    def is_admin(self):return False
    def chain_result(self,chain):return chain
    def plain_result(self,text):return text
    async def send(self,result):self.sent.append(result)
    async def call_action(self,**kw):self.calls.append(kw);return {'role':'admin' if self.owner else 'member'}

class CommandTests(unittest.IsolatedAsyncioTestCase):
    def setup_plugin(self,d):
        p=GroupDataPlugin.__new__(GroupDataPlugin);p.config={};p.card_dir=Path(d);p.store=Store(Path(d)/'test.db');self.addCleanup(p.store.close)
        p.store.enable('12345:987654321','12345','987654321')
        from unittest.mock import AsyncMock
        p.recognizer=SimpleNamespace(provider=AsyncMock(return_value=None),model_error="",running=set())
        return p
    def test_flexible_mode_time_metric_and_small_record_ids(self):
        a=parse_query('三麻 被飞率 30天 至少5场');b=parse_query('30天 被飞率 5场 三麻')
        self.assertEqual(a,b)
        self.assertEqual(parse_query('原图 3')['rest'],['3'])
        self.assertEqual(parse_query('个人 "昵称 有空格" 四麻')['rest'],['昵称 有空格'])
    async def test_member_can_query_but_cannot_modify(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.setup_plugin(d);e=Event()
            await p.handle(e,'关闭');self.assertTrue(p.store.group('12345:987654321')['enabled'])
            self.assertIn('管理员',e.sent[-1]);await p.handle(e,'四麻 被飞率');self.assertIsInstance(e.sent[-1],list)
    async def test_ai_cannot_modify_even_for_admin(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.setup_plugin(d);e=Event(admin=True)
            await p.handle(e,'关闭',allow_write=False);self.assertIn('明确发送',e.sent[-1])
            self.assertTrue(p.store.group('12345:987654321')['enabled'])
    async def test_admin_can_pause_and_crossgroup_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.setup_plugin(d);e=Event(admin=True)
            await p.handle(e,'关闭');self.assertFalse(p.store.group('12345:987654321')['enabled'])
            other=Event('999');await p.handle(other,'原图 1');self.assertIsInstance(other.sent[-1],list)
            self.assertIsNone(p.store.group('12345:999'))
    async def test_private_chat_does_not_read_group(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.setup_plugin(d);e=Event('');await p.handle(e,'')
            self.assertIn('QQ 群',e.sent[0]);self.assertEqual(e.calls,[])
    def test_render_real_chinese_cards(self):
        with tempfile.TemporaryDirectory() as d:
            m=[{'id':150,'result':validate(result(names=['一剑风起醉英豪','かな 空格ABC','丙']))}]
            cards.overview(Path(d)/'overview.png','987654321',m,{}, {'review':2})
            s=aggregate(m,3)
            cards.leaderboard(Path(d)/'rank.png','987654321',3,'被飞率',s)
            cards.profile(Path(d)/'person.png','987654321',3,s,'一剑风起醉英豪')
            from PIL import Image
            for file in Path(d).glob('*.png'):
                with Image.open(file) as im:self.assertEqual(im.width,1440);im.verify()

if __name__=='__main__':unittest.main()
