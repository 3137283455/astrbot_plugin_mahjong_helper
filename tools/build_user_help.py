"""Build the single-image help card sent for /help and /帮助."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_mahjong_helper.stat_card import _font  # noqa: E402


WIDTH = 1800
MARGIN = 54
GAP = 30
COL = (WIDTH - 2 * MARGIN - GAP) // 2
INK = "#18313D"
MUTED = "#60767C"
TEAL = "#167F76"
GOLD = "#D8963F"
RED = "#BD544B"
PAPER = "#F3F6F4"
WHITE = "#FFFFFF"

TEXT_APIS = [
    "安慰", "嘲讽", "倒数日", "电影票房", "毒鸡汤", "发病", "高铁大屏", "号码归属地",
    "讲个笑话", "讲讲爱情", "讲讲摆烂", "讲讲古诗", "讲讲人生", "讲讲伤感",
    "讲讲舔狗", "讲讲温柔", "讲讲英汉", "金铲铲公告", "垃圾分类", "来点文案",
    "来句情话", "来句骚话", "来句诗", "来篇文章", "来碗鸡汤", "起个网名", "弱智吧",
    "三角洲密码", "挑战古诗词", "五言藏头诗", "显卡排行榜", "刑法", "中草药", "KFC",
    "qq估价", "QQ签名",
]
IMAGE_APIS = [
    "电脑壁纸", "读世界", "短剧搜索", "风景壁纸", "高清壁纸", "今日老婆", "今日运势",
    "看看腹肌", "看看妞", "看看腿", "坤", "来份早报", "来个头像", "龙图", "日历",
    "生成二维码", "搜表情", "搜菜谱", "搜图", "随机上色", "原神黄历", "AI资讯", "bing图",
]
VIDEO_APIS = [
    "看看漫画", "看看女大", "看看骚的", "看看色色", "看看帅哥", "看看甜妹",
    "看看小姐姐", "看看玉足", "看看治愈", "看看emo",
]
AUDIO_APIS = ["哈基米音乐", "鸡叫", "绿茶语音", "每日听力", "逆天语音", "王者语音", "御姐语音"]


def _rounded(draw: ImageDraw.ImageDraw, box, fill=WHITE, radius=24, outline=None):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=2 if outline else 1)


def _fit(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _panel(draw, x, y, width, height, title, color=TEAL):
    _rounded(draw, (x, y, x + width, y + height), WHITE)
    draw.rounded_rectangle((x + 22, y + 24, x + 32, y + 64), radius=5, fill=color)
    draw.text((x + 50, y + 18), title, font=_font(34, True), fill=INK)


def _bullet_lines(draw, x, y, width, lines, size=26, line_h=43, color=INK, bullet=TEAL):
    font = _font(size)
    for i, line in enumerate(lines):
        yy = y + i * line_h
        draw.rounded_rectangle((x, yy + 12, x + 9, yy + 21), radius=4, fill=bullet)
        draw.text((x + 20, yy), _fit(draw, line, font, width - 22), font=font, fill=color)


def _api_card(draw, x, y, width, title, items, color, two_columns=True, font_size=24, line_h=36):
    draw.text((x, y), title, font=_font(30, True), fill=color)
    y += 47
    if two_columns:
        middle = (len(items) + 1) // 2
        columns = [items[:middle], items[middle:]]
        col_w = width // 2
        for col_i, values in enumerate(columns):
            xx = x + col_i * col_w
            for i, item in enumerate(values):
                draw.rounded_rectangle((xx, y + i * line_h + 10, xx + 7, y + i * line_h + 25), radius=3, fill=color)
                draw.text((xx + 17, y + i * line_h), _fit(draw, item, _font(font_size), col_w - 24),
                          font=_font(font_size), fill=INK)
        return y + max(map(len, columns)) * line_h
    for i, item in enumerate(items):
        draw.rounded_rectangle((x, y + i * line_h + 10, x + 7, y + i * line_h + 25), radius=3, fill=color)
        draw.text((x + 17, y + i * line_h), _fit(draw, item, _font(font_size), width - 24),
                  font=_font(font_size), fill=INK)
    return y + len(items) * line_h


def render(path: Path) -> Path:
    height = 2720
    image = Image.new("RGB", (WIDTH, height), PAPER)
    draw = ImageDraw.Draw(image)

    _rounded(draw, (MARGIN, 38, WIDTH - MARGIN, 206), INK, 34)
    draw.text((92, 68), "麻将助手 · 一图使用说明", font=_font(56, True), fill=WHITE)
    draw.text((96, 145), "日麻练习  ·  雀魂数据  ·  B站点歌  ·  娱乐 API", font=_font(30), fill="#C9DDDA")

    _rounded(draw, (MARGIN, 228, WIDTH - MARGIN, 362), "#E3EFEC", 28)
    draw.text((84, 248), "快速开始", font=_font(30, True), fill=TEAL)
    quick = [("雀魂菜单", "/雀"), ("全部帮助", "/help"), ("何切出题", "/何切"), ("点歌菜单", "/歌单"), ("娱乐菜单", "/娱乐")]
    pill_w = 310
    for i, (label, command) in enumerate(quick):
        x = 78 + i * 330
        _rounded(draw, (x, 292, x + pill_w, 344), WHITE, 18)
        draw.text((x + 14, 300), f"{label}  {command}", font=_font(24, True), fill=INK)

    y = 390
    left_x, right_x = MARGIN, MARGIN + COL + GAP
    h = 390
    _panel(draw, left_x, y, COL, h, "何切练习 · 601 道题")
    _bullet_lines(draw, left_x + 32, y + 84, COL - 64, [
        "/何切：随机出题，每轮不重复",
        "/何切 123：查看指定题",
        "/何切答案：查看当前题原书答案",
        "/何切状态：查看本轮进度",
        "群友讨论答案，机器人不会自动判题",
    ], size=25, line_h=48)

    _panel(draw, right_x, y, COL, h, "雀魂战绩与查询", RED)
    _rounded(draw, (right_x + 26, y + 78, right_x + COL - 26, y + 144), "#FBE9E6", 16)
    draw.text((right_x + 44, y + 94), "数据范围：牌谱屋只收录金之间及以上公开场次", font=_font(24, True), fill=RED)
    draw.text((right_x + 44, y + 124), "金之间以下或没有公开记录的玩家可能查不到。", font=_font(22), fill=INK)
    _bullet_lines(draw, right_x + 32, y + 164, COL - 64, [
        "/雀 搜 昵称 → /雀 绑 UID；/雀 号查看绑定",
        "/雀 基 顺 立 风 和 运：统计卡片；/雀 铳：最近大铳",
        "/雀 近：趋势；/雀 桌：同桌；/雀 局：对局；/雀 页：网页",
        "筛选可加：三/四、金/玉/王座、7天/30天/90天/365天",
        "/友 房间号或链接：群内 @全体广播友人房",
    ], size=21, line_h=47, bullet=RED)

    y += h + 24
    h = 260
    _panel(draw, left_x, y, COL, h, "B站点歌与歌单")
    _bullet_lines(draw, left_x + 32, y + 84, COL - 64, [
        "搜索歌曲 晴天 / 搜索视频 BV号：列出候选",
        "发送 1 音频：播放第 1 首；发送 取消：退出选歌",
        "/歌单 加 1：收藏；/歌单 播 1：下次直接播放",
        "/歌单 删 1：删除；/歌单：查看列表",
    ], size=22, line_h=42)

    _panel(draw, right_x, y, COL, h, "娱乐 API 怎么用", GOLD)
    _bullet_lines(draw, right_x + 32, y + 84, COL - 64, [
        "私聊直接发关键词；群聊必须同一条消息 @机器人",
        "触发词后可空格加参数：搜图 猫、号码归属地 138…",
        "/娱乐：总菜单；/娱乐 文字、图片、视频、语音：分类菜单",
        "第三方项目偶尔失效；部分图片/视频内容请注意场合",
    ], size=22, line_h=42, bullet=GOLD)

    y += h + 24
    draw.text((MARGIN + 4, y), "娱乐项目速查（当前菜单）", font=_font(38, True), fill=INK)
    y += 60
    api_top = y
    api_h = 1230
    _panel(draw, left_x, api_top, COL, api_h, "文字类 · 36 项", TEAL)
    _panel(draw, right_x, api_top, COL, api_h, "图片 / 视频 / 语音 · 40 项", GOLD)

    lx = left_x + 34
    lw = COL - 68
    _api_card(draw, lx, api_top + 82, lw, "文字与查询", TEXT_APIS, TEAL, two_columns=True, font_size=22, line_h=43)
    draw.text((lx, api_top + 1054), "别名：疯狂星期四 / 肯德基 / v我50 → KFC", font=_font(19), fill=MUTED)
    draw.text((lx, api_top + 1084), "小黑子 → 鸡叫；御姐撒娇 → 御姐语音", font=_font(19), fill=MUTED)

    rx = right_x + 34
    rw = COL - 68
    yy = _api_card(draw, rx, api_top + 82, rw, "图片 · 23 项", IMAGE_APIS, GOLD, two_columns=True, font_size=22, line_h=39)
    yy += 10
    yy = _api_card(draw, rx, yy, rw, "视频 · 10 项", VIDEO_APIS, "#7B63A9", two_columns=True, font_size=21, line_h=36)
    yy += 7
    _api_card(draw, rx, yy, rw, "语音 · 7 项", AUDIO_APIS, RED, two_columns=True, font_size=21, line_h=35)

    footer_y = api_top + api_h + 22
    _rounded(draw, (MARGIN, footer_y, WIDTH - MARGIN, height - 35), INK, 24)
    draw.text((MARGIN + 32, footer_y + 18), "命令忘了就发 /help；查询结果仍受公开数据范围与外部服务状态影响。",
              font=_font(24, True), fill=WHITE)
    draw.text((MARGIN + 32, footer_y + 57), "娱乐关键词不加斜杠；其他斜杠命令可直接复制使用。",
              font=_font(22), fill="#C9DDDA")

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", optimize=True)
    return path


if __name__ == "__main__":
    target = ROOT / "data" / "help_one_page.png"
    render(target)
    print(target)
