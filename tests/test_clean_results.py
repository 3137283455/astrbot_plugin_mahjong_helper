import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image,ImageDraw
from astrbot_plugin_mahjong_helper.player_card import render_player_card
from astrbot_plugin_mahjong_helper.replay_card import render_replay_card,render_batch_card
from astrbot_plugin_mahjong_helper.replay_core import summarize
from test_replay import fixture


class CleanResultTests(unittest.TestCase):
    def test_cards_keep_statistics_without_instruction_footers(self):
        drawn=[];original=ImageDraw.ImageDraw.text
        def record(draw,xy,text,*args,**kwargs):
            drawn.append(str(text));return original(draw,xy,text,*args,**kwargs)
        with tempfile.TemporaryDirectory() as folder,patch.object(ImageDraw.ImageDraw,'text',record):
            root=Path(folder)
            render_player_card(root/'player.png','10001','基本',{4:{'stats':{'count':10,'nickname':'玩家'},'extended':{}}})
            report=summarize(fixture(),1)
            render_replay_card(report,root/'single.png','123456abcdef')
            report['differences']=[]
            render_replay_card(report,root/'equal.png','123456abcdef')
            render_batch_card({'id':'B1','model':'4.1b','done':1,'pending':0,'failed':0,
                'accordance':.8,'matches':8,'total':10,'games':[{'position':1,'seat':1,'status':'done','matches':8,'total':10,'accordance':.8}]},root/'batch.png')
            for path in root.glob('*.png'):
                with Image.open(path) as image:image.verify()
        for text in drawn:
            self.assertFalse(any(s in text for s in ('不等于','仅供参考','详单：','筛选参数可换序','来源：牌谱屋公开数据','汇总按决策数','未推断为同一玩家')),text)
        self.assertIn('AI 一致率',drawn);self.assertTrue(any('Rating' in text for text in drawn))


if __name__=='__main__':unittest.main()
