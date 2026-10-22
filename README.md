# MicaLang

[![CI](https://github.com/LeoLoudWarden/micalang/actions/workflows/ci.yml/badge.svg)](https://github.com/LeoLoudWarden/micalang/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/micalang)](https://pypi.org/project/micalang/)
[![Python](https://img.shields.io/pypi/pyversions/micalang)](https://pypi.org/project/micalang/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/LeoLoudWarden/micalang/blob/main/LICENSE)

**Mica** is a scripting language and its execution engine, built around a single idea: creating a fully isolated, arbitrarily nestable runtime environment should be a cheap, first-class operation in the language itself.

Syntax borrows a subset of Python, but Mica source is compiled into Mica's own instruction stream and executed by the Mica engine — never through `exec`/`eval`. Every VM starts with zero capabilities: its own virtual IO, its own in-memory filesystem, and the ability to create child VMs. Everything else (network, host data, whatever you choose) is granted explicitly, layer by layer, through FFI.

[中文文档与项目说明](https://github.com/LeoLoudWarden/micalang/blob/main/README.zh-CN.md)

## Status

0.1.0 prototype. The engine is a Python reference implementation (codename **Mica-PY**): zero runtime dependencies, Python ≥ 3.12, 198 regression tests. APIs and semantics may change before 1.0.

> **Security note.** Mica is designed for isolation, but the current prototype runs in the host process and has not been formally audited. Do not rely on it as the *only* boundary when executing adversarial code. See the [security model](https://github.com/LeoLoudWarden/micalang/blob/main/docs/security.md) for exactly what is and is not guaranteed.

## Install

```bash
pip install micalang
```

The distribution is named `micalang`; the import package is `mica`.

## Quick start

As a CLI:

```bash
printf 'print("hello from mica")\n' > hello.mica
micalang hello.mica        # hello from mica
```

Embedded in Python:

```python
from mica import MicaEngine

engine = MicaEngine()
vm = engine.create_root("""
sub = VM("print('hello from a child VM')")
print(sub.run())
""")
print(vm.run(), end="")
```

Grant a capability to a VM (FFI):

```python
def double(x):
    return x * 2

vm = engine.create_root("print(double(21))", functions={"double": double})
print(vm.run(), end="")    # 42
```

## What a Mica VM is

A VM is a cheap, self-contained workspace:

- it cannot see its parent's variables, files, or handles, and cannot tell how deep it is nested;
- it has its own empty in-memory filesystem (text only — structurally unable to touch the real disk);
- resource quotas (instructions, memory, output, child VMs, nesting depth, …) can only be tightened, never loosened, and a child's consumption is charged to all its ancestors;
- its parent can preset files, feed input, single-step it statement by statement or instruction by instruction, snapshot its state, or terminate it;
- it can create its own child VMs — isolation is recursive, because untrusted code is.

## Documentation

| Document | Language | Contents |
|---|---|---|
| [Design notes](https://github.com/LeoLoudWarden/micalang/blob/main/docs/design.md) | EN | What Mica is, why it exists, where the boundaries are ([中文](https://github.com/LeoLoudWarden/micalang/blob/main/docs/design.zh-CN.md)) |
| [Language reference](https://github.com/LeoLoudWarden/micalang/blob/main/docs/language-reference.md) | EN | The language subset and semantics, self-contained ([中文](https://github.com/LeoLoudWarden/micalang/blob/main/docs/language-reference.zh-CN.md)) |
| [Architecture](https://github.com/LeoLoudWarden/micalang/blob/main/docs/architecture.zh-CN.md) | 中文 | Engine structure, invariants, resource quotas |
| [Security model](https://github.com/LeoLoudWarden/micalang/blob/main/docs/security.md) | EN | Threat model; what the engine does and does not guarantee ([中文](https://github.com/LeoLoudWarden/micalang/blob/main/docs/security.zh-CN.md)) |
| [Build journal](https://github.com/LeoLoudWarden/micalang/blob/main/docs/stages/README.md) | 中文 | How Mica 0.1.0 was built, stage by stage, with runnable checkpoints (`git checkout stage-1` … `stage-6`) |
| [Behind the scenes](https://github.com/LeoLoudWarden/micalang/blob/main/docs/behind-the-scenes/README.md) | 中文 | Origin story and how AI-assisted development was used |

Architecture notes, build journal, and behind-the-scenes are currently 中文-only; English versions are planned.

## How this project was built

Mica was developed with AI assistance throughout — design discussion, implementation, testing, adversarial review, and documentation drafting. What the AI did, what the author did, and how outputs were verified are documented in [AI-assisted development](https://github.com/LeoLoudWarden/micalang/blob/main/docs/behind-the-scenes/ai-assisted-development.zh-CN.md) (中文), with selected conversation records alongside.

## Roadmap

- List/dict/string methods and more ergonomic syntax
- Performance benchmarks (child-VM creation cost, per-instruction overhead)
- A normative language spec and a conformance test suite, so a future non-Python engine can be validated against them
- Evaluation of a native engine (the package name is language-level on purpose: `micalang` survives an engine rewrite)

## The name

Mica (云母) is a layered mineral: it cleaves into thin sheets, each sheet complete in itself, and it is an insulator — layers do not conduct into each other. That is the shape of a Mica VM tree.

Not related to the Windows "Mica" UI material, the PyPI bioinformatics package MICA, or the (inactive) Rust scripting language named Mica.

## License

[Apache-2.0](https://github.com/LeoLoudWarden/micalang/blob/main/LICENSE) — see [NOTICE](https://github.com/LeoLoudWarden/micalang/blob/main/NOTICE).
