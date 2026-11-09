import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from astrbot_plugin_group_data.verified_names import VerifiedNames,apply_verified,fingerprint,similarity,name_regions,guard_historical_labels
from astrbot_plugin_group_data.store import Store

class FakeStore:
    def __init__(self,rows):self.records=rows
    def rows(self,scope):return [r for r in self.records if r['scope']==scope]

class VerifiedNameTests(unittest.TestCase):
    def test_missing_full_ocr_nickname_can_use_focused_region(self):
        import copy
        with tempfile.TemporaryDirectory() as d:
            row,ocr=self.screen(d,1,manual=False)
            ocr['focused']=copy.deepcopy(ocr['full'])
            ocr['full']=[r for i,r in enumerate(ocr['full']) if i!=4]
            regions=name_regions(row['path'],ocr)
            self.assertEqual([r['place'] for r in regions],[1,2,3])
            self.assertEqual(regions[-1]['points'],10000)
            s=Store(Path(d)/'test.db')
            try:
                row['raw']=json.dumps([{'local_ocr':ocr}])
                bank=VerifiedNames(s)
                bank.enroll('b:g',row,3,'I咲仡')
                result=bank.match(row,ocr)
                self.assertEqual([(m['place'],m['name']) for m in result],[(3,'I咲仡')])
                ocr.pop('focused')
                self.assertEqual([r['place'] for r in name_regions(row['path'],ocr)],[1,2])
            finally:s.close()

    def test_dictionary_correction_can_supersede_frozen_label_without_editing_archive(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'state.db')
            try:
                row,ocr=self.screen(d,1,manual=False)
                payload=json.loads(row['result']);payload['players'][0]['name']='天爻沓药'
                row.update(result=json.dumps(payload),status='accepted',_glyph_archived=True)
                original=row['result'];store.training_rows=lambda scope:[row]
                bank=VerifiedNames(store);bank.remove('b:g','天爻沓药')
                bank.enroll('b:g',row,1,'天爻杳药')
                restored=VerifiedNames(store)
                match=next(x for x in restored.match(row,ocr) if x['place']==1)
                self.assertEqual(match['name'],'天爻杳药')
                self.assertNotIn('天爻沓药',restored.summary('b:g'))
                self.assertEqual(row['result'],original)
            finally:store.close()

    def test_confirmed_archive_names_are_read_only_training_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            a,_=self.screen(d,1,manual=False);b,_=self.screen(d,2,manual=False,offset=100)
            target,ocr=self.screen(d,3,manual=False,offset=200)
            for r in (a,b):r['_glyph_archived']=True;r['status']='accepted'
            original=json.dumps([a,b],sort_keys=True)
            index=VerifiedNames(FakeStore([a,b,target]))
            self.assertEqual([r['name'] for r in index.match(target,ocr)],['ALPHA','BETA','GAMMA'])
            self.assertEqual(json.dumps([a,b],sort_keys=True),original)

    def test_old_wrong_label_cannot_rewrite_agreeing_model_names(self):
        reading={'is_result':True,'mode':3,'confidence':.99,'players':[{'place':1,'points':60000,'name':'天爻杳药'}]}
        old={'mode':3,'place':1,'points':60000,'name':'天爻杏药','method':'historical-pair'}
        self.assertEqual(guard_historical_labels([reading,reading],[old]),[])

    def test_user_registered_correction_has_explicit_provenance(self):
        reading={'is_result':True,'mode':3,'confidence':.99,'players':[{'place':1,'points':60000,'name':'天爻杏药'}]}
        registered={'mode':3,'place':1,'points':60000,'name':'天爻杳药','method':'registered-region'}
        self.assertEqual(guard_historical_labels([reading,reading],[registered]),[registered])

    def test_enrolled_sample_persists_across_restart_and_is_group_local(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)/'test.db')
            try:
                a,_=self.screen(d,1,manual=False);target,ocr=self.screen(d,2,manual=False,offset=100)
                bank=VerifiedNames(s);bank.enroll('b:g',a,1,'慪龢')
                other=VerifiedNames(s)
                self.assertEqual(other.match(target,ocr)[0]['name'],'慪龢')
                self.assertEqual(other.match(a,ocr)[0]['method'],'registered-region')
                self.assertEqual(other.match({**target,'scope':'b:other'},ocr),[])
                self.assertEqual(other.summary('b:g'),{'慪龢':1})
                other.remove('b:g','慪龢')
                self.assertEqual(VerifiedNames(s).match(target,ocr),[])
            finally:s.close()

    def test_enrollment_checks_source_scope_and_full_nickname_region(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)/'test.db')
            try:
                row,_=self.screen(d,1,manual=False);bank=VerifiedNames(s)
                with self.assertRaises(ValueError):bank.enroll('b:other',row,1,'慪龢')
                with self.assertRaises(ValueError):bank.enroll('b:g',row,4,'慪龢')
                with self.assertRaises(ValueError):bank.enroll('b:g',row,1,'?')
                self.assertEqual(bank.summary('b:g'),{})
            finally:s.close()

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
