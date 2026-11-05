import tempfile,unittest
from pathlib import Path
from PIL import Image
from astrbot_plugin_mahjong_helper.player_card import BG,metric_rows,render_player_card
from astrbot_plugin_mahjong_helper.stat_card import CARD_SECTIONS

class PlayerCardTests(unittest.TestCase):
    def test_same_metrics_align_across_three_and_four(self):
        stats={'count':10,'rank_rates':[4,3,3],'avg_rank':1.9}
        a=metric_rows(3,'基本',stats,{})
        b=metric_rows(4,'基本',{**stats,'rank_rates':[4,3,2,1]}, {})
        self.assertEqual([r[0] for r in a],[r[0] for r in b])
        self.assertEqual(a[5][1],'不适用')

    def test_every_section_handles_missing_mode_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            data={3:{'stats':None},4:{'stats':{'nickname':'长昵称'*10,'count':10},'extended':{}}}
            for section in CARD_SECTIONS:
                path=render_player_card(Path(directory)/(section+'.png'),'12105509',section,data,'玉','近30天')
                with Image.open(path) as im:
                    self.assertEqual(im.size[0],1440)
                    self.assertGreaterEqual(im.size[1],1000)
                    self.assertEqual(im.getpixel((0,im.height-1)),(16,23,32))
