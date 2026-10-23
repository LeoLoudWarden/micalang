# Sericite

[![CI](https://github.com/LeoLoudWarden/sericite/actions/workflows/ci.yml/badge.svg)](https://github.com/LeoLoudWarden/sericite/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/sericite)](https://pypi.org/project/sericite/)
[![Python](https://img.shields.io/pypi/pyversions/sericite)](https://pypi.org/project/sericite/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/LeoLoudWarden/sericite/blob/main/LICENSE)

**Sericite** is a scripting language and its execution engine, built around a single idea: creating a fully isolated, arbitrarily nestable runtime environment should be a cheap, first-class operation in the language itself.

Syntax borrows a subset of Python, but Sericite source is compiled into Sericite's own instruction stream and executed by the Sericite engine — never through `exec`/`eval`. Every VM starts with zero capabilities: its own virtual IO, its own in-memory filesystem, and the ability to create child VMs. Everything else (network, host data, whatever you choose) is granted explicitly, layer by layer, through FFI.

[中文文档与项目说明](https://github.com/LeoLoudWarden/sericite/blob/main/README.zh-CN.md)

## Status

0.3.1 prototype. The engine is a Python reference implementation (codename **Sericite-PY**): zero runtime dependencies, Python ≥ 3.12, 224 regression tests. APIs and semantics may change before 1.0.

> **Security note.** Sericite is designed for isolation, but the current prototype runs in the host process and has not been formally audited. Do not rely on it as the *only* boundary when executing adversarial code. See the [security model](https://github.com/LeoLoudWarden/sericite/blob/main/docs/security.md) for exactly what is and is not guaranteed.

## Install

```bash
pip install sericite
```

The distribution is named `sericite`; the import package is `sericite`.

## Quick start

As a CLI:

```bash
printf 'print("hello from sericite")\n' > hello.sericite
sericite hello.sericite        # hello from sericite
```

Embedded in Python:

```python
from sericite import SericiteEngine

engine = SericiteEngine()
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

## What a Sericite VM is

A VM is a cheap, self-contained workspace:

- it cannot see its parent's variables, files, or handles, and cannot tell how deep it is nested;
- it has its own empty in-memory filesystem (text only — structurally unable to touch the real disk);
- resource quotas (instructions, memory, output, child VMs, nesting depth, …) can only be tightened, never loosened, and a child's consumption is charged to all its ancestors;
- its parent can preset files, feed input, single-step it statement by statement or instruction by instruction, snapshot its state, or terminate it;
- it can create its own child VMs — isolation is recursive, because untrusted code is.

Cheap is measured, not just claimed: in the current Python prototype, creating a child VM from inside a script costs ~0.05 ms — the same order as spawning a thread, one to two orders of magnitude below a process. A representative loop averages ~0.65 µs per instruction, or ~1.86 µs per instruction with instruction-level stepping (environment and reproduction scripts: [benchmarks/](https://github.com/LeoLoudWarden/sericite/tree/main/benchmarks)).

## Documentation

| Document | Language | Contents |
|---|---|---|
| [Design notes](https://github.com/LeoLoudWarden/sericite/blob/main/docs/design.md) | EN | What Sericite is, why it exists, where the boundaries are ([中文](https://github.com/LeoLoudWarden/sericite/blob/main/docs/design.zh-CN.md)) |
| [Prior art](https://github.com/LeoLoudWarden/sericite/blob/main/docs/prior-art.md) | EN | Safe-Tcl and related work: what Sericite inherits, what it adds ([中文](https://github.com/LeoLoudWarden/sericite/blob/main/docs/prior-art.zh-CN.md)) |
| [Language reference](https://github.com/LeoLoudWarden/sericite/blob/main/docs/language-reference.md) | EN | The language subset and semantics, self-contained ([中文](https://github.com/LeoLoudWarden/sericite/blob/main/docs/language-reference.zh-CN.md)) |
| [Architecture](https://github.com/LeoLoudWarden/sericite/blob/main/docs/architecture.md) | EN | Engine structure, invariants, resource quotas ([中文](https://github.com/LeoLoudWarden/sericite/blob/main/docs/architecture.zh-CN.md)) |
| [Security model](https://github.com/LeoLoudWarden/sericite/blob/main/docs/security.md) | EN | Threat model; what the engine does and does not guarantee ([中文](https://github.com/LeoLoudWarden/sericite/blob/main/docs/security.zh-CN.md)) |
| [Build journal](https://github.com/LeoLoudWarden/sericite/blob/main/docs/stages/README.md) | EN index | How Sericite 0.1.0 was built, stage by stage, with runnable checkpoints (`git checkout stage-1` … `stage-6`); articles in [中文](https://github.com/LeoLoudWarden/sericite/blob/main/docs/stages/README.zh-CN.md) |
| [Behind the scenes](https://github.com/LeoLoudWarden/sericite/blob/main/docs/behind-the-scenes/README.md) | EN index | Origin story and how AI-assisted development was used; articles in [中文](https://github.com/LeoLoudWarden/sericite/blob/main/docs/behind-the-scenes/README.zh-CN.md) |

The build-journal and behind-the-scenes articles are Chinese-only (their index pages have English versions); full translations are not planned — they are frozen history.

## How this project was built

Sericite was developed with AI assistance throughout — design discussion, implementation, testing, adversarial review, and documentation drafting. What the AI did, what the author did, and how outputs were verified are documented in [AI-assisted development](https://github.com/LeoLoudWarden/sericite/blob/main/docs/behind-the-scenes/ai-assisted-development.zh-CN.md) (中文), with selected conversation records alongside.

## Roadmap

- List/dict/string methods and more ergonomic syntax
- Performance benchmarks: VM creation, per-instruction execution, and debugging costs measured (see [benchmarks/](https://github.com/LeoLoudWarden/sericite/tree/main/benchmarks)); broader workload coverage still pending
- A normative language spec and a conformance test suite, so a future non-Python engine can be validated against them
- Evaluation of a native engine (the package name is language-level on purpose: `sericite` survives an engine rewrite)

## The name

Sericite (绢云母) is the finest-grained member of the mica family: thin silky scales that stack into sheets, and an insulator like every mica. The name keeps the three promises the project is named after — layering, insulation, lightness.

(The project was briefly named Mica/MicaLang; it was renamed after discovering the name was occupied multiple times over, including an actively developed commercial language publishing Docker images under the `micalang` namespace.)

## License

[Apache-2.0](https://github.com/LeoLoudWarden/sericite/blob/main/LICENSE) — see [NOTICE](https://github.com/LeoLoudWarden/sericite/blob/main/NOTICE).
