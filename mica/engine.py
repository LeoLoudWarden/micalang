from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from . import isa
from .compiler import compile_source
from .errors import MicaError
from .io import InputRequired, OutputLimitExceeded, VirtualIO

_TERMINAL_STATES = {"done", "error", "resource_exhausted", "terminated"}


@dataclass(frozen=True)
class MicaLimits:
    max_instructions: int = 1_000_000
    max_output_bytes: int = 1_048_576
    max_frames: int = 100
    max_child_vms: int = 100
    max_list_size: int = 100_000


@dataclass
class Frame:
    code: isa.CodeUnit
    pc: int = 0
    stack: list[object] = field(default_factory=list)
    locals: dict[str, object] = field(default_factory=dict)


class MicaFunction:
    def __init__(self, code: isa.CodeUnit) -> None:
        self.code = code

    def __repr__(self) -> str:
        return f"<function {self.code.name}>"


@dataclass(frozen=True)
class _NativeFunction:
    name: str
    callback: Callable[..., object]


@dataclass
class VMContext:
    vm_id: int
    layer: int
    parent_id: int | None
    io: VirtualIO
    allow_vm: bool
    limits: MicaLimits
    frames: list[Frame] = field(default_factory=list)
    state: str = "ready"
    last_line: int | None = None
    error: MicaError | None = None
    instruction_count: int = 0
    child_count: int = 0

    @property
    def filename(self) -> str:
        return f"<mica:layer_{self.layer}>"


class VMHandle:
    _METHODS = {
        "run",
        "feed_input",
        "step",
        "step_instruction",
        "snapshot",
        "stdout",
        "terminate",
    }

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

    def __repr__(self) -> str:
        context = self._engine._context(self)
        return (
            f"<VM id={context.vm_id} layer={context.layer} "
            f"state={context.state}>"
        )


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
    ) -> VMHandle:
        effective = limits or self._limits
        io = VirtualIO(
            inputs=inputs,
            input_provider=input_provider,
            max_output_bytes=effective.max_output_bytes,
        )
        return self._create_vm(
            source=source,
            parent_id=None,
            layer=0,
            io=io,
            allow_vm=True,
            limits=effective,
        )

    def _create_vm(
        self,
        *,
        source: str,
        parent_id: int | None,
        layer: int,
        io: VirtualIO,
        allow_vm: bool,
        limits: MicaLimits,
    ) -> VMHandle:
        filename = f"<mica:layer_{layer}>"
        code = compile_source(source, filename)
        vm_id = self._next_vm_id
        self._next_vm_id += 1
        context = VMContext(
            vm_id=vm_id,
            layer=layer,
            parent_id=parent_id,
            io=io,
            allow_vm=allow_vm,
            limits=limits,
            frames=[Frame(code=code)],
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

    def _fail(self, context: VMContext, error: MicaError, line: int) -> None:
        context.error = error.bind(context.filename, line)
        context.state = (
            "resource_exhausted"
            if error.kind == "resource_exhausted"
            else "error"
        )

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
        elif op == isa.LOAD_INDEX:
            index = stack.pop()
            value = stack.pop()
            stack.append(self._index(value, index))
        elif op == isa.STORE_INDEX:
            index = stack.pop()
            target = stack.pop()
            value = stack.pop()
            self._store_index(target, index, value)
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
            self._call(context, frame, arg)
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

    def _call(self, context: VMContext, frame: Frame, argc: int) -> None:
        stack = frame.stack
        function = stack[len(stack) - argc - 1]
        if isinstance(function, MicaFunction):
            code = function.code
            if argc != len(code.params):
                raise MicaError(
                    f"{code.name}() expects {len(code.params)} "
                    f"argument(s), got {argc}",
                    kind="arity_error",
                )
            if len(context.frames) + 1 > context.limits.max_frames:
                raise MicaError(
                    "call depth limit exceeded", kind="resource_exhausted"
                )
            args = tuple(stack[len(stack) - argc :]) if argc else ()
            del stack[len(stack) - argc - 1 :]
            frame.pc += 1
            context.frames.append(
                Frame(code=code, locals=dict(zip(code.params, args)))
            )
            return
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
        raise MicaError("value is not callable", kind="type_error")

    def _load_name(self, context: VMContext, frame: Frame, name: str) -> object:
        if name in frame.locals:
            return frame.locals[name]
        module_locals = context.frames[0].locals
        if frame is not context.frames[0] and name in module_locals:
            return module_locals[name]
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
        if name == "VM" and context.allow_vm:
            return _NativeFunction(
                "VM", lambda source: self._create_child(context, source)
            )
        return None

    def _create_child(self, parent: VMContext, source: object) -> VMHandle:
        if not isinstance(source, str):
            raise MicaError("VM source must be a string", kind="type_error")
        if parent.child_count >= parent.limits.max_child_vms:
            raise MicaError(
                "child VM limit exceeded", kind="resource_exhausted"
            )
        parent.child_count += 1
        return self._create_vm(
            source=source,
            parent_id=parent.vm_id,
            layer=parent.layer + 1,
            io=VirtualIO(max_output_bytes=parent.limits.max_output_bytes),
            allow_vm=False,
            limits=parent.limits,
        )

    def _print(self, context: VMContext, values: tuple[object, ...]) -> None:
        context.io.write_output(" ".join(_to_str(v) for v in values) + "\n")

    def _len(self, value: object) -> int:
        if isinstance(value, (str, list)):
            return len(value)
        raise MicaError(
            f"object of type '{_type_name(value)}' has no len()",
            kind="type_error",
        )

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

    def _store_index(self, target: object, index: object, value: object) -> None:
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
            "layer": context.layer,
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
        return f"<{type(value).__name__}>"


def _is_number(value: object) -> bool:
    return type(value) in (int, float)


def _truthy(value: object) -> bool:
    if value is None:
        return False
    if type(value) is bool:
        return value
    if type(value) in (int, float):
        return value != 0
    if isinstance(value, (str, list)):
        return len(value) > 0
    return True


def _equals(left: object, right: object) -> bool:
    if _is_number(left) and _is_number(right):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, (str, bool, list)) or left is None:
        return left == right
    return left is right


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
