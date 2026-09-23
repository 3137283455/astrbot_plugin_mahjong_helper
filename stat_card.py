"""Readable PNG cards for the public Amae-Koromo statistics views."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .formatters import level_text, percent
from .koromo_views import FIELDS, LABELS, _value


CARD_SECTIONS = {"基本", "顺位", "立直", "更多", "和铳", "血统"}
WIDTH = 1400
INK = "#18313D"
MUTED = "#667B83"
TEAL = "#167F76"
GOLD = "#D8963F"
RED = "#C86155"
PAPER = "#F3F6F4"
WHITE = "#FFFFFF"


def _font(size: int, bold: bool = False):
    candidates = [
        (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), 2),
        (Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"), 0),
        (Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"), 0),
    ]
    for path, index in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size, index=index)
    raise RuntimeError("未找到支持中文的字体")


def _fit(draw, value: str, font, max_width: int) -> str:
    if draw.textlength(value, font=font) <= max_width:
        return value
    while value and draw.textlength(value + "…", font=font) > max_width:
        value = value[:-1]
    return value + "…"


def _head(draw, nickname: str, uid: str, mode: int, section: str, room: str, period: str):
    draw.rounded_rectangle((46, 42, WIDTH - 46, 252), radius=38, fill=INK)
    draw.rounded_rectangle((89, 84, 179, 174), radius=22, fill=TEAL)
    draw.text((107, 96), "麻", font=_font(52, True), fill=WHITE)
    draw.text((216, 79), _fit(draw, nickname, _font(61, True), 950), font=_font(61, True), fill=WHITE)
    subtitle = f"{'四麻' if mode == 4 else '三麻'} · {section}"
    if room:
        subtitle += f" · {room if room.endswith('间') else room + '之间'}"
    if period:
        subtitle += f" · {period}"
    draw.text((217, 172), _fit(draw, subtitle, _font(31), 1050), font=_font(31), fill="#C4D9D7")
    draw.text((91, 276), f"UID {uid}", font=_font(27), fill=MUTED)


def _metric(draw, x: int, y: int, width: int, label: str, value: str, color: str):
    draw.rounded_rectangle((x, y, x + width, y + 172), radius=28, fill=WHITE)
    draw.rectangle((x + 29, y + 32, x + 37, y + 71), fill=color)
    draw.text((x + 56, y + 34), label, font=_font(28), fill=MUTED)
    draw.text((x + 33, y + 93), _fit(draw, value, _font(48, True), width - 63),
              font=_font(48, True), fill=color)


def _highlights(mode: int, section: str, stats: dict, ext: dict):
    rank = str(stats.get("avg_rank", "--"))
    try:
        rank = f"{float(rank):.3f}"
    except ValueError:
        rank = "--"
    rates = stats.get("rank_rates") or []
    total = sum(rates)
    first = percent(rates[0] / total) if rates and total else "--"
    last = percent(rates[-1] / total) if rates and total else "--"
    presets = {
        "基本": [("平均顺位", rank, TEAL), ("和牌率", percent(ext.get("和牌率")), GOLD),
               ("放铳率", percent(ext.get("放铳率")), RED)],
        "顺位": [("平均顺位", rank, TEAL), ("一位率", first, GOLD),
               (f"{mode}位率", last, RED)],
        "立直": [("立直率", percent(ext.get("立直率")), TEAL),
               ("立直和了率", percent(ext.get("立直后和牌率")), GOLD),
               ("立直收支", _value("立直收支", ext.get("立直收支")), RED)],
        "更多": [("副露率", percent(ext.get("副露率")), TEAL),
               ("净打点效率", _value("净打点效率", ext.get("净打点效率")), GOLD),
               ("局收支", _value("局收支", ext.get("局收支")), RED)],
        "血统": [("役满", _value("役满", ext.get("役满") or 0), TEAL),
               ("最大累计番数", _value("最大累计番数", ext.get("最大累计番数") or 0), GOLD),
               ("起手向听", _value("平均起手向听", ext.get("平均起手向听")), RED)],
        "和铳": [("立直和了", str(int(ext.get("立直和了") or 0)), TEAL),
               ("副露和了", str(int(ext.get("副露和了") or 0)), GOLD),
               ("放铳率", percent(ext.get("放铳率")), RED)],
    }
    return presets[section]


def _bar(draw, x: int, y: int, label: str, count: int, rate: float, color: str,
         unit: str = "场"):
    draw.text((x, y), label, font=_font(32, True), fill=INK)
    draw.text((x + 790, y), f"{percent(rate)}  ·  {count}{unit}", font=_font(31, True), fill=color)
    draw.rounded_rectangle((x, y + 55, x + 1165, y + 82), radius=14, fill="#E4EBE8")
    if rate > 0:
        draw.rounded_rectangle((x, y + 55, x + max(27, int(1165 * rate)), y + 82),
                               radius=14, fill=color)


def _draw_rank(draw, stats: dict, y: int):
    rates = stats.get("rank_rates") or []
    total = sum(rates)
    if not total:
        draw.text((91, y), "暂无顺位数据", font=_font(34), fill=MUTED)
        return
    colors = [TEAL, GOLD, "#6E93B2", RED]
    for i, count in enumerate(rates):
        _bar(draw, 92, y + i * 125, f"第{i + 1}位", int(count), count / total, colors[i])


def _draw_distribution(draw, ext: dict, y: int):
    groups = [
        ("和了方式", [("立直", "立直和了"), ("副露", "副露和了"), ("默听", "默听和了")]),
        ("放铳对象", [("立直", "放铳至立直"), ("副露", "放铳至副露"),
                  ("默听", "放铳至默听")]),
    ]
    colors = [TEAL, GOLD, RED]
    for heading, entries in groups:
        draw.text((92, y), heading, font=_font(35, True), fill=INK)
        y += 65
        total = sum(ext.get(key, 0) or 0 for _, key in entries)
        for i, (label, key) in enumerate(entries):
            count = int(ext.get(key, 0) or 0)
            _bar(draw, 92, y + i * 113, label, count, count / total if total else 0,
                 colors[i], "局")
        y += 390


def _draw_fields(draw, section: str, stats: dict, ext: dict, y: int):
    excluded = {
        "基本": {"和牌率", "放铳率"},
        "立直": {"立直率", "立直后和牌率", "立直收支"},
        "更多": {"副露率", "净打点效率", "局收支"},
        "血统": {"役满", "最大累计番数", "平均起手向听"},
    }.get(section, set())
    items = [(LABELS.get(key, key), _value(key, ext[key]))
             for key in FIELDS[section] if key not in excluded and ext.get(key) is not None]
    if section == "基本":
        if stats.get("negative_rate") is not None:
            items.append(("被飞率", percent(stats["negative_rate"])))
    for i, (label, value) in enumerate(items):
        row, col = divmod(i, 2)
        x = 92 + col * 615
        top = y + row * 110
        draw.rounded_rectangle((x, top, x + 594, top + 91), radius=20, fill=WHITE)
        draw.text((x + 24, top + 25), _fit(draw, label, _font(27), 335),
                  font=_font(27), fill=MUTED)
        draw.text((x + 365, top + 20), _fit(draw, value, _font(31, True), 205),
                  font=_font(31, True), fill=INK)


def render_stats_card(
    path: Path, uid: str, mode: int, section: str, stats: dict,
    extended: dict | None = None, room: str = "", period: str = "",
) -> Path:
    if section not in CARD_SECTIONS:
        raise ValueError(f"不支持卡片栏目：{section}")
    ext = extended or {}
    if section == "顺位":
        body_height = max(460, len(stats.get("rank_rates") or []) * 125)
    elif section == "和铳":
        body_height = 900
    else:
        excluded = {
            "基本": 2, "立直": 3, "更多": 3, "血统": 3,
        }[section]
        count = sum(ext.get(key) is not None for key in FIELDS[section]) - excluded
        count += 1 if section == "基本" else 0
        body_height = max(300, ((max(count, 0) + 1) // 2) * 110)
    height = 625 + body_height + 100
    image = Image.new("RGB", (WIDTH, height), PAPER)
    draw = ImageDraw.Draw(image)
    _head(draw, str(stats.get("nickname") or uid), uid, mode, section, room, period)
    draw.text((365, 276), _fit(draw, level_text(stats.get("level")), _font(27), 560),
              font=_font(27), fill=MUTED)
    draw.text((WIDTH - 380, 276), f"{stats.get('count', 0)} 场记录",
              font=_font(27), fill=MUTED)
    metrics = _highlights(mode, section, stats, ext)
    for i, (label, value, color) in enumerate(metrics):
        _metric(draw, 76 + i * 424, 330, 402, label, value, color)
    draw.text((92, 542), "详细数据", font=_font(35, True), fill=INK)
    draw.line((92, 603, 1308, 603), fill="#DCE6E2", width=3)
    if section == "顺位":
        _draw_rank(draw, stats, 645)
    elif section == "和铳":
        _draw_distribution(draw, ext, 643)
    else:
        _draw_fields(draw, section, stats, ext, 637)
    draw.text((92, height - 72), "数据来源：牌谱屋 · 统计结果以当前筛选为准",
              font=_font(25), fill=MUTED)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", optimize=True)
    return path
