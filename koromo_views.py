"""Short, chat-friendly views of Amae-Koromo's public player statistics."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .formatters import level_text, percent, pick, player_name, player_uid, record_time


SECTIONS = {
    "基本": "基本", "概览": "基本", "战绩": "基本",
    "基": "基本", "战": "基本",
    "立直": "立直", "更多": "更多", "牌风": "更多",
    "立": "立直", "风": "更多",
    "和铳": "和铳", "和铳分布": "和铳",
    "和": "和铳",
    "血统": "血统", "运气": "血统",
    "运": "血统",
    "大铳": "大铳", "最近大铳": "大铳",
    "铳": "大铳",
    "顺位": "顺位", "顺": "顺位", "趋势": "趋势", "近": "趋势",
    "同桌": "同桌", "桌": "同桌", "对局": "对局", "局": "对局",
    "网页": "网页", "页": "网页",
}


@dataclass(frozen=True)
class PlayerQuery:
    section: str
    player: str = ""
    mode: int = 4
    room: str = ""
    days: int = 0
    page: int = 1
    text: bool = False


def parse_player_query(section: str, *args: str) -> PlayerQuery:
    """Read the player first and accept query options in any trailing order."""
    tokens = [token.strip() for token in (section, *args) if token.strip()]
    prefix_section = SECTIONS.get(tokens[0]) if tokens else None
    if prefix_section:
        tokens.pop(0)  # Keep the old /雀 铳 玩家 form working.

    has_records_section = prefix_section == "对局" or any(
        SECTIONS.get(token) == "对局" for token in tokens
    )

    def is_option(token: str) -> bool:
        return (
            token in SECTIONS
            or token in {"三", "三麻", "四", "四麻", "3", "4"}
            or token in {"金", "金间", "金之间", "玉", "玉间", "玉之间",
                         "王", "王座", "王座间", "王座之间", "文", "文字"}
            or token.removeprefix("近") in {"7天", "30天", "90天", "365天"}
            or token.startswith("第") and token.endswith("页") and token[1:-1].isdigit()
            or has_records_section and token.isdigit() and len(token) <= 2
        )

    option_start = len(tokens)
    while option_start and is_option(tokens[option_start - 1]):
        option_start -= 1
    player = " ".join(tokens[:option_start])
    options = tokens[option_start:]
    suffix_sections = [SECTIONS[token] for token in options if token in SECTIONS]
    if len(suffix_sections) + bool(prefix_section) > 1:
        raise ValueError("查询栏目只能填写一个，例如 /雀 玩家 三 铳 30天。")
    canonical = prefix_section or (suffix_sections[0] if suffix_sections else "基本")

    mode, room, days, page, as_text = 4, "", 0, 1, False
    for token in options:
        if token in SECTIONS:
            continue
        if token in {"三", "三麻"}:
            mode = 3
        elif token in {"四", "四麻"}:
            mode = 4
        elif token in {"金", "金间", "金之间"}:
            room = "金"
        elif token in {"玉", "玉间", "玉之间"}:
            room = "玉"
        elif token in {"王", "王座", "王座间", "王座之间"}:
            room = "王座"
        elif token in {"文", "文字"}:
            as_text = True
        elif token.removeprefix("近") in {"7天", "30天", "90天", "365天"}:
            days = int(token.removeprefix("近")[:-1])
        elif canonical == "对局" and (
            token.isdigit() and len(token) <= 2 or
            token.startswith("第") and token.endswith("页") and token[1:-1].isdigit()
        ):
            page = int(token[1:-1] if token.startswith("第") else token)
        elif token == "3":
            mode = 3
        elif token == "4":
            mode = 4
        else:
            raise ValueError("页码只适用于 /雀 玩家 局 页码。")
    if not 1 <= page <= 20:
        raise ValueError("对局页码只能是 1～20。")
    return PlayerQuery(canonical, player, mode, room, days, page, as_text)

RATES = {
    "和牌率", "放铳率", "自摸率", "默听率", "流局率", "流听率", "副露率", "立直率",
    "里宝率", "被炸率", "放铳时立直率", "放铳时副露率", "副露后放铳率",
    "副露后和牌率", "副露后流局率", "立直后和牌率", "立直后放铳率",
    "立直后非瞬间放铳率", "立直后流局率", "先制率", "追立率", "被追率",
    "一发率", "振听立直率", "立直多面", "立直好型2",
}

FIELDS = {
    "基本": ["和牌率", "放铳率", "自摸率", "默听率", "流局率", "流听率", "副露率", "立直率", "和了巡数", "平均打点", "平均铳点"],
    "立直": ["立直率", "立直后和牌率", "立直后放铳率", "立直后非瞬间放铳率", "立直收支", "立直收入", "立直支出", "先制率", "追立率", "被追率", "立直巡目", "立直后流局率", "一发率", "振听立直率", "立直多面", "立直好型2"],
    "更多": ["最大连庄", "里宝率", "被炸率", "平均被炸点数", "放铳时立直率", "放铳时副露率", "副露后放铳率", "副露后和牌率", "副露后流局率", "打点效率", "铳点损失", "净打点效率", "局收支"],
    "血统": ["役满", "累计役满", "最大累计番数", "流满", "W立直", "平均起手向听", "平均起手向听亲", "平均起手向听子"],
}

LABELS = {
    "默听率": "默胡率", "立直后和牌率": "立直和了率",
    "立直后放铳率": "立直放铳率（含宣言）",
    "立直后非瞬间放铳率": "立直放铳率（不含宣言）",
    "立直后流局率": "立直流局率", "振听立直率": "振听率",
    "立直好型2": "立直好型率", "W立直": "两立直",
    "平均起手向听": "起手向听", "平均起手向听亲": "亲起手向听",
    "平均起手向听子": "子起手向听",
}


def _number(value, digits=1):
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "--"


def _value(key: str, value) -> str:
    if key in RATES:
        return percent(value)
    if key in {"和了巡数", "立直巡目", "平均起手向听", "平均起手向听亲", "平均起手向听子"}:
        return _number(value, 2)
    if key in {"最大连庄", "役满", "累计役满", "最大累计番数", "流满", "W立直"}:
        return str(int(value))
    return _number(value, 0)


def format_view(
    uid: str, mode: int, section: str, stats: dict, extended: dict | None,
    room: str = "", period: str = "",
) -> str:
    extended = extended or {}
    title = f"🀄 {stats.get('nickname', uid)} · {'四麻' if mode == 4 else '三麻'}{section}"
    if room:
        title += f" · {room if room.endswith('间') else room + '之间'}"
    if period:
        title += f" · {period}"
    lines = [title, f"UID：{uid}｜记录场数：{stats.get('count', 0)}"]
    if section == "基本":
        lines.append(f"段位：{level_text(stats.get('level'))}")
        if stats.get("avg_rank") is not None:
            lines.append(f"平均顺位：{_number(stats['avg_rank'], 3)}")
        if stats.get("negative_rate") is not None:
            lines.append(f"被飞率：{percent(stats['negative_rate'])}")
    elif section == "顺位":
        rates = stats.get("rank_rates") or []
        if rates:
            total = sum(rates)
            lines.extend(f"{i}位：{percent(value / total)}（{int(value)}场）" for i, value in enumerate(rates, 1) if total)
        if stats.get("avg_rank") is not None:
            lines.append(f"平均顺位：{_number(stats['avg_rank'], 3)}")
        return "\n".join(lines) if rates else "没有查到顺位数据。"
    elif section == "和铳":
        wins = [("立直", "立直和了"), ("副露", "副露和了"), ("默听", "默听和了")]
        losses = [("立直", "放铳至立直"), ("副露", "放铳至副露"), ("默听", "放铳至默听")]
        for heading, items in (("和了方式", wins), ("放铳对象", losses)):
            total = sum(extended.get(key, 0) or 0 for _, key in items)
            lines.append(f"【{heading}】")
            lines.extend(f"{label}：{int(extended.get(key, 0) or 0)}（{percent((extended.get(key, 0) or 0) / total)}）" for label, key in items if total)
            if not total:
                lines.append("暂无数据")
        return "\n".join(lines)
    elif section == "大铳":
        item = extended.get("最近大铳")
        if not item:
            return "没有超过满贯的近期大铳记录。"
        lines.append(f"时间：{record_time({'startTime': item.get('start_time')})}")
        lines.extend(f"{fan.get('label', '役种')}：{fan.get('count', 0)}番" for fan in item.get("fans", []))
        if item.get("id"):
            lines.append(f"牌谱：https://game.maj-soul.net/1/?paipu={item['id']}")
        return "\n".join(lines)
    for key in FIELDS.get(section, []):
        if extended.get(key) is not None:
            lines.append(f"{LABELS.get(key, key)}：{_value(key, extended[key])}")
    if extended.get("count") is not None and section in {"基本", "更多"}:
        lines.append(f"统计局数：{extended['count']}")
    return "\n".join(lines) if len(lines) > 2 else "没有查到该栏目的统计数据。"


def _rank(record: dict, uid: str) -> int | None:
    players = record.get("players") or []
    target = next((p for p in players if player_uid(p) == uid), None)
    if not target:
        return None
    ordered = sorted(enumerate(players), key=lambda item: (-float(item[1].get("score", 0) or 0), item[0]))
    return next((i for i, (_, player) in enumerate(ordered, 1) if player is target), None)


def format_trend(uid: str, mode: int, records: list[dict]) -> str:
    if not records:
        return "没有查到最近对局。"
    ranks = [_rank(record, uid) for record in records]
    ranks = [rank for rank in ranks if rank is not None]
    if not ranks:
        return "最近对局缺少玩家顺位信息。"
    counts = Counter(ranks)
    deltas = []
    for record in records:
        player = next((p for p in record.get("players", []) if player_uid(p) == uid), None)
        if player and player.get("gradingScore") is not None:
            deltas.append(player["gradingScore"])
    lines = [f"🀄 UID {uid} · 最近{len(ranks)}场{'四麻' if mode == 4 else '三麻'}趋势",
             "顺位（由旧到新）：" + " → ".join(str(x) for x in reversed(ranks)),
             f"平均顺位：{sum(ranks) / len(ranks):.3f}",
             "顺位分布：" + "｜".join(f"{i}位 {counts[i]}" for i in range(1, mode + 1))]
    if deltas:
        lines.append(f"段位分合计：{sum(deltas):+d}pt")
    return "\n".join(lines)


def format_deskmates(uid: str, mode: int, records: list[dict]) -> str:
    opponents: Counter[tuple[str, str]] = Counter()
    for record in records:
        for player in record.get("players", []):
            other_uid = player_uid(player)
            if other_uid and other_uid != uid:
                opponents[(other_uid, player_name(player))] += 1
    if not opponents:
        return "最近对局没有可用的同桌数据。"
    lines = [f"🀄 UID {uid} · {'四麻' if mode == 4 else '三麻'}最常同桌（近{len(records)}场）"]
    lines.extend(f"{i}. {name}（{other_uid}）{count}场" for i, ((other_uid, name), count) in enumerate(opponents.most_common(10), 1))
    return "\n".join(lines)
