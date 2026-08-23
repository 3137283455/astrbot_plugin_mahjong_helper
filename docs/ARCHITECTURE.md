# 架构与行为契约

本文件是设计事实来源；实现细节冲突时以 `tests/` 为准。

## 定位

只做三件事：按意图直接交付一个可信作品；显式搜索后由用户选择；用户确认后下载音频。

不引入第二音源、评分重排、缓存、历史、歌单或播放器抽象。

## 分层

```text
main.py            AstrBot 生命周期、命令、LLM 工具、WebUI
core/services.py   搜索展开、快照、交付编排
core/matcher.py    纯函数守卫：时长、去重、上限
core/bilibili.py   WBI、搜索、详情、DASH、二维码
core/media.py      有界下载、ffmpeg 封装、临时文件
core/selection.py  会话绑定快照
core/accounts.py   管理员 Cookie 与二维码会话
core/models.py     不可变领域值
```

`main.py` 是唯一允许依赖 AstrBot 的层；`core/` 保持零 AstrBot 依赖、可单测。

## 候选身份

- 候选身份是 `bvid:cid`，不是标题或搜索词。
- 选定后只解析该页；失败即报错，不换歌、不换 P。
- 每次搜索生成会话绑定、5 分钟有效的 `SearchSnapshot`。
- LLM 只能使用本次 `search_id + position`；用户只能回复当前列表序号。
- 原始 `bvid`、`cid`、链接不能绕过快照直接交付。

## 搜索与判断

- Bilibili 负责召回和排序；本地只做时长、去重、数量守卫。
- 不自动追加“原版”；首轮不足 10 条时，最多按纯歌名补一次。
- 关键词多 P 仅在结构化歌名可确认页面身份时展开；精确视频保留全部分 P。
- Live、翻唱、AI、DJ、Remix、伴奏、MV 等标签仅作 LLM 判断证据，不做本地过滤。
- LLM 负责语义选择：歌名 > 歌手佐证 > 版本一致 > 合理时长；无可信候选时不交付。

## 交付

| 动作 | 交付 | 限制 |
| --- | --- | --- |
| `audio` | 语音 | 15 分钟 / 25 MiB / 180 秒 |
| `video` | 视频 | 150 MiB / 900 秒 |
| `download` | 音频文件 | 100 MiB / 900 秒，必须用户确认 |

- 直接看视频固定第一候选；精确 AV/BV 多分 P 强制用户选择（`by_video_reference`）。
- 语音超限或平台不支持时回退文件；发送完成 `finally release()` 删除媒体。
- DASH 由 `ffmpeg` 无损封装；缺少 ffmpeg 时明确报错。

## 会话

- 显式搜索等待 90 秒；超时发送结束提示。
- 新消息或新搜索静默取消旧候选；无效序号回复保留等待。
- LLM 交付返回 `delivered/choose/error`，不输出过程或确认文字。

## 账号与安全

- 仅 Dashboard 主账号可管理登录；Cookie 原子写入 `accounts.json`（`0600`）。
- Cookie、二维码 token、原始 URL 不进入响应、日志、LLM 或聊天。
- 退出只删本地凭证；匿名模式始终可用。
- 启动时清理遗留媒体，停止时取消在途任务；详情并发 ≤ 4，媒体并发 ≤ 2。

## 资源边界

| 资源 | 值 |
| --- | --- |
| 候选数 | 10 |
| 快照 TTL | 5 分钟 |
| 选歌等待 | 90 秒 |
| HTTP 连接 | 16 总 / 8 每主机 |
| 详情并发 | 4 |
| 媒体并发 | 2 |

## 验证

单元测试覆盖上述契约；真实适配器、二维码 SSE、ffmpeg 与 Bilibili 流仍需手工验收。

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile main.py core/*.py tests/*.py
ruff format --check .
ruff check .
```
