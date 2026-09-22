# 来源与许可说明

雀魂玩家搜索、战绩、最近对局和订阅功能参考了
[Xcheng-dada/Majsoul-Plugin](https://github.com/Xcheng-dada/Majsoul-Plugin)
的接口参数与功能设计。原项目使用 GNU Affero General Public License v3.0，
本项目因此同样以 AGPL-3.0 发布。

本项目没有移植原项目的抽卡、货币、签到、商店和经济数据，也没有复制其
Yunzai 专用渲染代码。牌谱 Review 的 protobuf 解析和场况渲染被定义为可选
HTTP 网关，未配置时不会影响何切、查询和订阅功能。

`data/images` 中的题面和答案图片来自用户提供的《何切300》《何切301》资料，
只应在你有权使用这些资料的范围内保存和部署，不随代码公开再授权。
