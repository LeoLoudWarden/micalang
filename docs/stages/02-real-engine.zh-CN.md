# Stage 2：编译器与栈式引擎

上回说到，我们用 749 行代码验证了那个最要命的假设：所有层级的 VM 可以共用同一个引擎。但第一阶段留下的东西，说客气点是原型，说直接点是玩具。

当时的程序计数器（PC）仅仅是语句列表的下标。每执行一步，引擎都要从语法树里拎出一条语句重新遍历。在这种线性结构下，没有跳转就意味着没有 `if` 和 `while`；函数和递归同样无从实现。

这一篇要做的事只有一件：把引擎做"真"。

## 三个痛点，一个解

在实现真引擎之前，需要先分析当前架构存在的三个核心痛点。

其一，没有控制流。AST 直走的结构里根本没有"跳转的目标"，`pc` 只能顺序往下走。

其二，挂起逻辑是个权宜之计。还记得那个 `ast.walk` 预数 `input()` 个数的预检吗？在"一条语句里嵌着函数调用、函数里又有 `input()`"这类情况下，这种预检必然失准。那时还没有函数，但函数总要来的，而预检回答不了真正的问题：执行到一半没输入了怎么办？

其三，一件当时还写不出来、但迟早要命的事：死循环。语法集里没有 `while`，死循环暂时造不出来；可一旦有了循环，宿主将没有任何手段叫停一台失控的 VM。

我们看看这三个痛点各自都需要什么：

- 要跳转，就得有可以跳转的目标——指令的下标；
- 要精确挂起，就得有比语句更小的执行单位——在指令边界上挂起，pc 不动、栈原样，恢复后从断点继续；
- 要叫停失控的 VM，就得有一个所有执行都经过的计数点——每条指令先计数再执行。

怎么解决？将 AST 编译为扁平的指令序列，pc 得以升级为真正的指令指针。这意味着资源计量、单步执行、挂起恢复和异常终止，都可以统一在同一个指令分发循环中实现。

所以这一篇的标题虽然叫"编译器与栈式引擎"，叫它"资源治理的第一块基石"也未尝不可——在当时它们是同一件事。

## 十九条指令

指令集该长什么样？先想想"不该长什么样"：

- 直接复用 CPython 字节码——最省事，但等于把语义解释权又交还给 Python，和"不调用 Python 执行器"的立身之本矛盾。
- 继续 AST 直走、给每个节点类型硬加跳转语义——那会把本已拥挤的执行器变成缠结的怪物。

我的选择是自建一个迷你栈式指令集，设计参考 CPython 的语义，但仅保留实现核心功能所需的最小集：

```python
LOAD_CONST = "LOAD_CONST"
LOAD_NAME = "LOAD_NAME"
STORE_NAME = "STORE_NAME"
DUP = "DUP"
POP = "POP"
BUILD_LIST = "BUILD_LIST"
LOAD_INDEX = "LOAD_INDEX"
STORE_INDEX = "STORE_INDEX"
BINARY_OP = "BINARY_OP"
UNARY_OP = "UNARY_OP"
COMPARE_OP = "COMPARE_OP"
JUMP = "JUMP"
JUMP_IF_FALSE = "JUMP_IF_FALSE"
JUMP_IF_TRUE_KEEP = "JUMP_IF_TRUE_KEEP"
JUMP_IF_FALSE_KEEP = "JUMP_IF_FALSE_KEEP"
LOAD_ATTR = "LOAD_ATTR"
MAKE_FUNCTION = "MAKE_FUNCTION"
CALL = "CALL"
RETURN = "RETURN"

JUMP_OPS = {JUMP, JUMP_IF_FALSE, JUMP_IF_TRUE_KEEP, JUMP_IF_FALSE_KEEP}


@dataclass
class Instr:
    op: str
    arg: object = None
    line: int = 0
    stmt_start: bool = False


@dataclass
class CodeUnit:
    name: str
    params: list[str]
    instrs: list[Instr] = field(default_factory=list)
```

（`mica/isa.py:5-40`）

共十九条。加载、存储、栈操作、算术比较、四种跳转、函数创建与调用——没了。

细看跳转指令会发现，这里没有 `JUMP_IF_TRUE`，只有 `JUMP_IF_FALSE` 和两个带 `KEEP` 的变体。

`KEEP` 变体与普通跳转的区别在于：它在跳转时不会弹出栈顶的操作数。这正是为了实现 `and / or` 的短路求值（即一旦结果确定，就不再计算后续表达式）。

例如执行 `a or b` 时，如果 `a` 是真值，结果就已经确定了，引擎需要直接跳转到结束。但此时，结果不仅需要是"真"，还得是 `a` 本身。如果使用普通跳转指令，为了判断真假，栈顶的 `a` 会被弹出丢弃，我们得在跳转后重新压入一个值；而 `KEEP` 变体则在判断完真假后，把 `a` 原样留在栈顶。这样跳转之后，栈上直接就是最终结果。

（顺带坦白一句：代码中的 `JUMP_OPS` 集合定义后从未被使用，直到 0.1.0 版本依然是死代码，这里如实保留。）

注意 `Instr` 上的两个字段——`line` 和 `stmt_start`——它们是后面所有调试语义的地基。

## 白名单成为编译的第一道工序

在第一阶段，白名单校验还只是引擎类里的一个辅助方法，和执行逻辑混在一起。现在，我们要让校验过程独立出来，变成编译器模块的首道工序。所有源码在转化为指令前，必须先过这道关卡——校验不通过，绝不会产生任何指令：

```python
def compile_source(source: object, filename: str) -> isa.CodeUnit:
    if not isinstance(source, str):
        raise MicaError("VM source must be a string", filename=filename)
    try:
        tree = ast.parse(source, filename=filename, mode="exec")
    except SyntaxError as exc:
        raise MicaError(
            exc.msg or "invalid syntax",
            kind="syntax_error",
            filename=filename,
            line=exc.lineno,
            column=exc.offset,
        ) from None
    _validate(tree, filename)
    compiler = _Compiler(filename)
    code = isa.CodeUnit(name="<module>", params=[])
    compiler.compile_body(tree.body, code)
    compiler.emit(code, isa.LOAD_CONST, None, 0)
    compiler.emit(code, isa.RETURN, None, 0)
    return code
```

（`mica/compiler.py:72-91`）

流程是：`ast.parse` → 白名单校验 → 编译。

这里有一个编译期的细节：我对模块体和函数体做了统一处理，在末尾强制补上 `LOAD_CONST None; RETURN`。

为什么要这么做？如果指令序列没有明确的终点，引擎在执行时必须时刻警惕，在每条指令前都检查一次 pc 是否越界，以免程序"跌出"指令列表。而现在，通过在编译期强制补齐 `RETURN`，每个 `CodeUnit` 都拥有了一个明确的收场动作。这样，`RETURN` 指令就自然地承担了终止执行的职责，调度循环无需再做冗余的边界检查，只需在命中 `RETURN` 时优雅地停下即可。

顺带一提，`MicaError` 在这个阶段从 engine.py 搬进了 34 行的独立 `errors.py`。目的是让 compiler 和 engine 不循环 import。属于工程痕迹上的小调整。

## 语句级单步，如何活在指令世界里

设计上我们许下过一个契约：`step()` 一次执行一条源码语句。执行单位变成指令之后，这个契约怎么保？

答案在编译期就埋下了。每编译完一条语句，回填它的第一条指令：

```python
    def compile_body(self, body: list[ast.stmt], code: isa.CodeUnit) -> None:
        for statement in body:
            first = len(code.instrs)
            self.compile_statement(statement, code)
            if len(code.instrs) > first:
                code.instrs[first].stmt_start = True
```

（`mica/compiler.py:188-193`）

`stmt_start` 这个字段就此登场。

那么语句级 `step` 的停止条件呢？看代码：

```python
    def _step(self, handle: VMHandle) -> dict[str, object]:
        context = self._context(handle)
        if context.state != "ready":
            return self._snapshot(context)
        start_depth = len(context.frames)
        start_line = self._current_instr(context).line
        executed = False
        while context.state == "ready":
            instr = self._current_instr(context)
            if executed and len(context.frames) <= start_depth and instr.stmt_start:
                break
            if not self._execute_one(context):
                break
            executed = True
        if executed and context.state in {"ready", "done"}:
            context.last_line = start_line
        return self._snapshot(context)
```

（`mica/engine.py:190-206`）

三个条件：执行过至少一条指令（防止原地停下）、帧深度回落到起始深度、下一条指令带着 `stmt_start` 标记。

引入帧深度（`start_depth`）的判断，是为了确保单步执行的"层级一致性"。想想看：单步执行一条语句时，如果这条语句恰好是函数调用，程序会跳进函数内部；此时若只检查 `stmt_start`，在函数内的第一条语句时，调试就会停下来——而我们原本的意图是"执行完这个调用"。所以我们希望把整个函数调用视为一个整体：只有当执行流从函数中"上浮"到起始层级、且遇到了下一条语句，才算真正完成了一步。

这种机制在处理循环时同样自然：循环体会被编译成一段连续的指令，其起始指令天然带有 `stmt_start` 标记，所以每次跳回循环起点，`step()` 都会准确地在原地停下。

至此，`run()`、语句级 `step()` 和指令级 `step_instruction()`（一次一条指令）在底层完全收敛，它们最终都汇向同一个 `_execute_one`。**执行语义只有一份，调试只是在不同地方停顿**，运行、单步执行、指令调试，本质上并没有区别，它们只是在执行过程中选择了不同的停顿点而已。第一阶段那种"每次 step 都要从语法树里重新捞一条语句、重新遍历求值"的暴力做法，就此退出舞台。

## 挂起的正确答案：先验证，后提交

还记得第一阶段那个尴尬的预检逻辑吗？为了防止 `input()` 在语句执行到一半时卡死，我们不得不提前扫描 AST 来数个数。

现在的指令化架构，终于给了这个难题一个正经的答案：直接利用 `waiting_input` 状态。当 `CALL input` 输入不足时，直接抛出异常 `InputRequired`，引擎在指令边界将其捕获并挂起。

这个"先验证、后提交"的模式，让挂起恢复变得简单：pc 没有前移，栈分毫未动，一旦输入就绪，引擎只需在原地重新触发这条指令，就像刚才的挂起从未发生过一样。

为什么会这么设计呢？我最初的写法是先弹栈、再调原生函数：`InputRequired` 抛出时参数已经从栈上消失了，挂起恢复后重放这条指令，栈是坏的。修完之后的形态长这样：

```python
        if isinstance(function, _NativeFunction):
            args = tuple(stack[len(stack) - argc :]) if argc else ()
            try:
                result = function.callback(*args)
            except InputRequired:
                raise
            except MicaError:
                raise
            except TypeError:
                raise MicaError(
                    f"invalid arguments for {function.name}", kind="type_error"
                ) from None
            del stack[len(stack) - argc - 1 :]
            frame.pc += 1
            stack.append(result)
            return
```

（`mica/engine.py:370-385`）

顺序是关键：先试着调用，**成功之后**才 `del stack`、`pc += 1`、压返回值。异常在任何栈变更之前原样上抛。

此处存在一个可以探讨的点：处理挂起有多种方案（比如在调用前快照整个栈，或者在调用后尝试回滚状态），但快照太重，回滚太复杂。而"先验证，后提交"只需要调整两行代码的顺序，就能在不增加内存开销的情况下实现原子性。因此，我选择了这个方案。之后，这个模式也会作为整个引擎的错误处理惯例。

第一阶段的语句级预检逻辑，至此可以安心删除了。上篇末尾的悬念，落地形态就是这样。

## 值语义与 Python 分居

换上指令集之后，脚本中的值操作全部收敛到 `BINARY_OP`/`COMPARE_OP` 几个分发口。现在可以通过助手函数重新定义运算逻辑，确保 Mica 的行为不再受 Python 隐式规则的干扰。

出于轻量、安全性等考虑，我选择这样设计：

- `_is_number` 用 `type(value) in (int, float)` 显式排除 `bool`——在 Python 里 `True + 1 == 2`，在 Mica 里这是 `type_error`（有专门测试 `test_bool_not_a_number`）；
- 比较运算只允许同族类型；`==` 跨类型返回 `False`，不做隐式转换；
- `/` 恒为浮点除法。

原则和第一阶段一样：所有值操作经过引擎的助手函数，不直接接驳 Python 运算符的隐式行为。脚本世界的规则由脚本世界自己定义。

## 资源配额：让"失控"成为一种可报告的状态

现在轮到那个"迟早要命"的问题了。五项配额集中在一个 frozen dataclass 里：

```python
@dataclass(frozen=True)
class MicaLimits:
    max_instructions: int = 1_000_000
    max_output_bytes: int = 1_048_576
    max_frames: int = 100
    max_child_vms: int = 100
    max_list_size: int = 100_000
```

（`mica/engine.py:14-20`）

默认值即预算：一百万条指令、1MB 输出、100 帧调用深度、100 个子 VM、十万元素的列表。落点在 `_execute_one` 的开头——每条指令先计数，超限即失败：

```python
    def _execute_one(self, context: VMContext) -> bool:
        if context.state != "ready":
            return False
        frame = context.frames[-1]
        instr = frame.code.instrs[frame.pc]
        context.instruction_count += 1
        if context.instruction_count > context.limits.max_instructions:
            self._fail(
                context,
                MicaError("instruction limit exceeded", kind="resource_exhausted"),
                instr.line,
            )
            return False
        try:
            self._dispatch(context, frame, instr)
        except InputRequired:
            context.state = "waiting_input"
            return False
        except OutputLimitExceeded:
            self._fail(
                context,
                MicaError("output limit exceeded", kind="resource_exhausted"),
                instr.line,
            )
            return False
        except MicaError as exc:
            self._fail(context, exc, instr.line)
            return False
        except Exception:
            self._fail(
                context,
                MicaError("internal runtime error", kind="internal_error"),
                instr.line,
            )
            return False
        return True
```

（`mica/engine.py:221-256`）

这个方法是整个引擎的心脏，值得逐行读。注意四类异常的不同归宿：
- `InputRequired` 是挂起不是错误
- 输出超限和指令超限都映射为 `resource_exhausted`
- 脚本错误进 `error`
- 引擎自己的意外异常被兜成 `internal_error`

**VM 的世界里没有未捕获异常。**

或许会有人问：为什么 `resource_exhausted` 要单独成为一种 VM 状态，而不是普通错误？看 `_fail` 这三行：

```python
    def _fail(self, context: VMContext, error: MicaError, line: int) -> None:
        context.error = error.bind(context.filename, line)
        context.state = (
            "resource_exhausted"
            if error.kind == "resource_exhausted"
            else "error"
        )
```

（`mica/engine.py:258-264`）

因为宿主需要区分"程序写错了"和"程序想消耗超过配额的资源"——前者是逻辑问题，后者是安全问题。这条分界线此时还看不出全部价值，到第五篇对抗性审查时它会变成主角。

## 验收：一个文件验证全部新能力

本阶段新增的验收示例是 `examples/fib.mica`：递归斐波那契加 `while` 循环，一个文件同时覆盖 if/else、while、def、递归、比较、算术：

```bash
python -m unittest discover -s tests   # 66 个用例：第一阶段 20 个回归原样通过 + 46 个新用例
python -m mica examples/fib.mica       # 输出 0–34 斐波那契序列
```

测试从 20 涨到 66。引擎从 412 行重写为 741 行——几乎全换，但"所有 VM 共享一个引擎、调试是内生的、IO 按 VM 隔离"这三根柱子一根没动。第一阶段证明的结构，经受住了换心脏的手术。

我们可以重点看两条测试，它们最能直观地证明本阶段的这些设计点：

一条是 `test_loop_stepping_is_per_statement`：逐语句单步一个 `while` 循环，验证每一圈都能在语句边界停下——`stmt_start` 设计的端到端证明。

另一条是 `test_instruction_limit_kills_infinite_loop`：它断言死循环被叫停时 `instruction_count == 10_001`——配额是 10000，第 10001 条指令先被计数、再触发超限。边界上差一条指令的细节被测试钉死，这正是"读测试读懂引擎"的范例。

## 留下了什么坑

换完心脏，并不代表万事大吉。作为一个简陋的原型，我们的引擎还存在许多问题：

- **单条指令内部不设防。** `_execute_one` 只在指令边界计数，而大整数乘法一条指令可以跑很久。指令配额防得住循环，防不住单条"重"指令。这个坑要到第五篇才真正填上。
- **脚本无法自恢复错误。** 当前没有 try/except，一处出错，整个引擎直接 panic。有一条测试名字叫 `test_child_vm_shares_instruction_budget_independence`——名字想说的是"子 VM 的预算独立于父级"（坦率说，名字起得不好），实际行为则是：子 VM 死循环耗尽配额后，父 VM 的状态**也**变成了 `resource_exhausted`。名字起反了，但确实展示了这个现状。没有错误恢复机制，子级出错，父级连坐，隔离就不完整。
- **"layer 1 禁止创建 VM"这条临时禁令还在。** 第一阶段为了权限收缩硬编码的 `allow_vm=False` 原样保留到了现在。可如果嵌套隔离是语言的核心命题，凭什么哪一层不配创建 VM？
- **VM 没有自己的工作空间。** 没有文件，脚本产出只能走 stdout 这条独木桥；宿主也无法在运行前给 VM 预置材料。
- 函数可读模块级变量但不能写、没有闭包、错误只带行号不带列号——记在小账本上。

下一篇，我们处理中间三件事情：try/except、任意层都能创建子 VM、每台 VM 一个私有虚拟文件系统。用一句话预告：我们要给每台 VM 一个完整的工作区。

---

- tag：`stage-2`
- 测试：66 个用例全部通过
