# 开发维护

## 阅读顺序

`README.md` → `ARCHITECTURE.md` → 对应 `tests/` → 实现。

## 模块地图

| 问题 | 先读 | 通常只改 |
| --- | --- | --- |
| 候选可交付性、页身份 | `tests/test_matcher.py` | `core/matcher.py` |
| 搜索、快照、交付编排 | `tests/test_services.py`、`tests/test_selection.py` | `core/services.py`、`core/selection.py` |
| WBI、详情、DASH、二维码 | `tests/test_bilibili.py` | `core/bilibili.py` |
| 下载、封装、临时文件 | `tests/test_media.py` | `core/media.py` |
| 命令、LLM、消息、WebUI | `tests/test_main.py` | `main.py` |
| Cookie、账号、权限 | `tests/test_accounts.py` | `core/accounts.py`、`main.py` |

## 硬约束

- 单源 Bilibili；候选身份固定为 `bvid:cid`，失败不换歌、不换 P。
- 本地不评分、不过滤版本标签；LLM 只从受限候选集中选择。
- `MediaLimits` 是唯一资源边界来源，不在调用点散落常量。
- 下载音频必须用户确认；精确 AV/BV 多分 P 必须用户选择。
- 媒体发送后必须 `release()`；Cookie 不得进入聊天、日志、LLM 或 WebUI。
- 不为了少量重复引入跨层框架或音源抽象。

## 修改前检查

- 改搜索/过滤：补候选去重、顺序、分 P、精确视频不回退的正反例。
- 改 LLM 协作：覆盖候选范围验证、`delivered/choose/error`、下载强制用户确认。
- 改交付：覆盖平台回退、准备失败、取消、发送失败、文件清理、插件停止。
- 改账号/WebUI：覆盖管理员身份、会话归属、SSE 脱敏、Cookie 文件权限。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile main.py core/*.py tests/*.py
ruff format --check .
ruff check .
```

涉及真实 AstrBot 适配器、二维码 SSE、Bilibili 流或 ffmpeg 的改动，仍需在 AstrBot `>=4.26,<5` 手工验收。
