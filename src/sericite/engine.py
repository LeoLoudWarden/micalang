from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from . import isa
from .compiler import RESERVED_NAMES, VM_METHODS, compile_source
from .errors import SericiteError
from .io import InputRequired, OutputLimitExceeded, VirtualIO
from .vfs import VirtualFileSystem

_TERMINAL_STATES = {"done", "error", "resource_exhausted", "terminated"}
_UNCATCHABLE_KINDS = {"resource_exhausted", "internal_error"}
_MAX_SOURCE_BYTES = 4 * 1024 * 1024
_VM_OPTIONS = {
    "max_instructions",
    "max_output_bytes",
    "max_frames",
    "max_child_vms",
    "max_list_size",
    "max_dict_size",
    "max_string_bytes",
    "max_int_bits",
    "max_total_bytes",
    "max_total_vms",
    "max_vm_depth",
    "max_fs_files",
    "max_fs_file_bytes",
}


@dataclass(frozen=True)
class SericiteLimits:
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


_UNBOUND = object()


class _Cell:
    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value = _UNBOUND


@dataclass
class Frame:
    code: isa.CodeUnit
    pc: int = 0
    stack: list[object] = field(default_factory=list)
    locals: dict[str, object] = field(default_factory=dict)
    cells: dict[str, _Cell] = field(default_factory=dict)
    closure: dict[str, _Cell] = field(default_factory=dict)
    module_locals: dict[str, object] | None = None
    active_error: SericiteError | None = None


class SericiteFunction:
    def __init__(
        self,
        code: isa.CodeUnit,
        defaults: tuple[object, ...],
        closure: dict[str, _Cell],
        module_locals: dict[str, object],
    ) -> None:
        self.code = code
        self.defaults = defaults
        self.closure = closure
        self.module_locals = module_locals

    def __repr__(self) -> str:
        return f"<function {self.code.name}>"


class _Iterator:
    __slots__ = ("items", "pos")

    def __init__(self, items: list[object]) -> None:
        self.items = items
        self.pos = 0


def _make_iterator(value: object) -> _Iterator:
    if type(value) is list:
        return _Iterator(value)
    if type(value) is str:
        return _Iterator(list(value))
    if type(value) is dict:
        return _Iterator(list(value.keys()))
    raise SericiteError(
        f"'{_type_name(value)}' is not iterable", kind="type_error"
    )


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
class _GrantedSericiteFunction:
    """父 VM 授予的函数：在调用方帧栈执行，保留定义方闭包与模块全局。"""

    name: str
    code: isa.CodeUnit
    module_locals: dict[str, object]
    closure: dict[str, _Cell]
    defaults: tuple[object, ...] = ()

    def __repr__(self) -> str:
        return f"<function {self.name}>"


@dataclass
class VMContext:
    vm_id: int
    depth: int
    parent_id: int | None
    io: VirtualIO
    fs: VirtualFileSystem
    limits: SericiteLimits
    frames: list[Frame] = field(default_factory=list)
    granted: dict[str, object] = field(default_factory=dict)
    state: str = "ready"
    last_line: int | None = None
    error: SericiteError | None = None
    instruction_count: int = 0
    alloc_bytes: int = 0
    child_count: int = 0
    running: bool = False

    @property
    def filename(self) -> str:
        return f"<sericite:vm_{self.vm_id}>"


class VMHandle:
    _METHODS = VM_METHODS

    def __init__(self, engine: SericiteEngine, vm_id: int) -> None:
        self._engine = engine
        self._vm_id = vm_id

    def run(self, inputs: object = None) -> str:
        if inputs is not None:
            if not isinstance(inputs, (list, tuple)):
                raise SericiteError("VM.run inputs must be a list of strings")
            for value in inputs:
                self.feed_input(value)
        return self._engine._run(self)

    def feed_input(self, value: object) -> None:
        if not isinstance(value, str):
            raise SericiteError("VM input must be a string")
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


class SericiteEngine:
    def __init__(self, limits: SericiteLimits | None = None) -> None:
        self._limits = limits or SericiteLimits()
        self._contexts: dict[int, VMContext] = {}
        self._next_vm_id = 0

    def create_root(
        self,
        source: str,
        inputs: Iterable[str] | None = None,
        input_provider: Callable[[], str | None] | None = None,
        limits: SericiteLimits | None = None,
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
                raise SericiteError(
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
        limits: SericiteLimits,
        granted: dict[str, object] | None = None,
    ) -> VMHandle:
        filename = f"<sericite:vm_{self._next_vm_id}>"
        if len(source.encode("utf-8")) > _MAX_SOURCE_BYTES:
            raise SericiteError(
                "source too large", kind="validation_error", filename=filename
            )
        if len(self._contexts) >= limits.max_total_vms:
            raise SericiteError(
                "total VM limit exceeded",
                kind="resource_exhausted",
                filename=filename,
            )
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
        self._charge_alloc(context, _const_bytes(code))
        return VMHandle(self, vm_id)

    def _context(self, handle: VMHandle) -> VMContext:
        if handle._engine is not self:
            raise SericiteError("VM handle belongs to another engine", kind="internal_error")
        return self._contexts[handle._vm_id]

    def _run(self, handle: VMHandle) -> str:
        context = self._context(handle)
        if context.running:
            raise SericiteError("VM is already running")
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

    def _step(self, handle: VMHandle) -> dict[str, object]:
        context = self._context(handle)
        if context.running:
            raise SericiteError("VM is already running")
        if context.state != "ready":
            return self._snapshot(context)
        context.running = True
        try:
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
        finally:
            context.running = False
        return self._snapshot(context)

    def _step_instruction(self, handle: VMHandle) -> dict[str, object]:
        context = self._context(handle)
        if context.running:
            raise SericiteError("VM is already running")
        if context.state != "ready":
            return self._snapshot(context)
        context.running = True
        try:
            line = self._current_instr(context).line
            if self._execute_one(context):
                context.last_line = line
        finally:
            context.running = False
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
                SericiteError("instruction limit exceeded", kind="resource_exhausted"),
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
                SericiteError("output limit exceeded", kind="resource_exhausted"),
                instr.line,
            )
            return False
        except SericiteError as exc:
            if self._handle_exception(context, exc):
                return True
            self._fail(context, exc, instr.line)
            return False
        except RecursionError:
            self._fail(
                context,
                SericiteError(
                    "recursion limit exceeded", kind="resource_exhausted"
                ),
                instr.line,
            )
            return False
        except OverflowError:
            error = SericiteError("numeric overflow", kind="conversion_error")
            if self._handle_exception(context, error):
                return True
            self._fail(context, error, instr.line)
            return False
        except Exception:
            self._fail(
                context,
                SericiteError("internal runtime error", kind="internal_error"),
                instr.line,
            )
            return False
        return True

    def _fail(self, context: VMContext, error: SericiteError, line: int) -> None:
        context.error = error.bind(context.filename, line)
        context.state = (
            "resource_exhausted"
            if error.kind == "resource_exhausted"
            else "error"
        )

    def _handle_exception(self, context: VMContext, error: SericiteError) -> bool:
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
            frame.active_error = error
            if handler.error_name is not None:
                self._store_local(frame, handler.error_name, error)
            if handler.bind is not None:
                self._store_local(frame, handler.bind, error.message)
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
            if type(arg) is int:
                if arg.bit_length() > context.limits.max_int_bits:
                    raise SericiteError(
                        "integer size limit exceeded", kind="resource_exhausted"
                    )
            elif (
                type(arg) is str
                and len(arg) > context.limits.max_string_bytes
            ):
                raise SericiteError(
                    "string size limit exceeded", kind="resource_exhausted"
                )
            stack.append(arg)
        elif op == isa.LOAD_NAME:
            stack.append(self._load_name(context, frame, arg))
        elif op == isa.STORE_NAME:
            frame.locals[arg] = stack.pop()
        elif op == isa.LOAD_DEREF:
            stack.append(self._load_deref(frame, arg))
        elif op == isa.STORE_DEREF:
            self._cell(frame, arg).value = stack.pop()
        elif op == isa.DUP:
            stack.append(stack[-1])
        elif op == isa.POP:
            stack.pop()
        elif op == isa.BUILD_LIST:
            if arg > context.limits.max_list_size:
                raise SericiteError(
                    "list size limit exceeded", kind="resource_exhausted"
                )
            items = stack[len(stack) - arg :] if arg else []
            if arg:
                del stack[len(stack) - arg :]
            self._charge_alloc(context, arg * 8)
            stack.append(list(items))
        elif op == isa.BUILD_DICT:
            if arg > context.limits.max_dict_size:
                raise SericiteError(
                    "dict size limit exceeded", kind="resource_exhausted"
                )
            pairs = stack[len(stack) - 2 * arg :] if arg else []
            if arg:
                del stack[len(stack) - 2 * arg :]
            mapping: dict[object, object] = {}
            for position in range(0, len(pairs), 2):
                key = pairs[position]
                if type(key) not in (str, int, float, bool) and key is not None:
                    raise SericiteError(
                        f"unhashable key type: {_type_name(key)}",
                        kind="type_error",
                    )
                mapping[key] = pairs[position + 1]
            self._charge_alloc(context, arg * 16)
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
            stack.append(self._unary_op(context, arg, stack.pop()))
        elif op == isa.COMPARE_OP:
            right = stack.pop()
            left = stack.pop()
            stack.append(self._compare_op(context, arg, left, right))
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
            stack.append(self._load_attr(context, stack.pop(), arg))
        elif op == isa.MAKE_FUNCTION:
            code, defaults_count = arg
            defaults = (
                tuple(stack[len(stack) - defaults_count :])
                if defaults_count
                else ()
            )
            if defaults_count:
                del stack[len(stack) - defaults_count :]
            closure = {name: self._cell(frame, name) for name in code.free_names}
            module_locals = (
                frame.module_locals
                if frame.module_locals is not None
                else context.frames[0].locals
            )
            self._charge_alloc(context, len(closure) * 8)
            stack.append(
                SericiteFunction(code, defaults, closure, module_locals)
            )
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
        elif op == isa.RAISE:
            if isinstance(arg, tuple):
                raise frame.locals[arg[1]]
            if arg is None:
                if frame.active_error is None:
                    raise SericiteError(
                        "raise without an active error", kind="raise_error"
                    )
                raise frame.active_error
            message = stack.pop()
            if type(message) is not str:
                raise SericiteError(
                    "raise expects a string message", kind="type_error"
                )
            raise SericiteError(message, kind="raise_error")
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
            raise SericiteError(f"unknown opcode: {op}", kind="internal_error")
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
        if isinstance(function, SericiteFunction):
            code = function.code
            locals_ = self._bind_arguments(
                code, args, kwargs, function.defaults
            )
            if len(context.frames) + 1 > context.limits.max_frames:
                raise SericiteError(
                    "call depth limit exceeded", kind="resource_exhausted"
                )
            del stack[base - 1 :]
            frame.pc += 1
            context.frames.append(
                self._new_frame(
                    context,
                    code,
                    locals_,
                    function.module_locals,
                    function.closure,
                )
            )
            return
        if isinstance(function, _NativeFunction):
            try:
                result = function.callback(*args, **kwargs)
            except InputRequired:
                raise
            except SericiteError:
                raise
            except TypeError:
                raise SericiteError(
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
            except SericiteError:
                raise
            except Exception as exc:
                raise SericiteError(
                    f"host function '{function.name}' failed with "
                    f"{type(exc).__name__}",
                    kind="ffi_error",
                ) from None
            result_budget = [
                context.limits.max_list_size + context.limits.max_dict_size
            ]
            result = _from_host(
                raw, result_budget, context.limits.max_string_bytes
            )
            spent = (
                context.limits.max_list_size
                + context.limits.max_dict_size
                - result_budget[0]
            )
            self._charge_alloc(context, spent * 8)
            del stack[base - 1 :]
            frame.pc += 1
            stack.append(result)
            return
        if isinstance(function, _GrantedSericiteFunction):
            code = function.code
            locals_ = self._bind_arguments(
                code, args, kwargs, function.defaults
            )
            if len(context.frames) + 1 > context.limits.max_frames:
                raise SericiteError(
                    "call depth limit exceeded", kind="resource_exhausted"
                )
            del stack[base - 1 :]
            frame.pc += 1
            context.frames.append(
                self._new_frame(
                    context,
                    code,
                    locals_,
                    function.module_locals,
                    function.closure,
                )
            )
            return
        raise SericiteError("value is not callable", kind="type_error")

    def _new_frame(
        self,
        context: VMContext,
        code: isa.CodeUnit,
        locals_: dict[str, object],
        module_locals: dict[str, object],
        closure: dict[str, _Cell],
    ) -> Frame:
        cells = {name: _Cell() for name in code.cell_names}
        for name, cell in cells.items():
            if name in locals_:
                cell.value = locals_.pop(name)
        self._charge_alloc(context, len(cells) * 8)
        return Frame(
            code=code,
            locals=locals_,
            cells=cells,
            closure=closure,
            module_locals=module_locals,
        )

    def _bind_arguments(
        self,
        code: isa.CodeUnit,
        args: tuple[object, ...],
        kwargs: dict[str, object],
        defaults: tuple[object, ...] = (),
    ) -> dict[str, object]:
        params = code.params
        if len(args) > len(params):
            raise SericiteError(
                f"{code.name}() expects {len(params)} argument(s), "
                f"got {len(args)}",
                kind="arity_error",
            )
        locals_ = dict(zip(params, args))
        for key, value in kwargs.items():
            if key in locals_:
                raise SericiteError(
                    f"{code.name}() got multiple values for '{key}'",
                    kind="arity_error",
                )
            if key not in params:
                raise SericiteError(
                    f"{code.name}() got unexpected argument '{key}'",
                    kind="arity_error",
                )
            locals_[key] = value
        if defaults:
            default_start = len(params) - len(defaults)
            for offset, name in enumerate(params[default_start:]):
                if name not in locals_:
                    locals_[name] = defaults[offset]
        if len(locals_) != len(params):
            missing = [name for name in params if name not in locals_]
            raise SericiteError(
                f"{code.name}() missing argument(s): {', '.join(missing)}",
                kind="arity_error",
            )
        return locals_

    def _store_local(self, frame: Frame, name: str, value: object) -> None:
        if name in frame.cells:
            frame.cells[name].value = value
        else:
            frame.locals[name] = value

    def _cell(self, frame: Frame, name: str) -> _Cell:
        if name in frame.cells:
            return frame.cells[name]
        if name in frame.closure:
            return frame.closure[name]
        raise SericiteError("missing closure cell", kind="internal_error")

    def _load_deref(self, frame: Frame, name: str) -> object:
        value = self._cell(frame, name).value
        if value is _UNBOUND:
            raise SericiteError(f"name '{name}' is not defined", kind="name_error")
        return value

    def _load_name(self, context: VMContext, frame: Frame, name: str) -> object:
        if name in frame.locals:
            return frame.locals[name]
        if frame.code.name != "<module>" and name in frame.code.local_names:
            raise SericiteError(f"name '{name}' is not defined", kind="name_error")
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
        raise SericiteError(f"name '{name}' is not defined", kind="name_error")

    def _builtin(self, context: VMContext, name: str) -> _NativeFunction | None:
        if name == "input":
            return _NativeFunction("input", lambda: self._read_input(context))
        if name == "print":
            return _NativeFunction(
                "print", lambda *values: self._print(context, values)
            )
        if name == "len":
            return _NativeFunction("len", self._len)
        if name == "int":
            return _NativeFunction(
                "int", lambda value: self._convert_int(context, value)
            )
        if name == "float":
            return _NativeFunction("float", self._convert_float)
        if name == "str":
            return _NativeFunction(
                "str", lambda value: self._str_builtin(context, value)
            )
        if name == "range":
            return _NativeFunction(
                "range", lambda *args: self._range(context, args)
            )
        if name == "keys":
            return _NativeFunction(
                "keys", lambda value: self._keys(context, value)
            )
        if name == "read_file":
            return _NativeFunction(
                "read_file", lambda path: self._read_file(context, path)
            )
        if name == "write_file":
            return _NativeFunction(
                "write_file",
                lambda path, text: self._write_file(context, path, text),
            )
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
            raise SericiteError("VM source must be a string", kind="type_error")
        self._charge_work(parent, 1 + len(source) // 64)
        granted = self._validate_child_functions(parent, config.pop("functions", None))
        limits = parent.limits
        if config:
            overrides: dict[str, int] = {}
            for key, value in config.items():
                if key not in _VM_OPTIONS:
                    raise SericiteError(
                        f"unknown VM option: {key}", kind="type_error"
                    )
                if type(value) is not int or value < 0:
                    raise SericiteError(
                        f"VM option {key} must be a non-negative int",
                        kind="type_error",
                    )
                overrides[key] = min(getattr(parent.limits, key), value)
            limits = dataclasses.replace(parent.limits, **overrides)
        depth = parent.depth + 1
        if depth > limits.max_vm_depth:
            raise SericiteError("VM depth limit exceeded", kind="resource_exhausted")
        if parent.child_count >= parent.limits.max_child_vms:
            raise SericiteError(
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
            raise SericiteError(
                "VM option functions must be a dict", kind="type_error"
            )
        granted: dict[str, object] = {}
        for name, value in functions.items():
            _check_grant_name(name)
            if isinstance(value, SericiteFunction):
                granted[name] = _GrantedSericiteFunction(
                    name,
                    value.code,
                    value.module_locals,
                    value.closure,
                    value.defaults,
                )
            elif isinstance(value, _GrantedHostFunction):
                granted[name] = _GrantedHostFunction(name, value.callback)
            elif isinstance(value, _GrantedSericiteFunction):
                granted[name] = _GrantedSericiteFunction(
                    name,
                    value.code,
                    value.module_locals,
                    value.closure,
                    value.defaults,
                )
            else:
                raise SericiteError(
                    f"granted value '{name}' is not a function",
                    kind="type_error",
                )
        return granted

    def _print(self, context: VMContext, values: tuple[object, ...]) -> None:
        line = " ".join(_to_str(v) for v in values) + "\n"
        self._charge_work(context, len(line) // 64)
        context.io.write_output(line)

    def _read_input(self, context: VMContext) -> str:
        value = context.io.read_input()
        if len(value) > context.limits.max_string_bytes:
            raise SericiteError(
                "string size limit exceeded", kind="resource_exhausted"
            )
        self._charge_alloc(context, len(value) * 4)
        return value

    def _read_file(self, context: VMContext, path: object) -> str:
        text = context.fs.read_file(path)
        self._charge_alloc(context, len(text) * 4)
        return text

    def _write_file(self, context: VMContext, path: object, text: object) -> None:
        if isinstance(text, str):
            self._charge_alloc(context, len(text) * 4)
        context.fs.write_file(path, text)

    def _len(self, value: object) -> int:
        if isinstance(value, (str, list, dict)):
            return len(value)
        raise SericiteError(
            f"object of type '{_type_name(value)}' has no len()",
            kind="type_error",
        )

    def _keys(self, context: VMContext, value: object) -> list[object]:
        if type(value) is not dict:
            raise SericiteError(
                f"keys() expects a dict, got {_type_name(value)}",
                kind="type_error",
            )
        self._charge_alloc(context, len(value) * 8)
        return list(value.keys())
    def _convert_int(self, context: VMContext, value: object) -> int:
        if type(value) is bool:
            return int(value)
        if type(value) is int:
            return self._check_int(context, value)
        if type(value) is float:
            try:
                return self._check_int(context, int(value))
            except ValueError:
                raise SericiteError(
                    "cannot convert nan to int", kind="conversion_error"
                ) from None
        if type(value) is str:
            text = value.strip()
            if len(text) > context.limits.max_int_bits // 3:
                raise SericiteError(
                    "integer size limit exceeded", kind="resource_exhausted"
                )
            try:
                return self._check_int(context, int(text, 10))
            except ValueError:
                raise SericiteError(
                    f"cannot convert to int: {value!r}", kind="conversion_error"
                ) from None
        raise SericiteError(
            f"cannot convert {_type_name(value)} to int", kind="type_error"
        )

    def _str_builtin(self, context: VMContext, value: object) -> str:
        result = _to_str(value)
        if len(result) > context.limits.max_string_bytes:
            raise SericiteError(
                "string size limit exceeded", kind="resource_exhausted"
            )
        self._charge_alloc(context, len(result) * 4)
        self._charge_work(context, len(result) // 64)
        return result

    def _convert_float(self, value: object) -> float:
        if type(value) is bool:
            return float(value)
        if type(value) in (int, float):
            return float(value)
        if type(value) is str:
            try:
                return float(value.strip())
            except ValueError:
                raise SericiteError(
                    f"cannot convert to float: {value!r}",
                    kind="conversion_error",
                ) from None
        raise SericiteError(
            f"cannot convert {_type_name(value)} to float", kind="type_error"
        )

    def _range(self, context: VMContext, args: tuple[object, ...]) -> list[int]:
        if not 1 <= len(args) <= 3:
            raise SericiteError(
                f"range() expects 1 to 3 argument(s), got {len(args)}",
                kind="arity_error",
            )
        for value in args:
            if type(value) is not int:
                raise SericiteError(
                    f"range() arguments must be int, got {_type_name(value)}",
                    kind="type_error",
                )
        if len(args) == 3 and args[2] == 0:
            raise SericiteError("range() step cannot be zero", kind="type_error")
        result = range(*args)
        if len(result) > context.limits.max_list_size:
            raise SericiteError(
                "list size limit exceeded", kind="resource_exhausted"
            )
        self._charge_alloc(context, len(result) * 8)
        return list(result)

    def _binary_op(
        self, context: VMContext, op: str, left: object, right: object
    ) -> object:
        if op == "+":
            if type(left) is str and type(right) is str:
                if len(left) + len(right) > context.limits.max_string_bytes:
                    raise SericiteError(
                        "string size limit exceeded", kind="resource_exhausted"
                    )
                self._charge_alloc(context, (len(left) + len(right)) * 4)
                self._charge_work(context, (len(left) + len(right)) // 64)
                return left + right
            if type(left) is list and type(right) is list:
                result = left + right
                if len(result) > context.limits.max_list_size:
                    raise SericiteError(
                        "list size limit exceeded", kind="resource_exhausted"
                    )
                self._charge_alloc(context, len(result) * 8)
                self._charge_work(context, len(result))
                return result
            if _is_number(left) and _is_number(right):
                return self._check_int(context, left + right)
            raise _operand_error(op, left, right)
        if op in ("-", "*"):
            if _is_number(left) and _is_number(right):
                return self._check_int(
                    context, left - right if op == "-" else left * right
                )
            raise _operand_error(op, left, right)
        if op in ("/", "//", "%"):
            if _is_number(left) and _is_number(right):
                if right == 0:
                    raise SericiteError(
                        "division by zero"
                        if op != "%"
                        else "modulo by zero",
                        kind="zero_division",
                    )
                if op == "/":
                    return left / right
                if op == "//":
                    return self._check_int(context, left // right)
                return self._check_int(context, left % right)
            raise _operand_error(op, left, right)
        raise SericiteError(f"unknown operator: {op}", kind="internal_error")

    def _check_int(self, context: VMContext, value: object) -> object:
        if type(value) is int and value.bit_length() > context.limits.max_int_bits:
            raise SericiteError(
                "integer size limit exceeded", kind="resource_exhausted"
            )
        return value

    def _charge_alloc(self, context: VMContext, amount: int) -> None:
        """为新创建的对象记账（近似字节数，只增不减），并计入祖先。

        记账沿祖先链累加，链上任意一环超出自己的配额即失败——
        否则祖先的配额要等到它自己再次分配时才会被检查。
        """
        current = context
        while True:
            current.alloc_bytes += amount
            if current.alloc_bytes > current.limits.max_total_bytes:
                raise SericiteError(
                    "total memory limit exceeded", kind="resource_exhausted"
                )
            if current.parent_id is None:
                break
            current = self._contexts[current.parent_id]

    def _charge_work(self, context: VMContext, amount: int) -> None:
        """把大体量操作折算成指令数记账（防 O(n) 操作只计 1 条指令）。"""
        if amount <= 0:
            return
        current = context
        while True:
            current.instruction_count += amount
            if current.instruction_count > current.limits.max_instructions:
                raise SericiteError(
                    "instruction limit exceeded", kind="resource_exhausted"
                )
            if current.parent_id is None:
                break
            current = self._contexts[current.parent_id]

    def _unary_op(self, context: VMContext, op: str, value: object) -> object:
        if op == "-":
            if _is_number(value):
                return self._check_int(context, -value)
            raise SericiteError(
                f"bad operand type for unary '-': {_type_name(value)}",
                kind="type_error",
            )
        if op == "not":
            return not _truthy(value)
        raise SericiteError(f"unknown operator: {op}", kind="internal_error")

    def _compare_op(
        self, context: VMContext, op: str, left: object, right: object
    ) -> bool:
        if op == "==":
            self._charge_work(context, _container_size(left))
            return _equals(left, right)
        if op == "!=":
            self._charge_work(context, _container_size(left))
            return not _equals(left, right)
        if op in ("in", "not in"):
            self._charge_work(context, _container_size(right))
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
        raise SericiteError(
            f"'{op}' not supported between {_type_name(left)} "
            f"and {_type_name(right)}",
            kind="type_error",
        )

    def _index(self, value: object, index: object) -> object:
        if type(value) is dict:
            if type(index) not in (str, int, float, bool) and index is not None:
                raise SericiteError(
                    f"unhashable key type: {_type_name(index)}",
                    kind="type_error",
                )
            if index not in value:
                raise SericiteError(
                    f"missing key: {_to_str(index)!s}", kind="key_error"
                )
            return value[index]
        if isinstance(value, (str, list)):
            if type(index) is not int:
                raise SericiteError(
                    f"indices must be int, got {_type_name(index)}",
                    kind="type_error",
                )
            if not -len(value) <= index < len(value):
                raise SericiteError("index out of range", kind="index_error")
            return value[index]
        raise SericiteError(
            f"'{_type_name(value)}' is not indexable", kind="type_error"
        )

    def _store_index(
        self, context: VMContext, target: object, index: object, value: object
    ) -> None:
        if type(target) is dict:
            if type(index) not in (str, int, float, bool) and index is not None:
                raise SericiteError(
                    f"unhashable key type: {_type_name(index)}",
                    kind="type_error",
                )
            if (
                index not in target
                and len(target) >= context.limits.max_dict_size
            ):
                raise SericiteError(
                    "dict size limit exceeded", kind="resource_exhausted"
                )
            if index not in target:
                self._charge_alloc(context, 16)
            target[index] = value
            return
        if type(target) is not list:
            raise SericiteError(
                f"'{_type_name(target)}' does not support item assignment",
                kind="type_error",
            )
        if type(index) is not int:
            raise SericiteError(
                f"indices must be int, got {_type_name(index)}",
                kind="type_error",
            )
        if not -len(target) <= index < len(target):
            raise SericiteError("index out of range", kind="index_error")
        target[index] = value

    def _load_attr(self, context: VMContext, value: object, attr: object) -> object:
        if isinstance(value, VMHandle) and attr in VMHandle._METHODS:
            if attr in ("snapshot", "stdout", "list_dir", "run"):
                return _NativeFunction(
                    f"VM.{attr}",
                    lambda *a, **k: self._charged_vm_method(
                        context, value, attr, a, k
                    ),
                )
            return _NativeFunction(f"VM.{attr}", getattr(value, attr))
        raise SericiteError(
            "attribute access is not available for this value",
            kind="type_error",
        )

    def _charged_vm_method(
        self,
        context: VMContext,
        handle: VMHandle,
        attr: str,
        args: tuple[object, ...],
        kwargs: dict[str, object],
    ) -> object:
        result = getattr(handle, attr)(*args, **kwargs)
        budget = [10_000]
        size = _estimate_size(result, budget)
        self._charge_alloc(context, size)
        self._charge_work(context, size // 64)
        return result

    def _snapshot(self, context: VMContext) -> dict[str, object]:
        next_line = None
        if context.state in {"ready", "waiting_input"} and context.frames:
            frame = context.frames[-1]
            if frame.pc < len(frame.code.instrs):
                next_line = frame.code.instrs[frame.pc].line
        locals_: dict[str, object] = {}
        if context.frames:
            frame = context.frames[-1]
            visible = dict(frame.locals)
            for name, cell in frame.cells.items():
                if cell.value is not _UNBOUND:
                    visible[name] = cell.value
            locals_ = {
                name: self._safe_value(value)
                for name, value in visible.items()
                if not name.startswith("$")
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

    def _safe_value(
        self,
        value: object,
        _seen: set[int] | None = None,
        _depth: int = 0,
    ) -> object:
        if value is None or type(value) in {str, int, float, bool}:
            return value
        if isinstance(value, (list, dict)):
            if _depth > 64:
                return "<max depth>"
            if _seen is None:
                _seen = set()
            ident = id(value)
            if ident in _seen:
                return "[...]"
            _seen.add(ident)
            try:
                if isinstance(value, list):
                    return [
                        self._safe_value(item, _seen, _depth + 1)
                        for item in value
                    ]
                return {
                    str(key): self._safe_value(item, _seen, _depth + 1)
                    for key, item in value.items()
                }
            finally:
                _seen.discard(ident)
        if isinstance(value, (VMHandle, SericiteFunction)):
            return repr(value)
        if isinstance(value, (_GrantedHostFunction, _GrantedSericiteFunction)):
            return repr(value)
        return f"<{type(value).__name__}>"


def _is_number(value: object) -> bool:
    return type(value) in (int, float)


def _check_grant_name(name: object) -> None:
    if not isinstance(name, str) or not name.isidentifier() or name.startswith("__"):
        raise SericiteError(
            f"invalid granted function name: {name!r}", kind="type_error"
        )
    if name in RESERVED_NAMES:
        raise SericiteError(
            f"granted function name '{name}' conflicts with a builtin",
            kind="type_error",
        )


def _to_host(value: object, budget: list[int], depth: int = 0) -> object:
    """把 sericite 值深拷贝为宿主侧纯数据结构；不允许句柄/函数越界。"""
    budget[0] -= 1
    if budget[0] < 0:
        raise SericiteError("FFI argument structure too large", kind="ffi_error")
    if depth > 64:
        raise SericiteError("FFI argument nested too deeply", kind="ffi_error")
    if value is None or type(value) in (str, int, float, bool):
        return value
    if type(value) is list:
        return [_to_host(item, budget, depth + 1) for item in value]
    if type(value) is dict:
        return {
            _to_host(key, budget, depth + 1): _to_host(item, budget, depth + 1)
            for key, item in value.items()
        }
    raise SericiteError(
        f"value of type '{_type_name(value)}' cannot cross the host boundary",
        kind="ffi_error",
    )


def _from_host(
    value: object, budget: list[int], max_string_bytes: int, depth: int = 0
) -> object:
    """把宿主返回值校验并深拷贝为 sericite 值；异物与巨型结构拒绝。"""
    budget[0] -= 1
    if budget[0] < 0:
        raise SericiteError("FFI result structure too large", kind="ffi_error")
    if depth > 64:
        raise SericiteError("FFI result nested too deeply", kind="ffi_error")
    if value is None or type(value) in (int, float, bool):
        return value
    if type(value) is str:
        if len(value) > max_string_bytes:
            raise SericiteError(
                "FFI result string too large", kind="ffi_error"
            )
        return value
    if type(value) in (list, tuple):
        return [_from_host(item, budget, max_string_bytes, depth + 1) for item in value]
    if type(value) is dict:
        result: dict[object, object] = {}
        for key, item in value.items():
            if type(key) not in (str, int, float, bool) and key is not None:
                raise SericiteError(
                    f"FFI result has unhashable key of type "
                    f"'{type(key).__name__}'",
                    kind="ffi_error",
                )
            result[key] = _from_host(item, budget, max_string_bytes, depth + 1)
        return result
    raise SericiteError(
        f"host function returned unsupported type "
        f"'{type(value).__name__}'",
        kind="ffi_error",
    )


def _container_size(value: object) -> int:
    if isinstance(value, (str, list, dict)):
        return len(value)
    return 1


def _const_bytes(code: isa.CodeUnit) -> int:
    total = 0
    for instr in code.instrs:
        if instr.op == isa.LOAD_CONST and type(instr.arg) is str:
            total += len(instr.arg) * 4
        elif instr.op == isa.MAKE_FUNCTION:
            total += _const_bytes(instr.arg[0])
    return total


def _estimate_size(value: object, budget: list[int]) -> int:
    """估算一个纯数据结构的近似字节数，节点数封顶防失控。"""
    if budget[0] <= 0:
        return 0
    budget[0] -= 1
    if type(value) is str:
        return len(value) * 4
    if type(value) is list:
        return len(value) * 8 + sum(
            _estimate_size(item, budget) for item in value
        )
    if type(value) is dict:
        return len(value) * 16 + sum(
            _estimate_size(key, budget) + _estimate_size(item, budget)
            for key, item in value.items()
        )
    return 8


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
    if left is right:
        return True
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
            raise SericiteError(
                f"unhashable key type: {_type_name(item)}",
                kind="type_error",
            )
        return item in container
    if type(container) is list:
        return any(_equals(element, item) for element in container)
    if type(container) is str and type(item) is str:
        return item in container
    raise SericiteError(
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
    if isinstance(value, SericiteFunction):
        return "function"
    if isinstance(value, VMHandle):
        return "VM"
    return type(value).__name__


def _operand_error(op: str, left: object, right: object) -> SericiteError:
    return SericiteError(
        f"unsupported operand types for '{op}': "
        f"{_type_name(left)} and {_type_name(right)}",
        kind="type_error",
    )


def _to_str(value: object, _seen: set[int] | None = None) -> str:
    if value is None:
        return "None"
    if type(value) is bool:
        return "True" if value else "False"
    if type(value) is str:
        return value
    if type(value) in (int, float):
        return str(value)
    if type(value) in (list, dict):
        if _seen is None:
            _seen = set()
        ident = id(value)
        if ident in _seen:
            return "[...]" if type(value) is list else "{...}"
        _seen.add(ident)
        try:
            if type(value) is list:
                return "[" + ", ".join(
                    _repr_str(item, _seen) for item in value
                ) + "]"
            return (
                "{"
                + ", ".join(
                    f"{_repr_str(key, _seen)}: {_repr_str(item, _seen)}"
                    for key, item in value.items()
                )
                + "}"
            )
        finally:
            _seen.discard(ident)
    return repr(value)


def _repr_str(value: object, _seen: set[int] | None = None) -> str:
    if type(value) is str:
        return repr(value)
    return _to_str(value, _seen)
