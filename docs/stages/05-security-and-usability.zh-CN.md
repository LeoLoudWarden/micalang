# Stage 5：先查安全，再加语法

上回说到，能力授予把攻击面一下子撑大了。按本来的路线图，这一阶段该补易用性语法：for 循环、finally——[开发对话](../behind-the-scenes/conversation.zh-CN.md)里记录的推荐顺序，甚至把 LLM demo 排在了更前面。

但这一篇我们得先从安全检查入手。理由很简单：每加一个语法，就多一处空子。FFI 落地之后，Mica 第一次真正具备了"执行不可信代码"的形态，而安全债这个东西，越拖越贵。**先把已经造出来的东西往死里打一遍，再谈新语法。** 事后看，这个判断是对的。

本次审查、调整过程主要由 AI 完成，诚实地说，我未必能解释清楚每一处细节。添加的新语法，在行为细节上也不完全符合规范。因此，本篇将更倾向于**记录"我做了什么，哪里可能做得不好）"**，而不是**说明"我们怎么做，应该选择什么设计"**。

## 怎么打：多视角对抗性审查

审查用了三个视角：

- 当前的 coding-agent 直接排查；
- 在 coding 应用里，另起一个独立的子 agent 做排查。目的是避免项目历史带来的偏差；
- 在 chat 应用里（自用，个人实现），让仅有文件读取工具的 AI 做排查。目的是避免不同模型、不同上下文形态带来的偏差。

三类审查大体遵从："先审 → 修复 → 下一种方式再审"的流程。前两类排查都在 coding 应用中完成。第三种属于外部独立审查意见，按"采纳修复 / 合理但容后 / 牛角尖不采纳"三类处理，下文细说。

审查发现的问题几乎都是同一个模式：**某个会增长的东西没有配额。**

- `s = s + s`：从 1 个字符起，循环 24 次就是 2²⁴ = 16MB——字符串没有配额；
- `x = x * x`：Python 的任意精度整数让数值可以无限长大——整数没有位宽上限；
- 每条指令造一个新对象：内存线性累积，总量无人记账——单对象配额防得住一个巨大的列表，防不住一百万个小对象；
- 父 VM 可以喂入任意长的 input，源码长度没有上限，VM 总数没有上限——树状创建子 VM，消耗按复利增长。

于是这一阶段立下了总原则，后来成为设计不变量第 14 条：**一切会增长的资源都必须有配额。**（"设计不变量"是[架构说明](../architecture.zh-CN.md)里维护的一份引擎信条清单，逐条编号。）

调整后 `MicaLimits` 的字段变成了这样：

```python
class MicaLimits:
    max_instructions: int = 1_000_000
    max_output_bytes: int = 1_048_576
    max_frames: int = 100
    max_child_vms: int = 100
    max_list_size: int = 100_000
    max_dict_size: int = 100_000
    max_string_bytes: int = 1_048_576
    max_int_bits: int = 4096
    max_total_bytes: int = 67_108_864
    max_total_vms: int = 1000
    max_vm_depth: int = 8
    max_fs_files: int = 1000
    max_fs_file_bytes: int = 1_048_576
```

（`mica/engine.py:34-47`）

其中 `max_total_bytes` 是一本近似内存总账：字符串按字符数 ×4、list 槽 ×8、dict 项 ×16，创建点记账、沿祖先链累加、只增不减。保守，但安全。

## 最严重的问题：调试路径是资源旁路

现在讲子 agent 的战果，也是这轮审查学到的最重要的一课。

问题出在 `_run()`。第二篇里我们说过"**执行语义只有一份，调试只是在不同地方停顿**"——`run()` 直接复用 `_step()`。当时优雅的设计埋藏着隐患。

原理是：`_step()` 每条语句做一次全量 locals 深拷贝快照，而快照的成本取决于 locals 的大小，**完全不受指令配额约束**。子 agent 实测组合出了一个载荷：locals 大小 × 语句数的乘积，估算要跑数十小时 CPU，而指令计数远未超限。

换句话说，生产快照是有成本的，而且是不固定、可涨价的。我们在 `_execute_one` 里每条指令计 1 分，但 `_step()` 在执行完语句后会做一次全量 locals 深拷贝。如果一个恶意脚本构造了一个巨大的字典，虽然它只执行了 1 条指令（计费 1 分），但这次深拷贝可能会瞬间吃掉数 GB 内存并卡死 CPU 数小时——"计费点"和"成本点"脱节了。

给快照计费可行吗？快照成本和 locals 大小耦合，折价模型复杂，而且违背原则。

原则是：**调试路径不得成为资源旁路。** 所以调试路径与执行路径彻底分开：

```python
    def _run(self, handle: VMHandle) -> str:
        context = self._context(handle)
        if context.running:
            raise MicaError("VM is already running")
        context.running = True
        try:
            while context.state == "ready":
                if not self._execute_one(context):
                    break
        finally:
            context.running = False
        if context.error is not None:
            raise context.error
        return context.io.stdout()
```

（`mica/engine.py:293-306`）

`run()` 改成直接循环 `_execute_one`，一条指令一条指令地跑，不产生任何快照。快照只在宿主显式 `step()`/`snapshot()` 时生成——而那是宿主自己的行为，宿主为自己的调试买单，天经地义。（`running` 标志是外部审查补的重入保护。）

## 宿主异常不得以原生形态泄漏

第二类问题是泄漏。上一篇我们留过一笔账：FFI 的异常净化只是 `str(exc)[:200]` 截断。这个处理方法聊胜于无——截断挡不住泄漏，宿主异常的消息里可能有路径、有密钥。

怎么处理呢？我选择的方式是展示异常**类型名**，并且使用 `from None` 掐断异常链：

```python
            try:
                raw = function.callback(*host_args, **host_kwargs)
            except MicaError:
                raise
            except Exception as exc:
                raise MicaError(
                    f"host function '{function.name}' failed with "
                    f"{type(exc).__name__}",
                    kind="ffi_error",
                ) from None
```

（`mica/engine.py:655-664`）

坦率地说，这么做有点粗暴，未必是最好的做法。

攻击载荷被直接固化成了回归测试：宿主抛 `ValueError("kaput /home/secret")`，断言脚本侧的输出**不含** `kaput` 和 `/home/secret`。

这只是最显眼的问题。此外，还有两条问题需要处理：

- 第二条：脚本构造出自引用结构（比如把自己 append 进自己的列表）时，快照的深拷贝会跟着无限递归——宿主的 `RecursionError` 曾经就这样从 `run()` 裸抛给调用方，这违反"宿主只见 MicaError"的契约。
- 第三条：大整数转浮点的 `OverflowError` 原来被兜成误导性的 `internal_error`，而它本该是脚本可以捕获处理的转换失败。

第二、三条由同一段代码处理：

```python
        except RecursionError:
            self._fail(
                context,
                MicaError(
                    "recursion limit exceeded", kind="resource_exhausted"
                ),
                instr.line,
            )
            return False
        except OverflowError:
            error = MicaError("numeric overflow", kind="conversion_error")
            if self._handle_exception(context, error):
                return True
            self._fail(context, error, instr.line)
            return False
```

（`mica/engine.py:385-398`）

归宿各有不同：第二条收为 `resource_exhausted`——递归耗尽本质就是资源问题；第三条映射为可捕获的 `conversion_error`——脚本可以 `except` 它，因为常识而言，数值转换失败是可以被脚本自己处理和恢复的。

## 按条计费防不住 O(n)：把工作量折算成指令数

外部审查指出了第三类问题，模式又很统一：引擎按指令条数计费，但有些指令一条就值千金。

容器比较 `==`、成员判断 `in`、拼接、`str()`，都是 O(n) 操作，却都只计 1 条指令——大列表空转比较，实际工作量可以放大上万倍。

修复思路是"折算"：大体量操作不再只计 1 条指令，而是按规模折算成与实际工作量相称的指令数来记账。

为此，要引入两个记账助手 `_charge_alloc`（记字节）和 `_charge_work`（记指令）。这两个助手共用同一个模子，以下只展示其中一个的代码：

```python
    def _charge_alloc(self, context: VMContext, amount: int) -> None:
        """为新创建的对象记账（近似字节数，只增不减），并计入祖先。

        记账沿祖先链累加，链上任意一环超出自己的配额即失败——
        否则祖先的配额要等到它自己再次分配时才会被检查。
        """
        current = context
        while True:
            current.alloc_bytes += amount
            if current.alloc_bytes > current.limits.max_total_bytes:
                raise MicaError(
                    "total memory limit exceeded", kind="resource_exhausted"
                )
            if current.parent_id is None:
                break
            current = self._contexts[current.parent_id]
```

（`mica/engine.py:1047-1062`；`_charge_work` 在 1064-1077，函数体是同一个沿祖先链的循环，只是把 `alloc_bytes` 换成 `instruction_count`）

`_charge_alloc` 的 docstring 值得读一遍，它顺手解释了为什么不能只记自己：只记自己的话，祖先的配额要等它"下次分配"才被检查，而祖先可能一直不分配。记账必须主动连坐。——此修复对应第三篇里，**记账即时，检查延迟**的特性坑。

## 关于外部审查意见

审查意见不是照单全收的。外部审查一共提了十六项，按三类处理：

- 采纳修复，12 项，全部附回归测试；
- 牛角尖不采纳，2 项——比如"`snapshot()` 泄漏自增 VM id"：`mica_exec` 模式下每任务一个引擎，这不构成信息；
- 合理但容后，2 项记入展望——比如 VM 终态释放，它和"运行后仍可查看 locals"的快照语义冲突，工程量大。

## 查完安全，才轮到语法

安全收口之后，语言终于迎来了迟到的易用性。

**pass**的实现较为简单，采用的是"编译期蒸发"的方法：把 `ast.Pass` 加进 `_ALLOWED_NODES`，仅此而已。按理说， `pass` 早该在之前的阶段就支持了。但出于优先级考虑，它被排在了核心结构的后面，直到本阶段才正式落地。

需要注意，过去的测试文件有一处改动：由于 `pass` 的实现，原本在 `test_engine.py` 里当作反例的 `def f(): pass` 和 `while True: pass` 现在全部合法化，换成了 `del x` 或 `class A: pass` 这样的语法。

第三篇埋下的那条 `test_finally_rejected` 也干脆改名 `test_finally_runs_on_both_paths`——被拒绝的功能清单变成了验收清单，预告的戏份就此兑现。

**for 循环**，全部实现只有两条新指令：

```python
        elif op == isa.GET_ITER:
            iterator = _make_iterator(stack.pop())
            self._charge_alloc(context, len(iterator.items) * 8)
            frame.locals[arg] = iterator
        elif op == isa.FOR_ITER:
            name, target = arg
            iterator = frame.locals[name]
            if iterator.pos >= len(iterator.items):
                frame.pc = target
                advance = False
            else:
                stack.append(iterator.items[iterator.pos])
                iterator.pos += 1
```

（`mica/engine.py:558-570`）

两个指令的分工：

- `GET_ITER` 把迭代器藏进 `$` 前缀的隐藏局部变量，快照里不显示；
- `FOR_ITER` 每圈取出一项，耗尽即跳出。

注意 `GET_ITER` 按迭代项数记账——参见外部审查的 O(n) / O(1) 问题。

语义上，当前的迭代实现，对于列表和字典有些不对称：

```python
def _make_iterator(value: object) -> _Iterator:
    if type(value) is list:
        return _Iterator(value)
    if type(value) is str:
        return _Iterator(list(value))
    if type(value) is dict:
        return _Iterator(list(value.keys()))
    raise MicaError(
        f"'{_type_name(value)}' is not iterable", kind="type_error"
    )
```

（`mica/engine.py:77-87`）

遍历 list 是**活视图**——边遍历边改会反映出来；遍历 dict 和 str 是**快照**。

坦率说，实现一套统一且严谨的迭代器协议在这个阶段太"重"了。对于一个原型，快速验证的优先级高于语义完美。为了避免在细节上过度设计，我暂且选择了这种最快捷的实现路径。这种不对称记录在"已知边界"中，暂不处理。

为了实现 **finally**，我采用了编译器中常见的"脱糖"（Desugaring）方案。下面这段接在普通 try/except 编译之后：

```python
        if not has_finally:
            return
        self._finally_stack.pop()
        self.compile_body(node.finalbody, code)
        normal_exit = self.emit(code, isa.JUMP, None, line)
        catch_target = len(code.instrs)
        error_name = f"$err_{self._tmp_counter}"
        self._tmp_counter += 1
        self.compile_body(node.finalbody, code)
        self.emit(code, isa.RAISE, ("saved", error_name), line)
        end = len(code.instrs)
        code.instrs[normal_exit].arg = end
        code.handlers.append(
            isa.Handler(
                start=body_start,
                end=after_else,
                target=catch_target,
                kinds=None,
                bind=None,
                error_name=error_name,
            )
        )
```

（`mica/compiler.py:568-589`）

`after_else` 是 except 链的末尾，`self._finally_stack` 是嵌套 finally 的记账栈，`$err_N` 是存错误对象的隐藏局部变量。

简单来说，就是"暴力复制粘贴"，编译器在分析代码时，一旦发现有 `finally` 块，它会把这个块的内容，在所有可能的出口（正常结束、`return`、`break` 以及异常抛出点）全部复制一份。这样在运行期，无论程序怎么走，只要经过出口，就一定会执行那份"副本"。而为了在异常路径上还能把原错误重抛，我们才引入了 `$err_N` 这个隐藏变量来暂存错误对象。

眼尖的读者还会发现 `Handler` 比第三篇多了一个 `error_name` 字段——它是这一阶段为 finally 脱糖新加的：异常表项命中时，错误对象先存进这个隐藏局部变量，等 finally 副本跑完，再由最后那条 `RAISE ("saved", error_name)` 原样重抛。

如果遇到了 `break`、`continue` 或 `return` 这种会直接跳出 try 块的指令，编译器会采取最简单的办法：按由内到外的顺序，把所有涉及的 `finally` 块代码直接"复制粘贴"到跳出点之前。至于在运行时动态管理 `finally` 栈的方案，因为工程量太大，在原型阶段被我舍弃了。

脱糖方案有一个已知偏差，如实记录在这里，也写进了架构文档的"已知边界"：**finally 副本内再抛异常时，外层 finally 可能多执行一次。** 

顺带解锁的还有 **raise**。它的实现和 for 循环一样只有一条新指令，就在 `FOR_ITER` 隔壁：

```python
        elif op == isa.RAISE:
            if isinstance(arg, tuple):
                raise frame.locals[arg[1]]
            if arg is None:
                if frame.active_error is None:
                    raise MicaError(
                        "raise without an active error", kind="raise_error"
                    )
                raise frame.active_error
            message = stack.pop()
            if type(message) is not str:
                raise MicaError(
                    "raise expects a string message", kind="type_error"
                )
            raise MicaError(message, kind="raise_error")
```

（`mica/engine.py:571-585`）

三个分支分别对应：

- finally 脱糖用的"重抛暂存的错误对象"（`arg` 是元组）；
- 裸 `raise` 重抛当前异常（`arg` 为 None，经 `Frame.active_error` 找回正在处理的那一个）；
- 常规的 `raise "msg"`（kind 是 `raise_error`，可捕获、可跨函数传播）。

至于 `while/else` 和默认参数这些基础语法，我直接交给 AI 快速"填坑"了。因为涉及的细节太多，且实现相对常规，我就不再在这里逐行分析（坦白说，我对这些部分的信任主要基于测试用例），想研究具体实现逻辑的读者，可以参考历史提交。

## 验证

```bash
python -m unittest discover -s tests   # 195 个用例全部通过
python -m mica examples/for_finally.mica
```

测试用例数的增长，本身就是这个项目的纪年方式：20 → 66 → 97 → 135 → **195**。新示例 `for_finally.mica` 一个文件演示 finally 的 return 路径、for/range/continue、`raise` 加 `except raise_error`。

值得说的还有数字的比例：这个阶段引擎涨了 366 行，指令集只加了 3 条——这是好消息，说明安全性改进并没有带来机制的膨胀，我们的修复落脚点在已有的记账逻辑上。

## 留下了什么坑

- 内存总账是近似值，只增不减——保守但安全，精确化留给性能基线阶段。
- finally 的偏差在案；另外 `except` 嵌套时，裸 `raise` 重抛的对象可能不是你以为的那一个异常——也在案。两处都写进了架构文档的"已知边界"。
- 仍未做性能基准：设计说明里"创建子 VM 像建一张表一样便宜"这个命题，还缺数字。

但最重要的一件事变了：到这个阶段，"执行不可信代码"终于从口号变成了有配额、有审查、有回归测试支撑的东西。安全不是这个阶段的一个议题，它成了此后每一行引擎代码的默认前提。

下一篇是尾声，也是第一次实战：FFI 的第一个真实用户——一个 HTTP 能力授予 + 多层虚拟化的 LLM 代码执行 demo。纸面上修好的城墙，该拉出去见见真敌人了。

---

- tag：`stage-5`（对应真实开发历史：第五阶段完成 + 追加一轮安全性补充，两次提交合并为一个教学节点）
- 测试：195 个用例全部通过
