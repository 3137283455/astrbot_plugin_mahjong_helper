"""Compact presentation data for the dark Bilibili notification template."""
from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import re


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.hidden += 1
        if tag in {'br', 'p', 'div', 'li'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'} and self.hidden:
            self.hidden -= 1
        if tag in {'p', 'div', 'li'}:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value):
    parser = _PlainText()
    parser.feed(str(value or ''))
    return '\n'.join(filter(None, (re.sub(r'\s+', ' ', line).strip() for line in unescape(''.join(parser.parts)).splitlines())))


def _image_url(value):
    value = str(value or '').strip()
    if re.match(r'^https?://', value, re.I) or re.match(r'^data:image/(png|jpeg|webp|gif);base64,', value, re.I):
        return value
    return ''


def _chapter_number(value):
    if value.isascii() and value.isdecimal():
        return str(int(value))
    digits = dict(zip('零一二三四五六七八九两', [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2]))
    units = {'十': 10, '百': 100, '千': 1000}
    if not any(c in units for c in value):
        return ''.join(str(digits[c]) for c in value)
    total = number = 0
    for c in value:
        if c in digits:
            number = digits[c]
        else:
            total += (number or 1) * units[c]
            number = 0
    return str(total + number)


def dark_context(payload):
    body = plain_text(getattr(payload, 'text', '') or getattr(payload, 'summary', ''))
    lines = [line.strip('☆★✦ ') for line in body.splitlines()
             if line and not re.fullmatch(r'(?:#[^#]+#\s*)+', line)]
    title = plain_text(getattr(payload, 'title', ''))
    kind = str(getattr(payload, 'type', '') or '')
    url = str(getattr(payload, 'url', '') or '')
    name = plain_text(getattr(payload, 'name', '')) or '未知UP主'
    label = {'DYNAMIC_TYPE_AV': '视频投稿', 'DYNAMIC_TYPE_DRAW': '图文动态',
             'DYNAMIC_TYPE_WORD': '文字动态', 'DYNAMIC_TYPE_ARTICLE': '专栏更新',
             'DYNAMIC_TYPE_FORWARD': '转发动态'}.get(kind, '动态更新')
    tag = '新动态'
    subtitle = ''
    if 'live.bilibili.com/' in url:
        label = '直播提醒'
        tag = '下播提醒' if '下播了' in body else '开播提醒'
        owner = re.search(r'UP\s*[「“]([^」”]+)[」”]', body)
        if owner:
            name = owner.group(1)
        title = title or tag
    elif '订阅成功' in body:
        tag = label = '订阅成功'
        title = title or '订阅已开启'
    else:
        title = title or (lines[0] if lines else '发布了新动态')
        comic = re.search(r'漫画第([0-9零一二三四五六七八九十百千两]+)话', title)
        if comic:
            title, subtitle = '漫画更新', '第' + _chapter_number(comic.group(1)) + '话'
            lines = [line for line in lines if not re.search(r'漫画第.+话', line)]
    excerpt = next((line for line in lines if line != title and not line.startswith(('作者', 'UP 主', 'UP主'))), '')
    images = [_image_url(u) for u in getattr(payload, 'image_urls', []) or []]
    images = [u for u in images if u]
    forward = getattr(payload, 'forward', None)
    if not images and forward:
        images = [u for u in (_image_url(u) for u in forward.image_urls or []) if u]
    return {
        'news_name': name[:80], 'news_avatar': _image_url(getattr(payload, 'avatar', '')),
        'news_title': title[:100], 'news_subtitle': subtitle, 'news_excerpt': excerpt[:100],
        'news_label': label, 'news_tag': tag,
        'news_cover': images[0] if images else '', 'news_count': len(images),
    }


def notification_text(payload, category='dynamic'):
    data = dark_context(payload)
    title = data['news_tag'] if category == 'live' else 'B站动态更新'
    return '🔔 ' + title + '\n' + data['news_name']
