# 日麻助手 AstrBot 插件

一个可直接安装的 AstrBot 插件，合并了 601 道何切题和雀魂玩家工具。已移除
抽卡、货币、签到、商店等经济系统。

## 功能

- 601 道何切题：随机不重复、指定题号、原书答案、每日自动出题；发送前自动生成高清 PNG 缓存。
- 雀魂账号：昵称搜索、UID 绑定、多账号与主账号切换。
- 战绩：四麻/三麻统计、金之间/玉之间/王座之间筛选。
- 对局：最近牌谱链接与名次结果。
- 订阅：按群或私聊会话自动播报新对局。
- 可选接入本地 `Majsoul.ProtocolLogin.Api`。
- 可选接入牌谱 Review/场况生成 HTTP 网关。

## 安装

在 AstrBot 插件市场的“通过 GitHub 仓库安装”中填写：

```text
https://github.com/3137283455/astrbot_plugin_mahjong_helper
```

也可以将仓库目录放入 AstrBot 的 `data/plugins/`，然后在 WebUI 重载插件。
运行数据默认保存在
`data/plugin_data/astrbot_plugin_mahjong_helper/mahjong_helper.db`。

本仓库如设为私有，需要在运行 AstrBot 的机器上配置有权访问该仓库的 GitHub
凭据；也可以下载 Release 压缩包后手动安装。

## 首次配置

雀魂搜索、战绩和订阅通过牌谱屋接口读取。管理员取得牌谱屋 API Token 后，
在 AstrBot 配置页填写 `koromo_token`，或**私聊**机器人：

```text
/设置牌谱屋Token 你的Token
```

不要在群聊中发送 Token。何切功能不需要 Token，题库已经内置，无需另外整理。

## 常用命令

- `/help` 或 `/雀魂帮助`：按当前用户权限显示功能介绍、常用命令和管理员命令。

### 何切

- `/何切`：随机出题，本轮 601 题不重复。
- `/何切 123`：显示指定题号。
- `/何切答案` 或 `/何切答案 123`：查看原书答案。
- `/何切状态`：查看进度。
- `/何切自动 开启 19:30`：管理员开启当前会话每日出题。
- `/何切自动 关闭`：关闭每日出题。

题图默认以 2 倍尺寸转为 PNG 并轻度锐化，以减少 QQ 二次压缩导致的模糊。首次打开
某道题时生成缓存，后续直接复用；可在插件配置中通过 `enhance_images` 关闭，或用
`image_scale` 设置 2～4 倍放大。

### 玩家与战绩

- `/雀魂搜索 玩家名`
- `/雀魂绑定 UID`、`/雀魂我的绑定`
- `/雀魂切换 UID`、`/雀魂解绑 [UID]`
- `/雀魂查询 [UID或昵称] [金|玉|王座]`
- `/查询三麻 [UID或昵称] [金|玉|王座]`
- `/雀魂对局 [UID或昵称] [金|玉|王座]`
- `/三麻对局 [UID或昵称] [金|玉|王座]`

不填写玩家时使用发送者的主绑定账号。

### 自动订阅

- `/雀魂订阅 UID或昵称`、`/三麻订阅 UID或昵称`
- `/雀魂订阅状态`、`/三麻订阅状态`
- `/开启雀魂订阅 玩家`、`/关闭雀魂订阅 玩家`、`/删除雀魂订阅 玩家`
- 三麻对应命令为 `/开启三麻订阅`、`/关闭三麻订阅`、`/删除三麻订阅`

订阅管理只允许 AstrBot 管理员执行。首次订阅只记录当前最新牌谱，之后才播报
新对局，避免一次发送全部历史记录。

## 权限与凭据

- 普通用户可以使用何切、玩家绑定、玩家查询、战绩、最近对局和查看订阅状态。
- 何切重置、每日自动出题、订阅管理、设置牌谱屋 Token、本地雀魂登录和取谱测试
  只允许 AstrBot 管理员执行。
- 牌谱屋 Token、雀魂账号和密码只能在私聊中提交；插件不会把雀魂登录密码写入
  插件数据库。
- AstrBot 管理员需要在 AstrBot 配置中的管理员 ID 列表里填写自己的 QQ 号。

## 本地 API 与 Review

本地 API 使用上游项目 Release 发布的 `Majsoul.ProtocolLogin.Api`：

- [下载 Majsoul-Plugin 最新 Release](https://github.com/Xcheng-dada/Majsoul-Plugin/releases/latest)
- Windows：解压 `MajsoulAPI-*-windows-x64.zip`，把
  `Majsoul.ProtocolLogin.Api-win-x64.exe` 放入本插件 `api/`。
- Linux：解压 `MajsoulAPI-*-linux-x64.zip`，把
  `Majsoul.ProtocolLogin.Api-linux-x64` 放入本插件 `api/`，并赋予执行权限。

插件默认会在未检测到 `127.0.0.1:5088` 服务时自动启动对应程序；可通过
`protocol_auto_launch` 关闭，也可用 `protocol_executable` 指定其他位置。程序一次
只能启动一个实例。

使用 `/雀魂API状态` 检查服务，在私聊中使用 `/雀魂登录 账号 密码` 登录；再用
`/雀魂取谱测试 牌谱链接` 验证是否能取得原始牌谱。登录信息仅转交本地服务，
不写入插件数据库。建议使用专门的小号。

原项目的 Review 依赖完整雀魂 protobuf 解码、牌谱转换和图片渲染，无法只靠一个
本地 API 完成：该程序负责登录和取谱，但不负责 Mortal 分析。本插件提供稳定的
分析网关合同：

- `POST /review`：`{"paipu_url": "...", "seat": "..."}`
- `POST /scene`：`{"paipu_url": "...", "round": 1, "turn": 8}`

配置 `review_gateway_url` 后可使用 `/牌谱Review` 和 `/雀魂场况`。网关可以返回
`message`、`summary`、`report_url`、`image_url` 或 `image_urls`。

## 许可

代码使用 [AGPL-3.0](LICENSE)。题库图片的使用范围见 [NOTICE.md](NOTICE.md)，
上游来源和参考范围见 [参考项目.md](参考项目.md)。
