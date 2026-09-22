from __future__ import annotations

from datetime import datetime
from typing import Any


ROOM_MODES = {
    4: {
        "金": "9.8",
        "金间": "9.8",
        "金之间": "9.8",
        "玉": "12.11",
        "玉间": "12.11",
        "玉之间": "12.11",
        "王座": "16.15",
        "王座间": "16.15",
        "王座之间": "16.15",
    },
    3: {
        "金": "22.21",
        "金间": "22.21",
        "金之间": "22.21",
        "玉": "24.23",
        "玉间": "24.23",
        "玉之间": "24.23",
        "王座": "26.25",
        "王座间": "26.25",
        "王座之间": "26.25",
    },
}


def pick(data: dict | None, *keys: str, default=None):
    if not data:
        return default
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def player_uid(player: dict) -> str:
    return str(pick(player, "id", "uid", "account_id", "accountId", default=""))


def player_name(player: dict) -> str:
    return str(pick(player, "nickname", "name", default="未知玩家"))


def level_text(level: Any) -> str:
    if not level:
        return "暂无段位"
    if isinstance(level, str):
        return level
    if not isinstance(level, dict):
        return str(level)
    if level.get("name"):
        return str(level["name"])
    level_id = int(pick(level, "id", "level_id", default=0) or 0)
    score = int(pick(level, "score", "point", default=0) or 0)
    rank_names = ["未知", "初心", "雀士", "雀杰", "雀豪", "雀圣", "魂天"]
    local = level_id % 10000
    major, star = local // 100, local % 100
    name = rank_names[major] if 0 < major < len(rank_names) else f"段位{level_id}"
    suffix = f"{star}星" if 0 < star <= 3 and major < 6 else ""
    return f"{name}{suffix} {score}pt".strip()


def percent(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    if abs(number) <= 1:
        number *= 100
    return f"{number:.1f}%"


def room_modes(mode: int, room: str) -> str | None:
    return ROOM_MODES.get(mode, {}).get(room.strip()) if room else None


def format_search(players: list[dict], mode: int) -> str:
    title = "四麻" if mode == 4 else "三麻"
    lines = [f"{title}搜索结果："]
    for index, player in enumerate(players[:10], 1):
        level = pick(player, "level", default={})
        lines.append(
            f"{index}. {player_name(player)}｜UID {player_uid(player)}｜{level_text(level)}"
        )
    return "\n".join(lines)


def format_stats(uid: str, mode: int, stats: dict, extended: dict | None = None) -> str:
    title = "四麻战绩" if mode == 4 else "三麻战绩"
    nickname = pick(stats, "nickname", "name", default=uid)
    level = level_text(pick(stats, "level", default={}))
    count = pick(stats, "count", "games", "game_count", default=0)
    avg_rank = pick(stats, "avg_rank", "avgRank", "rank_avg_score", default=None)
    lines = [f"🀄 {nickname} · {title}", f"UID：{uid}", f"段位：{level}", f"对局数：{count}"]
    if avg_rank is not None:
        lines.append(f"平均顺位：{float(avg_rank):.2f}")
    fields = [
        ("一位率", ("rank1_rate", "rank1Rate", "rank_1_rate")),
        ("和牌率", ("win_rate", "winRate")),
        ("放铳率", ("dama_rate", "deal_in_rate", "dealInRate", "rank_rate", "rankRate")),
        ("自摸率", ("self_draw_rate", "selfDrawRate")),
        ("立直率", ("riichi_rate", "riichiRate")),
    ]
    merged = dict(stats)
    if isinstance(extended, dict):
        merged.update(extended)
    for label, keys in fields:
        value = pick(merged, *keys)
        if value is not None:
            lines.append(f"{label}：{percent(value)}")
    return "\n".join(lines)


def record_uuid(record: dict) -> str:
    return str(pick(record, "uuid", "game_uuid", "gameUuid", default=""))


def record_time(record: dict) -> str:
    value = pick(record, "start_time", "startTime", "end_time", "endTime", default=0)
    try:
        number = int(value)
        if number > 10_000_000_000:
            number //= 1000
        return datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "时间未知"


def record_players(record: dict) -> list[dict]:
    for key in ("players", "accounts", "all_players", "allPlayers"):
        value = record.get(key)
        if isinstance(value, list):
            return value
    return []


def format_record(record: dict, target_uid: str | None = None) -> str:
    players = record_players(record)
    target = None
    if target_uid:
        target = next((p for p in players if player_uid(p) == str(target_uid)), None)
    if target is None and players:
        target = players[0]
    result = ""
    if target:
        rank = pick(target, "rank", "grading_rank", "placement")
        if rank is None and players:
            ordered = sorted(players, key=lambda item: float(pick(item, "score", default=0) or 0), reverse=True)
            rank = ordered.index(target) + 1
        score = pick(target, "score", "final_score", "part_point_1")
        delta = pick(target, "delta", "grading_score", "gradingScore")
        parts = [player_name(target)]
        if rank is not None:
            parts.append(f"第{rank}位")
        if score is not None:
            parts.append(f"{score}点")
        if delta is not None:
            parts.append(f"段位分 {int(delta):+d}")
        result = "｜".join(parts)
    uuid = record_uuid(record)
    link = f"https://game.maj-soul.net/1/?paipu={uuid}" if uuid else "无牌谱链接"
    return f"{record_time(record)}\n{result}\n{link}".strip()


def format_records(uid: str, mode: int, records: list[dict]) -> str:
    title = "四麻" if mode == 4 else "三麻"
    lines = [f"🀄 UID {uid} 最近{title}对局"]
    for index, record in enumerate(records, 1):
        lines.append(f"\n{index}. {format_record(record, uid)}")
    return "\n".join(lines)
