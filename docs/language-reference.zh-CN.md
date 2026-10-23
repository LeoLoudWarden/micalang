# Sericite 语言参考

Sericite 是一门脚本语言，语法是 Python 的子集——写起来就是 Python，但**不是所有 Python 写法都合法**，而且部分语义有自己的规矩。本文自完备：读完即可开写。本文描述 Sericite 0.3.0 的语言行为。

## 1. 基本样子

```python
name = "world"            # 赋值
print("hello", name)      # 输出：hello world
x = input()               # 读入一行（字符串）
```

## 2. 类型与运算

内建类型：`int`、`float`、`str`、`bool`、`None`、`list`、`dict`。

```python
7 / 2        # 3.5（除法恒为浮点）
7 // 2       # 3
7 % 3        # 1
"ab" + "cd"  # 字符串拼接（只有 +，没有 * 重复、没有 f-string、没有切片）
[1, 2] + [3] # 列表拼接
not x        # 逻辑非；and / or 短路求值
1 < 2        # 比较：== != < <= > >=（不支持链式 a < b < c）
"a" in {"a": 1}    # in / not in：dict 查键、list 查元素、str 查子串
```

与 Python 的关键差异（写错会报 `type_error`）：

- `bool` 不参与算术：`True + 1` 报错。
- 跨类型大小比较报错：`1 < "a"` 报错（`==`/`!=` 可以跨类型，结果为 False/True）。
- 字典键只允许 `str/int/float/bool/None`，缺键读取报 `key_error`。
- 字符串只能用整数索引读（`s[0]`），不能写、不能切片；列表可索引读写。

## 3. 控制流

```python
if x < 0:
    print("neg")
elif x < 10:
    print("small")
else:
    print("big")

while i < 10:
    i = i + 1
    if i % 2 == 0:
        continue
    if i > 8:
        break
else:
    print("没被 break")     # while/else

for item in [1, 2, 3]:      # for 可迭代：list、dict（键）、str（逐字符）、range() 结果
    print(item)
for k in {"a": 1, "b": 2}:
    print(k, "->", ...)     # 取值用 d[k]
else:
    print("没被 break")     # for/else

pass                        # 空语句，合法

label = "big" if x >= 10 else "small"   # 三元表达式（短路：只算被选中的分支）
```

注意：for 的循环变量只能是单个名字（`for a, b in ...` 不支持）；遍历 list 是活视图（边遍历边改会反映），遍历 dict 是键快照。

## 4. 函数

```python
def add(a, b=10):           # 支持默认参数（定义时求值一次）
    return a + b

add(1)                      # 11
add(1, b=20)                # 21，支持关键字实参

def fib(n):                 # 递归合法
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)
```

嵌套函数使用词法作用域，按引用捕获外层函数的局部变量。闭包在调用时读取最新绑定，而不是定义时的值快照：

```python
def make_reader():
    x = 1
    def read():
        return x
    x = 2
    return read

reader = make_reader()
print(reader())             # 2
```

同一次外层调用创建的闭包共享捕获变量，不同次外层调用的环境彼此独立。`for` 循环不会为每轮迭代创建新变量，因此循环中创建的闭包共享最终绑定；如需定义时快照，可使用 `def read(value=x): ...` 这样的默认参数。

名字在函数内任何位置被赋值，就会在整个函数内作为局部变量。内层赋值因此会遮蔽外层绑定，而不是修改它；不支持 `nonlocal` 和 `global`。模块全局仍从定义方模块解析，不会复制进闭包。

函数装饰器遵循 Python 的应用语义：

```python
def trace(func):
    def wrapper(x):
        print("before")
        result = func(x)
        print("after")
        return result
    return wrapper

@trace
def double(x):
    return x * 2
```

这等价于先定义 `double`，再执行 `double = trace(double)` 重新绑定。装饰器表达式从上到下求值；堆叠装饰器从下往上应用。

不支持：可变参数（`*args`）、仅关键字参数。

## 5. 异常

```python
try:
    x = 1 / 0
except zero_division as e:   # 按错误类型过滤，e 是消息字符串
    print("caught:", e)
except:                      # 裸 except 兜底
    print("wrong")
else:
    print("没出错")
finally:
    print("一定执行")

raise "出问题了"             # 抛出 raise_error 类型，可捕获
raise                        # 在 except 块内裸 raise：重新抛出当前错误
```

常见错误类型（kind）：`type_error`、`name_error`、`index_error`、`key_error`、`zero_division`、`conversion_error`、`arity_error`、`raise_error`、`file_not_found`、`ffi_error`。异常可跨函数传播。`resource_exhausted`（资源耗尽）不可捕获。

## 6. 内建函数

- `input()` / `print(...)`：一行输入 / 一行输出。
- `len(x)`（str/list/dict）、`int(x)`、`float(x)`、`str(x)`、`range(...)`（返回 list）、`keys(d)`（返回 dict 键的 list）。
- 文件（纯文本）：`read_file(path)`、`write_file(path, text)`、`list_dir(path)`、`path_exists(path)`。路径形如 `/notes/a.txt`，目录靠路径前缀隐含。

**没有方法**：list/dict/str 一律没有方法——`append`、`get`、`join`、`split`、`strip` 全都不存在。追加元素用 `lst = lst + [x]`，取字典键用 `d[k] if k in d else 默认`。

## 7. 创建虚拟机（VM）

在 sericite 里可以创建新的 VM，VM 内跑的代码语法也还是 sericite：

```python
sub = VM("print('hi')")          # 源码字符串
out = sub.run()                  # 运行并取回它的全部输出（字符串）
print(out)                       # 'hi\n'
```

VM 是相互隔离的工作空间：看不到你的变量，碰不到你的文件——它有自己的空白文件系统。配置方式：

```python
sub = VM(code, max_instructions=10000)   # 资源配额，只能比当前环境更严
sub.write_file("/input.txt", "数据")      # 预置文件给它
sub.feed_input("一行输入")                # 喂 input()
out = sub.run()
print(sub.read_file("/result.txt"))       # 取回它写的文件
```

调试：`sub.step()` 逐语句单步（返回状态快照 dict），`sub.step_instruction()` 逐指令，`sub.snapshot()` 查看 locals/状态，`sub.terminate()` 终止。

把函数授予 VM（它就能调用你的函数，包括你被授予的函数）：

```python
def double(x):
    return x * 2
sub = VM("print(double(21))", functions={"double": double})
sub.run()                        # 输出 42
```

## 8. 明确不能写（编译期直接拒绝）

`import`、`class`、`del`、列表/字典/字符串方法、`*args/**kwargs`、链式比较、切片、元组、f-string、双下划线名称、属性访问（VM 句柄的白名单方法除外）。

## 9. 一个完整例子

```python
def count_words(path):
    try:
        text = read_file(path)
    except file_not_found:
        return {"ok": False}
    count = 0
    word = ""
    for ch in text:
        if ch == " " or ch == "\n":
            if word != "":
                count = count + 1
                word = ""
        else:
            word = word + ch
    if word != "":
        count = count + 1
    return {"ok": True, "count": count}

r = count_words("/notes.txt")
if r["ok"]:
    print("words:", r["count"])
else:
    print("file missing")
```
