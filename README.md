# MicaLang

**Mica** is a scripting language and its execution engine, built around a single idea: creating a fully isolated, arbitrarily nestable runtime environment should be a cheap, first-class operation in the language itself.

Syntax borrows a subset of Python, but Mica source is compiled into Mica's own instruction stream and executed by the Mica engine — never through `exec`/`eval`. Every VM starts with zero capabilities: its own virtual IO, its own in-memory filesystem, and the ability to create child VMs. Everything else (network, host data, whatever you choose) is granted explicitly, layer by layer, through FFI.

> **Status: pre-release.** This repository currently contains the design documents and the build journal. The engine arrives commit by commit — see [the build journal](docs/stages/README.md), which reconstructs how Mica 0.1.0 was built, stage by stage.

## Documentation

| Document | Language | Contents |
|---|---|---|
| [Design notes](docs/design.zh-CN.md) | 中文 | What Mica is, why it exists, where the boundaries are |
| [Language reference](docs/language-reference.zh-CN.md) | 中文 | The language subset and semantics, self-contained |
| [Architecture](docs/architecture.zh-CN.md) | 中文 | Engine structure, invariants, resource quotas |
| [Security model](docs/security.zh-CN.md) | 中文 | Threat model; what the engine does and does not guarantee |
| [Build journal](docs/stages/README.md) | 中文 | How Mica was built, stage by stage, with runnable checkpoints |
| [Behind the scenes](docs/behind-the-scenes/README.md) | 中文 | Origin story and how AI-assisted development was used |

An English documentation set is planned after the 0.1.0 release.

## The name

Mica is a layered mineral: it cleaves into thin sheets, each sheet complete in itself, and it is an insulator — layers do not conduct into each other. That is the shape of a Mica VM tree: peel off a child VM inside the current one, each layer a complete world, each layer isolated from its parent.

MicaLang is not related to the Windows "Mica" UI material, the PyPI bioinformatics package named MICA, or the (inactive) Rust scripting language named Mica. The public identifier of this project is **MicaLang**; the package and repository are named `micalang`.

## License

[Apache-2.0](LICENSE)
