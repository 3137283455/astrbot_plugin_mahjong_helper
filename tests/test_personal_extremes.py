import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from PIL import Image
import test_commands
from test_commands import Event
from astrbot_plugin_group_data import cards,periods
from astrbot_plugin_group_data.core import validate


class PersonalExtremesTests(unittest.IsolatedAsyncioTestCase):
    def seed(self,folder,one=False,archive=True):
        p=test_commands.CommandTests.setup_plugin(self,folder);scope='12345:987654321'
        target='清风难胡'
        games=[(3,['甲',target,'乙'],[65000,35000,5000],10),
               (3,[target,'甲','乙'],[60000,40000,5000],40),
               (4,['甲',target,'乙','丙'],[65000,25000,15000,-5000],10),
               (4,['甲','乙','丙',target],[80000,20000,10000,-10000],20)]
        if one:games=games[:1]
        p.store.reconcile(scope,[{'msg':str(i),'idx':0,'stamp':int(time.time()-game[3]*86400)} for i,game in enumerate(games,1)])
        paths={}
        for i,(mode,names,points,days) in enumerate(games,1):
            path=Path(folder)/f'original-{i}.png';Image.new('RGB',(50,50),(i*40,10,10)).save(path);paths[i]=str(path)
            source=p.store.source(scope,str(i),0);p.store.attach(source['id'],str(i),path)
            p.store.save(str(i),'accepted',validate({'is_result':True,'mode':mode,'confidence':1,'players':[
                {'name':name,'place':place,'points':score} for place,(name,score) in enumerate(zip(names,points),1)]}))
        if archive:periods.cutover(p.store,scope)
        return p,paths

    async def test_named_highest_and_lowest_send_that_players_original_not_global_winner(self):
        with tempfile.TemporaryDirectory() as folder:
            p,paths=self.seed(folder);event=Event()
            with patch('astrbot_plugin_group_data.main.Comp.Image.fromFileSystem',side_effect=lambda path:('image',path)):
                await p.handle(event,'最高 清风难胡 三麻 S0')
                await p.handle(event,'最低 清风难胡 S0 四麻')
            self.assertEqual(len(event.sent),2)
            self.assertEqual(event.sent[0][1],('image',paths[2]))
            self.assertIn('60,000 点',event.sent[0][0].text)
            self.assertIn('本局1位',event.sent[0][0].text)
            self.assertEqual(event.sent[1][1],('image',paths[4]))
            self.assertIn('-10,000 点',event.sent[1][0].text)
            self.assertIn('S0赛季',event.sent[1][0].text)

    async def test_both_extremes_separate_modes_and_deduplicate_same_game(self):
        with tempfile.TemporaryDirectory() as folder:
            p,paths=self.seed(folder);event=Event()
            with patch('astrbot_plugin_group_data.main.Comp.Image.fromFileSystem',side_effect=lambda path:('image',path)):
                await p.handle(event,'极值 清风难胡 S0')
            self.assertEqual([r[1][1] for r in event.sent],[paths[i] for i in (2,1,3,4)])
        with tempfile.TemporaryDirectory() as folder:
            p,paths=self.seed(folder,one=True);event=Event()
            with patch('astrbot_plugin_group_data.main.Comp.Image.fromFileSystem',side_effect=lambda path:('image',path)):
                await p.handle(event,'最高最低 清风难胡 三麻 S0')
            self.assertEqual(len(event.sent),1);self.assertIn('最高 / 最低',event.sent[0][0].text)

    async def test_missing_person_or_wrong_season_never_falls_back_to_global_or_history(self):
        with tempfile.TemporaryDirectory() as folder:
            p,paths=self.seed(folder);p.card=AsyncMock()
            with patch('astrbot_plugin_group_data.main.Comp.Image.fromFileSystem') as image:
                await p.handle(Event(),'最高 清风难胡 三麻')
                await p.handle(Event(),'最低 不存在 四麻 S0')
            image.assert_not_called();self.assertEqual(p.card.await_count,2)
            self.assertIn('没有符合条件',p.card.call_args.args[-1][0])

    async def test_alias_with_spaces_time_filter_and_minimum_are_honored(self):
        with tempfile.TemporaryDirectory() as folder:
            p,paths=self.seed(folder,archive=False);p.store.merge('12345:987654321','清风难胡','清风 别名');periods.cutover(p.store,'12345:987654321')
            event=Event()
            with patch('astrbot_plugin_group_data.main.Comp.Image.fromFileSystem',side_effect=lambda path:('image',path)):
                await p.handle(event,'最高 "清风 别名" 三麻 S0 30天')
            self.assertEqual(event.sent[0][1],('image',paths[1]))
            self.assertIn('清风 别名',event.sent[0][0].text)
            p.card=AsyncMock();await p.handle(Event(),'最高 "清风 别名" 三麻 S0 30天 2场')
            p.card.assert_awaited_once()

    async def test_personal_card_retains_extreme_record_ids_for_original_lookup(self):
        with tempfile.TemporaryDirectory() as folder:
            p,paths=self.seed(folder);event=Event()
            with patch('astrbot_plugin_group_data.main.cards.profile',wraps=cards.profile) as render:
                await p.handle(event,'个人 清风难胡 三麻 S0')
            player=next(s for s in render.call_args.args[3] if s['name']=='清风难胡')
            self.assertEqual((player['max_points_source'],player['min_points_source']),(2,1))
            with patch('astrbot_plugin_group_data.main.Comp.Image.fromFileSystem',side_effect=lambda path:('image',path)):
                await p.handle(event,'原图 2')
                await p.handle(event,'原图 1')
            self.assertEqual(event.sent[-2][1],('image',paths[2]));self.assertEqual(event.sent[-1][1],('image',paths[1]))


if __name__=='__main__':unittest.main()
