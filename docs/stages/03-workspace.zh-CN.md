# Stage 3：给每台 VM 一个工作区

上回说到，引擎换上了指令流的心脏，`if`、`while`、函数、递归都活了。但引擎还存在许多问题。

这一篇，我们来解决其中的三个问题：脚本无法自恢复错误、"layer 1 不配创建 VM"这条说不通的禁令，以及 VM 没有自己的工作空间。

解决后，每台 VM 都会拥有一个完整的工作区：自己的文件、自己的错误处理、自己创建下一层 VM 的权利。

## 先做异常，因为它是地基

我们先做 try/except，顺序是有讲究的：后面要做的文件 IO，每个方法都会抛 `file_not_found` 之类的错误；子 VM 运行失败也要向父级传播。这些错误要是没有异常机制接住，全都无处可去。

先看看没有它的时候有多窘迫。stage-2 的 `_execute_one` 里，`except MicaError` 只有一条路——`_fail()`。脚本除以零、读不存在的名字，VM 直接进 `error` 终态，连一句"让我试试别的办法"都说不出口。

## 异常表：正常路径零开销

实现 try/except 时，核心难题是：当错误发生时，如何把求值栈（Evaluation Stack）恢复到进入 try 块时的状态？教科书里有两条路线：

动态标记（Runtime Markers）：
  - 做法：每当程序进入一个 `try` 块，就在栈上压入一个特殊的标记，或者在另一个专门的标记栈里记录当前深度，用于说明"出事跳这里"。
  - 出错时：弹出栈顶所有元素，直到遇到特殊的标记，恢复成功。
  - 代价：正常运行每一路 `try` 都要执行指令（压栈/弹栈）。

静态分析（Static Exception Tables）：
  - 做法：在编译期就计算出每个 `try` 块开始时的栈深度 S，并把 S 记录在异常表里。
  - 出错时：直接执行 `del stack[S:]`， 瞬间恢复。
  - 代价：运行期零开销，但**编译器必须具备"预知"栈深度的能力**。

经过取舍，我的选择是后者。因为动态标记法中，正常路径要为异常处理付出栈操作代价，而且 `break`/`continue`/`return` 穿越 try 时每种都要额外编译处理。

我的选择是 CPython 3.11 式的异常表。异常处理不占用任何一条指令，只是 `CodeUnit` 旁边的一张表：

```python
@dataclass
class Handler:
    start: int
    end: int
    target: int
    kinds: tuple[str, ...] | None
    bind: str | None
    stack_depth: int = 0


@dataclass
class CodeUnit:
    name: str
    params: list[str]
    instrs: list[Instr] = field(default_factory=list)
    handlers: list[Handler] = field(default_factory=list)
```

（`mica/isa.py:37-52`）

具体的实现方式是：为每个 `try` 块建立一个 `Handler`。它记录了指令的起止区间 `[start, end)`，一旦在这个范围内出了 `kinds` 里定义的错，就跳到 `target` 处执行，并把错误消息交给变量 `bind`（`kinds=None` 就是裸 `except`）。

这里有个关键的设计点：**异常机制没有引入任何新指令（opcode）**。这套机制在指令集层面是透明的，它是一张在侧边挂载的查询表，不占用执行路径，对正常运行毫无干扰。

至于本阶段新增的 `CALL_KW` 指令，是为了支持关键字参数，与异常机制无关，稍后会讲。

编译 try 语句时，正常路径的代价就是末尾一个 `JUMP`；所有 except 子句共享同一个 `(body_start, body_end)` 区间，靠 `target` 区分：

```python
        body_start = len(code.instrs)
        self.compile_body(node.body, code)
        body_end = len(code.instrs)
        exit_jumps = [self.emit(code, isa.JUMP, None, line)]
        ranges: list[tuple[int, int, int, tuple[str, ...] | None, str | None]] = []
        for handler in node.handlers:
            target = len(code.instrs)
            kinds = (
                None
                if handler.type is None
                else (handler.type.id,)  # type: ignore[union-attr]
            )
            ranges.append((body_start, body_end, target, kinds, handler.name))
            self.compile_body(handler.body, code)
            exit_jumps.append(self.emit(code, isa.JUMP, None, line))
        end = len(code.instrs)
        for index in exit_jumps:
            code.instrs[index].arg = end
        for start, stop, target, kinds, bind in ranges:
            code.handlers.append(
                isa.Handler(
                    start=start,
                    end=stop,
                    target=target,
                    kinds=kinds,
                    bind=bind,
                )
            )
```

（`mica/compiler.py:421-448`）

## 一个难缠的细节：栈上可能有垃圾

异常表方案有个难缠的细节：异常被捕获的那一刻，求值栈可能处于**任意深度**——错误也许发生在 `f(a + b, g(c), ...)` 求值到一半的时候，栈上躺着半成品的参数。跳到 handler 之前，必须把栈截断回进入 try body 时的深度。可这个深度是多少？运行时算不出来，只能编译期算。

为了让编译器"预知"栈深，我们得做一次栈深度分析（在编译器理论中称为"不动点分析"）。简单来说，就是一种填空游戏：我们先假设入口深度是 0，然后沿着程序的执行路径把这个深度往后传。如果遇到跳转，就分两路传。我们反复地传、传、传，直到所有指令的深度都稳定下来，不再变化为止。

传播规则是：

- 对于普通指令——按固定的栈增减量，把深度传给下一条；
- 对于跳转指令——则两路后继都要传播；
- 如果某条指令从两条路收到不一样的深度，说明编译器输出的指令流不自洽——直接 `internal_error`，视为编译器自身的 bug（汇合校验在函数后半，摘录从略）。

代码不长，但有两处值得停下来看：

```python
def _analyze_stack(code: isa.CodeUnit, filename: str) -> None:
    instrs = code.instrs
    if not instrs:
        return
    depths: list[int | None] = [None] * len(instrs)
    depths[0] = 0
    worklist = [0]
    while worklist:
        i = worklist.pop()
        depth = depths[i]
        assert depth is not None
        instr = instrs[i]
        successors: list[tuple[int, int]] = []
        if instr.op == isa.JUMP:
            successors.append((instr.arg, depth))
        elif instr.op == isa.JUMP_IF_FALSE:
            successors.append((instr.arg, depth - 1))
            successors.append((i + 1, depth - 1))
        elif instr.op in (isa.JUMP_IF_TRUE_KEEP, isa.JUMP_IF_FALSE_KEEP):
            successors.append((instr.arg, depth))
            successors.append((i + 1, depth - 1))
        elif instr.op == isa.RETURN:
            pass
        else:
            if instr.op == isa.BUILD_LIST:
                delta = 1 - instr.arg
            elif instr.op == isa.CALL:
                delta = -instr.arg
            elif instr.op == isa.CALL_KW:
                delta = -(instr.arg[0] + len(instr.arg[1]))
            else:
                delta = _STACK_DELTAS[instr.op]
            successors.append((i + 1, depth + delta))
```

（`mica/compiler.py:139-171`）

先看两个 `KEEP` 变体的后继深度为什么不对称：跳转走的那路深度不变，因为栈顶的值要留下来当短路结果；顺序走的那路深度减一，因为值被弹掉了。上一篇"弹不弹栈"的语义差别，在这里体现为两条不同的传播边。

再看那句 `assert depth is not None`：能进 worklist 的指令，必然已被某条路径标注过深度。这个断言守的是算法本身的不变量。

分析完，给每个 handler 回填它看护区间起点的深度：

```python
    for handler in code.handlers:
        start_depth = depths[handler.start]
        if start_depth is not None:
            handler.stack_depth = start_depth
```

（`mica/compiler.py:188-191`）

这一步还有一个副产品：编译器由此第一次具备了自校验能力——它会验证自己输出的指令流在跳转汇合处是否自洽。

运行时的异常展开，代码很短，但三行各干了一件大事：

```python
    def _handle_exception(self, context: VMContext, error: MicaError) -> bool:
        if error.kind in _UNCATCHABLE_KINDS:
            return False
        while context.frames:
            frame = context.frames[-1]
            handler = self._find_handler(frame, error.kind)
            if handler is None:
                if len(context.frames) == 1:
                    return False
                context.frames.pop()
                continue
            del frame.stack[handler.stack_depth :]
            if handler.bind is not None:
                frame.locals[handler.bind] = error.message
            frame.pc = handler.target
            return True
        return False
```

（`mica/engine.py:290-306`）

- `del frame.stack[handler.stack_depth:]`：截断求值栈，编译期的深度分析在此兑现。
- `frames.pop()`：本帧没人接，就弹帧继续找——异常由此跨函数传播。
- `bind`：把错误消息字符串写进局部变量，脚本里 `except kind as name` 拿到的就是它。

## 配额错误必须不可捕获

注意 `_handle_exception` 的第一行。`_UNCATCHABLE_KINDS = {"resource_exhausted", "internal_error"}`，这两类错误直接放行失败，任何 `except` 都接不住。

这不是技术限制，是一条铁律。想想看：如果 `resource_exhausted` 能被脚本捕获，脚本就可以"犯错—捕获—重试"无限循环，指令配额形同虚设。资源治理的前提是**超限这件事不容商量**。第二篇里"程序错了"与"资源耗尽了"的那条分界线，在这里第一次显出威力。

`internal_error` 不可捕获，则是另一个方向的考虑：它是引擎自身故障的信号，脚本无权把它当成一种可以处理的普通情况——引擎病了，游戏就该停。

## 删掉"层级"这个概念

现在解决第二个问题：把"层级"这个概念从引擎里删掉。

先看它当时的样子。stage-2 里 `VMContext` 带着 `layer` 和 `allow_vm` 两个字段：root 是 `allow_vm=True`，子 VM 硬编码 `False`。还有一条测试 `test_layer1_has_no_vm`，把"layer 1 禁止创建 VM"固化成了预期行为。

开发时觉得这是"权限默认收缩"，但越往后越说不通：如果嵌套隔离是语言的核心命题，凭什么哪一层不配创建 VM？递归的隔离需求，意味着每一层都可能需要造下一个盒子。

所以这个阶段，`allow_vm`、`layer`、`<mica:layer_N>` 文件名、`layer=` 的 repr 全部删除：

- 句柄的 repr 变成 `<VM id=N state=...>`，文件名变成 `<mica:vm_N>`；
- 保留下来的是一个降格的 `depth` 字段——它不再是身份标识，只是 `max_vm_depth=8` 这条配额的计数器。

理念是：**VM 不感知自己的层深度，"我自己就是根"**——脚本无法知道自己嵌套在第几层，这本身就是隔离的一部分。

最直观的物证是测试的反转：`test_layer1_has_no_vm` 被改写为 `test_layer1_can_create_vm`——同一个场景，上一阶段断言"必须报错"，这一阶段断言"必须成功"。

连 `cli.py` 里一处错误消息的文件名，都从 `<mica:layer_0>` 改成了 `<mica>`。去层级化连字符串字面量都不放过，这也是"概念删除"类重构最容易漏的角落。

## 子 VM 的配额：只紧不松，祖先连坐

任意层都能创建 VM 了，资源治理就得跟上，否则嵌套就是敞开的后门。规则有两条。

第一，子 VM 可以声明自己想要的配额，但只紧不松。`VM(code, max_instructions=10000)` 的有效值是 `min(父值, 请求值)`：

```python
    def _create_child(
        self, parent: VMContext, source: object, config: dict[str, object]
    ) -> VMHandle:
        if not isinstance(source, str):
            raise MicaError("VM source must be a string", kind="type_error")
        limits = parent.limits
        if config:
            overrides: dict[str, int] = {}
            for key, value in config.items():
                if key not in _VM_OPTIONS:
                    raise MicaError(
                        f"unknown VM option: {key}", kind="type_error"
                    )
                if type(value) is not int or value < 0:
                    raise MicaError(
                        f"VM option {key} must be a non-negative int",
                        kind="type_error",
                    )
                overrides[key] = min(getattr(parent.limits, key), value)
            limits = dataclasses.replace(parent.limits, **overrides)
        depth = parent.depth + 1
        if depth > limits.max_vm_depth:
            raise MicaError("VM depth limit exceeded", kind="resource_exhausted")
        if parent.child_count >= parent.limits.max_child_vms:
            raise MicaError(
                "child VM limit exceeded", kind="resource_exhausted"
            )
        parent.child_count += 1
        return self._create_vm(
            source=source,
            parent_id=parent.vm_id,
            depth=depth,
            io=VirtualIO(max_output_bytes=limits.max_output_bytes),
            limits=limits,
        )
```

（`mica/engine.py:530-564`）

`min(getattr(parent.limits, key), value)` 这一行就是"只紧不松"的全部实现。为了支持 `VM(code, max_instructions=...)` 这种写法，语言顺便获得了关键字实参（新指令 `CALL_KW`）。

第二，祖先记账。子 VM 每执行一条指令，除了记在自己头上，还沿 `parent_id` 链记到所有祖先头上。看 `_execute_one` 开头这一段（第二篇见过的计数检查没动，夹在中间的祖先记账循环是新加的）：

```python
        context.instruction_count += 1
        ancestor = context
        while ancestor.parent_id is not None:
            ancestor = self._contexts[ancestor.parent_id]
            ancestor.instruction_count += 1
        if context.instruction_count > context.limits.max_instructions:
            self._fail(
                context,
                MicaError("instruction limit exceeded", kind="resource_exhausted"),
                instr.line,
            )
            return False
```

（`mica/engine.py:244-255`）

没有这条，嵌套树的总开销就是无界的：一台 VM 配额一百万条指令，它生一百个孙子各跑一百万，世界就烧掉了。有了祖先记账，整棵树的消耗收敛于根的预算。

细心的读者会注意到：记账是即时的，但检查是延迟的，检查只针对当前 context 自己——子 VM 执行一条指令，祖先的计数器立刻加 1，但祖先此时在"睡眠"状态，不会触发超限检查。只有当子 VM 运行完，控制权回到祖先，祖先尝试执行下一条指令时，才会发现自己的配额已经被子孙耗尽了。这种"**有界但不即时**"的特性在细节上有坑，暂且先记下。

## 虚拟文件系统：让"碰不到真磁盘"成为结构事实

最后一个大件：每台 VM 一个私有虚拟文件系统。

设计说明里立过规矩：VFS 就是内建能力的上限，到此为止不再多给。但"上限"也得是真隔离才行。我的思路是，把安全性做成**结构事实**，而不是**纪律承诺**。

所谓的"纪律承诺"是：我在代码里写 `if path.startswith('/'): raise Error`，这依赖于我没写漏，依赖于我的警觉。而`结构事实`是：VFS 根本就不 `import os` 模块，也没有任何接口能接触到真实磁盘。这意味着即便我想写漏，在物理上也不可能逃逸。

具体实现方式为：

- VFS 就是一个平坦的 `{规范化路径: 文本}` 字典，目录靠路径前缀隐式存在；
- `VirtualFileSystem` 不 import `os`/`pathlib`；
- 引擎里不存在任何把 VFS 路径交给真实 `open()` 的通道。

没有任何 API 能拼出指向宿主文件系统的路径——不是"不应该"，是"不能够"。

路径规范化是纯字符串运算，`..` 逃逸在结构上不可能：

```python
def normalize_path(path: object) -> str:
    if not isinstance(path, str):
        raise MicaError("path must be a string", kind="type_error")
    parts: list[str] = []
    for segment in path.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(segment)
    return "/" + "/".join(parts)
```

（`mica/vfs.py:6-18`）

`..` 在根处被静默钳制——`/../../etc/passwd` 规范化后是 `/etc/passwd`，仍然落在自己的沙箱里（`test_path_normalization_no_escape` 专门验证这个输入）。VFS 只存文本，二进制不在引擎的论域里。

目录既然只是隐式存在的前缀，怎么判断一个路径"是目录"？答案是没有专门 API：对一个目录路径调 `read_file`，会吃到一个 `is_directory` 错误。这个设计此时看不出妙处，先记下这个错误种类——第六篇它会派上大用场。

有了 VFS，父 VM 和子 VM 的协作姿势就完整了：运行前往子 VM 的文件系统里预置材料（比如一份 `/config.txt`），运行后从里面取回产出（比如 `/result.txt`）。而父 VM 自己的 `path_exists("/config.txt")` 是 `False`——隔离成立的直接证据。

## 一个顺手修掉的分叉隐患

讲个开发中的小事故。stage-2 时，VM 句柄的方法白名单存在两份：compiler.py 一份，engine.py 一份，两个一模一样的集合。

这个阶段给句柄加了 4 个文件方法，编译器那份没跟上——编译期拒绝、运行期放行，行为分叉。重复即隐患，教科书级的案例，而且真的咬了我一口。

修复方式是单一来源：白名单只在 compiler.py 定义，engine.py `from .compiler import VM_METHODS`。

## 验证

本阶段新增三个示例：

- `try_except.mica`：按 kind 捕获、跨函数帧捕获、捕获 VFS 的 `file_not_found`、循环里反复捕获 `zero_division`；
- `nested_vm.mica`：三层嵌套，最深的 VM 打印的话被中间层捕获再上交；
- `vfs.mica`：预置配置、写结果、取回、验证父 FS 里不存在那份配置。

```bash
python -m unittest discover -s tests   # 97 个用例
python -m mica examples/nested_vm.mica
python -m mica examples/vfs.mica
```

测试 66 → 97。值得一看的是 `test_stack_sane_after_catch`——捕获异常后栈上无残留垃圾，栈深度分析的端到端证明。

## 留下了什么坑

- **异常还太简陋。** 没有 `raise`，脚本无法主动抛错；没有 `finally`——编译器显式拒绝，还有一条 `test_finally_rejected` 把"必须拒绝"固化成了预期行为（记住这条测试，第五篇它有戏份）；`except` 只能按 kind 名字过滤；绑定的异常值只是消息字符串，没有结构化信息。
- **记账不即时。** 祖先配额要等祖先恢复执行才检查，前面说过了。
- **VFS 只有四个固定方法。** `read_file`/`write_file`/`list_dir`/`path_exists`，到此为止。可如果宿主想给 VM 一份"从网络拉数据"的能力呢？难道每个需求都要进引擎加一个内建？
- 没有 `for`、没有字典。

最后这条是最大的缺口，也正是下一篇的主题。下一篇讲数据与能力：字典补全表达能力，而 FFI 能力授予——宿主把函数注入 VM——会回答"能力从哪来"这个根本问题。可以说从下一篇起，Mica 才开始像一门"能干活"的语言。

---

- tag：`stage-3`
- 测试：97 个用例全部通过
