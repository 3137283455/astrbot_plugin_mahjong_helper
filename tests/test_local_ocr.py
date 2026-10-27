import unittest
from astrbot_plugin_group_data.local_ocr import interpret

def word(text,x,y,width,height,score=.999):
 return {'text':text,'box':[[x,y],[x+width,y],[x+width,y+height],[x,y+height]],'score':score,'crop_text':text,'crop_score':score}
class LocalOCRTests(unittest.TestCase):
 def fixture(self):
  rows=[word('终局',500,20,60,30)]
  for i,(name,point) in enumerate([('甲','65000'),('乙','42000'),('丙','-2000')]):
   y=100+i*135;rows.extend([word(name,600,y,50,20),word(point,610,y+32,120,40)])
  return {'width':1200,'height':650,'full':rows,'focused':[dict(r) for r in rows]}
 def test_local_complete_two_readings_accepted_no_cloud_needed(self):
  r=interpret(self.fixture());self.assertEqual(r['decision'],'accepted');self.assertEqual(r['result']['mode'],3)
 def test_non_result_ignored_before_model(self):
  self.assertEqual(interpret({'width':1200,'height':650,'full':[word('哈哈',10,10,80,30)],'focused':[]})['decision'],'ignored')
 def test_missing_name_not_autofilled(self):
  r=self.fixture();r['full']=r['full'][:-2]+r['full'][-1:]
  self.assertEqual(interpret(r)['decision'],'llm_pending')
 def test_small_name_uncertain_queued_not_counted(self):
  r=self.fixture();r['focused'][1]['score']=.85
  self.assertEqual(interpret(r)['decision'],'llm_pending')
 def test_no_final_marker_never_local_acceptance(self):
  r=self.fixture();r['full']=r['full'][1:];r['focused']=r['focused'][1:]
  self.assertNotEqual(interpret(r)['decision'],'accepted')
 def test_missing_edge_character_is_never_admitted(self):
  r=self.fixture();r['full'][1]['crop_text']='甲_'
  self.assertEqual(interpret(r)['decision'],'llm_pending')
if __name__=='__main__':unittest.main()
