# 开发文档

面向开发者。用户文档见根目录 `README.md`。

| 文档 | 内容 |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 定位、分层、候选身份、交付边界与安全契约 |
| [DEVELOPMENT.md](DEVELOPMENT.md) | 模块地图、修改原则与验证命令 |
| [REFERENCES.md](REFERENCES.md) | 外部参考范围与许可边界 |

阅读顺序：`ARCHITECTURE.md` → 对应 `tests/` → 实现。测试是可执行契约，文档与实现冲突时以测试为准。
