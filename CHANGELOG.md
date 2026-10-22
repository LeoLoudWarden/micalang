# Changelog

本项目的所有重要变更都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.1.0] - 未发布

首次公开发布。构建过程见构建手记 `docs/stages/`（tag `stage-0`…`stage-6`）。

### Added

- Mica-PY 参考实现：源码经白名单校验编译为内部指令流，由统一调度循环执行，不经 `exec`/`eval`。
- 语言：int/float/str/bool/None/list/dict；`if/while/for`、`break/continue`、`try/except/else/finally`、`raise`、三元表达式；函数、递归、默认参数与关键字实参。
- 嵌套 VM：任意层可创建子 VM，子 VM 不感知层深；父 VM 可预置文件、喂输入、单步调试、快照、终止。
- 能力授予（FFI）：宿主向 root VM 注入函数，任意层 VM 向下一层授予函数；跨宿主边界双向深拷贝 + 纯数据类型白名单。
- 资源治理：指令数、输出字节、调用深度、子 VM 数量、嵌套深度、容器大小、字符串字节、整数位宽、近似内存总量、VM 总数、文件数与单文件字节配额；子 VM 消耗计入所有祖先。
- 每个 VM 自带私有虚拟文件系统（纯内存、纯文本）。
- 回归测试：198 个用例。
- demo：HTTP 能力授予 + 多层虚拟化的 LLM 代码执行示例（`demo` extra，含离线桩服务器）。
