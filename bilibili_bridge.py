"""Reuse the installed Bilibili plugin without routing subscriptions to APIs."""
from __future__ import annotations

import copy
import re
from pathlib import Path
from types import SimpleNamespace

import astrbot.api.message_components as Comp

BILIBILI_INSTRUCTIONS = """【B站订阅】
用户要求订阅/取消订阅 B站 UP主、查看订阅列表、查看最新动态或询问 B站推送帮助时，调用 bilibili_subscription。
这不是娱乐 API，不使用 query_available_apis 或 call_api_by_name 查找订阅功能；听歌、搜索或播放视频仍使用已有音乐/媒体工具。
action: add订阅，remove取消，list订阅列表，latest重发最新可见动态（可重复），status登录与监听状态，menu完整帮助。
uid 使用用户给出的 B站数字 UID 或个人主页链接，不把昵称、QQ号当成 UID，也不自行猜测 UID；不明确时询问。
filters 是排除项：lottery不看抽奖，forward不看转发，live不看直播，live_end不看下播，video不看投稿，draw不看图文，article不看专栏，forward_lottery不看转发抽奖。
仅在用户明确要求时添加 at_all/live_atall，权限由原插件核验。登录必须由管理员私聊 /bili_login 扫码，不让用户发 Cookie 或密钥。
只操作当前聊天的订阅，不代操作其他群；工具会发送真实结果，不再编造成功提示或重复调用娱乐 API。
"""

BILIBILI_HELP = """📺 B站订阅帮助
快捷命令（参数放后面）：
/B站 订阅 UID [排除项]
/B站 取消 UID
/B站 列表
/B站 状态
/B站 测试 UID（重发最新可见动态，保留内容过滤）

原插件完整命令：
/订阅动态 UID [过滤器...] = /bili_sub
/订阅列表 = /bili_sub_list
/订阅删除 UID = /bili_sub_del
/订阅测试 UID = /bili_sub_test（原版仍按未推送记录筛选；重发请用 /B站 测试）
/bili_login（管理员私聊扫码）
/bili_logout（管理员，退出B站登录）
/卡片样式 [dark_news|template_1|template_2|simple] = /bili_card_style（管理员）
/全局订阅 「UMO」 UID [过滤器...] = /bili_global_sub（管理员）
/全局删除 「UMO」 = /bili_global_del（管理员，清空指定聊天订阅）
/全局列表 = /bili_global_list（管理员）

排除项：forward转发、lottery抽奖、video投稿、article专栏、draw图文、live直播、live_end下播、forward_lottery转发抽奖；其他词按原插件正则过滤。
提醒：at_sub只@我；unat_sub取消@我；at_all动态和开播@全体；live_atall仅开播@全体（需权限）。
例：/B站 订阅 123456 lottery forward
默认约5分钟检查一次；首次使用须先登录。群内订阅在该群接收，私聊订阅在私聊接收。
也可私聊或@慧慧说“帮我订阅B站UID 123456，不看抽奖和转发”。"""


def bilibili_instance(context):
    getter = getattr(context, "get_registered_star", None)
    metadata = getter("astrbot_plugin_bilibili") if getter else None
    instance = metadata.star_cls if metadata is not None and metadata.activated else None
    return instance if callable(getattr(instance, "dynamic_sub", None)) else None


def normalize_uid(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("请提供 B站数字 UID 或 space.bilibili.com 个人主页链接。")
    value = value.strip()
    match = re.fullmatch(r"https?://space\.bilibili\.com/([0-9]{1,19})/?(?:[?#][^\s]*)?", value)
    uid = match.group(1) if match else value
    if not re.fullmatch(r"[0-9]{1,19}", uid) or not 0 < int(uid) < 2**63:
        raise ValueError("请提供 B站数字 UID 或 space.bilibili.com 个人主页链接；昵称不能代替 UID。")
    return str(int(uid))


def has_login(instance) -> bool:
    credential = getattr(getattr(instance, "bili_client", None), "credential", None)
    return bool(credential and getattr(credential, "sessdata", None))


async def run_bilibili_action(context, event, action: str, uid: str = "", filters: str = ""):
    if action == "menu":
        await event.send(event.plain_result(BILIBILI_HELP))
        return
    if action not in {"add", "remove", "list", "latest", "status"}:
        await event.send(event.plain_result("可用操作：订阅、取消、列表、测试、状态、帮助。发送 /B站 查看完整说明。"))
        return
    instance = bilibili_instance(context)
    if instance is None:
        await event.send(event.plain_result("B站订阅插件尚未启用，请稍后重试。"))
        return
    if action == "status":
        task = getattr(instance, "dynamic_listener_task", None)
        subscriptions = instance.data_manager.get_subscriptions_by_user(event.unified_msg_origin) or []
        login = "已配置登录凭据" if has_login(instance) else "尚未登录；请管理员私聊 /bili_login 扫码"
        polling = "运行中" if task is not None and not task.done() else "未运行"
        await event.send(event.plain_result(f"B站订阅状态\n登录：{login}\n监听任务：{polling}\n当前聊天订阅：{len(subscriptions)} 个UP主"))
        return
    if action in {"add", "remove", "latest"}:
        try:
            uid = normalize_uid(uid)
        except ValueError as exc:
            await event.send(event.plain_result(str(exc)))
            return
    if action in {"add", "latest"} and not has_login(instance):
        await event.send(event.plain_result("B站尚未登录，暂时无法订阅或获取动态。请管理员私聊机器人发送 /bili_login，用B站App扫码后再试；无需提供 API Key。"))
        return
    if action == "add":
        if not isinstance(filters, str) or len(filters) > 300:
            await event.send(event.plain_result("过滤参数过长，请发送 /B站 查看用法。"))
            return
        result = await instance.dynamic_sub(event, uid, filters.strip())
    elif action == "remove":
        result = await instance.sub_del(event, uid)
    elif action == "list":
        result = await instance.sub_list(event)
    else:
        await resend_latest(instance, event, uid)
        return
    # The original plugin either returns a result, or sends its card itself.
    if result is not None:
        await event.send(result)


async def resend_latest(instance, event, uid: str):
    """Preview the latest visible entry without changing the polling cursor."""
    saved = instance.data_manager.get_subscription(event.unified_msg_origin, int(uid))
    preview = copy.deepcopy(saved) if saved is not None else SimpleNamespace(
        uid=int(uid), filter_types=[], filter_regex=[])
    preview.last = ""
    preview.recent_ids = []
    dynamic = await instance.bili_client.get_latest_dynamics(int(uid))
    if not dynamic:
        await event.send(event.plain_result("未获取到B站动态，请检查登录状态后重试。"))
        return
    candidates = instance.dynamic_listener._parse_and_filter_dynamics(dynamic, preview)
    payload = next((item.payload for item in candidates or [] if item.has_payload()), None)
    if payload is None:
        await event.send(event.plain_result("最近可见动态均被当前内容过滤规则排除，或动态类型暂不支持。发送 /B站 列表 查看过滤条件。"))
        return
    image = await instance.renderer.render_dynamic(payload) if instance.rai else None
    if image:
        from PIL import Image
        image_path = Path(image)
        with Image.open(image_path) as picture:
            as_file = picture.height > 25000 or image_path.stat().st_size > 10 * 1024 * 1024
        component = (Comp.File(file=str(image_path), name="bilibili_latest" + image_path.suffix)
                     if as_file else Comp.Image.fromFileSystem(str(image_path)))
        chain = [component]
        if payload.url:
            chain.append(Comp.Plain("\n" + payload.url))
    else:
        chain = instance.dynamic_listener._compose_plain_push(payload, render_fail=bool(instance.rai))
    # Manual previews reply directly. Background deduplication, silent recovery
    # and @ notifications belong to automatic pushes, not this reply.
    make_notice = getattr(instance.renderer, "notification_text", None)
    if callable(make_notice):
        notice = make_notice(payload)
        if notice:
            chain.insert(0, Comp.Plain(notice + "\n"))
    await event.send(event.chain_result(chain))
