# Changelog

本项目的所有重要变更都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.4.0] - 2026-10-24

### Design

本次更新是类型系统的路线决断：Sericite 不引入名义类型，而是把"类型"拆成两半——**运行期的结构 schema**（一等值，可传递、可授予、按字段与契约比较）与**编译期的词法标签**（只服务报错文本，出作用域即擦除）。

**为什么不引入名义类型。** 名义身份由创建事件决定，在层层嵌套、逐层授予的世界里跨层必撞身份墙：父层的 `Point` 与子层同形状同名的 `Point` 算同一类型吗？结构语义下 schema 按字段集与契约比较，天然跨层可比，这个问题整体不存在。同时沙箱的输出必须是可枚举的纯数据，名字不污染值。

**为什么标签必须随作用域擦除。** Sericite 的宇宙观是层层封闭：你无法知道自己是否嵌套在更大的宇宙里，布景上也不该留着外层世界的厂商标签。一个值若烙着父层起的名字跨层旅行，在子层出错时就会报出一个子层全部源码中从未出现过的符号；而子层的错误消息是可被 `except` 捕获的数据——这个名字就是一条谁都没有显式打开的信息信道，楚门的世界就此破洞。名字是层本地命名空间里的指针，跨层运输一个名字等于运输一个悬垂指针。所以类名与注解名只活在编译期；每一层在自己的入口显式命名（`r2: T2 = make()`），命名是接收方的本地行为，不是随货附赠的遗产。

完整论述见 `docs/design` §3.4「结构跨层，名字层本地」（中英双语），执行纪律固化为架构不变量 16。

### Added
- 语言新增 record 与 schema：`class` 定义运行期结构契约（字段声明 `x` 或 `y: int`），schema 是一等运行期值、每次执行 `class` 语句即创建；record = 载荷 + schema 引用，支持点号成员读写，形状固定（字段不可增删、写入受字段契约约束）。
- schema/record 相等为结构相等（字段名 + 字段契约；record 另要求载荷相等）；record 与 dict 永不相等。新增内建 `wrap(schema, dict)`（严格键集套皮）与 `unwrap(record)`（浅拷贝扒皮）；新增契约标记 `any`。
- 注解从静默忽略变为真实执行的运行期契约：`def f(p: T)`、`x: T = value`、`def f(...) -> T` 分别在调用/赋值/返回时检查（宽度子类型：实参 record 包含期望字段即可），dict 不自动套皮；报错带 blame 方向（argument / return value / annotated assignment）。新增错误 kind：`attribute_error`、`contract_error`。
- 报错标签：类名与注解文本是编译期、作用域本地的诊断标签（如 `record Point {x: int} has no field 'y'`），不进入值、出作用域失效，无标签时按结构显示。
- record/schema 不能跨宿主 FFI 边界（报 `ffi_error` 并提示 unwrap）；经授予函数跨 VM 按引用传递，契约检查跨 VM 成立；授予函数在子 VM 的报错（参数契约、返回契约、参数数量）一律使用授予名与结构描述，定义方的函数名与类型标签不越界。新值创建纳入内存配额，契约检查按字段数折算指令。
- 属性访问的 VM 白名单校验从编译期移到运行期（`sub.evil()` 现在编译通过、运行期报 `attribute_error`）；`def f(p: int)` 等注解位不再误报保留名错误。
- design 文档新增 §3.4"结构跨层，名字层本地"，架构文档新增不变量 16（标签只烘焙进本层 CodeUnit、授予函数报错用授予名与结构描述），固化 record/schema 设计的纪律。
- 回归测试增至 285 个用例。

## [0.3.1] - 2026-10-23

### Security
- 修复授予函数的名字解析：函数体未命中定义方模块变量时，不再回退到调用方 VM 的同名 `granted` 函数，而是保留定义方已持有的授予函数环境，避免调用方伪造同名能力。
- 提高子 VM 源码编译的指令折算系数，防止大源码以过低的指令预算消耗宿主 CPU。
- 闭包 cell 与捕获环境改用更保守的内存记账系数。
- 安全文档明确：被授予的 sericite 函数会共享定义方可达的可变状态，授予函数即授予这部分能力；未被授予的父级变量仍不可见。
- 回归测试增至 224 个用例。

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
