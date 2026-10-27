import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from astrbot_plugin_group_data.store import Store
from astrbot_plugin_group_data.source import EssenceSource

class SourceTests(unittest.IsolatedAsyncioTestCase):
 async def test_failed_listing_does_not_clear_existing_sources(self):
  with tempfile.TemporaryDirectory() as d:
   store=Store(Path(d)/'state.db');self.addCleanup(store.close);store.enable('b:g','b','g');store.reconcile('b:g',[{'msg':'1','idx':0,'stamp':1}])
   src=EssenceSource(store,Path(d)/'images');bot=type('Bot',(),{'call_action':AsyncMock(side_effect=RuntimeError('offline'))})()
   self.assertFalse(await src.sync('b:g',bot,123));self.assertEqual(len(store.rows('b:g')),1)
 async def test_expired_image_uses_same_original_message(self):
  with tempfile.TemporaryDirectory() as d:
   store=Store(Path(d)/'state.db');self.addCleanup(store.close);store.enable('b:g','b','g');src=EssenceSource(store,Path(d)/'images')
   calls=[]
   async def api(action,**kw):
    calls.append(action)
    if action=='get_essence_msg_list':return [{'message_id':123,'content':[{'type':'image','data':{'file':'hash.jpg','url':'https://qpic.cn/expired'}}]}]
    if action=='get_image':return {'url':'https://qpic.cn/still-expired'}
    if action=='get_msg':return {'message':[{'type':'image','data':{'file':'hash.jpg','url':'https://qpic.cn/refreshed'}}]}
   bot=type('Bot',(),{})();bot.call_action=api
   async def fetch(url,folder):
    if 'refreshed' not in url:raise RuntimeError('expired')
    return 'hash',Path(d)/'real.png'
   with patch('astrbot_plugin_group_data.source.download',side_effect=fetch):self.assertTrue(await src.sync('b:g',bot,123))
   self.assertEqual(store.rows('b:g')[0]['hash'],'hash');self.assertEqual(calls,['get_essence_msg_list','get_image','get_msg'])
if __name__=='__main__':unittest.main()
