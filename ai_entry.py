"""Small policies for AstrBot's native tools; no command router or model client."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import astrbot.api.message_components as Comp
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter


AI_TOOLS = frozenset({
    "mahjong_quiz", "mahjong_query", "mahjong_review", "bot_menu", "music_search", "music_action", "find_in_bilibili", "deliver_media",
    "query_available_apis", "call_api_by_name",
})
AI_PERSONA_NAME = "慧慧"
AI_UNAVAILABLE = "慧慧暂时连不上模型，请稍后再聊；功能仍可直接使用 /何切、/雀 或 /搜索歌曲 等原有命令。"
AI_PERSONA = json.loads(
    (Path(__file__).parent / "personas" / "huihui.json").read_text(encoding="utf-8")
)["system_prompt"]


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



def eligible(event) -> bool:
    if event.get_platform_name() != "aiocqhttp":
        return False
    self_id = str(event.get_self_id() or "")
    if not self_id or str(event.get_sender_id()) == self_id:
        return False
    addressed = event.is_private_chat() or explicit_group_mention(event)
    return addressed and not has_native_command(event) and bool(event.message_str.strip())


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
