import tempfile,unittest
from pathlib import Path
from astrbot_plugin_group_data.core import aggregate,profile_positions,validate
from astrbot_plugin_group_data import cards
from test_core import result

class ProfilePositionsTests(unittest.TestCase):
 def stats(self):
  games=[(['甲','乙','丙','丁'],[60000,30000,12000,-2000]),(['乙','甲','戊','丁'],[50000,40000,10000,0]),(['甲','丙','丁','戊'],[45000,35000,20000,0])]
  return aggregate([{'id':i,'result':validate(result(4,names=names,points=points))} for i,(names,points) in enumerate(games,1)],4)
 def test_count_rate_ties_and_both_sort_directions(self):
  stats=self.stats();a=profile_positions(stats,'甲',4);b=profile_positions(stats,'乙',4);d=profile_positions(stats,'丁',4)
  self.assertEqual(a['场次'],{'position':1,'total':5,'tied':True,'label':'并列第1/5'})
  self.assertEqual(a['平均顺位']['position'],1);self.assertFalse(a['平均顺位']['tied'])
  self.assertEqual(a['被飞']['position'],2);self.assertTrue(a['被飞']['tied'])
  self.assertEqual(d['被飞']['position'],1);self.assertEqual(d['最低']['position'],1)
  self.assertEqual(b['最高']['position'],2);self.assertEqual(b['最低']['position'],4)
  self.assertEqual(a['一位率']['position'],1);self.assertEqual(b['一位率']['position'],2)
 def test_minimum_and_mode_define_rank_pool(self):
  stats=self.stats();a=profile_positions(stats,'甲',4,3)
  self.assertTrue(all(r['total']==2 for r in a.values()));self.assertEqual(profile_positions(stats,'乙',4,3),{})
  three=aggregate([{'id':1,'result':validate(result())}],3);rank=profile_positions(three,'甲',3)
  self.assertTrue(all(r['total']==3 for r in rank.values()));self.assertNotIn('四位',rank)
 def test_text_and_card_keep_original_ids_and_rank_every_displayed_metric(self):
  stats=self.stats();text=cards.profile_text('group',4,stats,'甲',period_name='S1赛季')
  for metric in ['出场','平均顺位','被飞','平均持点','最高持点','最低持点','1位','2位','3位','4位']:self.assertIn(metric,text)
  self.assertGreaterEqual(text.count('第'),15);self.assertIn('原图 #1',text);self.assertIn('原图 #2',text)
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'card.png';cards.profile(path,'group',4,stats,'甲',period_name='S1赛季')
   from PIL import Image
   with Image.open(path) as image:self.assertEqual(image.size,(1440,1240));image.verify()
if __name__=='__main__':unittest.main()
