from __future__ import annotations

from dataclasses import dataclass, field

LOAD_CONST = "LOAD_CONST"
LOAD_NAME = "LOAD_NAME"
STORE_NAME = "STORE_NAME"
DUP = "DUP"
POP = "POP"
BUILD_LIST = "BUILD_LIST"
BUILD_DICT = "BUILD_DICT"
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
CALL_KW = "CALL_KW"
RETURN = "RETURN"

JUMP_OPS = {JUMP, JUMP_IF_FALSE, JUMP_IF_TRUE_KEEP, JUMP_IF_FALSE_KEEP}


@dataclass
class Instr:
    op: str
    arg: object = None
    line: int = 0
    stmt_start: bool = False


@dataclass
class Handler:
    start: int
    end: int
    target: int
    kinds: tuple[str, ...] | None
    bind: str | None
    stack_depth: int = 0


@dataclass
class CodeUnit:
    name: str
    params: list[str]
    instrs: list[Instr] = field(default_factory=list)
    handlers: list[Handler] = field(default_factory=list)
