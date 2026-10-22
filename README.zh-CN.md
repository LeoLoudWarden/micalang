# MicaLang

**Mica** 是一门脚本语言及其执行引擎，围绕一个想法构建：**创建一个完全隔离、可以任意嵌套的运行环境，应当是语言自身的一种廉价的第一类操作。**

语法借用 Python 的一个子集，但 Mica 源码会被编译为 Mica 自己的指令流，由 Mica 引擎逐条执行——不经过 `exec`/`eval`。每台 VM 初始能力为零：独立的虚拟 IO、独立的内存文件系统、创建子 VM。其余一切能力（网络、宿主数据、任何你选择授予的东西）都通过 FFI 逐层显式授予。

> **状态：发布前。** 当前仓库包含设计文档与构建手记；引擎代码随提交逐阶段进入仓库——见[构建手记](docs/stages/README.md)，它按阶段重构了 Mica 0.1.0 的构建过程。

## 文档

| 文档 | 内容 |
|---|---|
| [设计说明](docs/design.zh-CN.md) | Mica 是什么、为什么存在、边界划在哪里 |
| [语言参考](docs/language-reference.zh-CN.md) | 自完备的语言子集与语义 |
| [架构说明](docs/architecture.zh-CN.md) | 引擎结构、设计不变量、资源配额 |
| [安全模型](docs/security.zh-CN.md) | 威胁模型；引擎保证什么、不保证什么 |
| [构建手记](docs/stages/README.md) | 逐阶段构建过程，含可运行的检查点 |
| [幕后](docs/behind-the-scenes/README.md) | 项目由来与 AI 辅助开发过程 |

## 名字

Mica（云母）是一种层状矿物：可以沿解理面剥成薄片，每一片都是完整的一层，且层间绝缘。类比于 Mica VM 树的形状：在当前 VM 里创建一台子 VM，每层是一个完整的世界，每层与父级绝缘。

本项目与 Windows 的 Mica 界面材质、PyPI 上的生物信息学包 MICA、以及 GitHub 上已停更的同名 Rust 脚本语言均无关。本项目的公开标识是 **MicaLang**，包名与仓库名为 `micalang`。

## 许可证

[Apache-2.0](LICENSE)
