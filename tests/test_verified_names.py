import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from astrbot_plugin_group_data.verified_names import VerifiedNames,apply_verified,fingerprint,similarity,name_regions

class FakeStore:
    def __init__(self,rows):self.records=rows
    def rows(self,scope):return [r for r in self.records if r['scope']==scope]

class VerifiedNameTests(unittest.TestCase):
    def screen(self,folder,id,names=('ALPHA','BETA','GAMMA'),scope='b:g',manual=True,offset=0):
        path=Path(folder)/f'{id}.png';im=Image.new('RGB',(1500,1000),'#14233b');d=ImageDraw.Draw(im);f=ImageFont.load_default(size=27);rows=[];players=[]
        for i,name in enumerate(names):
            y=120+i*260;pts=(60000,35000,10000)[i]+(offset if i==0 else -offset if i==1 else 0)
            d.text((720,y),name,font=f,fill='white');b=d.textbbox((720,y),name,font=f);rows.append({'text':'OCR-WRONG','box':[[b[0],b[1]],[b[2],b[1]],[b[2],b[3]],[b[0],b[3]]]})
            d.text((720,y+65),str(pts),font=f,fill='white');rows.append({'text':str(pts),'box':[[720,y+65],[850,y+65],[850,y+110],[720,y+110]]});players.append({'name':name,'place':i+1,'points':pts})
        im.save(path);ocr={'width':1500,'height':1000,'full':rows};payload={'mode':3,'players':players}
        return {'id':id,'hash':str(id),'path':str(path),'scope':scope,'override_status':'accepted' if manual else None,'override_result':json.dumps(payload) if manual else None,'raw':json.dumps([{'local_ocr':ocr}]),'result':json.dumps(payload)},ocr
    def test_two_manual_originals_match_but_machine_results_are_not_training(self):
        with tempfile.TemporaryDirectory() as d:
            a,_=self.screen(d,1,offset=100);b,_=self.screen(d,2,offset=200);target,ocr=self.screen(d,3,manual=False)
            index=VerifiedNames(FakeStore([a,b,target]));matches=index.match(target,ocr)
            self.assertEqual([m['name'] for m in matches],['ALPHA','BETA','GAMMA'])
            a['override_status']=None
            self.assertEqual(index.match(target,ocr),[])
    def test_group_isolation_and_no_self_certification(self):
        with tempfile.TemporaryDirectory() as d:
            a,ocr=self.screen(d,1);b,_=self.screen(d,2,scope='b:other')
            self.assertEqual(VerifiedNames(FakeStore([a,b])).match(a,ocr),[])
    def test_partial_nickname_and_competing_name_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            a,_=self.screen(d,1);b,_=self.screen(d,2,offset=100);target,ocr=self.screen(d,3,names=('ALPH','BETA','GAMMA'),manual=False)
            matches=VerifiedNames(FakeStore([a,b])).match(target,ocr)
            self.assertNotIn(1,[m['place'] for m in matches])
            rival,_=self.screen(d,4,names=('WRONG-LABEL','BETA','GAMMA'));rival['path']=a['path']
            self.assertNotIn(1,[m['place'] for m in VerifiedNames(FakeStore([a,b,rival])).match(target,ocr)])
    def test_correction_requires_exact_mode_place_and_points_and_preserves_raw(self):
        obj={'is_result':True,'mode':3,'players':[{'name':'wrong','place':1,'points':60000}]};e=[{'mode':3,'place':1,'points':60000,'name':'慪龢'}]
        corrected=apply_verified(obj,e);self.assertEqual(corrected['players'][0]['name'],'慪龢');self.assertEqual(obj['players'][0]['name'],'wrong')
        self.assertEqual(apply_verified(obj,[{**e[0],'points':59000}]),obj)
        self.assertEqual(apply_verified(obj,[{**e[0],'mode':4}]),obj)
    def test_no_name_matching_on_grid_or_unordered_points(self):
        with tempfile.TemporaryDirectory() as d:
            a,ocr=self.screen(d,1);ocr['full'][1]['text']='-1000'
            self.assertEqual(name_regions(a['path'],ocr),[])
    def test_small_or_blank_glyphs_are_not_certified(self):
        self.assertIsNone(fingerprint(Image.new('RGB',(100,50),'#14233b')))
        self.assertEqual(similarity(None,None),-1)

if __name__=='__main__':unittest.main()
