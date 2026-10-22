from __future__ import annotations

import ast

from . import isa
from .errors import MicaError

RESERVED_NAMES = {"input", "print", "VM", "len", "int", "float", "str", "range"}

_ALLOWED_NODES = {
    ast.Module,
    ast.Assign,
    ast.Expr,
    ast.If,
    ast.While,
    ast.Break,
    ast.Continue,
    ast.FunctionDef,
    ast.Return,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.List,
    ast.Subscript,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.Call,
    ast.Attribute,
    ast.arguments,
    ast.arg,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.USub,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
}

_BINARY_OPS = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.FloorDiv: "//",
    ast.Mod: "%",
}
_UNARY_OPS = {ast.USub: "-", ast.Not: "not"}
_COMPARE_OPS = {
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">="}

VM_METHODS = {"run", "feed_input", "step", "step_instruction", "snapshot", "stdout", "terminate"}


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


def _validate(tree: ast.Module, filename: str) -> None:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_NODES:
            _reject(node, filename, f"unsupported syntax: {type(node).__name__}")
        if isinstance(node, ast.Constant) and type(node.value) not in {
            str,
            int,
            float,
            bool,
            type(None),
        }:
            _reject(node, filename, "unsupported constant")
        if isinstance(node, ast.Name):
            if node.id.startswith("__"):
                _reject(node, filename, "dunder names are not available")
            if node.id in RESERVED_NAMES:
                parent = parents.get(node)
                if not (isinstance(parent, ast.Call) and parent.func is node):
                    _reject(
                        node,
                        filename,
                        f"builtin '{node.id}' can only be called directly",
                    )
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                _reject(node, filename, "dunder attributes are not available")
            if node.attr not in VM_METHODS:
                _reject(node, filename, f"unsupported attribute: {node.attr}")
            parent = parents.get(node)
            if not (isinstance(parent, ast.Call) and parent.func is node):
                _reject(node, filename, "VM methods can only be called directly")
        if isinstance(node, ast.Call) and node.keywords:
            _reject(node, filename, "keyword arguments are not supported")
        if isinstance(node, ast.FunctionDef):
            if node.name.startswith("__"):
                _reject(node, filename, "dunder names are not available")
            if node.decorator_list:
                _reject(node, filename, "decorators are not supported")
            args = node.args
            if (
                args.defaults
                or args.kw_defaults
                or args.vararg
                or args.kwarg
                or args.kwonlyargs
                or getattr(args, "posonlyargs", [])
            ):
                _reject(
                    node,
                    filename,
                    "only plain positional parameters are supported",
                )
        if isinstance(node, ast.Compare) and len(node.ops) != 1:
            _reject(node, filename, "chained comparisons are not supported")
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, (ast.Name, ast.Subscript)):
                    _reject(node, filename, "unsupported assignment target")
            if len(node.targets) > 1 and any(
                isinstance(target, ast.Subscript) for target in node.targets
            ):
                _reject(
                    node,
                    filename,
                    "multiple targets with subscript are not supported",
                )


def _reject(node: ast.AST, filename: str, message: str) -> None:
    raise MicaError(
        message,
        kind="validation_error",
        filename=filename,
        line=getattr(node, "lineno", None),
        column=(node.col_offset + 1 if hasattr(node, "col_offset") else None),
    )


class _Compiler:
    def __init__(self, filename: str) -> None:
        self._filename = filename
        self._loops: list[tuple[int, list[int]]] = []
        self._function_depth = 0

    def emit(
        self, code: isa.CodeUnit, op: str, arg: object, line: int
    ) -> int:
        code.instrs.append(isa.Instr(op=op, arg=arg, line=line))
        return len(code.instrs) - 1

    def compile_body(self, body: list[ast.stmt], code: isa.CodeUnit) -> None:
        for statement in body:
            first = len(code.instrs)
            self.compile_statement(statement, code)
            if len(code.instrs) > first:
                code.instrs[first].stmt_start = True

    def compile_statement(self, node: ast.stmt, code: isa.CodeUnit) -> None:
        line = node.lineno
        if isinstance(node, ast.Assign):
            self.compile_expression(node.value, code)
            targets = node.targets
            for target in targets[:-1]:
                self.emit(code, isa.DUP, None, line)
                assert isinstance(target, ast.Name)
                self.emit(code, isa.STORE_NAME, target.id, line)
            last = targets[-1]
            if isinstance(last, ast.Name):
                self.emit(code, isa.STORE_NAME, last.id, line)
            else:
                assert isinstance(last, ast.Subscript)
                self.compile_expression(last.value, code)
                self.compile_expression(last.slice, code)
                self.emit(code, isa.STORE_INDEX, None, line)
            return
        if isinstance(node, ast.Expr):
            self.compile_expression(node.value, code)
            self.emit(code, isa.POP, None, line)
            return
        if isinstance(node, ast.If):
            self.compile_expression(node.test, code)
            jump_false = self.emit(code, isa.JUMP_IF_FALSE, None, line)
            self.compile_body(node.body, code)
            jump_end = self.emit(code, isa.JUMP, None, line)
            code.instrs[jump_false].arg = len(code.instrs)
            self.compile_body(node.orelse, code)
            code.instrs[jump_end].arg = len(code.instrs)
            return
        if isinstance(node, ast.While):
            start = len(code.instrs)
            self.compile_expression(node.test, code)
            jump_false = self.emit(code, isa.JUMP_IF_FALSE, None, line)
            breaks: list[int] = []
            self._loops.append((start, breaks))
            self.compile_body(node.body, code)
            self._loops.pop()
            self.emit(code, isa.JUMP, start, line)
            end = len(code.instrs)
            code.instrs[jump_false].arg = end
            for index in breaks:
                code.instrs[index].arg = end
            if node.orelse:
                raise MicaError(
                    "while/else is not supported",
                    kind="validation_error",
                    filename=self._filename,
                    line=line,
                )
            return
        if isinstance(node, ast.Break):
            if not self._loops:
                self._outside_loop(node)
            index = self.emit(code, isa.JUMP, None, line)
            self._loops[-1][1].append(index)
            return
        if isinstance(node, ast.Continue):
            if not self._loops:
                self._outside_loop(node)
            self.emit(code, isa.JUMP, self._loops[-1][0], line)
            return
        if isinstance(node, ast.FunctionDef):
            params = [argument.arg for argument in node.args.args]
            child = isa.CodeUnit(name=node.name, params=params)
            self._function_depth += 1
            loop_stack, self._loops = self._loops, []
            try:
                self.compile_body(node.body, child)
            finally:
                self._loops = loop_stack
                self._function_depth -= 1
            self.emit(child, isa.LOAD_CONST, None, 0)
            self.emit(child, isa.RETURN, None, 0)
            self.emit(code, isa.MAKE_FUNCTION, child, line)
            self.emit(code, isa.STORE_NAME, node.name, line)
            return
        if isinstance(node, ast.Return):
            if self._function_depth == 0:
                raise MicaError(
                    "return outside function",
                    kind="validation_error",
                    filename=self._filename,
                    line=line,
                )
            if node.value is not None:
                self.compile_expression(node.value, code)
            else:
                self.emit(code, isa.LOAD_CONST, None, line)
            self.emit(code, isa.RETURN, None, line)
            return
        raise MicaError(
            f"unsupported statement: {type(node).__name__}",
            kind="validation_error",
            filename=self._filename,
            line=line,
        )

    def compile_expression(self, node: ast.AST, code: isa.CodeUnit) -> None:
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Constant):
            self.emit(code, isa.LOAD_CONST, node.value, line)
            return
        if isinstance(node, ast.Name):
            self.emit(code, isa.LOAD_NAME, node.id, line)
            return
        if isinstance(node, ast.List):
            for element in node.elts:
                self.compile_expression(element, code)
            self.emit(code, isa.BUILD_LIST, len(node.elts), line)
            return
        if isinstance(node, ast.BinOp):
            self.compile_expression(node.left, code)
            self.compile_expression(node.right, code)
            self.emit(code, isa.BINARY_OP, _BINARY_OPS[type(node.op)], line)
            return
        if isinstance(node, ast.UnaryOp):
            self.compile_expression(node.operand, code)
            self.emit(code, isa.UNARY_OP, _UNARY_OPS[type(node.op)], line)
            return
        if isinstance(node, ast.BoolOp):
            keep_op = (
                isa.JUMP_IF_FALSE_KEEP
                if isinstance(node.op, ast.And)
                else isa.JUMP_IF_TRUE_KEEP
            )
            self.compile_expression(node.values[0], code)
            jumps: list[int] = []
            for value in node.values[1:]:
                jumps.append(self.emit(code, keep_op, None, line))
                self.compile_expression(value, code)
            end = len(code.instrs)
            for index in jumps:
                code.instrs[index].arg = end
            return
        if isinstance(node, ast.Compare):
            self.compile_expression(node.left, code)
            self.compile_expression(node.comparators[0], code)
            self.emit(
                code, isa.COMPARE_OP, _COMPARE_OPS[type(node.ops[0])], line
            )
            return
        if isinstance(node, ast.Subscript):
            self.compile_expression(node.value, code)
            self.compile_expression(node.slice, code)
            self.emit(code, isa.LOAD_INDEX, None, line)
            return
        if isinstance(node, ast.Attribute):
            self.compile_expression(node.value, code)
            self.emit(code, isa.LOAD_ATTR, node.attr, line)
            return
        if isinstance(node, ast.Call):
            self.compile_expression(node.func, code)
            for argument in node.args:
                self.compile_expression(argument, code)
            self.emit(code, isa.CALL, len(node.args), line)
            return
        raise MicaError(
            f"unsupported expression: {type(node).__name__}",
            kind="validation_error",
            filename=self._filename,
            line=line,
        )

    def _outside_loop(self, node: ast.stmt) -> None:
        raise MicaError(
            f"{type(node).__name__.lower()} outside loop",
            kind="validation_error",
            filename=self._filename,
            line=node.lineno,
        )
