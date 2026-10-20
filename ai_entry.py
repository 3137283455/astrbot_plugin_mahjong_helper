"""Small policies for AstrBot's native tools; no command router or model client."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import astrbot.api.message_components as Comp
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter


AI_TOOLS = frozenset({"mahjong_quiz", "mahjong_query", "find_in_bilibili", "deliver_media"})
AI_PERSONA_NAME = "智能功能助手"
AI_UNAVAILABLE = "AI 功能识别暂时不可用，请直接使用 /何切、/雀 或 /搜索歌曲 等原有命令。"
AI_PERSONA = """你是 QQ 机器人的智能功能助手，只调度实际已注册的功能工具。
仅处理当前消息中的明确功能请求。普通聊天、问候、感想不调用工具，不回复。
何切、何且、何棋等明显错字按何切练习理解。出题数量使用 count，未指定为1，一次最多5道；超过5道交给工具校验，不能拆成多次调用规避限制。
何切出题、原书答案、练习状态或帮助使用 mahjong_quiz；雀魂公开数据、玩家搜索、我的绑定列表使用 mahjong_query。
雀魂昵称完整保留，玩家在前，三/四、栏目、房间、天数由结构化参数传给工具。数据仅覆盖金之间及以上的公开场次。
音乐或视频请求先 find_in_bilibili，再 deliver_media，严格使用工具给出的 search_id 和 position，不能编造；遵守其选歌和下载规则。
只使用工具实际支持的操作；不执行 Python、Shell、联网搜索、定时任务、群广播或管理操作。
没有排行榜工具。无法识别的功能不编造执行结果。信息不足时调用相关工具的帮助或让业务逻辑提示缺失信息。
原生命令已经由插件处理，不重复执行。每条请求只完成一次功能；批量何切使用一次工具调用。
工具直接发送图片、文字或媒体后结束，不输出预告、过程、闲聊或重复确认。工具失败不得宣称成功。
"""


def has_native_command(event) -> bool:
    """Use framework-matched handlers, rather than scanning command definitions."""
    return any(
        isinstance(item, (CommandFilter, CommandGroupFilter))
        for handler in event.get_extra("activated_handlers", []) or []
        for item in handler.event_filters
    )


def explicit_group_mention(event) -> bool:
    if event.get_platform_name() != "aiocqhttp" or not event.get_group_id():
        return False
    self_id = str(event.get_self_id() or "")
    return bool(self_id) and str(event.get_sender_id()) != self_id and any(
        isinstance(part, Comp.At) and str(part.qq) == self_id
        for part in event.get_messages()
    )


def is_greeting(text: str) -> bool:
    clean = re.sub(r"[\s，。！？、,.!?~～]+", "", text).lower()
    return clean in {"", "你好", "您好", "哈喽", "嗨", "hi", "hello", "早", "早上好", "晚上好", "晚安"}


def eligible(event) -> bool:
    return explicit_group_mention(event) and not has_native_command(event) and not is_greeting(event.message_str)


def modelscope_provider(provider) -> bool:
    config = getattr(provider, "provider_config", {}) or {}
    parsed = urlparse(str(config.get("api_base", "")))
    return (
        parsed.scheme == "https"
        and parsed.hostname == "api-inference.modelscope.cn"
        and config.get("enable", True)
    )


def validate_quiz(action, count, question_id):
    if not isinstance(action, str) or action not in {"question", "answer", "status", "menu"}:
        raise ValueError("可用操作：出题、查看答案、查看状态或帮助。")
    if type(count) is not int or not 1 <= count <= 5:
        raise ValueError("每次只能出 1～5 道何切题。")
    if type(question_id) is not int or question_id < 0:
        raise ValueError("题号必须是非负整数，0 表示随机题或当前题。")
    if count != 1 and (question_id or action != "question"):
        raise ValueError("只有随机出题支持批量；指定题号、答案和状态一次只能查看一项。")
