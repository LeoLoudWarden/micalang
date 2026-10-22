# Stage 4：数据与能力——字典、FFI 授予与跨边界对象传递

上回说到，每台 VM 都有了自己的工作区：私有文件系统、自己的错误处理、创建下一层的权利。工作区是闭环了，但这个闭环封得太死——除了文件和输入输出，VM 与宿主之间零通道。

这就引出一个根本问题：能力从哪来？[设计说明](../design.zh-CN.md)里立过一根支柱：**内建到此为止，能力靠授予**。VM 默认什么权限都没有，但宿主（或父 VM）可以把自己写好的函数注入给它。前三篇我们都在造"隔离"这一半，这一篇造另一半："授予"。

顺带还要先补一块砖：字典。为什么是字典？想想看，一个外部函数天然要返回 `{"status": 200, "body": ...}` 这类结构，没有字典就只能用字符串拼凑。所以字典其实是 FFI 的前置工程——这也是为什么这两件事被绑在了同一个阶段。

## 字典：白名单精神的又一次应用

字典本身的实现很简单。指令集只加了一行 `BUILD_DICT`，编译器只加了一个分支：

```python
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                self.compile_expression(key, code)
                self.compile_expression(value, code)
            self.emit(code, isa.BUILD_DICT, len(node.keys), line)
```

（`mica/compiler.py:474-478`）

值得讲的是键的类型。Python 里任何可散列对象都能当键，Mica 不行——只有 `str/int/float/bool/None` 五种标量：

```python
    def _index(self, value: object, index: object) -> object:
        if type(value) is dict:
            if type(index) not in (str, int, float, bool) and index is not None:
                raise MicaError(
                    f"unhashable key type: {_type_name(index)}",
                    kind="type_error",
                )
            if index not in value:
                raise MicaError(
                    f"missing key: {_to_str(index)!s}", kind="key_error"
                )
            return value[index]
```

（`mica/engine.py:859-870`）

还是第一篇那个白名单精神：允许什么，一一列举；没列举的，默认非法。

随字典一起来的还有两个配套：

- 缺键错误 `key_error`，一种新增的可捕获错误——脚本可以 `except key_error`，这正是上一篇异常机制开始干活的样子；
- 一条 `max_dict_size` 配额（默认十万个键）——第二篇的配额全家福里没有它，它是和字典一起落地的。

## FFI 的两个入口，和一条铁律

能力授予有两个入口：宿主在 Python 侧 `create_root(functions=...)`，以及脚本内 `VM(code, functions=...)`。两种情况下，都要遵循同样的原则：**默认零能力，逐层显式给**。

或许会有人问：为什么不干脆让子 VM 自动继承父级的能力？省得每层都要写一遍 `functions=...`。

因为"每层都有机会不给"，恰恰是安全模型的精髓。root 拿到宿主注入的网络函数，不代表它创建的孙 VM 也该拿到。能力沿创建链传递，每一环都是一次显式的决定，没有隐式继承这回事。

这里先把"函数"这个概念分个家，避免后文混淆：

- 引擎自带的内建（`input`/`print`/`VM`）：每台 VM 出生就有，不走授予通道；
- 宿主写的 Python 函数、VM 自己 `def` 的 Mica 函数：授予通道里的两种货物；
- 一层 VM 把自己已经拿到的授予函数再转授给下一层：叫*再授予*。

这一篇的目标是构建一套完整的授予通道，兼容这三种"物流方式"。而函数的"物流管理"，包含两个必须要解决的问题。

## 问题一：跨宿主边界，数据怎么传递

宿主函数和 Mica 引擎之间隔着一条真正的边界：一边是 Python 对象的世界，一边是 Mica 值的世界。

这条边界上最容易犯的错是按引用传递。想想看，如果宿主拿到 VM 内部某个 list 的同一引用，事后偷偷修改，会怎样？两层后果：

- 引擎运行时，引用传递会创建一条不受控的隐式通道，让数据在绕过所有校验与配额的情况下直接传输。输出配额能管住 stdout 这条明路，却管不住共享内存这条暗道。
- 引擎终态之后，如果宿主持有内部对象的引用，即使 VM 终止，相关内存也无法释放，生命周期边界将失去实际意义。

所以我们的选择是：过界的东西，**只允许纯数据，且双向深拷贝**。边界海关就是 `_to_host`（去程）和 `_from_host`（回程）两个函数。它们镜像对称，读代码时留意三处差异：

- 回程额外宽容地接受 `tuple`，转成 list；
- 错误文案各自指明"清关"方向；
- 最重要的一处：**去程和回程共用同一个 `budget`**——调用现场只初始化一次，一次 FFI 调用的参数和返回值，合计受这一条预算约束。

```python
def _to_host(value: object, budget: list[int]) -> object:
    """把 mica 值深拷贝为宿主侧纯数据结构；不允许句柄/函数越界。"""
    budget[0] -= 1
    if budget[0] < 0:
        raise MicaError("FFI argument structure too large", kind="ffi_error")
    if value is None or type(value) in (str, int, float, bool):
        return value
    if type(value) is list:
        return [_to_host(item, budget) for item in value]
    if type(value) is dict:
        return {
            _to_host(key, budget): _to_host(item, budget)
            for key, item in value.items()
        }
    raise MicaError(
        f"value of type '{_type_name(value)}' cannot cross the host boundary",
        kind="ffi_error",
    )


def _from_host(value: object, budget: list[int]) -> object:
    """把宿主返回值校验并深拷贝为 mica 值；异物与巨型结构拒绝。"""
    budget[0] -= 1
    if budget[0] < 0:
        raise MicaError("FFI result structure too large", kind="ffi_error")
    if value is None or type(value) in (str, int, float, bool):
        return value
    if type(value) in (list, tuple):
        return [_from_host(item, budget) for item in value]
    if type(value) is dict:
        result: dict[object, object] = {}
        for key, item in value.items():
            if type(key) not in (str, int, float, bool) and key is not None:
                raise MicaError(
                    f"FFI result has unhashable key of type "
                    f"'{type(key).__name__}'",
                    kind="ffi_error",
                )
            result[key] = _from_host(item, budget)
        return result
    raise MicaError(
        f"host function returned unsupported type "
        f"'{type(value).__name__}'",
        kind="ffi_error",
    )
```

（`mica/engine.py:987-1031`）

入白名单的（`None/bool/int/float/str/list/dict` 的递归组合）全量拷贝过去；句柄、函数这类活物直接拒绝，`ffi_error`。

`budget` 是一个节点计数器：初值取 `max_list_size + max_dict_size` 之和，每拷贝一个节点扣 1（标量、容器、容器的每个成员都算一个），扣穿即拒。恶意脚本别想用一个千层嵌套的结构，把拷贝本身变成拒绝服务。

注意这里 `budget` 被定义为一个单元素列表 `[int]`——这是一个 Python 小技巧，利用列表的可变性，让递归调用 `_to_host` 和 `_from_host` 能够共享同一个计数器，从而实现一次 FFI 调用总额的严格控制。

再看调用现场，注意异常处理的分支差异：

```python
        if isinstance(function, _GrantedHostFunction):
            budget = [
                context.limits.max_list_size + context.limits.max_dict_size
            ]
            host_args = [_to_host(value, budget) for value in args]
            host_kwargs = {
                key: _to_host(value, budget) for key, value in kwargs.items()
            }
            try:
                raw = function.callback(*host_args, **host_kwargs)
            except MicaError:
                raise
            except Exception as exc:
                message = str(exc)[:200]
                raise MicaError(
                    f"host function '{function.name}' failed: {message}",
                    kind="ffi_error",
                ) from None
            result = _from_host(raw, budget)
            del stack[base - 1 :]
            frame.pc += 1
            stack.append(result)
            return
```

（`mica/engine.py:513-535`）

两个分支，两种待遇：

- 宿主函数自己主动抛 `MicaError`：原样透传，脚本能按 kind 精确捕获；
- 宿主意外抛出的普通 Python 异常：净化为 `ffi_error`，消息还要截断到 200 字符。

为什么要截断？因为宿主异常的消息里可能有路径、有密钥、有各种不该进 VM 世界的东西。坦白说，`str(exc)[:200]` 这个净化并不彻底——先记下这笔账，下一篇会连本带利地还。

## 问题二：授予 Mica 函数，全局算谁的

宿主函数是一种授予，但更有意思的是另一种：父 VM 把自己 `def` 的 Mica 函数授予子 VM。两种授予函数的"身份证"长这样，docstring 本身就是设计宣言：

```python
@dataclass(frozen=True)
class _GrantedHostFunction:
    """宿主注入的 Python 函数：跨边界双向深拷贝，异常净化为 ffi_error。"""

    name: str
    callback: Callable[..., object]

    def __repr__(self) -> str:
        return f"<function {self.name}>"


@dataclass(frozen=True)
class _GrantedMicaFunction:
    """父 VM 授予的 mica 函数：在被授予 VM 的帧栈上执行，但解析定义方的模块全局。"""

    name: str
    code: isa.CodeUnit
    module_locals: dict[str, object]

    def __repr__(self) -> str:
        return f"<function {self.name}>"
```

（`mica/engine.py:64-84`）

授予一个 Mica 函数，藏着一个微妙的问题：这个函数体里引用的全局变量，算谁的？

比如父 VM 里有 `base = 100`，函数里写 `return base + x`。它在子 VM 里跑起来的时候，`base` 该解析成什么？

这里我们面临一个权衡：如果函数在定义方的帧栈上跑，全局变量自然正确，但指令消耗会记在父 VM 头上，子 VM 就成了"白嫖"计算资源的漏洞。

因此我们选择：**帧栈在调用方，全局在定义方**。这意味着：指令消耗由调用者（子 VM）买单，但函数执行时的"世界观"（全局变量）依然属于定义者（父 VM）。

为此，我们需要让函数在子 VM 的帧栈上执行（指令消耗计入子 VM 的配额），但携带定义方的模块全局（`module_locals`）：

```python
        if isinstance(function, _GrantedMicaFunction):
            code = function.code
            locals_ = self._bind_arguments(code, args, kwargs)
            if len(context.frames) + 1 > context.limits.max_frames:
                raise MicaError(
                    "call depth limit exceeded", kind="resource_exhausted"
                )
            del stack[base - 1 :]
            frame.pc += 1
            context.frames.append(
                Frame(
                    code=code,
                    locals=locals_,
                    module_locals=function.module_locals,
                )
            )
            return
```

（`mica/engine.py:536-552`）

撑起这条语义的，只是 `Frame` 上新增的一个字段 `module_locals`。

它带来的效果，可以用两条测试讲清楚：

- `test_granted_function_sees_parent_globals`：父 VM 的 `base = 100`，子 VM 调授予函数得到 101；
- `test_child_globals_not_visible_to_granted_function`：子 VM 自己也定义了 `base = 5`，授予函数看都不看它一眼，该报 `name_error` 还是报。

授予函数活在定义方的世界里，只是借调用方的帧栈跑一程。

那么"授予"这个动作本身做什么？捕获那一瞬间的定义方全局：

```python
    def _validate_child_functions(
        self, parent: VMContext, functions: object
    ) -> dict[str, object]:
        if functions is None:
            return {}
        if not isinstance(functions, dict):
            raise MicaError(
                "VM option functions must be a dict", kind="type_error"
            )
        granted: dict[str, object] = {}
        for name, value in functions.items():
            _check_grant_name(name)
            if isinstance(value, MicaFunction):
                granted[name] = _GrantedMicaFunction(
                    name, value.code, parent.frames[0].locals
                )
            elif isinstance(value, _GrantedHostFunction):
                granted[name] = _GrantedHostFunction(name, value.callback)
            elif isinstance(value, _GrantedMicaFunction):
                granted[name] = _GrantedMicaFunction(
                    name, value.code, value.module_locals
                )
            else:
                raise MicaError(
                    f"granted value '{name}' is not a function",
                    kind="type_error",
                )
        return granted
```

（`mica/engine.py:682-709`）

三个分支，对应能力链上的三种来源：自己 `def` 的函数、宿主注入的函数、从前一层拿到的授予函数（再授予）。

第一个分支里那行 `parent.frames[0].locals` 值得停顿一下。`frames[0]` 是模块帧，脚本的全局变量就存在它的 `locals` 里——所谓"捕获定义方全局"，就是把这个字典的引用存进授予函数。能力就这样一环一环传下去。

入口处还有个不起眼的小检查 `_check_grant_name`：拒绝非标识符、`__` 前缀、与内建冲突的名字——授予通道从入口就防注入与遮蔽。

细心的读者会注意到一个不对称：跨宿主边界双向深拷贝，VM 之间授予却按引用传递、不做任何拷贝。这是刻意的设计：

- 宿主 ↔ Mica 引擎：这是完全不同的两个世界，彼此不信任，所以必须经过"海关"进行双向深拷贝。
- 父 VM ↔ 子 VM：它们处于同一个引擎的信任域内，且父 VM 本就是子 VM 的"上帝"，因此采用引用传递。

同一引擎同一信任域，授予方本就是被授予方的"上帝"，共享引用是一种自主选择：你可以故意给子 VM 一个可观察的状态载体。边界设在哪里，防线就设在哪里。

## 一句话的诚实

讲个这个阶段的题外话。`mica/cli.py` 在本次提交里只改了一行：argparse 的描述从 `"MicaJIT-PY prototype"` 改成 `"Mica-PY prototype"`——"JIT"这个词被摘掉了。我得承认，项目早期起名字时存在错误：这个解释器跟 JIT 没有半点关系。

## 验证

两个新示例：

- `dict.mica`：演示字典的全部姿势——包括用 `keys() + while + 下标` 遍历字典的笨拙写法（没有 for 的时代就是这样，这份笨拙很快会成为下一篇的动机）；
- `ffi.mica`：父 VM 定义一个 `fake_http_get`，返回 `{"status": 200, "url": ..., "body": ...}`，授予子 VM 调用——示例注释里自己写了"真实场景里这是宿主注入的网络函数"。

```bash
python -m unittest discover -s tests   # 135 个用例（97 旧 + 38 新）
python -m mica examples/ffi.mica
```

测试 97 → 135。这个阶段的数字分布很说明问题：**测试加了 367 行，比实现还多**——ISA 加了 1 行，编译器 19 行，引擎 282 行。语义重心几乎全在边界防御上，而边界防御是要靠测试钉死的。

38 个新用例里，有双向拷贝各自独立的验证、跨两级再授予的链路验证、授予调用计入子 VM 配额的配额验证（给子 VM 只批 `max_instructions=200`，父方函数里的十万次循环跑到一半就被掐停）。

## 留下了什么坑

- **宿主函数的成本不可计量。** 每次 FFI 调用只计 1 条指令，宿主函数跑多久、吃多少内存，引擎管不了。这是刻意取舍——宿主可信，能力实现者自律——但它把"引擎管隔离、实现者管能力"的责任划分第一次推到了前台。
- **净化不彻底。** `str(exc)[:200]` 只是截断，不是净化。
- **活物不能越界。** "把子 VM 句柄交给宿主托管"做不到。
- 字典没有方法、没有 `for`，遍历只能 `keys() + while`。按本来的路线图，补齐这些易用性短板就是下一阶段的活。

但计划赶不上变化。能力授予把攻击面一下子撑大了，每条新语法都可能多一处空子。与其带着安全债往前跑，不如先停下来，把已经造出来的东西往死里打一遍。

下一篇就是这个项目里最特别的一个阶段：先查安全，再加语法。

---

- tag：`stage-4`
- 测试：135 个用例全部通过
