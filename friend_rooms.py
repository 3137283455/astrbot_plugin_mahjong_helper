"""Friend-room request parsing and preflight; never recover rooms from history."""
import re

_ROOM=r'(?:友人房|友人间|房间)(?:号码|房号|号|链接)?'
_ACTION=r'(?:艾特(?:一下)?(?:全体(?:成员)?|所有人|大家)?|@全体(?:成员)?|广播(?:一下)?|发布(?:一下)?|通知(?:一下)?(?:全体|大家)?|喊(?:一下)?(?:人|大家))'
_LEAD=r'(?:请|麻烦)?(?:帮我|给我|替我)?\s*'
_TOKEN=re.compile(r'(?:[0-9]{3,12}|https?://\S+)',re.I)
_PAYLOAD=re.compile(r'(?:[0-9]{3,12}|https?://[^\s<>]+|[A-Za-z][A-Za-z0-9_-]{1,99})',re.I)
_PLACEHOLDERS={'这个','那个','上一个','刚才那个','上面的','之前的','刚才的','房间号','链接','友人房','友人间'}


def room_request(text):
    """None: other intent; empty: ask this requester; string: explicit payload."""
    if not isinstance(text,str) or len(text)>600:return None
    text=text.strip().rstrip('。！!').strip()
    short=re.fullmatch(r'/?友(?:人房|人间)?\s*[:：]?\s*([0-9]{3,12}|https?://\S+)?',text,re.I)
    if short:return short[1] or ''
    forward=re.fullmatch(_LEAD+_ACTION+r'[，,\s]*(?:一下)?(?:这个)?(?:雀魂)?'+_ROOM+r'(?P<sep>[\s:：]*)(?P<room>[^\s，,。！？]*)',text)
    reverse=re.fullmatch(r'(?:雀魂)?'+_ROOM+r'(?P<sep>[\s:：]*)(?P<room>\S+?)[，,\s]+'+_LEAD+_ACTION,text)
    match=forward or reverse
    if not match:return None
    value=match['room'].strip('“”\'"')
    if not value or value in _PLACEHOLDERS:return ''
    if len(value)>300 or not _PAYLOAD.fullmatch(value):return None
    if not match['sep'] and not _TOKEN.fullmatch(value):return None
    return value


def current_room(room,text):
    """Require the selected room to be explicitly present in this message."""
    if not isinstance(room,str) or not isinstance(text,str):return None
    room=room.strip().strip('“”\'"')
    if not room or len(room)>300 or room in _PLACEHOLDERS:return None
    direct=room_request(text)
    if direct is not None:return room if direct==room else None
    if re.search(r'(?:不要|不用|别|取消|禁止|不想|不需要)\s*'+_ACTION,text):return None
    if re.search(r'为什么|怎么|如何|什么意思|介绍|解释|说明',text):return None
    if re.search(r'(?:不是|别用|不要用|不用|旧(?:的)?(?:房间|房号)?(?:是|为)?|原来的?|之前的?)\s*[:：]?\s*'+re.escape(room),text):return None
    # Flexible wording is left to the native tool, but its numeric/link
    # argument must occur literally in the current text, with a room intent.
    if not _TOKEN.fullmatch(room) or not re.search(_ROOM,text) or not re.search(_ACTION,text):return None
    if not re.search(r'(?<![A-Za-z0-9_])'+re.escape(room)+r'(?![A-Za-z0-9_])',text):return None
    return room


def room_quota_feedback(quota,is_owner=False):
    """Explain known exhausted counters before the generic can_at_all flag."""
    if not isinstance(quota,dict):return '暂时无法查询 @全体 剩余次数，本次未广播。'
    if is_owner and quota.get('can_at_all'):return None
    def count(key):
        value=quota.get(key)
        if type(value) is int:return value
        if isinstance(value,str) and re.fullmatch(r'-?\d+',value.strip()):return int(value)
        return None
    group=count('remain_at_all_count_for_group');account=count('remain_at_all_count_for_uin')
    if group==0 and account==0:return '本群和机器人账号的 @全体 次数都已用完，本次未广播。'
    if group==0:return '本群的 @全体 次数已用完，本次未广播。'
    if account==0:return '机器人账号的 @全体 次数已用完，本次未广播。'
    if not quota.get('can_at_all'):return '机器人当前无法在本群 @全体，本次未广播。'
    if group is None or account is None or group<0 or account<0:
        return '暂时无法确认 @全体 剩余次数，本次未广播。'
    return None


ROOM_INSTRUCTIONS='''【友人房广播】
用户明确请求广播友人房、帮忙艾特全体叫人打牌时，调用 broadcast_friend_room；保留原有群权限与个人冷却检查。
只能使用用户当前这条消息明确提供的房间号或链接，room逐字保留。不读取聊天历史、上一条消息、引用内容或其他群友消息里的房间号，也不猜测。
“友77182”“帮我艾特友人房77182”“广播友人房 77182”都属于广播请求。当前消息没提供房间号或链接，例如“帮我艾特友人房”，仍调用该工具且room留空；工具只@发起人询问“请提供房间号或链接”，绝不先@全体。
解释用法、取消或拒绝广播时不调用。工具完成后停止，不重复广播、不另发成功说明。
'''
