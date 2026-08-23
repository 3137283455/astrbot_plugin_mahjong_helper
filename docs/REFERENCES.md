# 参考与致谢

仅借鉴设计思路与 AstrBot 集成约定，不迁移代码；复用前核对上游许可证。

## NeriPlayer（GPL-3.0）

概念参考，未复制代码：

- 搜索视频与可播放分 P 分离，交付必须固定媒体身份。
- 选中页解析失败不得换歌、换 P。
- 借鉴“歌名优先、艺人佐证、可信度拒绝”的定性判断；不迁移评分公式、多平台与 Android 实现。

链接：[SearchManager.kt](https://github.com/cwuom/NeriPlayer/blob/e76bc4f21e010f67c05f9e6c9f846ec958b7985f/app/src/main/java/moe/ouom/neriplayer/core/api/search/SearchManager.kt)、[PlayerManagerNeteaseAutoSourceSwitch.kt](https://github.com/cwuom/NeriPlayer/blob/e76bc4f21e010f67c05f9e6c9f846ec958b7985f/app/src/main/java/moe/ouom/neriplayer/core/player/resolver/netease/PlayerManagerNeteaseAutoSourceSwitch.kt)

## astrbot_plugin_music

AstrBot 集成参考：

- `Star` 生命周期、`filter.command` / `FunctionTool`、`SessionWaiter`、`Record` / `File`、插件页面桥接。
- 不迁移平台注册表、`BaseMusicPlayer`、配置树、卡片渲染与下载器。

链接：[上游 Zhalslar/astrbot_plugin_music](https://github.com/Zhalslar/astrbot_plugin_music)、[审计用 fork](https://github.com/57Darling02/astrbot_plugin_music/tree/6cf27cb1fc603dcac6c0b390a616741c4abdab4a)

本项目仓库：[57Darling02/astrbot_plugin_bili_player](https://github.com/57Darling02/astrbot_plugin_bili_player)
