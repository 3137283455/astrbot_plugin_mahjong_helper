import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageDraw
from astrbot_plugin_group_data import cards
from astrbot_plugin_group_data.scoring import score_matches, display
from test_scoring import match


class OverviewMetricTests(unittest.TestCase):
    def render(self, folder, matches, mode=None):
        drawn=[]
        original=ImageDraw.ImageDraw.text
        def text(draw, xy, value, *args, **kwargs):
            drawn.append((xy,str(value)))
            return original(draw,xy,value,*args,**kwargs)
        path=Path(folder)/('overview-'+str(mode)+'.png')
        with patch.object(ImageDraw.ImageDraw,'text',text):
            cards.overview(path,'123456789',matches,{}, {},selected_mode=mode,period_name='S1赛季')
        return path,drawn

    def test_modes_show_score_win_count_and_their_own_last_place(self):
        matches=[match(i,4,names=['四甲','四乙','四丙','四丁'],points=[40000+i*100,30000,20000,10000-i*100]) for i in range(12)]
        matches += [match(i+30,3,names=['三甲','三乙','三丙'],points=[50000+i*100,35000,20000-i*100]) for i in range(12)]
        with tempfile.TemporaryDirectory() as folder:
            path,drawn=self.render(folder,matches)
            text=[value for _,value in drawn]
            self.assertNotIn('一位率最高',text);self.assertNotIn('二位率最高',text)
            self.assertFalse(any('一位率' in value or '二位率' in value for value in text))
            self.assertEqual(text.count('综合分最高'),2)
            self.assertEqual(text.count('一位最多'),2)
            self.assertIn('三位最多',text);self.assertIn('四位最多',text)
            for mode in (3,4):
                winner=next(row for row in score_matches(matches,mode)['rows'] if row['qualified'])
                self.assertIn(display(winner['score'])+'分',text)
            with Image.open(path) as image:
                self.assertEqual(image.size,(1440,1704))
                self.assertTrue(all(0<=xy[0]<image.width and 0<=xy[1]<image.height for xy,_ in drawn))
            for mode,label,other in ((3,'三位最多','四位最多'),(4,'四位最多','三位最多')):
                _,one=self.render(folder,matches,mode)
                values=[value for _,value in one]
                self.assertIn(label,values);self.assertNotIn(other,values)

    def test_score_does_not_promote_an_under_ten_game_player(self):
        matches=[match(i,4,points=[40000+i*100,30000,20000,10000-i*100]) for i in range(9)]
        with tempfile.TemporaryDirectory() as folder:
            _,drawn=self.render(folder,matches,4)
            self.assertIn('暂无满10场玩家',[value for _,value in drawn])

    def test_empty_mode_keeps_a_valid_card(self):
        with tempfile.TemporaryDirectory() as folder:
            path,drawn=self.render(folder,[])
            self.assertEqual(sum(value=='本赛季暂无该模式已确认对局' for _,value in drawn),2)
            self.assertTrue(path.is_file())


if __name__=='__main__':unittest.main()
