import tempfile,unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch
from PIL import Image,ImageDraw
from astrbot_plugin_group_data import cards
from astrbot_plugin_group_data.core import aggregate
from test_scoring import match


class CleanCardTests(unittest.TestCase):
    def capture(self,render):
        output=[];original=ImageDraw.ImageDraw.text
        def text(draw,xy,value,*args,**kwargs):
            output.append(str(value));return original(draw,xy,value,*args,**kwargs)
        with patch.object(ImageDraw.ImageDraw,'text',text):render()
        forbidden=('成绩90%','满10场正式入榜','暂定分不占','仅反映','仅统计','仅代表','独立统计',
                   '当前已展示完整榜单','每页10人','综合分=','评分纳入','本模式另有','由低到高','同值并列跳位')
        for value in output:
            self.assertFalse(any(part in value for part in forbidden),value)
        return output

    def test_empty_score_board_keeps_only_counts_and_empty_state(self):
        data={'rows':[],'mode':3,'minimum':1,'games':0,'excluded':[]}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'empty.png'
            text=self.capture(lambda:cards.score_board(path,'1',data,period_name='S1赛季'))
            self.assertIn('正式 0 人 · 暂定 0 人',text)
            self.assertFalse(any('页' in value for value in text))
            with Image.open(path) as image:self.assertEqual(image.height,380)
            fallback=cards.score_board_text(data,'S1赛季')
            self.assertNotIn('90%',fallback);self.assertNotIn('满10',fallback)

    def test_multi_page_keeps_page_number_and_original_ranks(self):
        rows=[{'name':f'玩家{i:02}','qualified':True,'position':i+1,'games':12,
               'performance':Fraction(80-i),'attendance':Fraction(3),'score':Fraction(75-i)} for i in range(13)]
        data={'rows':rows,'mode':4,'minimum':1,'games':50,'excluded':[]}
        with tempfile.TemporaryDirectory() as folder:
            text=self.capture(lambda:cards.score_board(Path(folder)/'page.png','1',data,page=2,provisional=False))
            self.assertIn('第2/2页',text);self.assertIn('玩家10',text);self.assertIn('11',text)
            self.assertFalse(any('下一页' in value or '/群数据' in value for value in text))

    def test_other_cards_keep_metrics_and_source_numbers_without_boilerplate(self):
        matches=[match(i) for i in range(10)];stats=aggregate(matches,4)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            self.capture(lambda:cards.overview(root/'overview.png','1',matches,{},{}))
            self.capture(lambda:cards.leaderboard(root/'board.png','1',4,'一位',stats))
            text=self.capture(lambda:cards.profile(root/'person.png','1',4,stats,'甲'))
            self.assertIn('最高持点',text);self.assertTrue(any('最高 #' in value for value in text))
            self.capture(lambda:cards.text_card(root/'text.png','群数据',['查询结果']))


if __name__=='__main__':unittest.main()
