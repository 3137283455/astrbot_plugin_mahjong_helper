import copy,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_group_data.store import Store
from astrbot_plugin_group_data.console_review import ManualReview

class ReviewTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();folder=Path(self.tmp.name);(folder/'images').mkdir()
  self.s=Store(folder/'fixture.db');self.s.enable('1:2','1','2')
  self.s.db.execute("INSERT INTO periods VALUES('current','1:2','S1-test','current',1,0,NULL,'')");self.s.db.commit()
  self.s.reconcile('1:2',[{'msg':'11','idx':0,'stamp':100},{'msg':'12','idx':0,'stamp':101}])
  self.id=self.s.source('1:2','11',0)['id'];self.missing=self.s.source('1:2','12',0)['id']
  path=folder/'images/image.png';path.write_bytes(b'\x89PNG\r\n\x1a\nfixture');self.s.attach(self.id,'hash',path)
  self.result={'is_result':True,'mode':4,'players':[{'name':n,'place':i+1,'points':v} for i,(n,v) in enumerate(zip(['TestA','TestB','TestC','TestD'],[40000,30000,20000,10000]))]}
  self.s.save('hash','review',raw=[self.result],reason='fixture');self.notifications=[];self.s.subscribe(self.notifications.append)
  self.plugin=SimpleNamespace(folder=folder,store=self.s,recognizer=SimpleNamespace(running=set(),verified_names=SimpleNamespace(cache={'1:2':{}})))
  self.actions=SimpleNamespace(plugin=self.plugin,closed=False);self.r=ManualReview(self.actions)
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def payload(self,**change):return {'action':'confirm','version':self.r.detail('1:2',self.id)['version'],'checked':True,'result':self.result,**change}
 def test_list_and_image_do_not_leak_paths(self):
  self.assertEqual(len(self.r.listing('1:2')['items']),2);d=self.r.detail('1:2',self.id)
  self.assertTrue(d['has_image']);self.assertEqual(len(d['candidates']),1);self.assertNotIn('path',d);self.assertNotIn('raw',d);self.assertIn('image',self.r.image('1:2',self.id))
 def test_confirm_updates_stats_glyph_boundary_and_notifies(self):
  self.r.apply('1:2',self.id,self.payload(),'admin');self.assertEqual(len(self.s.matches('1:2')),1)
  self.assertEqual(self.notifications,['1:2']);self.assertNotIn('1:2',self.plugin.recognizer.verified_names.cache)
  self.assertEqual(self.s.db.execute('SELECT actor FROM audit').fetchone()[0],'admin')
  self.assertEqual(self.s.get_source('1:2',self.id)['status'],'review');self.assertEqual(len(self.r.listing('1:2')['items']),1)
 def test_stale_or_inflight_cannot_overwrite(self):
  body=self.payload();self.s.save('hash','review',raw=[self.result],reason='changed')
  with self.assertRaises(ValueError):self.r.apply('1:2',self.id,body,'admin')
  body=self.payload();self.plugin.recognizer.running.add('hash')
  with self.assertRaises(ValueError):self.r.apply('1:2',self.id,body,'admin')
  self.assertEqual(self.s.matches('1:2'),[])
 def test_invalid_names_points_order_rejected(self):
  for field,value in [('name','TestB'),('points',123),('points',-30000)]:
   result=copy.deepcopy(self.result);result['players'][0][field]=value
   with self.assertRaises(ValueError):self.r.apply('1:2',self.id,self.payload(result=result),'admin')
  self.assertEqual(self.s.matches('1:2'),[])
 def test_custom_total_requires_second_confirmation(self):
  result=copy.deepcopy(self.result);result['players'][0]['points']=45000
  with self.assertRaises(ValueError):self.r.apply('1:2',self.id,self.payload(result=result),'admin')
  self.r.apply('1:2',self.id,self.payload(result=result,nonstandard=True),'admin');self.assertEqual(len(self.s.matches('1:2')),1)
 def test_missing_original_cannot_confirm_but_can_ignore(self):
  body={**self.payload(),'version':self.r.detail('1:2',self.missing)['version']}
  with self.assertRaises(ValueError):self.r.apply('1:2',self.missing,body,'admin')
  self.r.apply('1:2',self.missing,{**body,'action':'ignore'},'admin');self.assertIsNone(self.s.get_source('1:2',self.missing))
 def test_ignore_accepted_updates_stats(self):
  self.r.apply('1:2',self.id,self.payload(),'admin');self.r.apply('1:2',self.id,self.payload(action='ignore'),'admin')
  self.assertEqual(self.s.matches('1:2'),[]);self.assertEqual(len(self.notifications),2)
 def test_cross_group_and_constructed_frozen_season_blocked(self):
  self.s.enable('1:3','1','3')
  with self.assertRaises(ValueError):self.r.detail('1:3',self.id)
  self.s.db.execute("UPDATE periods SET state='archived',snapshot='{}' WHERE id='current'")
  self.s.db.execute("INSERT INTO periods VALUES('next','1:2','S2-test','current',200,0,NULL,'')");self.s.db.commit()
  with self.assertRaises(ValueError):self.r.detail('1:2',self.id)
  self.assertEqual(self.s.db.execute("SELECT snapshot FROM periods WHERE id='current'").fetchone()[0],'{}')
 def test_private_path_not_served_as_image(self):
  outside=Path(self.tmp.name)/'private.png';outside.write_bytes(b'\x89PNG\r\n\x1a\nprivate')
  self.s.db.execute('UPDATE images SET path=?',(str(outside),));self.s.db.commit()
  with self.assertRaises(ValueError):self.r.image('1:2',self.id)
 def test_missing_current_period_cannot_fall_back_to_all_history(self):
  self.s.db.execute("UPDATE periods SET state='archived',snapshot='{}' WHERE id='current'");self.s.db.commit()
  for call in (lambda:self.r.listing('1:2'),lambda:self.r.detail('1:2',self.id)):
   with self.assertRaises(ValueError):call()
 async def test_auth_and_frozen_period_rejected(self):
  with patch('astrbot.api.web.request',SimpleNamespace(username=None)):
   response=await self.r.api();self.assertEqual(response.status_code,403)
  request=SimpleNamespace(username='admin',json=AsyncMock(return_value={'action':'list','scope':'1:2','period':'S0'}))
  with patch('astrbot.api.web.request',request):
   response=await self.r.api();self.assertNotEqual(response.status_code,200)
  self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0],0)
