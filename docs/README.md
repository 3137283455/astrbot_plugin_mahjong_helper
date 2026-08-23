# bili播放器 开发文档

`bili播放器` 是 `astrbot_plugin_bili_player` 的用户可见名称。它是一个面向 AstrBot 4.26+ 的小型 Bilibili 点播插件：LLM 理解“我要看/我要听”意图后在受限候选中选定作品并交付视频或音频，平台不适配时用文件兜底；显式搜索（`搜索歌曲`/`搜索视频`）会展示最多十个候选，由用户回复序号发视频、播放音频或下载音频。

这组文档服务于项目的开发、维护和审阅。开始修改前按下面顺序阅读：

1. [ARCHITECTURE.md](ARCHITECTURE.md)：唯一的架构、行为契约、匹配算法、资源与安全事实来源。
2. [DEVELOPMENT.md](DEVELOPMENT.md)：模块定位、改动约束、测试地图与验证方式。
3. [REFERENCES.md](REFERENCES.md)：NeriPlayer 与 `astrbot_plugin_music` 的明确参考范围、固定 GitHub 链接和致谢。
4. 对应的测试文件，再阅读要改动的实现文件。测试是当前行为最精确的可执行说明。

项目的稳定标识仍是 `astrbot_plugin_bili_player`；不要为了显示名而改动数据目录、路由前缀或 Python 包名。
