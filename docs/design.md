# Mica Design Notes

This document explains what Mica is, why it exists, and where its design boundaries are drawn. For the language itself see the [language reference](language-reference.md); for the engine's internal structure and current implementation status see the architecture notes ([中文](architecture.zh-CN.md)).

## 1. What Mica is

Mica is a scripting language, together with its execution engine. Its syntax borrows a subset of Python (it reads like Python), but source code is compiled into Mica's own instruction stream and executed instruction by instruction by the Mica engine — never through the host's `exec`/`eval`. (The *host* is the program running the Mica engine.)

The language exists for exactly one reason: **to create fully isolated runtime environments, at very low cost, within the language's own domain — and to nest them arbitrarily.**

Its worldview in one sentence: you live in a universe, and you can create another universe inside it; to the universe you create, you are its god — and the universe you live in may have its own god, but you can never know.

## 2. Why it is needed: the cost spectrum of virtualization

"Carving out an isolated space inside a running system" is not new; existing solutions spread across a cost spectrum:

- Hardware-level virtualization: emulate full hardware, install an OS, pay heavily in disk and memory.
- Container-level virtualization: better, but still requires initializing a whole container environment.
- Process-level isolation: relies on the OS to police each process's permissions.

Process-level isolation looks sufficient, but has two fundamental problems.

**First, the process is too heavy a unit for the OS to govern.** Every IO or syscall a process makes presents the kernel with a very complex "menu". The kernel lacks the evidence to judge whether a given call is malicious, so it cannot police at fine granularity. The result: OS-level process policing is itself riddled with holes, and privilege escalation happens regularly.

**Second, kernel-space solutions are not light enough, and history has long shown user space is cheaper.** Recall the evolution of concurrency: why threads when we had processes? Because creating processes is expensive, passing data between them is expensive, everything is expensive. Why coroutines when we had threads? Because thread switching still traps into the kernel, and when the internet brought thousands of concurrent tasks, that was still too expensive. Every step in this evolution goes the same way: **whatever can be solved in user space should not be pushed to the kernel; do not treat the OS — an overworked, hole-ridden butler — as an omnipotent nanny.**

One more step in this direction is where Mica sits: **virtualization implemented entirely in user space, inside the language itself.** Creating a child VM in Mica should feel as cheap as creating a sub-table in Lua. Isolation no longer depends on emulating hardware, booting containers, or requesting processes — it is a built-in capability of the language runtime.

You might ask: why not WASM runtimes, microVM sandboxes, gVisor, or embedded scripting sandboxes like Starlark? Each is valid in its own scenario, but none sits at the point of "the language's own domain": they either pull in a heavy external runtime, or the isolation boundary is granted by outside facilities, or the language itself does not treat "creating an isolated world" as a first-class concept. What Mica sets out to validate is what it looks like when this is a built-in language capability.

Why insist on *arbitrary nesting* rather than one layer? Because the need for isolation is recursive: your program receives an untrusted script and runs it in isolation — and that script, once running, needs to execute code it obtained dynamically. Untrustworthiness in the real world is passed down layer by layer; the isolation mechanism should be able to follow it, not just guard the front gate.

## 3. Core ideas

### 3.1 Minimal, lightweight, complete runtime isolation

A Mica VM has no permissions by default. Built-in capabilities are deliberately compressed to very few:

- Basic input/output (each VM has its own virtual IO);
- A purely in-memory virtual filesystem (text only; physically unable to touch the real disk);
- Creating child VMs.

The virtual filesystem is the ceiling of built-in capability. Nothing more is given.

### 3.2 Capabilities are granted, not built in

A VM is of course not limited to built-ins — but that goes down a different road: **capability granting (FFI)**. The host writes functions in the host language (Python, in the current reference implementation) and injects them into the VMs it creates; likewise, any VM can write functions in its own world and grant them to the next layer down. Capabilities travel along the creation chain, layer by layer, explicitly, defaulting to zero.

Why not build network access into the VM? Because that road ends in absurdity: build in HTTP, then what about raw TCP? UDP? Bluetooth, serial ports, optical drives, microphones, cameras? — there is no end to what would need adapting. And even if all of it were adapted, every VM would grow its own network logic, Bluetooth logic, device logic — which amounts to re-simulating an entire set of physical hardware inside the language, precisely betraying the "minimal and lightweight" foundation.

So capabilities like network access belong outside, injected via FFI, not built into the engine.

This mechanism is already working in the prototype: the host injects Python functions into the root VM, and any VM can grant functions it defined with `def` (or granted functions it already holds) to the next layer. Across the host boundary, arguments and return values are deep-copied in both directions and restricted to pure data types — no shared mutable objects exist between VM and host, and host exceptions are sanitized into ordinary script-catchable errors.

### 3.3 Division of security responsibility

This design splits security responsibility into two clearly bounded halves:

- **The engine is responsible for isolation itself holding.** A child VM cannot read or write parent variables or files, cannot tell how deep it is nested, cannot reach host objects or reflection entry points, and cannot bypass resource quotas. That is Mica's design promise.
- **The capability's implementer is responsible for the capability's safety.** If you grant a VM a function via FFI, whether that function is safe inside is on you. The engine's philosophy is to keep the kernel minimal — at least not to bury landmines inside itself.

## 4. Mica's relationship with LLMs

A common misreading must be corrected: **Mica is not "a tool for LLMs".**

The real relationship is this: LLM applications are Mica's most direct and realistic current driver — when a system must dynamically face hundreds or thousands of generated scripts, all of which must run in isolation, "cheap, nestable isolation" goes from nice-to-have to hard requirement. Mica was born of this era's demand.

But the relationship is one-directional: **the scenario chose Mica; Mica's design is not coupled to the scenario.** The Mica engine does not know what an LLM is, has no LLM-related built-ins, and does not depend on any model service in any form; without the LLM scenario, the proposition of "cheap, nestable, user-space isolation" still stands. You can, at the application layer, hang the Mica engine behind a tool like `mica_exec` on some LLM service's tool list — the engine itself does not care how you use it.

## 5. Prototype vs. design: distinguishing "where we are going" from "where we are"

When reading this project, always separate two levels:

- **Design**: the goals and boundaries described in sections 1–4 above. This is where the project is going.
- **Prototype**: the code in the repository is the reference implementation (codename Mica-PY), written in Python. It is a work-in-progress draft, meant to answer "once this design lands, what does the syntax and usage actually look like" — to fix the language's blueprint.

So "the prototype is currently incomplete" and "whether the design holds" are two different things. Missing features will be added; the prototype's implementation limits (for example, it currently runs in the host's process) are the state of construction, not a retreat from the design.

The same distinction applies to "cost": the claim in section 2 that "creating a child VM should be as cheap as creating a table" is a design goal and direction of effort. The current Python prototype has no performance benchmark yet; how cheap it actually is remains to be measured, not a proven fact.

As for the future release form — whether the engine base stays in Python or migrates to another language to produce a standalone binary runtime (say, a `mica` executable) — no conclusion for now. Preliminary judgment: Python as a base is not bad; even if a migration happens later, the functional shape stays roughly the same: Mica's built-ins are few, and everything else is injected in cooperation with other languages. Either way, a complete prototype must exist as the blueprint before rebuilding it on another base.

## 6. Why the name

Mica (云母) is a layered mineral. The name was chosen because it states three of the language's design promises: layering, insulation, lightness.

- **Layering**: mica cleaves along its planes into thinner and thinner sheets, and every sheet is still a complete layer. This mirrors the goal of nested virtualization.
- **Insulation**: mica is an electrical insulator; layers do not conduct into each other. This mirrors the goal of isolation.
- **Lightness**: mica sheets are light and transparent. This mirrors the goals of "very low cost" and "imperceptible nesting".

A note: "Mica" is not a vacant name in public. Windows has a UI material called Mica, PyPI has a bioinformatics tool of the same name, and GitHub has an inactive Rust scripting language of the same name — none of them are related to this project. This project's public identifier is **MicaLang**; the repository and package name is `micalang`.

## 7. One-sentence summary

Mica borrows Python's syntax shell to do something Python cannot: **make "creating a fully isolated universe" an ordinary script statement, cheap enough to use casually, nestable layer upon layer, with layers unaware of each other.** It is a language whose central proposition is "user-space virtualization", not an accessory tool for LLMs — the LLM era merely made the problem it solves more conspicuous.
