# 慧慧控制台

独立的 AstrBot 管理网页，采用已确认的“瓷白·系统设置”方向。用户入口：`http://81.70.199.252:6192/admin/`，使用原 AstrBot 面板账号、密码；若启用了动态验证码，也会要求验证。

源码：[GitHub huihui-console 分支](https://github.com/3137283455/astrbot_plugin_mahjong_helper/tree/huihui-console)。控制台与AstrBot插件单独维护；更新控制台不重启机器人。

## 已实现

- 管理员登录、12小时会话、退出与按设备撤销会话。
- 读取实际插件列表，搜索、启用/停用、读取配置、分组编辑、保存和单插件重载。
- 保存前自动备份，版本指纹防止覆盖其他页面刚保存的配置；未知键保留。敏感字段不回传，空白保留原值；含嵌套凭据的复杂配置和服务部署参数不在这里编辑。
- 当前赛季群对局、独立昵称字库、音乐歌单、复盘任务、B站订阅、优惠订阅的搜索、分页与CSV导出。
- 模型信息浏览和人格设定编辑。人格保存前保留备份，保留工具与其他现有字段。
- QQ实际在线状态、当前赛季同步时间、近期复盘任务、管理操作记录。
- 手动插件配置备份、恢复、单插件重新加载与版本信息。
- 桌面三栏、平板紧凑导航、手机列表/详情分屏与底部导航；状态反馈、减少动态效果、键盘焦点与未保存修改提示。

本版业务数据界面为只读检索和导出，暂不提供对局删除、昵称合并、订阅增删、歌单增删，也不开放任意SQL或命令。插件源代码更新仍由AstrBot原面板处理；此处提供版本查看、配置备份与定向重载。库存系统不混入本控制台。

## 运行结构

生产根目录 `/bot/huihui-console/`，`current` 指向一个发布目录，持久数据位于 `data/`。PM2独立进程名 `huihui-console`，使用AstrBot现有Python虚拟环境中的aiohttp。

- 公网6192：控制台和复盘链接的统一入口。
- `/admin/`：静态界面；`/admin/api/`：管理员会话接口。
- `/r/随机标识`：只转发至 `127.0.0.1:6193` 的原复盘网页。原有链接不变，未知路径不能访问管理数据。
- 复盘插件配置：`replay_public_url` 保持公网6192，`replay_web_port=6193`，`replay_web_host=127.0.0.1`。主插件0.4.50支持内部端口与公网端口分离。

环境变量：`ASTRBOT_DIR`（默认`/bot/AstrBot`）、`ASTRBOT_URL`（默认`http://127.0.0.1:6185`）、`CONSOLE_DATA`、`CONSOLE_HOST`、`CONSOLE_PORT`、`REPORT_URL`（默认`http://127.0.0.1:6193`）。默认监听仅本机6194；生产由PM2显式设置为公网6192。

会话与配置备份仅存在权限受限的服务数据目录，不提交Git。浏览器Cookie为HttpOnly/SameSite=Strict，写操作校验CSRF和来源；无CORS放行。登录以原AstrBot接口核验，没有独立默认密码或免密入口。

S0及其他封存赛季只读；当前赛季SQL不读取封存快照，不重跑历史识别。昵称库来自独立字库，代码不会初始化业务Store或触发迁移、同步和模型调用。

## 验证与限制

运行 `python -m unittest discover -s tests -v`，使用临时库与接口替身验证鉴权、CSRF、敏感信息、并发版本冲突、单插件保存、备份与封存数据隔离。界面脚本运行 `node --check static/app.js`。

部署核对了真实只读数据接口、无变更保存、原复盘链接内容一致及QQ连接；未向真实群发送测试消息，也未变更实际插件业务设置。

本次浏览器预览工具因环境连接及URL识别限制中止，未完成真实浏览器视觉和设备兼容性验收。响应式样式已实现，但不能将静态代码检查等同于实际手机浏览器测试。

参考：[Apple HIG Layout](https://developer.apple.com/design/human-interface-guidelines/layout)、[Motion](https://developer.apple.com/design/human-interface-guidelines/motion)。这是网页实现，并非Apple原生控件。
