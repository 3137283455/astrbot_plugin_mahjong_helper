# B站订阅使用说明

```text
📺 B站订阅帮助
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
也可私聊或@慧慧说“帮我订阅B站UID 123456，不看抽奖和转发”。
```

当前样式为第三版“深色快讯”：图片外显示更新提示，卡片内显示标题和原图预览，原文链接放在图片外。漫画长图只在卡片中预览，完整内容从原文链接打开。可用 `/卡片样式 dark_news` 切回此样式，旧样式仍可选择。

`/B站 测试 UID` 会重发最新可见且符合内容过滤规则的动态，不修改自动推送进度，不触发自动群体提醒。

源码：[深色推送版](https://github.com/3137283455/astrbot_plugin_mahjong_helper/tree/bilibili-push)。参考项目：[Soulter/astrbot_plugin_bilibili](https://github.com/Soulter/astrbot_plugin_bilibili)。
