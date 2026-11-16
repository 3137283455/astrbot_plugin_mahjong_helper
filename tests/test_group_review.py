import json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from mcp.types import CallToolResult,TextContent
from astrbot.core.provider.entities import LLMResponse
from astrbot_plugin_mahjong_helper.group_review import build_group_review,GROUP_REVIEW_INSTRUCTIONS
from astrbot_plugin_mahjong_helper.ai_entry import AI_TOOLS
from astrbot_plugin_group_data.main import GroupDataPlugin
from astrbot_plugin_group_data.store import Store
from astrbot_plugin_group_data import periods
from test_ai_entry import Event,plugin,tools

class GroupEvent(Event):
    def __init__(self,text='锐评测试甲的群数据',**kwargs):super().__init__(text=text,group='987654321',**kwargs)
    def get_self_id(self):return '12345'
    def get_messages(self):
        import astrbot.api.message_components as Comp
        return [Comp.At(qq='12345'),Comp.Plain(self.message_str)]

class GroupReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.folder=Path(self.tmp.name)
        self.instance=GroupDataPlugin.__new__(GroupDataPlugin);self.instance.store=Store(self.folder/'group.db');self.scope='12345:987654321'
        self.instance.store.enable(self.scope,'12345','987654321')
        self.instance.store.db.execute("INSERT INTO periods VALUES('current',?,'S1赛季','current',1,0,NULL,'')",(self.scope,));self.instance.store.db.commit()
        self.seed(30)
        self.context=SimpleNamespace(get_registered_star=lambda name:SimpleNamespace(activated=True,star_cls=self.instance) if name=='astrbot_plugin_group_data' else None)
    def tearDown(self):self.instance.store.close();self.tmp.cleanup()
    def seed(self,n,mode=4):
        entries=[{'msg':str(mode)+'-'+str(i),'idx':0,'stamp':time.time()} for i in range(n)]
        self.instance.store.reconcile(self.scope,entries)
        for i,e in enumerate(entries):
            row=self.instance.store.source(self.scope,e['msg'],0);digest=e['msg'];self.instance.store.attach(row['id'],digest,'not-opened.png')
            points=[40000,30000,20000,10000] if mode==4 else [50000,35000,20000]
            self.instance.store.save(digest,'accepted',{'mode':mode,'game_id':digest,'players':[{'name':'测试'+'甲乙丙丁'[j],'place':j+1,'points':v} for j,v in enumerate(points)]})
    def test_personal_is_current_read_only_and_uses_live_score(self):
        before=self.instance.store.db.total_changes;d=build_group_review(self.context,GroupEvent(),'个人 测试甲 四麻')
        self.assertEqual(d['status'],'group_review_data');self.assertEqual(d['scope']['赛季'],'S1赛季')
        row=d['metrics']['4麻']['玩家'][0];self.assertEqual(row['综合分'],84.4);self.assertEqual(row['综合排名'],1);self.assertEqual(row['出场次数'],30)
        self.assertEqual(d['scoring_rules']['official_minimum'],30);self.assertEqual(self.instance.store.db.total_changes,before)
        self.assertNotIn('放铳率',row);self.assertNotIn('立直率',row)
    def test_partial_nickname_resolves_only_unique_candidate(self):
        d=build_group_review(self.context,GroupEvent(),'个人 试甲 四麻');self.assertEqual(d['player']['nickname'],'测试甲')
        with self.assertRaisesRegex(ValueError,'多个玩家'):build_group_review(self.context,GroupEvent(),'个人 测试 四麻')
        with self.assertRaisesRegex(ValueError,'没有'):build_group_review(self.context,GroupEvent(),'个人 不存在 四麻')
    def test_compare_and_board_return_only_requested_group(self):
        d=build_group_review(self.context,GroupEvent(),'对比 测试甲 测试乙 四麻');self.assertEqual(len(d['metrics']['4麻']['玩家']),2)
        board=build_group_review(self.context,GroupEvent(),'综合榜 四麻');self.assertEqual(board['metrics']['4麻']['榜单类型'],'正式综合榜')
        self.assertEqual(board['metrics']['4麻']['玩家'][0]['名次'],1)
        other=GroupEvent();other.group='123'
        with self.assertRaises(ValueError):build_group_review(self.context,other,'个人 测试甲')
    def test_no_private_no_writes_and_no_guessing_sender(self):
        event=GroupEvent();event.group='';before=self.instance.store.db.total_changes
        for query in ('个人 测试甲','开启','忽略 1','修正 1 四麻 甲=40000 乙=30000 丙=20000 丁=10000'):
            with self.assertRaises(ValueError):build_group_review(self.context,event,query)
        for query in ('开启','忽略 1','重识 1','个人'):
            with self.assertRaises(ValueError):build_group_review(self.context,GroupEvent(),query)
        self.assertEqual(self.instance.store.db.total_changes,before)
    def test_three_mode_has_no_fourth_place_and_provisional_has_no_rank(self):
        self.seed(5,3);d=build_group_review(self.context,GroupEvent(),'个人 测试甲')
        self.assertEqual(set(d['metrics']),{'3麻'});row=d['metrics']['3麻']['玩家'][0]
        self.assertNotIn('4位次数',row);self.assertIsNone(row['综合排名']);self.assertEqual(row['评分状态'],'暂定')
    def test_constructed_archive_retains_old_scoring_without_updates(self):
        # Artificial fixture only; no real archived data is loaded.
        self.instance.store.db.execute("DELETE FROM source_periods");self.instance.store.db.execute("DELETE FROM periods");self.instance.store.db.commit()
        periods.cutover(self.instance.store,self.scope)
        before=self.instance.store.db.total_changes;d=build_group_review(self.context,GroupEvent(),'个人 测试甲 四麻 S0')
        self.assertEqual(d['scoring_rules']['official_minimum'],10);self.assertEqual(d['scope']['赛季'],'S0赛季')
        self.assertEqual(self.instance.store.db.total_changes,before)
    async def test_tool_evidence_allows_one_persona_response_without_sending_card(self):
        p=plugin(str(self.folder/'helper'));p.context.get_registered_star=self.context.get_registered_star
        e=GroupEvent();e.set_extra('mahjong_ai_request',True);e.set_extra('mahjong_ai_toolset',tools(*AI_TOOLS))
        raw=await p.group_data_review_ai(e,'个人 测试甲 四麻');self.assertEqual(json.loads(raw)['status'],'group_review_data');self.assertEqual(e.sent,[])
        result=CallToolResult(content=[TextContent(type='text',text=raw)])
        await p.ai_tool_finished(e,SimpleNamespace(name='group_data_review'),{'query':'个人 测试甲 四麻'},result)
        self.assertTrue(e.get_extra('mahjong_ai_review_ready'));self.assertEqual(e.get_extra('mahjong_ai_toolset').tools,[])
        response=LLMResponse(role='assistant',completion_text='一位次数确实有底气，继续保持。')
        await p.ai_no_chat_response(e,response);self.assertIn('慧慧群数据锐评',response.completion_text);self.assertIn('S1赛季',response.completion_text)
        self.assertNotIn('金之间',response.completion_text);self.assertEqual(e.sent,[])
        self.assertIsNone(await p.group_data_review_ai(e,'个人 测试乙'))
    def test_instructions_separate_sources_and_forbid_invented_tactics(self):
        self.assertIn('group_data_review',AI_TOOLS);self.assertIn('mahjong_replay',GROUP_REVIEW_INSTRUCTIONS)
        self.assertIn('被飞与放铳是不同指标',GROUP_REVIEW_INSTRUCTIONS)
