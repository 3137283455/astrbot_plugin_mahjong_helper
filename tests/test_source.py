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
 async def test_excluded_unavailable_source_survives_sync_restart_and_group_isolation(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'state.db';store=Store(path);store.enable('b:g','b','g');store.reconcile('b:g',[{'msg':'1','idx':0,'stamp':1}]);row=store.source('b:g','1',0);store.source_error(row['id'],'expired');store.exclude_source('b:g',row);store.close()
   store=Store(path);self.addCleanup(store.close);src=EssenceSource(store,Path(d)/'images')
   async def api(action,**kw):
    return [{'message_id':1,'content':[{'type':'image','data':{'url':'https://qpic.cn/a'}}]}]
   bot=type('Bot',(),{})();bot.call_action=api
   with patch('astrbot_plugin_group_data.source.download',AsyncMock()) as download:
    self.assertTrue(await src.sync('b:g',bot,123));download.assert_not_awaited()
   self.assertEqual(store.rows('b:g'),[]);self.assertEqual(store.summary('b:g').get('download_error',0),0)
   store.reconcile('b:other',[{'msg':'1','idx':0,'stamp':1}]);self.assertEqual(len(store.rows('b:other')),1)
   store.reconcile('b:g',[{'msg':'1','idx':0,'stamp':1},{'msg':'2','idx':0,'stamp':2}]);self.assertEqual([r['msg'] for r in store.rows('b:g')],['2'])
 async def test_new_essence_image_is_enqueued_while_existing_is_not_downloaded_again(self):
  with tempfile.TemporaryDirectory() as d:
   store=Store(Path(d)/'state.db');self.addCleanup(store.close);store.enable('b:g','b','g');store.reconcile('b:g',[{'msg':'1','idx':0,'stamp':1}]);existing=Path(d)/'existing.png';existing.write_bytes(b'cached');row=store.source('b:g','1',0);store.attach(row['id'],'old',existing)
   src=EssenceSource(store,Path(d)/'images')
   async def api(action,**kw):
    return [{'message_id':i,'content':[{'type':'image','data':{'url':f'https://qpic.cn/{i}'}}]} for i in (1,2)]
   bot=type('Bot',(),{})();bot.call_action=api
   fetch=AsyncMock(return_value=('new',Path(d)/'new.png'))
   with patch('astrbot_plugin_group_data.source.download',fetch):self.assertTrue(await src.sync('b:g',bot,123))
   self.assertEqual(fetch.await_count,1);self.assertEqual(store.source('b:g','2',0)['hash'],'new');self.assertEqual(store.image('new')['status'],'new')
if __name__=='__main__':unittest.main()
