# Sericite Language Reference

Sericite is a scripting language whose syntax is a subset of Python — it reads like Python, but **not every Python construct is legal**, and some semantics have their own rules. This document is self-contained: read it and you can start writing. It describes the language behavior of Sericite 0.3.1.

## 1. What it looks like

```python
name = "world"            # assignment
print("hello", name)      # prints: hello world
x = input()               # read one line (a string)
```

## 2. Types and operators

Built-in types: `int`, `float`, `str`, `bool`, `None`, `list`, `dict`.

```python
7 / 2        # 3.5 (division always yields float)
7 // 2       # 3
7 % 3        # 1
"ab" + "cd"  # string concatenation (only +; no * repetition, no f-strings, no slicing)
[1, 2] + [3] # list concatenation
not x        # logical not; and / or short-circuit
1 < 2        # comparison: == != < <= > >= (no chaining like a < b < c)
"a" in {"a": 1}    # in / not in: dict keys, list elements, str substrings
```

Key differences from Python (mistakes raise `type_error`):

- `bool` does not participate in arithmetic: `True + 1` is an error.
- Cross-type ordering comparison is an error: `1 < "a"` raises (`==`/`!=` may cross types, evaluating to False/True).
- Dict keys may only be `str/int/float/bool/None`; reading a missing key raises `key_error`.
- Strings support integer-index reads only (`s[0]`); no writes, no slicing. Lists support indexed reads and writes.

## 3. Control flow

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
    print("no break")     # while/else

for item in [1, 2, 3]:      # for iterates: list, dict (keys), str (chars), range() results
    print(item)
for k in {"a": 1, "b": 2}:
    print(k, "->", ...)     # values via d[k]
else:
    print("no break")     # for/else

pass                        # empty statement, legal

label = "big" if x >= 10 else "small"   # ternary (short-circuits: only the chosen branch runs)
```

Note: the for loop variable must be a single name (`for a, b in ...` is not supported). Iterating a list is a live view (edits during iteration are visible); iterating a dict is a snapshot of its keys.

## 4. Functions

```python
def add(a, b=10):           # default arguments (evaluated once at def time)
    return a + b

add(1)                      # 11
add(1, b=20)                # 21, keyword arguments supported

def fib(n):                 # recursion is legal
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)
```

Nested functions use lexical scope and capture outer function locals by reference. A closure reads the latest binding when it is called, not a snapshot from its definition:

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

Closures created by the same outer call share captured variables; separate outer calls have independent environments. A `for` loop does not create a fresh variable per iteration, so closures created in a loop share its final binding. Use a default argument such as `def read(value=x): ...` when a definition-time snapshot is wanted.

Assigning a name anywhere in a function makes it local throughout that function. An inner assignment therefore shadows rather than modifies the outer binding; `nonlocal` and `global` are not supported. Module globals are still resolved from the defining module rather than copied into the closure.

Function decorators follow Python's application semantics:

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

This is equivalent to defining `double`, then rebinding it with `double = trace(double)`. Decorator expressions are evaluated from top to bottom; stacked decorators are applied from bottom to top.

Not supported: variadic arguments (`*args`), keyword-only parameters.

## 5. Exceptions

```python
try:
    x = 1 / 0
except zero_division as e:   # filter by error kind; e is the message string
    print("caught:", e)
except:                      # bare except catches everything catchable
    print("wrong")
else:
    print("no error")
finally:
    print("always runs")

raise "something wrong"      # raises kind raise_error, catchable
raise                        # bare raise inside except: re-raise the current error
```

Common error kinds: `type_error`, `name_error`, `index_error`, `key_error`, `zero_division`, `conversion_error`, `arity_error`, `raise_error`, `file_not_found`, `ffi_error`. Exceptions propagate across functions. `resource_exhausted` (quota exhaustion) is not catchable.

## 6. Built-in functions

- `input()` / `print(...)`: read a line / write a line.
- `len(x)` (str/list/dict), `int(x)`, `float(x)`, `str(x)`, `range(...)` (returns a list), `keys(d)` (returns a list of the dict's keys).
- Files (text only): `read_file(path)`, `write_file(path, text)`, `list_dir(path)`, `path_exists(path)`. Paths look like `/notes/a.txt`; directories exist implicitly via path prefixes.

**No methods**: list/dict/str have no methods at all — `append`, `get`, `join`, `split`, `strip` do not exist. Append with `lst = lst + [x]`; read dict keys with `d[k] if k in d else default`.

## 7. Creating VMs

Sericite code can create new VMs, and the code inside a VM is also sericite:

```python
sub = VM("print('hi')")          # source code string
out = sub.run()                  # run and get back all of its output (a string)
print(out)                       # 'hi\n'
```

VMs are mutually isolated workspaces: a VM cannot see your variables or touch your files — it has its own empty filesystem. Configuration:

```python
sub = VM(code, max_instructions=10000)   # quotas can only be tightened
sub.write_file("/input.txt", "data")     # preset a file for it
sub.feed_input("one line")               # feed input()
out = sub.run()
print(sub.read_file("/result.txt"))      # retrieve the file it wrote
```

Debugging: `sub.step()` single-steps statement by statement (returns a state snapshot dict), `sub.step_instruction()` steps instruction by instruction, `sub.snapshot()` shows locals/state, `sub.terminate()` kills it.

Granting functions to a VM (so it can call your functions, including ones granted to you):

```python
def double(x):
    return x * 2
sub = VM("print(double(21))", functions={"double": double})
sub.run()                        # prints 42
```

A granted sericite function retains its definition-side module variables, closure cells, and already-granted functions by reference. Mutable state reachable through those environments is shared capability: only grant functions whose reachable state the child VM may observe or modify.

## 8. Explicitly not writable (rejected at compile time)

`import`, `class`, `del`, list/dict/string methods, `*args/**kwargs`, chained comparisons, slicing, tuples, f-strings, dunder names, attribute access (except the whitelisted VM handle methods).

## 9. A complete example

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
