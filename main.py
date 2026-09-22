from __future__ import annotations

import asyncio
import re
import subprocess
import sys
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

from .database import MahjongDatabase
from .formatters import (
    format_record,
    format_records,
    format_search,
    format_stats,
    player_name,
    player_uid,
    record_uuid,
    room_modes,
)
from .majsoul_api import KoromoClient, MajsoulApiError, ProtocolClient, extract_paipu_id
from .nanikiru_core import Question, QuestionStore, StateStore
from .review_gateway import ReviewGateway


TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
UID_RE = re.compile(r"^\d{5,12}$")


class MahjongHelperPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self.questions: QuestionStore | None = None
        self.state: StateStore | None = None
        self.db: MahjongDatabase | None = None
        self.koromo: KoromoClient | None = None
        self.protocol: ProtocolClient | None = None
        self.review_gateway: ReviewGateway | None = None
        self._lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        self._protocol_process: asyncio.subprocess.Process | None = None
        self.plugin_dir = Path(__file__).resolve().parent
        self.state_dir: Path | None = None
        self.timezone = ZoneInfo(self.config.get("timezone", "Asia/Shanghai"))

    async def initialize(self):
        self.questions = QuestionStore(self.plugin_dir / "data")
        state_dir = self.config.get("state_dir") or "data/plugin_data/astrbot_plugin_mahjong_helper"
        self.state_dir = Path(state_dir)
        database_path = self.state_dir / "mahjong_helper.db"
        self.state = StateStore(database_path, self.questions.by_id)
        self.db = MahjongDatabase(database_path)
        self.koromo = KoromoClient(self._koromo_token)
        self.protocol = ProtocolClient(
            self.config.get("protocol_base_url", "http://127.0.0.1:5088"),
            self.config.get("protocol_api_key", ""),
        )
        self.review_gateway = ReviewGateway(
            self.config.get("review_gateway_url", ""),
            self.config.get("review_gateway_token", ""),
        )
        self._tasks = [
            asyncio.create_task(self._question_scheduler()),
            asyncio.create_task(self._subscription_scheduler()),
        ]
        if self.config.get("protocol_auto_launch", True):
            self._tasks.append(asyncio.create_task(self._auto_start_protocol()))
        logger.info("日麻助手已加载，共 %d 道何切题", len(self.questions.questions))

    async def terminate(self):
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError):
                await task
        if self._protocol_process and self._protocol_process.returncode is None:
            self._protocol_process.terminate()
            try:
                await asyncio.wait_for(self._protocol_process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._protocol_process.kill()
                await self._protocol_process.wait()

    def _ready(self) -> tuple[QuestionStore, StateStore, MahjongDatabase, KoromoClient]:
        if not all((self.questions, self.state, self.db, self.koromo)):
            raise RuntimeError("日麻助手尚未初始化")
        return self.questions, self.state, self.db, self.koromo

    def _koromo_token(self) -> str | None:
        configured = str(self.config.get("koromo_token", "") or "").strip()
        if configured:
            return configured
        return self.db.get_secret("koromo_token") if self.db else None

    @staticmethod
    def _source(question: Question) -> str:
        return f"《{question.book}》第{question.book_question_id}题"

    @staticmethod
    def _actor_id(event: AstrMessageEvent) -> str:
        return str(event.get_sender_id())

    @staticmethod
    def _admin_error(event: AstrMessageEvent) -> str | None:
        return None if event.role == "admin" else "只有 AstrBot 管理员可以执行此操作。"

    @staticmethod
    def _error_text(exc: Exception) -> str:
        if isinstance(exc, MajsoulApiError):
            return str(exc)
        logger.exception("日麻助手命令执行失败")
        return f"操作失败：{exc}"

    @staticmethod
    def _help_text(event: AstrMessageEvent) -> str:
        lines = [
            "🀄 日麻助手使用帮助",
            "内置 601 道何切题；玩家查询、战绩和订阅使用牌谱屋公开数据，无需 Token。",
            "",
            "【何切练习】",
            "/何切 [题号]｜随机出题或查看指定题",
            "/何切答案 [题号]｜查看原书答案",
            "/何切状态｜查看当前题和本轮进度",
            "",
            "【雀魂玩家】",
            "/雀魂搜索 玩家名｜搜索 UID",
            "/雀魂绑定 UID｜绑定自己的账号",
            "/雀魂我的绑定｜查看绑定",
            "/雀魂切换 UID｜切换主账号",
            "/雀魂解绑 [UID]｜解除绑定",
            "",
            "【战绩与对局】",
            "/雀魂查询 [玩家] [金|玉|王座]｜四麻战绩",
            "/查询三麻 [玩家] [金|玉|王座]｜三麻战绩",
            "/雀魂对局 [玩家] [金|玉|王座]｜最近四麻",
            "/三麻对局 [玩家] [金|玉|王座]｜最近三麻",
            "不填写玩家时使用自己的主绑定账号。",
            "",
            "【其他功能】",
            "/雀魂订阅状态、/三麻订阅状态｜查看当前会话订阅",
            "/雀魂API状态｜检查本地雀魂服务",
            "/牌谱Review 牌谱链接 [座位]｜可选牌谱分析",
            "/雀魂场况 牌谱链接 局数 [巡目]｜可选场况生成",
        ]
        if event.role == "admin":
            lines.extend(
                [
                    "",
                    "【管理员功能】",
                    "/何切自动 开启 HH:MM｜当前会话每日出题",
                    "/何切自动 关闭、/何切重置",
                    "/雀魂订阅 玩家、/三麻订阅 玩家｜管理自动播报",
                    "/开启雀魂订阅、/关闭雀魂订阅、/删除雀魂订阅",
                    "/设置牌谱屋Token TOKEN｜可选，必须私聊",
                    "/雀魂登录 账号 密码｜必须私聊",
                    "/雀魂取谱测试 牌谱链接",
                ]
            )
        else:
            lines.extend(["", "每日出题、订阅管理、可选 Token 与登录功能仅限管理员。"])
        lines.extend(["", "再次查看：/help 或 /雀魂帮助"])
        return "\n".join(lines)

    def _question_chain(self, event: AstrMessageEvent, question: Question, prefix="🀄 何切"):
        questions, _, _, _ = self._ready()
        return event.chain_result(
            [
                Comp.Plain(f"{prefix} #{question.global_id}\n{self._source(question)}"),
                Comp.Image.fromFileSystem(
                    str(self._enhanced_image_path(questions.image_path(question.question_image)))
                ),
                Comp.Plain("想好后使用：/何切答案"),
            ]
        )

    def _answer_chain(self, event: AstrMessageEvent, question: Question):
        questions, _, _, _ = self._ready()
        return event.chain_result(
            [
                Comp.Plain(f"✅ 何切 #{question.global_id} 原书答案\n{self._source(question)}"),
                Comp.Image.fromFileSystem(
                    str(self._enhanced_image_path(questions.image_path(question.answer_image)))
                ),
                Comp.Plain("继续随机：/何切　指定题号：/何切 123"),
            ]
        )

    def _enhanced_image_path(self, source: Path) -> Path:
        """放大并锐化低清题图，使用 PNG 缓存以减少 QQ 的 JPEG 二次损失。"""
        if not self.config.get("enhance_images", True) or self.state_dir is None:
            return source
        scale = max(2, min(4, int(self.config.get("image_scale", 2) or 2)))
        cache_dir = self.state_dir / "hd_images"
        target = cache_dir / f"{source.stem}-v1-{scale}x.png"
        try:
            if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
                return target
            from PIL import Image, ImageFilter

            cache_dir.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as original:
                image = original.convert("RGB")
                image = image.resize(
                    (image.width * scale, image.height * scale),
                    Image.Resampling.LANCZOS,
                )
                image = image.filter(
                    ImageFilter.UnsharpMask(radius=1.2, percent=135, threshold=3)
                )
                image.save(target, "PNG", optimize=True)
            return target
        except Exception:
            logger.exception("高清题图生成失败，将发送原图：%s", source)
            return source

    @filter.command("何切")
    async def nanikiru(self, event: AstrMessageEvent, question_id: int = 0):
        """随机出一道何切题；可附加 1 到 601 的题号。"""
        questions, state, _, _ = self._ready()
        session_id = event.unified_msg_origin
        if question_id:
            question = questions.get(question_id)
            if question is None:
                yield event.plain_result(f"题号范围是 1～{len(questions.questions)}。")
                return
            async with self._lock:
                state.set_current(session_id, question_id)
            yield self._question_chain(event, question)
            return
        async with self._lock:
            old = state.get(session_id)
            picked, new_round = state.draw(session_id)
        question = questions.get(picked)
        prefix = "🀄 何切"
        if old["current_question"] and not old["answer_seen"]:
            prefix = "上一题答案尚未查看，已切换到新题\n🀄 何切"
        if new_round:
            prefix = "新一轮开始\n🀄 何切"
        yield self._question_chain(event, question, prefix)

    @filter.command("何切答案")
    async def nanikiru_answer(self, event: AstrMessageEvent, question_id: int = 0):
        """查看当前何切题或指定题号的原书答案。"""
        questions, state, _, _ = self._ready()
        if question_id:
            question = questions.get(question_id)
            if question is None:
                yield event.plain_result(f"题号范围是 1～{len(questions.questions)}。")
                return
        else:
            row = state.get(event.unified_msg_origin)
            if row["current_question"] is None:
                yield event.plain_result("当前还没有何切题，使用 /何切 开始一题。")
                return
            question = questions.get(row["current_question"])
            async with self._lock:
                state.mark_answer_seen(event.unified_msg_origin)
        yield self._answer_chain(event, question)

    @filter.command("何切状态")
    async def nanikiru_status(self, event: AstrMessageEvent):
        """查看当前题和本轮随机进度。"""
        questions, state, _, _ = self._ready()
        row = state.get(event.unified_msg_origin)
        remaining = len(row["remaining_questions"])
        current = f"#{row['current_question']}" if row["current_question"] else "无"
        auto = f"开启（{row['auto_time']}）" if row["auto_enabled"] else "关闭"
        yield event.plain_result(
            f"当前题：{current}\n本轮进度：{len(questions.questions) - remaining} / "
            f"{len(questions.questions)}\n剩余：{remaining}\n"
            f"当前轮次：第{row['round_number']}轮\n每日自动：{auto}"
        )

    @filter.command("何切重置")
    async def nanikiru_reset(self, event: AstrMessageEvent):
        """管理员重置当前会话的随机轮次。"""
        error = self._admin_error(event)
        if error:
            yield event.plain_result(error)
            return
        _, state, _, _ = self._ready()
        async with self._lock:
            state.reset(event.unified_msg_origin)
        yield event.plain_result("当前会话已重置，将从新一轮随机牌堆开始。")

    @filter.command("何切自动")
    async def nanikiru_auto(self, event: AstrMessageEvent, action: str = "", time_text: str = ""):
        """管理员开启、关闭或查看当前会话的每日自动出题。"""
        error = self._admin_error(event)
        if error:
            yield event.plain_result(error)
            return
        _, state, _, _ = self._ready()
        row = state.get(event.unified_msg_origin)
        if not action:
            status = f"开启（{row['auto_time']}）" if row["auto_enabled"] else "关闭"
            yield event.plain_result(f"当前会话每日何切：{status}")
            return
        if action == "关闭":
            async with self._lock:
                state.set_auto(event.unified_msg_origin, False, row["auto_time"])
            yield event.plain_result("已关闭当前会话的每日何切。")
            return
        selected_time = time_text or row["auto_time"] or "20:00"
        if action != "开启" or not TIME_RE.fullmatch(selected_time):
            yield event.plain_result("用法：/何切自动 开启 [HH:MM] 或 /何切自动 关闭")
            return
        async with self._lock:
            state.set_auto(event.unified_msg_origin, True, selected_time)
        yield event.plain_result(f"已开启每日何切，发送时间：{selected_time}。")

    @filter.command("设置牌谱屋Token")
    async def set_koromo_token(self, event: AstrMessageEvent, token: str = ""):
        """管理员在私聊中保存牌谱屋 API Token。"""
        error = self._admin_error(event)
        if error:
            yield event.plain_result(error)
            return
        if event.get_group_id():
            yield event.plain_result("Token 只能在私聊中设置，请撤回含 Token 的群消息。")
            return
        if not token.strip():
            yield event.plain_result("用法：/设置牌谱屋Token TOKEN")
            return
        _, _, db, _ = self._ready()
        db.set_secret("koromo_token", token.strip())
        yield event.plain_result("牌谱屋 Token 已保存。")

    @filter.command("雀魂搜索")
    async def majsoul_search(self, event: AstrMessageEvent, name: str = ""):
        """按昵称搜索四麻和三麻玩家。"""
        if not name.strip():
            yield event.plain_result("用法：/雀魂搜索 玩家名")
            return
        _, _, _, api = self._ready()
        try:
            if name.isdigit() and self.protocol:
                try:
                    player = await self.protocol.resolve_friend_id(name)
                    if isinstance(player, dict) and player_uid(player):
                        yield event.plain_result(
                            "好友码查询结果：\n" + format_search([player], 4).split("：\n", 1)[-1]
                        )
                        return
                except Exception:
                    pass
            results = await asyncio.gather(api.search_player(name, 4), api.search_player(name, 3))
            parts = [format_search(rows, mode) for rows, mode in zip(results, (4, 3)) if rows]
            yield event.plain_result("\n\n".join(parts) if parts else "没有找到该玩家。")
        except Exception as exc:
            yield event.plain_result(self._error_text(exc))

    @filter.command("雀魂绑定")
    async def majsoul_bind(self, event: AstrMessageEvent, uid: str = ""):
        """绑定雀魂 UID，首次绑定自动设为主账号。"""
        if not UID_RE.fullmatch(uid.strip()):
            yield event.plain_result("用法：/雀魂绑定 数字UID")
            return
        _, _, db, api = self._ready()
        nickname = ""
        try:
            stats = await api.player_stats(uid, 4)
            if isinstance(stats, dict):
                nickname = str(stats.get("nickname") or stats.get("name") or "")
        except Exception:
            pass
        added = db.add_binding(self._actor_id(event), uid, nickname)
        if not added:
            yield event.plain_result("这个 UID 已经绑定。")
            return
        label = f"（{nickname}）" if nickname else ""
        yield event.plain_result(f"已绑定 UID {uid}{label}。首次绑定会作为主账号。")

    @filter.command("雀魂切换")
    async def majsoul_switch(self, event: AstrMessageEvent, uid: str = ""):
        """切换默认查询账号。"""
        _, _, db, _ = self._ready()
        if db.set_main_uid(self._actor_id(event), uid.strip()):
            yield event.plain_result(f"已将 UID {uid} 设为主账号。")
        else:
            yield event.plain_result("该 UID 尚未绑定。")

    @filter.command("雀魂解绑")
    async def majsoul_unbind(self, event: AstrMessageEvent, uid: str = ""):
        """解绑指定 UID；省略 UID 时解绑全部。"""
        _, _, db, _ = self._ready()
        count = db.remove_binding(self._actor_id(event), uid.strip() or None)
        yield event.plain_result(f"已解除 {count} 个绑定。" if count else "没有找到对应绑定。")

    @filter.command("雀魂我的绑定")
    async def majsoul_bindings(self, event: AstrMessageEvent):
        """查看自己的雀魂绑定。"""
        _, _, db, _ = self._ready()
        rows = db.list_bindings(self._actor_id(event))
        if not rows:
            yield event.plain_result("尚未绑定账号，使用 /雀魂绑定 UID。")
            return
        lines = ["我的雀魂绑定："]
        for row in rows:
            main = " [主账号]" if row["is_main"] else ""
            name = f" {row['nickname']}" if row["nickname"] else ""
            lines.append(f"• {row['uid']}{name}{main}")
        yield event.plain_result("\n".join(lines))

    async def _resolve_target(self, event: AstrMessageEvent, query: str, mode: int):
        _, _, db, api = self._ready()
        query = query.strip()
        if not query:
            uid = db.get_main_uid(self._actor_id(event))
            if not uid:
                raise ValueError("请先使用 /雀魂绑定 UID，或在命令后填写 UID/昵称。")
            return uid, uid
        if UID_RE.fullmatch(query):
            return query, query
        players = await api.search_player(query, mode)
        if not players:
            raise ValueError("没有找到该玩家。")
        player = next((item for item in players if player_name(item) == query), players[0])
        uid = player_uid(player)
        if not uid:
            raise ValueError("搜索结果中没有有效 UID。")
        return uid, player_name(player)

    async def _stats_text(self, event: AstrMessageEvent, mode: int, query: str, room: str):
        _, _, _, api = self._ready()
        modes = room_modes(mode, room)
        if room and modes is None:
            return "房间可填写：金、玉、王座。"
        try:
            uid, _ = await self._resolve_target(event, query, mode)
            stats, extended = await asyncio.gather(
                api.player_stats(uid, mode, modes), api.extended_stats(uid, mode, modes)
            )
            if not stats:
                return "没有查到战绩数据。"
            return format_stats(uid, mode, stats, extended)
        except Exception as exc:
            return self._error_text(exc)

    async def _records_text(self, event: AstrMessageEvent, mode: int, query: str, room: str):
        _, _, _, api = self._ready()
        modes = room_modes(mode, room)
        if room and modes is None:
            return "房间可填写：金、玉、王座。"
        try:
            uid, _ = await self._resolve_target(event, query, mode)
            limit = int(self.config.get("records_limit", 5))
            records = await api.recent_records(uid, mode, limit, modes)
            return format_records(uid, mode, records) if records else "没有查到最近对局。"
        except Exception as exc:
            return self._error_text(exc)

    @filter.command("雀魂查询")
    async def stats_four(self, event: AstrMessageEvent, query: str = "", room: str = ""):
        """查询四麻战绩。"""
        yield event.plain_result(await self._stats_text(event, 4, query, room))

    @filter.command("查询四麻")
    async def stats_four_alias(self, event: AstrMessageEvent, query: str = "", room: str = ""):
        """查询四麻战绩。"""
        yield event.plain_result(await self._stats_text(event, 4, query, room))

    @filter.command("查询三麻")
    async def stats_three(self, event: AstrMessageEvent, query: str = "", room: str = ""):
        """查询三麻战绩。"""
        yield event.plain_result(await self._stats_text(event, 3, query, room))

    @filter.command("雀魂对局")
    async def records_four(self, event: AstrMessageEvent, query: str = "", room: str = ""):
        """查询最近四麻对局。"""
        yield event.plain_result(await self._records_text(event, 4, query, room))

    @filter.command("四麻对局")
    async def records_four_alias(self, event: AstrMessageEvent, query: str = "", room: str = ""):
        """查询最近四麻对局。"""
        yield event.plain_result(await self._records_text(event, 4, query, room))

    @filter.command("三麻对局")
    async def records_three(self, event: AstrMessageEvent, query: str = "", room: str = ""):
        """查询最近三麻对局。"""
        yield event.plain_result(await self._records_text(event, 3, query, room))

    async def _subscribe(self, event: AstrMessageEvent, mode: int, query: str) -> str:
        error = self._admin_error(event)
        if error:
            return error
        if not query.strip():
            return "请填写 UID 或玩家昵称。"
        _, _, db, api = self._ready()
        try:
            uid, nickname = await self._resolve_target(event, query, mode)
            records = await api.recent_records(uid, mode, 1)
            cursor = record_uuid(records[0]) if records else None
            db.upsert_subscription(event.unified_msg_origin, uid, nickname, mode, cursor)
            label = "四麻" if mode == 4 else "三麻"
            return f"已订阅 {nickname}（UID {uid}）的{label}对局。"
        except Exception as exc:
            return self._error_text(exc)

    async def _subscription_action(
        self, event: AstrMessageEvent, mode: int, query: str, action: str
    ) -> str:
        error = self._admin_error(event)
        if error:
            return error
        if not query.strip():
            return "请填写已订阅玩家的 UID 或昵称。"
        _, _, db, _ = self._ready()
        rows = db.list_subscriptions(event.unified_msg_origin, mode)
        target = next(
            (row for row in rows if row["uid"] == query or row["nickname"] == query), None
        )
        if target is None:
            return "没有找到对应订阅。"
        if action == "delete":
            changed = db.delete_subscription(event.unified_msg_origin, target["uid"], mode)
        else:
            changed = db.set_subscription_active(
                event.unified_msg_origin, target["uid"], mode, action == "enable"
            )
        words = {"delete": "删除", "enable": "开启", "disable": "关闭"}
        return f"已{words[action]} {target['nickname']} 的订阅。" if changed else "订阅没有变化。"

    def _subscription_status(self, event: AstrMessageEvent, mode: int) -> str:
        _, _, db, _ = self._ready()
        rows = db.list_subscriptions(event.unified_msg_origin, mode)
        label = "四麻" if mode == 4 else "三麻"
        if not rows:
            return f"当前会话没有{label}订阅。"
        lines = [f"当前会话的{label}订阅："]
        for row in rows:
            status = "开启" if row["active"] else "关闭"
            lines.append(f"• {row['nickname']}｜UID {row['uid']}｜{status}")
        return "\n".join(lines)

    @filter.command("雀魂订阅")
    async def subscribe_four(self, event: AstrMessageEvent, query: str = ""):
        """管理员订阅玩家的四麻对局。"""
        yield event.plain_result(await self._subscribe(event, 4, query))

    @filter.command("三麻订阅")
    async def subscribe_three(self, event: AstrMessageEvent, query: str = ""):
        """管理员订阅玩家的三麻对局。"""
        yield event.plain_result(await self._subscribe(event, 3, query))

    @filter.command("开启雀魂订阅")
    async def enable_four(self, event: AstrMessageEvent, query: str = ""):
        yield event.plain_result(await self._subscription_action(event, 4, query, "enable"))

    @filter.command("关闭雀魂订阅")
    async def disable_four(self, event: AstrMessageEvent, query: str = ""):
        yield event.plain_result(await self._subscription_action(event, 4, query, "disable"))

    @filter.command("删除雀魂订阅")
    async def delete_four(self, event: AstrMessageEvent, query: str = ""):
        yield event.plain_result(await self._subscription_action(event, 4, query, "delete"))

    @filter.command("雀魂订阅状态")
    async def status_four(self, event: AstrMessageEvent):
        yield event.plain_result(self._subscription_status(event, 4))

    @filter.command("开启三麻订阅")
    async def enable_three(self, event: AstrMessageEvent, query: str = ""):
        yield event.plain_result(await self._subscription_action(event, 3, query, "enable"))

    @filter.command("关闭三麻订阅")
    async def disable_three(self, event: AstrMessageEvent, query: str = ""):
        yield event.plain_result(await self._subscription_action(event, 3, query, "disable"))

    @filter.command("删除三麻订阅")
    async def delete_three(self, event: AstrMessageEvent, query: str = ""):
        yield event.plain_result(await self._subscription_action(event, 3, query, "delete"))

    @filter.command("三麻订阅状态")
    async def status_three(self, event: AstrMessageEvent):
        yield event.plain_result(self._subscription_status(event, 3))

    @filter.command("雀魂API状态")
    async def protocol_status(self, event: AstrMessageEvent):
        """检查可选的本地雀魂协议服务。"""
        try:
            profiles = await self.protocol.profiles()
            count = len(profiles) if isinstance(profiles, list) else 0
            yield event.plain_result(f"本地雀魂协议服务可用，已保存 {count} 个登录档案。")
        except Exception:
            executable = self._protocol_executable()
            hint = f"\n预期程序位置：{executable}" if executable else ""
            yield event.plain_result(f"本地雀魂协议服务未连接。{hint}")

    @filter.command("雀魂登录")
    async def protocol_login(self, event: AstrMessageEvent, account: str = "", password: str = ""):
        """管理员在私聊中登录本地雀魂协议服务。"""
        error = self._admin_error(event)
        if error:
            yield event.plain_result(error)
            return
        if event.get_group_id():
            yield event.plain_result("账号和密码只能在私聊中提交，请撤回群消息。")
            return
        if not account or not password:
            yield event.plain_result("用法：/雀魂登录 账号 密码")
            return
        try:
            result = await self.protocol.login(account, password)
            message = result.get("message") if isinstance(result, dict) else None
            yield event.plain_result(message or "登录请求已完成，请用 /雀魂API状态 检查服务。")
        except Exception as exc:
            yield event.plain_result(f"本地协议服务登录失败：{exc}")

    @filter.command("雀魂取谱测试")
    async def protocol_fetch_test(self, event: AstrMessageEvent, paipu_url: str = ""):
        """管理员测试本地 API 是否能取回牌谱原始数据。"""
        error = self._admin_error(event)
        if error:
            yield event.plain_result(error)
            return
        paipu = extract_paipu_id(paipu_url)
        if not paipu:
            yield event.plain_result("用法：/雀魂取谱测试 牌谱链接")
            return
        try:
            result = await self.protocol.fetch_record(paipu)
            if not isinstance(result, dict):
                raise RuntimeError("本地 API 返回格式不正确")
            encoded = result.get("dataBase64") or ""
            reference = result.get("reference") or ""
            if not encoded and not reference:
                raise RuntimeError("本地 API 没有返回牌谱数据")
            details = []
            if encoded:
                details.append(f"原始牌谱约 {len(encoded) * 3 // 4:,} 字节")
            if reference:
                details.append("已返回牌谱引用")
            yield event.plain_result("本地 API 取谱成功：" + "，".join(details) + "。")
        except Exception as exc:
            yield event.plain_result(f"本地 API 取谱失败：{exc}")

    def _gateway_result(self, event: AstrMessageEvent, result: Any):
        if not isinstance(result, dict):
            return event.plain_result(str(result))
        components: list[Any] = []
        message = result.get("message") or result.get("summary")
        if message:
            components.append(Comp.Plain(str(message)))
        urls = result.get("image_urls") or []
        if result.get("image_url"):
            urls = [result["image_url"], *urls]
        for url in urls:
            components.append(Comp.Image.fromURL(str(url)))
        if result.get("report_url"):
            components.append(Comp.Plain(f"分析报告：{result['report_url']}"))
        return event.chain_result(components or [Comp.Plain("分析服务已完成，但没有返回内容。")])

    @filter.command("牌谱Review")
    async def review(self, event: AstrMessageEvent, paipu_url: str = "", seat: str = ""):
        """通过可选分析网关进行牌谱 Review。"""
        if not paipu_url:
            yield event.plain_result("用法：/牌谱Review 牌谱链接 [座位]")
            return
        try:
            yield self._gateway_result(event, await self.review_gateway.review(paipu_url, seat))
        except Exception as exc:
            yield event.plain_result(f"牌谱分析失败：{exc}")

    @filter.command("雀魂场况")
    async def scene(
        self, event: AstrMessageEvent, paipu_url: str = "", round_number: int = 0, turn: int = -1
    ):
        """通过可选分析网关生成指定场况。"""
        if not paipu_url or round_number <= 0:
            yield event.plain_result("用法：/雀魂场况 牌谱链接 局数 [巡目]")
            return
        try:
            selected_turn = None if turn < 0 else turn
            result = await self.review_gateway.scene(paipu_url, round_number, selected_turn)
            yield self._gateway_result(event, result)
        except Exception as exc:
            yield event.plain_result(f"场况生成失败：{exc}")

    @filter.command("help")
    async def help_command(self, event: AstrMessageEvent):
        """显示日麻助手功能、用法和当前用户可用的管理命令。"""
        yield event.plain_result(self._help_text(event))

    @filter.command("雀魂帮助")
    async def mahjong_help(self, event: AstrMessageEvent):
        """显示日麻助手功能、用法和当前用户可用的管理命令。"""
        yield event.plain_result(self._help_text(event))

    def _protocol_executable(self) -> Path | None:
        configured = str(self.config.get("protocol_executable", "") or "").strip()
        if configured:
            path = Path(configured).expanduser()
            return path if path.is_absolute() else (self.plugin_dir / path).resolve()
        if sys.platform == "win32":
            name = "Majsoul.ProtocolLogin.Api-win-x64.exe"
        elif sys.platform.startswith("linux"):
            name = "Majsoul.ProtocolLogin.Api-linux-x64"
        else:
            return None
        return self.plugin_dir / "api" / name

    async def _auto_start_protocol(self):
        if not self.protocol or await self.protocol.health():
            return
        executable = self._protocol_executable()
        if executable is None or not executable.is_file():
            logger.info("未发现本地雀魂 API 程序；查询和何切功能不受影响")
            return
        options: dict[str, Any] = {
            "cwd": str(executable.parent),
            "stdout": asyncio.subprocess.DEVNULL,
            "stderr": asyncio.subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            self._protocol_process = await asyncio.create_subprocess_exec(
                str(executable), **options
            )
            for _ in range(20):
                await asyncio.sleep(0.5)
                if self._protocol_process.returncode is not None:
                    raise RuntimeError(
                        f"程序提前退出，退出码 {self._protocol_process.returncode}"
                    )
                if await self.protocol.health():
                    logger.info("本地雀魂 API 已启动：%s", executable)
                    return
            logger.warning("本地雀魂 API 已启动，但 10 秒内没有通过健康检查")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("自动启动本地雀魂 API 失败：%s", exc)

    async def _question_scheduler(self):
        while True:
            try:
                now = datetime.now(self.timezone)
                date_text, time_text = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
                questions, state, _, _ = self._ready()
                async with self._lock:
                    due = state.due_auto_sessions(date_text, time_text)
                for session_id in due:
                    async with self._lock:
                        if not state.claim_auto(session_id, date_text):
                            continue
                        picked, _ = state.draw(session_id)
                    question = questions.get(picked)
                    chain = (
                        MessageChain()
                        .message(f"🀄 今日何切 #{question.global_id}\n{self._source(question)}")
                        .file_image(str(questions.image_path(question.question_image)))
                        .message("想好后使用：/何切答案")
                    )
                    await self.context.send_message(session_id, chain)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("每日何切调度执行失败")
            await asyncio.sleep(20)

    async def _subscription_scheduler(self):
        last_check = {3: 0.0, 4: 0.0}
        while True:
            try:
                now = time.monotonic()
                for mode in (4, 3):
                    interval = max(60, int(self.config.get(f"subscribe_interval_{mode}", 180 if mode == 4 else 300)))
                    if now - last_check[mode] < interval:
                        continue
                    last_check[mode] = now
                    await self._poll_subscriptions(mode)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("雀魂订阅调度执行失败")
            await asyncio.sleep(20)

    async def _poll_subscriptions(self, mode: int):
        _, _, db, api = self._ready()
        for row in db.list_subscriptions(mode=mode, active_only=True):
            try:
                records = await api.recent_records(row["uid"], mode, 5)
                if not records:
                    continue
                newest = record_uuid(records[0])
                if not row["last_uuid"]:
                    if newest:
                        db.update_subscription_cursor(row["session_id"], row["uid"], mode, newest)
                    continue
                pending = []
                for record in records:
                    if record_uuid(record) == row["last_uuid"]:
                        break
                    pending.append(record)
                for record in reversed(pending):
                    label = "四麻" if mode == 4 else "三麻"
                    chain = MessageChain().message(
                        f"🀄 {row['nickname']} 新{label}对局\n{format_record(record, row['uid'])}"
                    )
                    await self.context.send_message(row["session_id"], chain)
                if newest:
                    db.update_subscription_cursor(row["session_id"], row["uid"], mode, newest)
            except Exception as exc:
                logger.warning("订阅轮询失败 uid=%s mode=%s: %s", row["uid"], mode, exc)
