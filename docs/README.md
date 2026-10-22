# 文档索引

Mica 的文档各有性质，性质决定它什么时候变、为谁而写：设计文档很少变，参考跟随语言，实现跟随代码，历史写定后冻结。

| 文档 | 类别 | 回答的问题 |
|---|---|---|
| [design.zh-CN.md](design.zh-CN.md) | 设计 | Mica 是什么、为什么存在、边界划在哪里 |
| [language-reference.zh-CN.md](language-reference.zh-CN.md) | 参考 | 语言子集与语义，读完即可开写 |
| [architecture.zh-CN.md](architecture.zh-CN.md) | 实现 | 引擎结构、设计不变量、配额体系 |
| [security.zh-CN.md](security.zh-CN.md) | 设计+实现 | 威胁模型；保证什么、不保证什么 |
| [stages/](stages/README.md) | 历史 | 《从零构建 Mica》：逐阶段构建手记 |
| [behind-the-scenes/](behind-the-scenes/README.md) | 历史 | 项目由来、AI 辅助开发过程 |

其中 security 有意横跨两类：`威胁模型` 属于设计承诺（很少变），`配额与已知边界` 属于实现现状（跟随代码）。

阅读顺序建议：想了解项目先读 design；想上手写代码读 language-reference；想改引擎读 architecture；想执行不可信代码先读 security；想知道这个项目是怎么被造出来的，读 stages 与 behind-the-scenes。
