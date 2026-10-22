# 贡献指南

English readers: this project's development workflow is currently documented in Chinese. An English version is planned.

## 开发环境

- Python ≥ 3.12，包管理用 [uv](https://docs.astral.sh/uv/)。
- 引擎本体零第三方依赖；`demo` 的依赖在 `pyproject.toml` 的 `demo` extra 里。

## 验证

修改引擎、编译器或脚本接口后，必须先运行回归套件：

```bash
python -m unittest discover -s tests
```

## 设计纪律

- 理念问题以 [`docs/design.zh-CN.md`](docs/design.zh-CN.md) 为准。改理念先改它，不要在别处另造表述。
- 架构不变量见 [`docs/architecture.zh-CN.md`](docs/architecture.zh-CN.md)。新语法必须先过编译器白名单，禁止引入 `exec`/`eval`。
- 不给引擎加网络/设备等内建能力；能力的正确形态是 FFI 授予。安全边界见 [`docs/security.zh-CN.md`](docs/security.zh-CN.md)。

## 文档流程

本项目的说明性文档遵循一条规则：**AI 可以起草，入库前必须经作者人工打磨**。如果你提交的 PR 改动文档，请确保文本由人通读并修改过，而不是直接提交模型输出。

## 提交信息

使用中文，一句话说明这次提交做了什么，格式参考已有历史（如 `docs: 文档初始化——设计理念、语言参考与构建手记框架`）。
