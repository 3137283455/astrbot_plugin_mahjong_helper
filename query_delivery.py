"""Send query results without turning a missing QQ receipt into another reply."""
import asyncio
import hashlib
import re
import time
from pathlib import Path
from urllib.parse import unquote

from aiocqhttp.exceptions import ActionFailed
import astrbot.api.message_components as Comp
from astrbot.api import logger


async def history_receipt(event, result, since):
    parts = result.chain
    if not parts or not all(isinstance(p, (Comp.Plain, Comp.Image)) for p in parts):
        return False
    expected = ' '.join(''.join(p.text for p in parts if isinstance(p, Comp.Plain)).split())
    hashes = []
    try:
        for part in parts:
            if isinstance(part, Comp.Image):
                path = str(getattr(part, 'path', '') or getattr(part, 'file', ''))
                if path.startswith('file://'):
                    path = unquote(path[7:])
                hashes.append(await asyncio.to_thread(lambda p=path: hashlib.md5(Path(p).read_bytes()).hexdigest()))
        if event.get_group_id():
            action, params = 'get_group_msg_history', {'group_id': int(event.get_group_id())}
        else:
            action, params = 'get_friend_msg_history', {'user_id': int(event.get_sender_id())}
        data = await asyncio.wait_for(event.bot.call_action(action=action, count=40, **params), 6)
        if isinstance(data, dict) and 'data' in data:
            data = data['data']
        for msg in data.get('messages', []):
            if (str(msg.get('user_id')) != str(event.get_self_id())
                    or int(msg.get('real_seq') or 0) <= 0
                    or float(msg.get('time') or 0) < since - 1):
                continue
            items = msg.get('message', [])
            if not isinstance(items, list):
                continue
            text = ''.join(p.get('data', {}).get('text', '') for p in items if p.get('type') == 'text')
            found = [h.lower() for p in items if p.get('type') == 'image'
                     for h in re.findall(r'(?i)(?<![a-f0-9])[a-f0-9]{32}(?![a-f0-9])', str(p.get('data', {}).get('file', '')))]
            if ' '.join(text.split()) == expected and sorted(found) == sorted(hashes):
                return True
    except (Exception,):
        # A failed history lookup is never evidence that the message arrived.
        pass
    return False


async def send_query_result(event, result):
    image = any(isinstance(p, Comp.Image) for p in result.chain)
    since = time.time()
    for attempt in range(3 if image else 1):
        try:
            await event.send(result)
            return True
        except (ActionFailed, TimeoutError) as exc:
            logger.warning('雀魂查询QQ回执未确认 attempt=%s type=%s', attempt + 1, type(exc).__name__)
            await asyncio.sleep((2, 4, 2)[attempt])
            if await history_receipt(event, result, since):
                return True
    event.set_extra('mahjong_query_delivery_error', True)
    return False
