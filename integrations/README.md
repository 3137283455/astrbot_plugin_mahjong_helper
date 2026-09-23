# 娱乐 API 插件配套补丁

本目录的补丁适用于 [Zhalslar/astrbot_plugin_apis](https://github.com/Zhalslar/astrbot_plugin_apis) 当前的 v3.1.2 源码。上游插件按 AGPL-3.0 发布；补丁仅用于修改该上游插件，不属于日麻助手运行代码。

补丁增加 `/娱乐` 图片菜单，支持 `/娱乐 文字`、`/娱乐 图片`、`/娱乐 视频`、`/娱乐 语音` 查看完整分类。菜单只显示当前会话有权限使用且已启用的 API。娱乐插件的关键词、管理命令、图片菜单和 LLM 工具在群聊中都要求消息里实际 `@` 机器人；私聊可直接使用。日麻助手不受此限制。

在 AstrBot 的 `data/plugins/` 下安装上游插件后，进入 `data/plugins/astrbot_plugin_apis` 目录，执行：

```bash
git apply /path/to/astrbot_plugin_apis-entertainment.patch
```

然后安装该插件的 `requirements.txt` 依赖并重载插件。上游升级可能导致补丁不再适用，升级前请备份配置并重新核对补丁。服务器 `/bot/AstrBot/data/plugins/astrbot_plugin_apis` 已经应用同一份改动。
