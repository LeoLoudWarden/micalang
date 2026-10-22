# Stage 1：最小可用的 VM

这个系列讲怎样从零造出 Mica——一门可以把"创建一个完全隔离的宇宙"当作一句普通语句的脚本语言。设计理念在[设计说明](../design.zh-CN.md)里，这里不重复论证，只讲一件事：它是怎么一步步被造出来的。

仓库中 `stage-0` 只包含了文档，没有一行代码——即设计说明、语言参考、安全承诺这些。`stage-1` 为本阶段完成时的状况。

我们需要一步步用代码兑现文档中的目标——设计说明里说"创建子 VM 应当像建一张表一样便宜"，[原动力](../behind-the-scenes/origin.zh-CN.md)里说"'exec 加补丁'这条路已经证伪"，空口白话是站不住的。第一阶段的任务只有一个：**用最少的代码，验证最要命的结构假设**。

## 先验证结构，再谈语法

哪个假设最要命？**所有层级的 VM 共用同一个引擎。**

基于层层隔离的设计理念，我们来想想需要实现什么。如果每个子 VM 都套一层新的解释器实例，那嵌套就只是一种递归 trick——每多一层，计量、IO、调试都要在新实例里重做一遍，"上帝管理自己创造的宇宙"就成了一句空话。我们想要的画面是：根 VM 和第一百零八层孙 VM 在引擎眼里是同一类东西，只是执行上下文不同。这个假设如果是错的，后面加多少语法都是白搭。

所以第一阶段的语法能小则小。宁可简陋，也要让五件事同时成立：白名单校验、执行器、IO 隔离、单步调试、子 VM 创建。

小到什么程度？所有合法语法就在这两个集合里：

```python
class MicaEngine:
    _ALLOWED_NODES = {
        ast.Module,
        ast.Assign,
        ast.Expr,
        ast.Name,
        ast.Load,
        ast.Store,
        ast.Constant,
        ast.List,
        ast.BinOp,
        ast.Add,
        ast.Call,
        ast.Attribute,
    }
    _RESERVED_NAMES = {"input", "print", "VM"}
```

（`mica/engine.py:112-127`）

十二种 AST 节点，三个保留名。包括：赋值、表达式、字符串和数字常量、列表、`+`、函数调用。没有条件，没有循环，没有函数，没有异常。

这个语法集只够写一件事：

```python
a = input()
b = input()
c = a + b
print(c)
```

而这就够了——验证结构不需要更多语法。

## AST + 白名单

第一个问题：我们的 Mica 代码作为字符串，要如何运行？

请大家想想，Python 的 `exec` 有什么问题？为什么不能在 Python 里禁掉几个危险名字，让 `exec` 变得安全？——答案是显然的，此处不做展开。`exec` 如同一个脐带直通 Python，Mica 必须要与之分离，一开始就必须换结构。

分离思路是：源码经 `ast.parse()` 解析成树，过一遍正向白名单——**没见过的节点类型默认非法**——然后由 Mica 自己的执行器逐节点求值，全程不碰 `exec`/`eval`。白名单的好处是，语法集天然是个最小闭集。黑名单则不然，相当于从 Python 处借了全部能力，再一个个堵漏洞，不利于开发维护。

不过，节点类型合法还不够。这里需要引入一个关键概念：句柄（`Handle`）。我们需要让脚本能控制子 VM，又不能直接把 VM 的内部状态暴露出去。解决方案是，把 VM 包装成一个 `VMHandle` 对象，上面挂着一组白名单方法（`_METHODS`）——`run`、`step` 之类，后面会详细讲。

下面这段代码截取自校验器的 AST 节点遍历，有好几条规则就是为守住这些入口而设的：

```python
            if isinstance(node, ast.Name):
                if node.id.startswith("__"):
                    self._reject(node, filename, "dunder names are not available")
                if node.id in self._RESERVED_NAMES:
                    parent = parents.get(node)
                    if not (
                        isinstance(parent, ast.Call) and parent.func is node
                    ):
                        self._reject(
                            node,
                            filename,
                            f"builtin '{node.id}' can only be called directly",
                        )
            if isinstance(node, ast.Attribute):
                if node.attr.startswith("__"):
                    self._reject(node, filename, "dunder attributes are not available")
                if node.attr not in VMHandle._METHODS:
                    self._reject(node, filename, f"unsupported attribute: {node.attr}")
                parent = parents.get(node)
                if not (isinstance(parent, ast.Call) and parent.func is node):
                    self._reject(
                        node,
                        filename,
                        "VM methods can only be called directly",
                    )
```

（`mica/engine.py:206-230`）

以上代码中，`parents` 是预先建好的"每个节点 → 它的父节点"映射。判定非法时，通过函数 `self._reject` 带行号报错。有三条判定规则值得注意：

- dunder 名（`__class__` 之类）是 Python 反射的大门，直接禁掉。
- `print`、`VM` 这些保留名，以及 VM 句柄的方法，**只能被直接调用**。
- 如果有人写 `f = print; f("x")`试图把受控入口取个引用存进变量、脱离调用现场再用，校验器会直接拒绝。

关于第三条，我们可以对比一下两种写法在 AST 树中的结构差异：

直接调用： `print("Hello")`
```
Call
 ├── func: Name
 │    └── id: "print"  (父节点为 Call)
 └── args:
      └── Constant
           └── value: "Hello"
```

赋值引用： `f = print`
```
Assign
 ├── targets:
 │    └── Name
 │         └── id: "f"
 └── value:
      └── Name
           └── id: "print" (父节点为 Assign)
```

看到区别了吗？直接调用时，`print` 这个 `Name` 节点的父节点是一个 `Call` 节点。校验器看到这个结构，会觉得："哦，你是在直接调用 `print`，这是允许的。"赋值引用时，`print` 的父节点变成了 `Assign` 而不再是 `Call`。校验器靠 `parents` 映射"往回看一眼"，一旦发现父节点不是调用现场，直接拒绝。

为什么要这么做？为了安全。如果允许 `f = print`，那么用户就可以通过"绕路"来欺骗校验器。假设我们只禁用了某些危险操作，但允许 `print`，如果用户能写 `f = print`，他就可以把 `print` 这个函数像普通变量一样传来传去：
1. 把 `print` 存进一个列表 `list = [print]`。
2. 把 `print` 传递给另一个函数。
3. 在某个复杂的逻辑之后，突然调用 `f()`。

一旦函数引用被赋值给变量，它就脱离了调用现场。对于前者，校验器在扫描 AST 时，它能百分之百确定：这个 `print` 就在这里被执行了，我可以立刻检查这个调用是否安全。但如果允许 `f = print`，`print` 就变成了一个"潜伏者"，它可以在代码中被传递，直到在某个校验器没预料到的地方被执行。

**受控入口只能以它本来的面目出现**，这条原则以后每加一个新入口都会用到。AST 校验经常需要这种回头看父节点的能力，这个 `parents` 映射后面还会一再出场。

## 一个引擎，装下所有宇宙

结构验证的核心证据在这个 dataclass 里：

```python
@dataclass
class VMContext:
    vm_id: int
    layer: int
    parent_id: int | None
    source: str
    tree: ast.Module
    io: VirtualIO
    allow_vm: bool
    locals: dict[str, object] = field(default_factory=dict)
    pc: int = 0
    state: str = "ready"
    last_line: int | None = None
    error: MicaError | None = None
```

（`mica/engine.py:46-59`）

引擎内部就是一个扁平的 `dict[int, VMContext]`。没有"根引擎"和"子引擎"之分，没有递归嵌套的解释器实例——每台 VM 的全部状态就是这些字段：源码、语法树、自己的 IO、局部变量、程序计数器、生命周期状态。

那么"创建子 VM"和"创建根 VM"差在哪？只差三个参数：

```python
    def _create_child(self, parent: VMContext, source: object) -> VMHandle:
        if not isinstance(source, str):
            raise MicaError("VM source must be a string")
        return self._create_vm(
            source=source,
            parent_id=parent.vm_id,
            layer=parent.layer + 1,
            io=VirtualIO(),
            allow_vm=False,
        )
```

（`mica/engine.py:361-370`）

层数加一、一份全新的空 IO、`allow_vm=False`——这三个差异就是"隔离"的全部物理实现。子 VM 的 `VirtualIO` 不带输入提供器，物理上够不到宿主的 stdin；`allow_vm=False` 则意味着 layer 1 默认不能再创建 VM。权限按层递减，默认收缩，想给什么能力得显式开口子。

保留名则是在执行期按上下文现场构造的：

```python
    def _evaluate_name(self, context: VMContext, name: str) -> object:
        if name in context.locals:
            return context.locals[name]
        if name == "input":
            return _NativeFunction("input", context.io.read_input)
        if name == "print":
            return _NativeFunction(
                "print", lambda *values: self._print(context, values)
            )
        if name == "VM" and context.allow_vm:
            return _NativeFunction(
                "VM", lambda source: self._create_child(context, source)
            )
        raise MicaError(f"name '{name}' is not defined")
```

（`mica/engine.py:346-359`）

注意 `input`/`print`/`VM` 不是某个全局命名空间里的变量，而是动态的函数对象——每次求值名字时，就着当前 VM 的上下文现造出来。逻辑上，`print` 绑定本 VM 的输出缓冲区，`VM` 以"当前 VM"为父级。权限收缩时，解析会自然落空，不需要维护某种黑名单。

## 输入不足是一种状态，不是一次阻塞

IO 隔离还有个微妙处：脚本执行到 `input()` 而输入没喂够，怎么办？

真阻塞是不行的——引擎是宿主的调试对象，宿主还等着拿回控制权。需要引入状态机，把"等待"本身做成一种状态：

```python
    def _step(self, handle: VMHandle) -> dict[str, object]:
        context = self._context(handle)
        if context.state in {"done", "error"}:
            return self._snapshot(context)
        if context.pc >= len(context.tree.body):
            context.state = "done"
            return self._snapshot(context)
        statement = context.tree.body[context.pc]
        required_inputs = sum(
            1
            for node in ast.walk(statement)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "input"
        )
        if (
            not context.io.has_input_provider
            and context.io.buffered_input_count < required_inputs
        ):
            context.state = "waiting_input"
            return self._snapshot(context)
```

（`mica/engine.py:254-275`）

这是根 VM 和子 VM 共用的唯一执行入口。执行语句之前，先 `ast.walk` 一遍数出这条语句里有几个 `input()` 调用，输入不够就直接进入 `waiting_input`——程序计数器原地不动，返回快照，等父级 `feed_input` 之后再恢复。

这种预检机制确保了语句执行的"全有或全无"：要么输入充足，一次性跑完；要么输入不足，在起跑线前就进入等待状态，而不是在执行到一半时突然中断。

坦白说，这个预检是个有点小聪明的权宜之计：它把"语句"悄悄定义成了等待的原子单位。先记住这一点——下一篇引擎换成指令流之后，这段逻辑的命运会成为一个悬念。

## 调试是内生的，不是附加的

现在可以回答开头那个结构问题了：跨层调试需要额外造什么吗？不需要。子 VM 在引擎里就是一条上下文记录，根 VM 调 `sub_vm.step()`，引擎直接推进那条记录的 `pc`——layer 0 调试 layer 1，和宿主调试根 VM 是同一套机制。

验收示例 `examples/debug_sub_vm.mica` 就是这件事的完整演示：根 VM 像调试器一样逐语句推进子 VM，每一步打印快照：

```python
vm_code = """
a = input()
b = input()
c = a + b
print(c)
"""
sub_vm = VM(vm_code)
sub_vm.feed_input("2")
sub_vm.feed_input("3")
print(sub_vm.step())
print(sub_vm.step())
print(sub_vm.step())
print(sub_vm.step())
sub_vm_output = sub_vm.stdout()
print(sub_vm_output)
```

快照是只读的安全副本，调试视图不能成为泄漏句柄的通道：局部变量里的 VM 句柄会被降级成 repr 字符串；就算将来出现别的内部类型，也一律降级成 `<类型名>` 兜底；打印变量时也如此。

## 一个正确的错误答案

现在跑验收：

```bash
printf '2\n3\n' | python -m mica examples/root.mica
```

输出是 `23`，不是 `5`。这并非 Bug：`input()` 读入的是纯字符串，`+` 执行的是字符串拼接。

我们刻意设计了这样的验收点，用以证明 Mica 拥有独立的值语义——这意味着 Mica 没有简单地"套壳" Python，在运行时的类型行为上，实现了与宿主的分离。

对应到 `test_cli.py` 里，我们断言 `assertEqual(output, "23\n")`：三个 CLI 示例的精确 stdout、返回码、空 stderr 全部有断言。

完整的验证：

```bash
python -m unittest discover -s tests      # 20 个用例
printf '2\n3\n' | python -m mica examples/root.mica
python -m mica examples/sub_vm.mica
python -m mica examples/debug_sub_vm.mica
```

749 行代码（引擎 412、IO 60、CLI 49，加 `__init__.py`、`__main__.py` 两个壳文件和两个测试文件），20 个用例。值得一提的是节奏：这个阶段最初是用内联断言验证的，收尾时才固化为持久测试套件——"先内联验证、收尾时固化"这个节奏，后面每个阶段都保留了。

## 留下了什么坑

第一阶段证明了结构可行，但引擎还称不上引擎。看 `VMContext` 里那两个并列的字段就明白了：`tree: ast.Module` 和 `pc: int`——程序计数器只是**语句列表的下标**，每次 `step()` 都从树里取一条语句重新遍历求值。

没有指令流，就没有跳转；没有跳转，`if`、`while` 无从谈起；函数、递归更是空中楼阁。还有那个语句级预检：当执行单位从"语句"变成"指令"，`waiting_input` 该在哪一层做？

下一篇，我们给引擎换心脏：把 AST 编译成内部指令流，让 `pc` 变成真正的指令指针——"共用引擎"的结构不动，引擎内部重来。

---

- tag：`stage-1`（对应真实开发历史：第一阶段完成 + 回归测试固化，两次提交合并为一个教学节点）
- 测试：20 个用例全部通过
