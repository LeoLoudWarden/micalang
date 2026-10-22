# 文档索引 / Documentation Index

Sericite 的文档各有性质，性质决定它什么时候变、为谁而写：设计文档很少变，参考跟随语言，实现跟随代码，历史写定后冻结。

| 文档 | 类别 | 回答的问题 | English |
|---|---|---|---|
| [design.zh-CN.md](design.zh-CN.md) | 设计 | Sericite 是什么、为什么存在、边界划在哪里 | [design.md](design.md) |
| [language-reference.zh-CN.md](language-reference.zh-CN.md) | 参考 | 语言子集与语义，读完即可开写 | [language-reference.md](language-reference.md) |
| [architecture.zh-CN.md](architecture.zh-CN.md) | 实现 | 引擎结构、设计不变量、配额体系 | —（待补） |
| [security.zh-CN.md](security.zh-CN.md) | 设计+实现 | 威胁模型；保证什么、不保证什么 | [security.md](security.md) |
| [prior-art.zh-CN.md](prior-art.zh-CN.md) | 设计 | 先例考据：Safe-Tcl 证明了什么、停在哪里，Sericite 补什么 | [prior-art.md](prior-art.md) |
| [stages/](stages/README.md) | 历史 | 《从零构建 Sericite》：逐阶段构建手记 | —（待补） |
| [behind-the-scenes/](behind-the-scenes/README.md) | 历史 | 项目由来、AI 辅助开发过程 | —（待补） |

其中 security 有意横跨两类：`威胁模型` 属于设计承诺（很少变），`配额与已知边界` 属于实现现状（跟随代码）。

阅读顺序建议：想了解项目先读 design；想上手写代码读 language-reference；想改引擎读 architecture；想执行不可信代码先读 security；想知道这个项目是怎么被造出来的，读 stages 与 behind-the-scenes。

---

Sericite's documents have different natures, which determine when they change and whom they serve: design docs rarely change, references follow the language, implementation docs follow the code, and history freezes once written.

Suggested reading order: **design** to understand the project; **language-reference** to start writing code; **architecture** (中文, English pending) to work on the engine; **security** before running untrusted code; **stages** and **behind-the-scenes** (中文) for how the project was built.
