"""Read-only AI bridge to the separate essence statistics plugin."""
GROUP_DATA_INSTRUCTIONS='''【群数据】
用户查询本群精华中的雀魂结算、谁被飞最多、群内顺位次数/比例、群内最高最低持点时使用 group_data，不用娱乐API，也不用牌谱屋 mahjong_query。
query填原生 /群数据 后面的参数，如“四麻 被飞率”“三麻 一位率 30天”“个人 完整游戏昵称 四麻”；未指定模式留空则三麻和四麻分别统计。
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
    await instance.handle(event,query,allow_write=False)
