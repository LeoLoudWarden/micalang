from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from . import isa
from .compiler import RESERVED_NAMES, VM_METHODS, compile_source
from .errors import MicaError
from .io import InputRequired, OutputLimitExceeded, VirtualIO
from .vfs import VirtualFileSystem

_TERMINAL_STATES = {"done", "error", "resource_exhausted", "terminated"}
_UNCATCHABLE_KINDS = {"resource_exhausted", "internal_error"}
_VM_OPTIONS = {
    "max_instructions",
    "max_output_bytes",
    "max_frames",
    "max_child_vms",
    "max_list_size",
    "max_dict_size",
    "max_vm_depth",
    "max_fs_files",
    "max_fs_file_bytes",
}


@dataclass(frozen=True)
class MicaLimits:
    max_instructions: int = 1_000_000
    max_output_bytes: int = 1_048_576
    max_frames: int = 100
    max_child_vms: int = 100
    max_list_size: int = 100_000
    max_dict_size: int = 100_000
    max_vm_depth: int = 8
    max_fs_files: int = 1000
    max_fs_file_bytes: int = 1_048_576


@dataclass
class Frame:
    code: isa.CodeUnit
    pc: int = 0
    stack: list[object] = field(default_factory=list)
    locals: dict[str, object] = field(default_factory=dict)
    module_locals: dict[str, object] | None = None


class MicaFunction:
    def __init__(self, code: isa.CodeUnit) -> None:
        self.code = code

    def __repr__(self) -> str:
        return f"<function {self.code.name}>"


@dataclass(frozen=True)
class _NativeFunction:
    name: str
    callback: Callable[..., object]


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


@dataclass
class VMContext:
    vm_id: int
    depth: int
    parent_id: int | None
    io: VirtualIO
    fs: VirtualFileSystem
    limits: MicaLimits
    frames: list[Frame] = field(default_factory=list)
    granted: dict[str, object] = field(default_factory=dict)
    state: str = "ready"
    last_line: int | None = None
    error: MicaError | None = None
    instruction_count: int = 0
    child_count: int = 0

    @property
    def filename(self) -> str:
        return f"<mica:vm_{self.vm_id}>"


class VMHandle:
    _METHODS = VM_METHODS

    def __init__(self, engine: MicaEngine, vm_id: int) -> None:
        self._engine = engine
        self._vm_id = vm_id

    def run(self, inputs: object = None) -> str:
        if inputs is not None:
            if not isinstance(inputs, (list, tuple)):
                raise MicaError("VM.run inputs must be a list of strings")
            for value in inputs:
                self.feed_input(value)
        return self._engine._run(self)

    def feed_input(self, value: object) -> None:
        if not isinstance(value, str):
            raise MicaError("VM input must be a string")
        context = self._engine._context(self)
        context.io.feed_input(value)
        if context.state == "waiting_input":
            context.state = "ready"

    def step(self) -> dict[str, object]:
        return self._engine._step(self)

    def step_instruction(self) -> dict[str, object]:
        return self._engine._step_instruction(self)

    def snapshot(self) -> dict[str, object]:
        return self._engine._snapshot(self._engine._context(self))

    def stdout(self) -> str:
        return self._engine._context(self).io.stdout()

    def terminate(self) -> None:
        context = self._engine._context(self)
        if context.state in {"ready", "waiting_input"}:
            context.state = "terminated"

    def read_file(self, path: object) -> str:
        return self._engine._context(self).fs.read_file(path)

    def write_file(self, path: object, text: object) -> None:
        self._engine._context(self).fs.write_file(path, text)

    def list_dir(self, path: object) -> list[str]:
        return self._engine._context(self).fs.list_dir(path)

    def path_exists(self, path: object) -> bool:
        return self._engine._context(self).fs.path_exists(path)

    def __repr__(self) -> str:
        context = self._engine._context(self)
        return f"<VM id={context.vm_id} state={context.state}>"


class MicaEngine:
    def __init__(self, limits: MicaLimits | None = None) -> None:
        self._limits = limits or MicaLimits()
        self._contexts: dict[int, VMContext] = {}
        self._next_vm_id = 0

    def create_root(
        self,
        source: str,
        inputs: Iterable[str] | None = None,
        input_provider: Callable[[], str | None] | None = None,
        limits: MicaLimits | None = None,
        functions: dict[str, Callable[..., object]] | None = None,
    ) -> VMHandle:
        effective = limits or self._limits
        io = VirtualIO(
            inputs=inputs,
            input_provider=input_provider,
            max_output_bytes=effective.max_output_bytes,
        )
        granted = self._validate_host_functions(functions)
        return self._create_vm(
            source=source,
            parent_id=None,
            depth=0,
            io=io,
            limits=effective,
            granted=granted,
        )

    def _validate_host_functions(
        self, functions: dict[str, Callable[..., object]] | None
    ) -> dict[str, object]:
        granted: dict[str, object] = {}
        if not functions:
            return granted
        for name, callback in functions.items():
            _check_grant_name(name)
            if not callable(callback):
                raise MicaError(
                    f"granted function '{name}' is not callable",
                    kind="type_error",
                )
            granted[name] = _GrantedHostFunction(name, callback)
        return granted

    def _create_vm(
        self,
        *,
        source: str,
        parent_id: int | None,
        depth: int,
        io: VirtualIO,
        limits: MicaLimits,
        granted: dict[str, object] | None = None,
    ) -> VMHandle:
        filename = f"<mica:vm_{self._next_vm_id}>"
        code = compile_source(source, filename)
        vm_id = self._next_vm_id
        self._next_vm_id += 1
        context = VMContext(
            vm_id=vm_id,
            depth=depth,
            parent_id=parent_id,
            io=io,
            fs=VirtualFileSystem(
                max_files=limits.max_fs_files,
                max_file_bytes=limits.max_fs_file_bytes,
            ),
            limits=limits,
            frames=[Frame(code=code)],
            granted=dict(granted) if granted else {},
        )
        if not any(instr.stmt_start for instr in code.instrs):
            context.state = "done"
        self._contexts[vm_id] = context
        return VMHandle(self, vm_id)

    def _context(self, handle: VMHandle) -> VMContext:
        if handle._engine is not self:
            raise MicaError("VM handle belongs to another engine", kind="internal_error")
        return self._contexts[handle._vm_id]

    def _run(self, handle: VMHandle) -> str:
        context = self._context(handle)
        while context.state == "ready":
            self._step(handle)
        if context.error is not None:
            raise context.error
        return context.io.stdout()

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

    def _step_instruction(self, handle: VMHandle) -> dict[str, object]:
        context = self._context(handle)
        if context.state != "ready":
            return self._snapshot(context)
        line = self._current_instr(context).line
        if self._execute_one(context):
            context.last_line = line
        return self._snapshot(context)

    def _current_instr(self, context: VMContext) -> isa.Instr:
        frame = context.frames[-1]
        return frame.code.instrs[frame.pc]

    def _execute_one(self, context: VMContext) -> bool:
        if context.state != "ready":
            return False
        frame = context.frames[-1]
        instr = frame.code.instrs[frame.pc]
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
            if self._handle_exception(context, exc):
                return True
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

    def _fail(self, context: VMContext, error: MicaError, line: int) -> None:
        context.error = error.bind(context.filename, line)
        context.state = (
            "resource_exhausted"
            if error.kind == "resource_exhausted"
            else "error"
        )

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

    def _find_handler(
        self, frame: Frame, kind: str
    ) -> isa.Handler | None:
        best: isa.Handler | None = None
        pc = frame.pc
        for handler in frame.code.handlers:
            if not (handler.start <= pc < handler.end):
                continue
            if handler.kinds is not None and kind not in handler.kinds:
                continue
            if best is None or handler.start > best.start:
                best = handler
        return best

    def _dispatch(self, context: VMContext, frame: Frame, instr: isa.Instr) -> None:
        op = instr.op
        arg = instr.arg
        stack = frame.stack
        advance = True
        if op == isa.LOAD_CONST:
            stack.append(arg)
        elif op == isa.LOAD_NAME:
            stack.append(self._load_name(context, frame, arg))
        elif op == isa.STORE_NAME:
            frame.locals[arg] = stack.pop()
        elif op == isa.DUP:
            stack.append(stack[-1])
        elif op == isa.POP:
            stack.pop()
        elif op == isa.BUILD_LIST:
            if arg > context.limits.max_list_size:
                raise MicaError(
                    "list size limit exceeded", kind="resource_exhausted"
                )
            items = stack[len(stack) - arg :] if arg else []
            if arg:
                del stack[len(stack) - arg :]
            stack.append(list(items))
        elif op == isa.BUILD_DICT:
            if arg > context.limits.max_dict_size:
                raise MicaError(
                    "dict size limit exceeded", kind="resource_exhausted"
                )
            pairs = stack[len(stack) - 2 * arg :] if arg else []
            if arg:
                del stack[len(stack) - 2 * arg :]
            mapping: dict[object, object] = {}
            for position in range(0, len(pairs), 2):
                key = pairs[position]
                if type(key) not in (str, int, float, bool) and key is not None:
                    raise MicaError(
                        f"unhashable key type: {_type_name(key)}",
                        kind="type_error",
                    )
                mapping[key] = pairs[position + 1]
            stack.append(mapping)
        elif op == isa.LOAD_INDEX:
            index = stack.pop()
            value = stack.pop()
            stack.append(self._index(value, index))
        elif op == isa.STORE_INDEX:
            index = stack.pop()
            target = stack.pop()
            value = stack.pop()
            self._store_index(context, target, index, value)
        elif op == isa.BINARY_OP:
            right = stack.pop()
            left = stack.pop()
            stack.append(self._binary_op(context, arg, left, right))
        elif op == isa.UNARY_OP:
            stack.append(self._unary_op(arg, stack.pop()))
        elif op == isa.COMPARE_OP:
            right = stack.pop()
            left = stack.pop()
            stack.append(self._compare_op(arg, left, right))
        elif op == isa.JUMP:
            frame.pc = arg
            advance = False
        elif op == isa.JUMP_IF_FALSE:
            if not _truthy(stack.pop()):
                frame.pc = arg
                advance = False
        elif op == isa.JUMP_IF_TRUE_KEEP:
            if _truthy(stack[-1]):
                frame.pc = arg
                advance = False
            else:
                stack.pop()
        elif op == isa.JUMP_IF_FALSE_KEEP:
            if not _truthy(stack[-1]):
                frame.pc = arg
                advance = False
            else:
                stack.pop()
        elif op == isa.LOAD_ATTR:
            stack.append(self._load_attr(stack.pop(), arg))
        elif op == isa.MAKE_FUNCTION:
            stack.append(MicaFunction(arg))
        elif op == isa.CALL:
            argc, kw_names = arg, ()
            self._call(context, frame, argc, kw_names)
            advance = False
        elif op == isa.CALL_KW:
            argc, kw_names = arg
            self._call(context, frame, argc, kw_names)
            advance = False
        elif op == isa.RETURN:
            value = stack.pop() if stack else None
            if len(context.frames) == 1:
                context.state = "done"
            else:
                context.frames.pop()
                context.frames[-1].stack.append(value)
            advance = False
        else:
            raise MicaError(f"unknown opcode: {op}", kind="internal_error")
        if advance and context.state == "ready":
            frame.pc += 1

    def _call(
        self,
        context: VMContext,
        frame: Frame,
        argc: int,
        kw_names: tuple[str, ...],
    ) -> None:
        stack = frame.stack
        base = len(stack) - argc - len(kw_names)
        function = stack[base - 1]
        args = tuple(stack[base : base + argc])
        kwargs = dict(zip(kw_names, stack[base + argc :]))
        if isinstance(function, MicaFunction):
            code = function.code
            locals_ = self._bind_arguments(code, args, kwargs)
            if len(context.frames) + 1 > context.limits.max_frames:
                raise MicaError(
                    "call depth limit exceeded", kind="resource_exhausted"
                )
            del stack[base - 1 :]
            frame.pc += 1
            context.frames.append(Frame(code=code, locals=locals_))
            return
        if isinstance(function, _NativeFunction):
            try:
                result = function.callback(*args, **kwargs)
            except InputRequired:
                raise
            except MicaError:
                raise
            except TypeError:
                raise MicaError(
                    f"invalid arguments for {function.name}", kind="type_error"
                ) from None
            del stack[base - 1 :]
            frame.pc += 1
            stack.append(result)
            return
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
        raise MicaError("value is not callable", kind="type_error")

    def _bind_arguments(
        self,
        code: isa.CodeUnit,
        args: tuple[object, ...],
        kwargs: dict[str, object],
    ) -> dict[str, object]:
        params = code.params
        if len(args) > len(params):
            raise MicaError(
                f"{code.name}() expects {len(params)} argument(s), "
                f"got {len(args)}",
                kind="arity_error",
            )
        locals_ = dict(zip(params, args))
        for key, value in kwargs.items():
            if key in locals_:
                raise MicaError(
                    f"{code.name}() got multiple values for '{key}'",
                    kind="arity_error",
                )
            if key not in params:
                raise MicaError(
                    f"{code.name}() got unexpected argument '{key}'",
                    kind="arity_error",
                )
            locals_[key] = value
        if len(locals_) != len(params):
            missing = [name for name in params if name not in locals_]
            raise MicaError(
                f"{code.name}() missing argument(s): {', '.join(missing)}",
                kind="arity_error",
            )
        return locals_

    def _load_name(self, context: VMContext, frame: Frame, name: str) -> object:
        if name in frame.locals:
            return frame.locals[name]
        module_locals = (
            frame.module_locals
            if frame.module_locals is not None
            else context.frames[0].locals
        )
        if name in module_locals:
            return module_locals[name]
        if name in context.granted:
            return context.granted[name]
        builtin = self._builtin(context, name)
        if builtin is not None:
            return builtin
        raise MicaError(f"name '{name}' is not defined", kind="name_error")

    def _builtin(self, context: VMContext, name: str) -> _NativeFunction | None:
        if name == "input":
            return _NativeFunction("input", context.io.read_input)
        if name == "print":
            return _NativeFunction(
                "print", lambda *values: self._print(context, values)
            )
        if name == "len":
            return _NativeFunction("len", self._len)
        if name == "int":
            return _NativeFunction("int", self._convert_int)
        if name == "float":
            return _NativeFunction("float", self._convert_float)
        if name == "str":
            return _NativeFunction("str", _to_str)
        if name == "range":
            return _NativeFunction(
                "range", lambda *args: self._range(context, args)
            )
        if name == "keys":
            return _NativeFunction("keys", self._keys)
        if name == "read_file":
            return _NativeFunction("read_file", context.fs.read_file)
        if name == "write_file":
            return _NativeFunction("write_file", context.fs.write_file)
        if name == "list_dir":
            return _NativeFunction("list_dir", context.fs.list_dir)
        if name == "path_exists":
            return _NativeFunction("path_exists", context.fs.path_exists)
        if name == "VM":
            return _NativeFunction(
                "VM",
                lambda source, **kwargs: self._create_child(
                    context, source, kwargs
                ),
            )
        return None

    def _create_child(
        self, parent: VMContext, source: object, config: dict[str, object]
    ) -> VMHandle:
        if not isinstance(source, str):
            raise MicaError("VM source must be a string", kind="type_error")
        granted = self._validate_child_functions(parent, config.pop("functions", None))
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
            granted=granted,
        )

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

    def _print(self, context: VMContext, values: tuple[object, ...]) -> None:
        context.io.write_output(" ".join(_to_str(v) for v in values) + "\n")

    def _len(self, value: object) -> int:
        if isinstance(value, (str, list, dict)):
            return len(value)
        raise MicaError(
            f"object of type '{_type_name(value)}' has no len()",
            kind="type_error",
        )

    def _keys(self, value: object) -> list[object]:
        if type(value) is not dict:
            raise MicaError(
                f"keys() expects a dict, got {_type_name(value)}",
                kind="type_error",
            )
        return list(value.keys())

    def _convert_int(self, value: object) -> int:
        if type(value) is bool:
            return int(value)
        if type(value) is int:
            return value
        if type(value) is float:
            return int(value)
        if type(value) is str:
            try:
                return int(value.strip(), 10)
            except ValueError:
                raise MicaError(
                    f"cannot convert to int: {value!r}", kind="conversion_error"
                ) from None
        raise MicaError(
            f"cannot convert {_type_name(value)} to int", kind="type_error"
        )

    def _convert_float(self, value: object) -> float:
        if type(value) is bool:
            return float(value)
        if type(value) in (int, float):
            return float(value)
        if type(value) is str:
            try:
                return float(value.strip())
            except ValueError:
                raise MicaError(
                    f"cannot convert to float: {value!r}",
                    kind="conversion_error",
                ) from None
        raise MicaError(
            f"cannot convert {_type_name(value)} to float", kind="type_error"
        )

    def _range(self, context: VMContext, args: tuple[object, ...]) -> list[int]:
        if not 1 <= len(args) <= 3:
            raise MicaError(
                f"range() expects 1 to 3 argument(s), got {len(args)}",
                kind="arity_error",
            )
        for value in args:
            if type(value) is not int:
                raise MicaError(
                    f"range() arguments must be int, got {_type_name(value)}",
                    kind="type_error",
                )
        if len(args) == 3 and args[2] == 0:
            raise MicaError("range() step cannot be zero", kind="type_error")
        result = range(*args)
        if len(result) > context.limits.max_list_size:
            raise MicaError(
                "list size limit exceeded", kind="resource_exhausted"
            )
        return list(result)

    def _binary_op(
        self, context: VMContext, op: str, left: object, right: object
    ) -> object:
        if op == "+":
            if type(left) is str and type(right) is str:
                return left + right
            if type(left) is list and type(right) is list:
                result = left + right
                if len(result) > context.limits.max_list_size:
                    raise MicaError(
                        "list size limit exceeded", kind="resource_exhausted"
                    )
                return result
            if _is_number(left) and _is_number(right):
                return left + right
            raise _operand_error(op, left, right)
        if op in ("-", "*"):
            if _is_number(left) and _is_number(right):
                return left - right if op == "-" else left * right
            raise _operand_error(op, left, right)
        if op in ("/", "//", "%"):
            if _is_number(left) and _is_number(right):
                if right == 0:
                    raise MicaError(
                        "division by zero"
                        if op != "%"
                        else "modulo by zero",
                        kind="zero_division",
                    )
                if op == "/":
                    return left / right
                if op == "//":
                    return left // right
                return left % right
            raise _operand_error(op, left, right)
        raise MicaError(f"unknown operator: {op}", kind="internal_error")

    def _unary_op(self, op: str, value: object) -> object:
        if op == "-":
            if _is_number(value):
                return -value
            raise MicaError(
                f"bad operand type for unary '-': {_type_name(value)}",
                kind="type_error",
            )
        if op == "not":
            return not _truthy(value)
        raise MicaError(f"unknown operator: {op}", kind="internal_error")

    def _compare_op(self, op: str, left: object, right: object) -> bool:
        if op == "==":
            return _equals(left, right)
        if op == "!=":
            return not _equals(left, right)
        if op in ("in", "not in"):
            result = _contains(right, left)
            return result if op == "in" else not result
        if (_is_number(left) and _is_number(right)) or (
            type(left) is str and type(right) is str
        ):
            if op == "<":
                return left < right
            if op == "<=":
                return left <= right
            if op == ">":
                return left > right
            return left >= right
        raise MicaError(
            f"'{op}' not supported between {_type_name(left)} "
            f"and {_type_name(right)}",
            kind="type_error",
        )

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
        if isinstance(value, (str, list)):
            if type(index) is not int:
                raise MicaError(
                    f"indices must be int, got {_type_name(index)}",
                    kind="type_error",
                )
            if not -len(value) <= index < len(value):
                raise MicaError("index out of range", kind="index_error")
            return value[index]
        raise MicaError(
            f"'{_type_name(value)}' is not indexable", kind="type_error"
        )

    def _store_index(
        self, context: VMContext, target: object, index: object, value: object
    ) -> None:
        if type(target) is dict:
            if type(index) not in (str, int, float, bool) and index is not None:
                raise MicaError(
                    f"unhashable key type: {_type_name(index)}",
                    kind="type_error",
                )
            if (
                index not in target
                and len(target) >= context.limits.max_dict_size
            ):
                raise MicaError(
                    "dict size limit exceeded", kind="resource_exhausted"
                )
            target[index] = value
            return
        if type(target) is not list:
            raise MicaError(
                f"'{_type_name(target)}' does not support item assignment",
                kind="type_error",
            )
        if type(index) is not int:
            raise MicaError(
                f"indices must be int, got {_type_name(index)}",
                kind="type_error",
            )
        if not -len(target) <= index < len(target):
            raise MicaError("index out of range", kind="index_error")
        target[index] = value

    def _load_attr(self, value: object, attr: object) -> object:
        if isinstance(value, VMHandle) and attr in VMHandle._METHODS:
            return _NativeFunction(f"VM.{attr}", getattr(value, attr))
        raise MicaError(
            "attribute access is not available for this value",
            kind="type_error",
        )

    def _snapshot(self, context: VMContext) -> dict[str, object]:
        next_line = None
        if context.state in {"ready", "waiting_input"} and context.frames:
            frame = context.frames[-1]
            if frame.pc < len(frame.code.instrs):
                next_line = frame.code.instrs[frame.pc].line
        locals_: dict[str, object] = {}
        if context.frames:
            locals_ = {
                name: self._safe_value(value)
                for name, value in context.frames[-1].locals.items()
            }
        error = None
        if context.error is not None:
            error = {
                "kind": context.error.kind,
                "message": context.error.message,
                "line": context.error.line,
                "column": context.error.column,
            }
        return {
            "vm_id": context.vm_id,
            "state": context.state,
            "last_line": context.last_line,
            "next_line": next_line,
            "locals": locals_,
            "stdout": context.io.stdout(),
            "error": error,
            "instruction_count": context.instruction_count,
        }

    def _safe_value(self, value: object) -> object:
        if value is None or type(value) in {str, int, float, bool}:
            return value
        if isinstance(value, list):
            return [self._safe_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): self._safe_value(item) for key, item in value.items()
            }
        if isinstance(value, (VMHandle, MicaFunction)):
            return repr(value)
        if isinstance(value, (_GrantedHostFunction, _GrantedMicaFunction)):
            return repr(value)
        return f"<{type(value).__name__}>"


def _is_number(value: object) -> bool:
    return type(value) in (int, float)


def _check_grant_name(name: object) -> None:
    if not isinstance(name, str) or not name.isidentifier() or name.startswith("__"):
        raise MicaError(
            f"invalid granted function name: {name!r}", kind="type_error"
        )
    if name in RESERVED_NAMES:
        raise MicaError(
            f"granted function name '{name}' conflicts with a builtin",
            kind="type_error",
        )


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


def _truthy(value: object) -> bool:
    if value is None:
        return False
    if type(value) is bool:
        return value
    if type(value) in (int, float):
        return value != 0
    if isinstance(value, (str, list, dict)):
        return len(value) > 0
    return True


def _equals(left: object, right: object) -> bool:
    if _is_number(left) and _is_number(right):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, (str, bool, list, dict)) or left is None:
        return left == right
    return left is right


def _contains(container: object, item: object) -> bool:
    if type(container) is dict:
        if type(item) not in (str, int, float, bool) and item is not None:
            raise MicaError(
                f"unhashable key type: {_type_name(item)}",
                kind="type_error",
            )
        return item in container
    if type(container) is list:
        return any(_equals(element, item) for element in container)
    if type(container) is str and type(item) is str:
        return item in container
    raise MicaError(
        f"'in' not supported between {_type_name(item)} "
        f"and {_type_name(container)}",
        kind="type_error",
    )


def _type_name(value: object) -> str:
    if value is None:
        return "None"
    if type(value) is bool:
        return "bool"
    if type(value) is int:
        return "int"
    if type(value) is float:
        return "float"
    if type(value) is str:
        return "str"
    if type(value) is list:
        return "list"
    if type(value) is dict:
        return "dict"
    if isinstance(value, MicaFunction):
        return "function"
    if isinstance(value, VMHandle):
        return "VM"
    return type(value).__name__


def _operand_error(op: str, left: object, right: object) -> MicaError:
    return MicaError(
        f"unsupported operand types for '{op}': "
        f"{_type_name(left)} and {_type_name(right)}",
        kind="type_error",
    )


def _to_str(value: object) -> str:
    if value is None:
        return "None"
    if type(value) is bool:
        return "True" if value else "False"
    if type(value) is str:
        return value
    if type(value) in (int, float):
        return str(value)
    if type(value) is list:
        return "[" + ", ".join(_repr_str(item) for item in value) + "]"
    if type(value) is dict:
        return (
            "{"
            + ", ".join(
                f"{_repr_str(key)}: {_repr_str(item)}"
                for key, item in value.items()
            )
            + "}"
        )
    return repr(value)


def _repr_str(value: object) -> str:
    if type(value) is str:
        return repr(value)
    return _to_str(value)
