"""Read-only AI bridge to the separate essence statistics plugin."""
import re

GROUP_DATA_INSTRUCTIONS='''【群数据】
用户查询本群精华中的雀魂结算、谁被飞最多、群内顺位次数/比例、群内最高最低持点时使用 group_data，不用娱乐API，也不用牌谱屋 mahjong_query。
“被飞榜”“被飞排行”“谁被飞最多”默认按被飞次数排序，query使用“被飞”或“被飞榜”，不能擅自换成被飞率。只有用户明确要求被飞率、比例、概率或占比时才使用“被飞率”。
query填原生 /群数据 后面的参数，如“四麻 被飞榜”“四麻 被飞率”“三麻 一位率 30天”“个人 完整游戏昵称 四麻”；未指定模式留空则三麻和四麻分别统计。
默认只查询S1赛季；旧数据已定名S0赛季。用户要求S0、旧数据、历史或封存榜单时，在query后加“S0”，例如“四麻 被飞榜 S0”。要求S1时可加“S1”。不能混合两个赛季；赛季和模式参数顺序可调换。
也支持“周期”“最近 5 四麻”“对比 甲 乙 四麻”“同桌 甲 乙 三麻”“表格”。对比和同桌的两个昵称各为一个参数，含空格时加英文双引号。正常终局和被飞终局均统计。
用户查询某人的最高/最低持点对局时，query必须带完整昵称，例如“最高 清风难胡 三麻 S0”“最低 清风难胡 四麻 S0”；同时要最高和最低时用“极值 清风难胡 S0”。直接发送对应结算原图；不能省略姓名变成全群榜首，也不要求用户先记住记录编号。个人档案仍显示最高最低持点与对应原图编号。
只查当前QQ群；私聊不猜群号。可查帮助、状态、玩家列表、图片原图。管理操作需要管理员明确发原生命令，不用工具替人确认识别结果。
工具直接发送卡片和原图后停止，不重复发消息。不把精华样本的比例描述成玩家实际总战绩，也不要把QQ发送者当成截图里的玩家。
'''
async def run_group_data(context,event,query):
    meta=context.get_registered_star('astrbot_plugin_group_data')
    instance=meta.star_cls if meta is not None and meta.activated else None
    if not callable(getattr(instance,'handle',None)):
        await event.send(event.plain_result('群数据插件尚未启用，请联系管理员。'));return
    if not isinstance(query,str) or len(query)>500:
        await event.send(event.plain_result('群数据查询参数过长，请使用 /群数据 帮助。'));return
    # A model may choose the rate tool parameter despite a plain count request.
    # Preserve explicit rate requests and follow-up filters such as “换四麻”.
    message=str(getattr(event,'message_str','') or '')
    if '被飞' in message and not re.search(r'率|比例|概率|占比|百分',message):
        query=re.sub(r'(^|\s)被飞率(?:榜|排行(?:榜)?|排名)?(?=\s|$)',r'\1被飞',query)
    await instance.handle(event,query,allow_write=False)
