# MicaLang

**Mica** 是一门脚本语言及其执行引擎，围绕一个想法构建：**创建一个完全隔离、可以任意嵌套的运行环境，应当是语言自身的一种廉价的第一类操作。**

语法借用 Python 的一个子集，但 Mica 源码会被编译为 Mica 自己的指令流，由 Mica 引擎逐条执行——不经过 `exec`/`eval`。每台 VM 初始能力为零：独立的虚拟 IO、独立的内存文件系统、创建子 VM。其余一切能力（网络、宿主数据、任何你选择授予的东西）都通过 FFI 逐层显式授予。

## 状态

0.1.0 原型。引擎是 Python 参考实现（代号 **Mica-PY**）：零运行时依赖，Python ≥ 3.12，198 个回归测试用例。1.0 之前 API 与语义可能变化。

> **安全提示。** Mica 为隔离而设计，但当前原型与宿主同进程运行，未经正式安全审计。执行对抗性代码时，不要把 Mica 当作**唯一**的隔离边界。引擎保证什么、不保证什么，见[安全模型](docs/security.zh-CN.md)。

## 安装

```bash
pip install micalang
```

发行包名为 `micalang`，导入包名为 `mica`。

## 快速上手

命令行：

```bash
printf 'print("hello from mica")\n' > hello.mica
micalang hello.mica        # hello from mica
```

嵌入 Python：

```python
from mica import MicaEngine

engine = MicaEngine()
vm = engine.create_root("""
sub = VM("print('hello from a child VM')")
print(sub.run())
""")
print(vm.run(), end="")
```

向 VM 授予能力（FFI）：

```python
def double(x):
    return x * 2

vm = engine.create_root("print(double(21))", functions={"double": double})
print(vm.run(), end="")    # 42
```

## 一台 Mica VM 是什么

一台廉价、自包含的工作空间：

- 看不到父级的变量、文件和句柄，也不知道自己嵌套在第几层；
- 有独立的空白内存文件系统（纯文本——结构上碰不到真实磁盘）；
- 资源配额（指令、内存、输出、子 VM 数、嵌套深度……）只能收紧不能放宽，子 VM 的消耗计入所有祖先；
- 父级可以预置文件、喂输入、逐语句或逐指令单步、查看快照、随时终止；
- 它可以创建自己的子 VM——隔离是递归的，因为不可信的代码是递归的。

## 文档

| 文档 | 内容 |
|---|---|
| [设计说明](docs/design.zh-CN.md) | Mica 是什么、为什么存在、边界划在哪里 |
| [语言参考](docs/language-reference.zh-CN.md) | 自完备的语言子集与语义 |
| [架构说明](docs/architecture.zh-CN.md) | 引擎结构、设计不变量、资源配额 |
| [安全模型](docs/security.zh-CN.md) | 威胁模型；引擎保证什么、不保证什么 |
| [构建手记](docs/stages/README.md) | Mica 0.1.0 逐阶段构建过程，含可运行检查点（`git checkout stage-1` … `stage-6`） |
| [幕后](docs/behind-the-scenes/README.md) | 项目由来与 AI 辅助开发过程 |

## 这个项目是怎么写出来的

Mica 全程使用 AI 辅助开发——设计讨论、实现、测试、对抗性审查、文档起草。AI 做了什么、作者做了什么、产出如何被验证，记录在 [AI 辅助开发说明](docs/behind-the-scenes/ai-assisted-development.zh-CN.md)，附开发对话选编。

## 路线图

- 列表/字典/字符串方法与更多易用性语法
- 性能基准（子 VM 创建成本、每指令开销）
- 规范化的语言规范与一致性测试套件，供未来非 Python 引擎对照验证
- 原生引擎评估（包名刻意取语言级：`micalang` 在引擎重写后依然成立）

## 名字

Mica（云母）是一种层状矿物：可以沿解理面剥成薄片，每一片都是完整的一层，且层间绝缘。这正是 Mica VM 树的形状。

本项目与 Windows 的 Mica 界面材质、PyPI 上的生物信息学包 MICA、以及已停更的同名 Rust 脚本语言均无关。

## 许可证

[Apache-2.0](LICENSE)——见 [NOTICE](NOTICE)。
