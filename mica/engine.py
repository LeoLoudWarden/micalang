from __future__ import annotations

import ast
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .io import InputRequired, VirtualIO


class MicaError(Exception):
    def __init__(
        self,
        message: str,
        *,
        kind: str = "runtime_error",
        filename: str | None = None,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.filename = filename
        self.line = line
        self.column = column

    def bind(self, context: VMContext, node: ast.AST) -> MicaError:
        if self.filename is None:
            self.filename = context.filename
        if self.line is None:
            self.line = getattr(node, "lineno", None)
        if self.column is None:
            offset = getattr(node, "col_offset", None)
            self.column = offset + 1 if offset is not None else None
        return self

    def __str__(self) -> str:
        location = self.filename or "<mica>"
        if self.line is not None:
            location += f":{self.line}"
            if self.column is not None:
                location += f":{self.column}"
        return f"{location}: {self.kind}: {self.message}"


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

    @property
    def filename(self) -> str:
        return f"<mica:layer_{self.layer}>"


@dataclass(frozen=True)
class _NativeFunction:
    name: str
    callback: Callable[..., object]


class VMHandle:
    _METHODS = {"run", "feed_input", "step", "snapshot", "stdout"}

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

    def snapshot(self) -> dict[str, object]:
        return self._engine._snapshot(self._engine._context(self))

    def stdout(self) -> str:
        return self._engine._context(self).io.stdout()

    def __repr__(self) -> str:
        context = self._engine._context(self)
        return (
            f"<VM id={context.vm_id} layer={context.layer} "
            f"state={context.state}>"
        )


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

    def __init__(self) -> None:
        self._contexts: dict[int, VMContext] = {}
        self._next_vm_id = 0

    def create_root(
        self,
        source: str,
        inputs: Iterable[str] | None = None,
        input_provider: Callable[[], str | None] | None = None,
    ) -> VMHandle:
        io = VirtualIO(inputs=inputs, input_provider=input_provider)
        return self._create_vm(
            source=source,
            parent_id=None,
            layer=0,
            io=io,
            allow_vm=True,
        )

    def _create_vm(
        self,
        *,
        source: str,
        parent_id: int | None,
        layer: int,
        io: VirtualIO,
        allow_vm: bool,
    ) -> VMHandle:
        filename = f"<mica:layer_{layer}>"
        tree = self._parse(source, filename)
        self._validate(tree, filename)
        vm_id = self._next_vm_id
        self._next_vm_id += 1
        context = VMContext(
            vm_id=vm_id,
            layer=layer,
            parent_id=parent_id,
            source=source,
            tree=tree,
            io=io,
            allow_vm=allow_vm,
        )
        if not tree.body:
            context.state = "done"
        self._contexts[vm_id] = context
        return VMHandle(self, vm_id)

    def _parse(self, source: object, filename: str) -> ast.Module:
        if not isinstance(source, str):
            raise MicaError("VM source must be a string", filename=filename)
        try:
            return ast.parse(source, filename=filename, mode="exec")
        except SyntaxError as exc:
            raise MicaError(
                exc.msg or "invalid syntax",
                kind="syntax_error",
                filename=filename,
                line=exc.lineno,
                column=exc.offset,
            ) from None

    def _validate(self, tree: ast.Module, filename: str) -> None:
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if type(node) not in self._ALLOWED_NODES:
                self._reject(node, filename, f"unsupported syntax: {type(node).__name__}")
            if isinstance(node, ast.Constant) and type(node.value) not in {
                str,
                int,
                float,
                bool,
                type(None),
            }:
                self._reject(node, filename, "unsupported constant")
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

    def _reject(self, node: ast.AST, filename: str, message: str) -> None:
        raise MicaError(
            message,
            kind="validation_error",
            filename=filename,
            line=getattr(node, "lineno", None),
            column=(node.col_offset + 1 if hasattr(node, "col_offset") else None),
        )

    def _context(self, handle: VMHandle) -> VMContext:
        if handle._engine is not self:
            raise MicaError("VM handle belongs to another engine", kind="internal_error")
        return self._contexts[handle._vm_id]

    def _run(self, handle: VMHandle) -> str:
        context = self._context(handle)
        while context.state not in {"done", "error", "waiting_input"}:
            self._step(handle)
        if context.error is not None:
            raise context.error
        return context.io.stdout()

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
        context.state = "ready"
        try:
            self._execute_statement(context, statement)
        except InputRequired:
            context.state = "waiting_input"
        except MicaError as exc:
            context.error = exc.bind(context, statement)
            context.state = "error"
        except Exception:
            context.error = MicaError(
                "internal runtime error", kind="internal_error"
            ).bind(context, statement)
            context.state = "error"
        else:
            context.last_line = getattr(statement, "lineno", None)
            context.pc += 1
            context.state = (
                "done" if context.pc >= len(context.tree.body) else "ready"
            )
        return self._snapshot(context)

    def _execute_statement(self, context: VMContext, statement: ast.stmt) -> None:
        if isinstance(statement, ast.Assign):
            value = self._evaluate(context, statement.value)
            for target in statement.targets:
                assert isinstance(target, ast.Name)
                context.locals[target.id] = value
            return
        if isinstance(statement, ast.Expr):
            self._evaluate(context, statement.value)
            return
        raise MicaError(f"unsupported statement: {type(statement).__name__}")

    def _evaluate(self, context: VMContext, node: ast.AST) -> object:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return self._evaluate_name(context, node.id)
        if isinstance(node, ast.List):
            return [self._evaluate(context, item) for item in node.elts]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._evaluate(context, node.left)
            right = self._evaluate(context, node.right)
            try:
                return left + right  # type: ignore[operator]
            except TypeError:
                raise MicaError("unsupported operand types for '+'") from None
        if isinstance(node, ast.Attribute):
            value = self._evaluate(context, node.value)
            if isinstance(value, VMHandle) and node.attr in VMHandle._METHODS:
                return _NativeFunction(
                    f"VM.{node.attr}", getattr(value, node.attr)
                )
            raise MicaError("attribute access is not available for this value")
        if isinstance(node, ast.Call):
            function = self._evaluate(context, node.func)
            arguments = [self._evaluate(context, arg) for arg in node.args]
            if not isinstance(function, _NativeFunction):
                raise MicaError("value is not callable")
            try:
                return function.callback(*arguments)
            except InputRequired:
                raise
            except MicaError:
                raise
            except TypeError:
                raise MicaError(
                    f"invalid arguments for {function.name}"
                ) from None
        raise MicaError(f"unsupported expression: {type(node).__name__}")

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

    def _print(self, context: VMContext, values: tuple[object, ...]) -> None:
        context.io.write_output(" ".join(str(value) for value in values) + "\n")

    def _snapshot(self, context: VMContext) -> dict[str, object]:
        next_line = None
        if context.pc < len(context.tree.body):
            next_line = getattr(context.tree.body[context.pc], "lineno", None)
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
            "locals": {
                name: self._safe_value(value)
                for name, value in context.locals.items()
            },
            "stdout": context.io.stdout(),
            "error": error,
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
        if isinstance(value, VMHandle):
            return repr(value)
        return f"<{type(value).__name__}>"
