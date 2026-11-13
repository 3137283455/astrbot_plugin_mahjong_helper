"""Read-only AI bridge to the separate essence statistics plugin."""
import re
import json

def quick_group_query(text):
    """Unambiguous read-only requests only; all other language still uses LLM tools."""
    if not isinstance(text,str) or len(text)>300:return None
    text=text.strip().rstrip('。！？!? ').strip()
    filter_pattern=r'(?:S\d+(?:赛季)?|三麻|四麻|近?\d+天|(?:至少)?\d+场)'
    board_filter=filter_pattern+r'|第?[0-9一二三四五六七八九十百两]+页'
    # A standalone metric has no player argument. Do not reinterpret it as an
    # incomplete personal query, or consume contextual "最高那局" follow-ups.
    extreme=re.fullmatch(
        r'(?:请|麻烦)?(?:帮我|给我|我要|我想看|我想查)?(?:查询一下|查询|查一下|查下|查查|查|看一下|看看|看下|看)?\s*'
        r'(?P<before>(?:(?:'+board_filter+r')\s*)*)(?:全群|本群|群内|群友)?(?:群数据)?(?:的)?'
        r'(?P<metric>最高|最低)(?:持点|点数)(?:对局|结算图?|原图|截图|榜|排行(?:榜)?|是谁)?'
        r'(?P<after>(?:\s*(?:'+board_filter+r'))*)',text,re.I)
    if extreme:
        filters=re.findall(board_filter,extreme['before']+' '+extreme['after'],re.I)
        return ' '.join([extreme['metric'],*[f.upper() if re.match(r'^s\d',f,re.I) else f.removeprefix('近') for f in filters]])
    paging=re.fullmatch(r'(?:请|麻烦)?(?:帮我|给我)?(?:看看|看下|看一下|看|翻到|翻)?\s*(下一页|上一页|第?[0-9一二三四五六七八九十百两]+页)(?:\s*(三麻|四麻))?',text)
    if paging:return ' '.join(p for p in paging.groups() if p)
    verb=re.match(r'^(?:请|麻烦)?(?:帮我|给我)?(?:查询一下|查询|查一下|查下|查查|查|看一下|看看|看下|看)\s*',text)
    if not verb:return None
    body=text[verb.end():].strip()
    if body in ('群数据评分规则','综合评分规则','评分规则'):return '评分规则'
    board=re.fullmatch(r'(?P<before>(?:(?:'+board_filter+r')\s*)*)(?:本群|群友|群内)?(?:群数据)?(?P<metric>综合分榜单|综合表现榜|综合评分榜|综合排行榜|综合排名|综合榜|暂定榜|被飞次数榜|被飞率榜|被飞榜|(?:一位|二位|三位|四位|末位)(?:率)?榜|场次榜|平均顺位榜|平均持点榜|最高持点榜|最低持点榜)(?P<after>(?:\s*(?:'+board_filter+r'))*)',body,re.I)
    if board:
        metric=board['metric'];action='综合榜' if metric.startswith('综合') else {'被飞次数榜':'被飞','最高持点榜':'最高','最低持点榜':'最低'}.get(metric,metric.removesuffix('榜'))
        if metric=='暂定榜':action='暂定榜'
        filters=re.findall(board_filter,board['before']+' '+board['after'],re.I)
        return ' '.join([action,*[f.removeprefix('近') for f in filters]])
    # Player names are kept verbatim. Only whitespace-delimited trailing filters
    # are separated, so a nickname containing 三麻/四麻 is never silently rewritten.
    match=re.fullmatch(r'(?P<name>.+?)(?:的)?(?P<kind>群数据|群内数据|群战绩|综合评分|综合分)(?P<tail>(?:\s+'+filter_pattern+r')*)',body,re.I)
    if not match:return None
    name=match['name'].strip().removesuffix('的').strip()
    if (not name or len(name)>100 or re.search(r'[，。！？?!\n]|为什么|怎么|如何|锐评|评价|对比|同桌|规则|介绍|解释',name)):
        return None
    if name[0] in '“"' and name[-1] in '”"':name=name[1:-1]
    action='评分' if match['kind'] in ('综合评分','综合分') else '个人'
    filters=re.findall(filter_pattern,match['tail'],re.I)
    return ' '.join([action,json.dumps(name,ensure_ascii=False),*[f.removeprefix('近') for f in filters]])

GROUP_DATA_INSTRUCTIONS='''【群数据】
用户查询本群精华中的雀魂结算、谁被飞最多、群内顺位次数/比例、群内最高最低持点时使用 group_data，不用娱乐API，也不用牌谱屋 mahjong_query。
独立查询没有提供昵称时，“S0最低持点”“全群最低点数”“四麻最高持点”是全群极值请求，直接调用group_data，例如“最低 S0”“最低”“最高 四麻”。不要求补昵称，不擅自继承某个人；插件会发送榜单和对应结算原图。未指定模式时分别查三麻、四麻，指定模式则只查该模式。
用户问群友综合评分、综合实力榜、谁的综合表现最好，用group_data查询“综合榜”，例如“综合榜 三麻 S0”“综合榜 四麻 S1”。查某人的综合评分或分解用“评分 昵称”，例如“评分 清风难胡 三麻 S0”。查询规则用“评分规则”。不要改用场次榜、一位率榜或牌谱屋；也不要自己用文字估算分数。成绩90%＋场次10%，满10场正式入榜，少于10场为暂定，不占正式名次。普通个人档案也包含综合分和综合排名。S0、S1及三四麻各自独立，未说明赛季仍默认S1。
所有榜单每页10人；追加“第2页”可以查看后续，例如“综合榜 四麻 S0 第2页”“被飞 四麻 第2页”。不足10场的综合分单独用“暂定榜 四麻 S0”。没有正式玩家时综合榜展示暂定分，不虚构正式排名。用户追问“下一页”“上一页”“第3页”时query直接填“下一页”“上一页”“第3页”，可追加明确的三麻/四麻，由插件沿用该用户当前群10分钟内最近榜单的赛季、模式、天数、最少场次；不要猜测或复用其他用户的榜单。没有上下文或同时展示两种模式时按插件提示让用户补充。
“被飞榜”“被飞排行”“谁被飞最多”默认按被飞次数排序，query使用“被飞”或“被飞榜”，不能擅自换成被飞率。只有用户明确要求被飞率、比例、概率或占比时才使用“被飞率”。
query填原生 /群数据 后面的参数，如“四麻 被飞榜”“四麻 被飞率”“三麻 一位率 30天”“个人 完整游戏昵称 四麻”。新查询未指定模式时留空，三麻和四麻分别统计；连续追问则保留上轮已明确的模式。
新查询默认只查S1赛季；旧数据已定名S0赛季。用户要求S0、旧数据、历史或封存榜单时，在query后加“S0”，例如“四麻 被飞榜 S0”。要求S1时可加“S1”。连续追问沿用之前明确的赛季。不能混合两个赛季；赛季和模式参数顺序可调换。
也支持“周期”“最近 5 四麻”“对比 甲 乙 四麻”“同桌 甲 乙 三麻”“表格”。对比和同桌的两个昵称各为一个参数，含空格时加英文双引号。正常终局和被飞终局均统计。
昵称可使用用户提供的片段或省略特殊字符，由群数据插件查找真实候选；不要猜全名、添加标点或改用其他玩家。插件显示候选后停止本轮，等本人选择；用户说“第2个”时使用“选 2”，不能把序号当玩家名，也不能自行选择候选。候选保留原来的赛季、模式和时间条件，普通序号回复由插件直接处理。用户取消时可用“取消”。
玩家在当前赛季没有记录时，插件会提示其他赛季中的真实昵称和历史查询命令；只作提示，不自动切换S0。选择正在处理或已经完成时，插件会拦截重复选择，不重复出图。个人/个人极值查询未指定模式时，只返回有符合条件对局的模式；指定模式则保留指定条件。
用户查询某人的最高/最低持点对局时，query必须带用户给出的昵称或片段，例如“最高 清风难胡 三麻 S0”“最低 清风难胡 四麻 S0”；同时要最高和最低时，必须用一次group_data调用，query填“极值 清风难胡 S0”，不能拆成最高和最低两次调用。直接发送对应结算原图；不能省略姓名变成全群榜首，也不要求用户先记住记录编号。个人档案仍显示最高最低持点与对应原图编号。
接着问“最高那局”“那四麻最低呢”“最高最低都发来”时，把本会话上一轮已明确的玩家、赛季、三麻/四麻及时间筛选补全到query，仅覆盖用户明确改变的项。例如上一轮是“最低 清风难胡 四麻 S0”，再说“最高最低都给我”，query应为“极值 清风难胡 四麻 S0”；这里“都”指最高和最低，不能擅自扩大成三麻四麻都查。明确在追问某个人、但缺少可确认的玩家上下文时才询问；独立的全群极值请求不需要昵称。不猜其他成员或其他群的玩家。
只查当前QQ群；私聊不猜群号。可查帮助、状态、玩家列表、图片原图。管理操作需要管理员明确发原生命令，不用工具替人确认识别结果。
工具在本轮直接发送卡片、文字候选或原图后停止，不在同一轮重复发消息；用户发来新消息明确要重新看图时，可以再次查询。不把精华样本的比例或综合评分描述成玩家实际总战绩、官方评级，也不要把QQ发送者当成截图里的玩家。
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
