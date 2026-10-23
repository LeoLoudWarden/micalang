# Changelog

本项目的所有重要变更都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.3.0] - 2026-10-23

### Added
- 语言新增词法闭包：嵌套函数按引用捕获外层局部变量，跨 VM 授予与再次授予时保留定义方闭包环境和模块全局；闭包 cell 与捕获环境纳入内存配额。
- 语言新增函数装饰器：装饰器表达式从上到下求值，堆叠装饰器从下到上应用，结果重新绑定到函数名。
- 新增 `benchmarks/vm_creation.py`：VM 创建成本的可复现基准（宿主侧与脚本内两条路径，附线程/进程参照）。实测：脚本内创建子 VM 约 0.05 ms，宿主侧创建+运行全流程约 0.04 ms（Intel i3-9100，CPython 3.13/3.14），与线程同数量级、比进程便宜一到两个数量级；design 文档相应段落由"有待量化验证"更新为实测陈述。
- 新增 `benchmarks/instruction.py` 与 `benchmarks/debugging.py`：按真实指令计数测量连续执行、逐指令与逐语句调试成本，编译和 VM 创建不计时。在 Intel i3-9100、CPython 3.13.13 上，代表性循环约 0.65 µs/指令，逐指令约 1.86 µs/指令，逐语句约 4.91 µs/次调用；中英文架构文档与 README 同步记录结果。
- 英文文档补齐：`docs/architecture.md`（架构说明全译）；`docs/`、`docs/stages/`、`docs/behind-the-scenes/`、`demo/` 的索引页按"英文 `.md`、中文 `.zh-CN.md` 单文件不混排"的规范拆分为独立英文版。stages 正文与 behind-the-scenes 长文保持中文（历史文档，翻译成本高、价值低）。
- 回归测试增至 221 个用例。

## [0.2.0] - 2026-10-22

### Changed
- 项目更名：Mica/MicaLang → Sericite。发行包名 `micalang` → `sericite`，import 包名 `mica` → `sericite`，CLI 命令 `micalang` → `sericite`，源码扩展名 `.mica` → `.sericite`，Python API 类名 MicaEngine/MicaError/MicaLimits → SericiteEngine/SericiteError/SericiteLimits。更名缘由见 README "The name"。
- design 文档修正先例论述：补充 Safe-Tcl 作为直接祖先的考据，新增 docs/prior-art.md《先例与相关工作》（中英双语）。

## [0.1.0] - 未发布

首次公开发布。构建过程见构建手记 `docs/stages/`（tag `stage-0`…`stage-6`）。

### Added

- Sericite-PY 参考实现：源码经白名单校验编译为内部指令流，由统一调度循环执行，不经 `exec`/`eval`。
- 语言：int/float/str/bool/None/list/dict；`if/while/for`、`break/continue`、`try/except/else/finally`、`raise`、三元表达式；函数、递归、默认参数与关键字实参。
- 嵌套 VM：任意层可创建子 VM，子 VM 不感知层深；父 VM 可预置文件、喂输入、单步调试、快照、终止。
- 能力授予（FFI）：宿主向 root VM 注入函数，任意层 VM 向下一层授予函数；跨宿主边界双向深拷贝 + 纯数据类型白名单。
- 资源治理：指令数、输出字节、调用深度、子 VM 数量、嵌套深度、容器大小、字符串字节、整数位宽、近似内存总量、VM 总数、文件数与单文件字节配额；子 VM 消耗计入所有祖先。
- 每个 VM 自带私有虚拟文件系统（纯内存、纯文本）。
- 回归测试：198 个用例。
- demo：HTTP 能力授予 + 多层虚拟化的 LLM 代码执行示例（`demo` extra，含离线桩服务器）。
