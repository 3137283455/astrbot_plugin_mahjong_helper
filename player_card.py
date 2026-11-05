"""Group-data palette for public player stats, with aligned independent modes."""
from pathlib import Path
from PIL import Image, ImageDraw
from .formatters import level_text, percent
from .koromo_views import FIELDS, LABELS, _value
from .stat_card import _font, _fit

CARD_REVISION = 1
BG = '#101720'
PANEL = '#1B2633'
INK = '#F2F5F8'
MUTED = '#A0B0C2'
TEAL = '#54D6C6'
GOLD = '#FFCA78'
RED = '#FF858B'


def metric_rows(mode, section, stats, ext):
    rows = [('记录场数', f'{int(stats.get("count") or 0):,} 场', TEAL)]
    if section in ('基本', '顺位'):
        rank = stats.get('avg_rank')
        rows.append(('平均顺位', f'{float(rank):.3f}' if rank is not None else '—', TEAL))
        rates = stats.get('rank_rates') or []
        total = sum(rates)
        for i in range(4):
            count = rates[i] if i < len(rates) else None
            value = ('不适用' if i >= mode else
                     f'{percent(count / total)}  /  {int(count):,} 场' if count is not None and total else '—')
            rows.append((f'{i+1}位率 / 次数', value, GOLD if i == 0 else RED if i == mode-1 else TEAL))
        rows.append(('被飞率', percent(stats.get('negative_rate')), RED))
    if section == '和铳':
        for heading, keys in [('和了', ['立直和了', '副露和了', '默听和了']),
                              ('放铳', ['放铳至立直', '放铳至副露', '放铳至默听'])]:
            total = sum(ext.get(k, 0) or 0 for k in keys)
            for key in keys:
                count = ext.get(key)
                value = f'{percent(count / total)}  /  {int(count):,} 局' if count is not None and total else '—'
                rows.append((LABELS.get(key, key), value, RED if heading == '放铳' else TEAL))
    else:
        for key in FIELDS.get(section, []):
            rows.append((LABELS.get(key, key), _value(key, ext[key]) if ext.get(key) is not None else '—',
                         RED if key in ('放铳率', '平均铳点') else TEAL))
    return rows


def render_player_card(path, uid, section, modes, room='', period=''):
    """modes maps 3/4 to stats, extended and an optional independent error."""
    layouts = {mode: metric_rows(mode, section, data['stats'], data.get('extended') or {})
               for mode, data in modes.items() if data.get('stats')}
    row_count = max([len(rows) for rows in layouts.values()] or [8])
    height = max(1090, 650 + row_count * 66)
    im = Image.new('RGB', (1440, height), BG)
    draw = ImageDraw.Draw(im)

    def text(x, y, value, size, width, color=INK, bold=False, anchor=None):
        value = str(value)
        while size > 20 and draw.textlength(value, font=_font(size, bold)) > width:
            size -= 1
        draw.text((x, y), _fit(draw, value, _font(size, bold), width),
                  font=_font(size, bold), fill=color, anchor=anchor)

    draw.rectangle((0, 0, 1440, 258), fill=PANEL)
    text(64, 35, f'雀魂数据  /  {section}统计', 26, 1100, MUTED, True)
    nickname = next((str(d['stats'].get('nickname') or uid) for d in modes.values() if d.get('stats')), str(uid))
    text(64, 90, nickname, 61, 1280, INK, True)
    text(68, 186, f'UID {uid}    /    {room + "之间" if room and not room.endswith("间") else room or "金之间及以上"}    /    {period or "全部公开记录"}',
         27, 1304, MUTED)
    selected = sorted(modes)
    for col, mode in enumerate(selected):
        left = 64 + col * 674 if len(selected) == 2 else 64
        right = left + (638 if len(selected) == 2 else 1312)
        top, bottom = 302, height - 153
        data = modes[mode]
        draw.rounded_rectangle((left, top, right, bottom), radius=23, fill=PANEL)
        draw.rounded_rectangle((left+24, top+28, left+31, top+65), radius=3, fill=TEAL)
        text(left+48, top+22, '三麻' if mode == 3 else '四麻', 36, 150, INK, True)
        if not data.get('stats'):
            text(left+30, top+132, '暂时无法获取' if data.get('error') else '暂无公开战绩', 32, right-left-60, GOLD, True)
            text(left+30, top+200, data.get('error') or '仅收录金之间及以上对局', 24, right-left-60, MUTED)
            continue
        text(right-30, top+40, level_text(data['stats'].get('level')), 24, right-left-215, MUTED, anchor='ra')
        text(left+30, top+103, '指标', 23, 200, MUTED)
        text(right-30, top+103, '数据', 23, 260, MUTED, anchor='ra')
        for i, (label, value, color) in enumerate(layouts[mode]):
            y = top + 166 + i * 66
            draw.line((left+25, y-25, right-25, y-25), fill='#304050', width=1)
            text(left+30, y, label, 25, 270, MUTED, anchor='lm')
            text(right-30, y, value, 29, right-left-340, color, True, 'rm')
        if section in ('基本', '立直', '更多', '血统') and not data.get('extended'):
            text(left+30, bottom-40, '扩展指标暂缺，基础战绩仍可查看', 22, right-left-60, GOLD)
    text(64, height-115, '来源：牌谱屋公开数据；三麻 / 四麻分别统计，仅收录金之间及以上对局。', 24, 1312, MUTED)
    text(64, height-72, '筛选参数可换序：三 / 四、金 / 玉 / 王、30天、文。未填模式时并列展示。', 23, 1312, MUTED)
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, 'PNG')
    return path
