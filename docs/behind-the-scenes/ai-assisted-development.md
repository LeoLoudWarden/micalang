# AI-Assisted Development (Summary)

> This is a condensed English summary of the [full record (中文)](ai-assisted-development.zh-CN.md), which is authoritative. The articles in this directory were written while the project was named Mica; read `mica`/`micalang` as `sericite`.

This project used AI assistance throughout — design discussion, implementation, testing, adversarial review, and documentation drafting. This note describes how it was actually used: what was used, how work was divided, and how outputs were verified. Selected conversation records are [alongside](conversation.zh-CN.md) (中文).

## What was used

- **Pre-project design discussion**: evaluating the feasibility of "taking over CPython's IO"; comparing Starlark, gVisor, microVM, WASM (own chat app; records lost).
- **Engine development**: reading requirements, drafting designs, implementing the compiler/engine/VFS/FFI, writing tests and stage journals (kimi-code, K3).
- **Security review**: an independent subagent for adversarial analysis; another AI for an independent security opinion (kimi-code subagent, K3; own chat app, deepseek V4.1f).
- **Documentation check**: a subagent that had not written the docs read them blind and paraphrased its understanding, to test whether the text misleads (kimi-code subagent, K3).
- **Demo testing**: the LLM as the *test subject* — reading the language brief and writing code inside a Sericite VM (OpenAI-compatible API, several open models in the tens-of-billions-parameter range, e.g. qwen3.8).

Note the last row's difference: the demo model is not a development tool but an experiment subject. It tests "can a model write runnable Sericite code from a one-page language brief" — it says nothing about the engine's own quality.

## How work was divided

Basic principle: **stage goals and design philosophy are set by the author; designs and implementations are drafted by the AI; whether to adopt them is judged by the author.**

The author: held the design philosophy — the AI repeatedly drifted (trying to reuse the early mock_fs toy, wanting to build LLM requests into the engine, reading Sericite as "a tool for LLMs") and was corrected each time; the virtualization worldview, the "tool for AI" vs "language engine" layering, and capabilities-by-granting all came from the author. Turned vague ideas into stage goals. Required design-before-code and pressed on "done" reports. Triaged review findings (adopt / pedantic / reasonable-but-deferred). Tested by hand — personally found an `IfExp` compile-time crash the AI had never triggered. Finalized all prose: every explanatory document in this project is AI-drafted and human-polished before commit.

The AI: drafted design sketches (instruction set, exception tables, FFI boundary) and implemented code and tests; self-checked and reported test counts and leftovers; drafted documents; provided a different review angle — the stage-5 adversarial subagent review did find issues the author's own pass had missed (e.g. CPU amplification via per-statement snapshots).

Rejected AI output (partial list): reusing the tutorial mock_fs as an implementation asset; building LLM request / HTTP capabilities into the engine; an early AI-generated requirements document (misread intent, not kept as a design basis); the codename `MicaJIT-PY` (there is no JIT — renamed Mica-PY).

## How outputs were verified

"AI reports done" is not verification. Five layers were used:

1. **Regression tests.** Each stage froze inline assertions into a persistent suite; later stages must not break the exact output of earlier examples. Case count grew 20 → 66 → 97 → 135 → 183 → 195 → 198.
2. **Two-perspective adversarial review.** In stage 5 the author and a subagent audited independently, then merged conclusions. The subagent's value is its different angle, not greater cleverness — the snapshot amplification and the missing memory ledger were its finds.
3. **External independent opinion.** Another AI's security review was triaged: adopted findings were fixed and pinned with regression tests; pedantic ones were declined and not archived; reasonable but oversized ones went into the journal's outlook.
4. **Blind documentation test.** After the design doc was written, a subagent that had not participated read only the doc and paraphrased it; the first round found four misleading spots, the second round passed. Documents need testing like code.
5. **End-to-end runs.** Exact-output assertions on CLI examples, the demo's full chain against a mock stub, real-API runs, and the author's own reruns. The `IfExp` crash is exactly the gap between "AI says green" and "a user actually runs it".

A further rule cross-checks docs against implementation: the quick-reference once taught the ternary expression while the engine did not support it — the fault could have been in the code or in the doc; the ternary was judged reasonable and safe, so it was added to the language.

## Boundaries

- AI involvement is not proof of quality. The engine is still a prototype; security boundaries are defined by the [security model](../security.md).
- This note gives no "AI wrote X%" figure: measuring by lines or ratio is meaningless; the division of labor above is the real answer.
- This note itself follows the same rule: AI-drafted, human-finalized.
