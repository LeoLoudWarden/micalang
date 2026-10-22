# demo：HTTP 能力授予 + 多层虚拟化的 LLM code exec

展示 sericite 的完整闭环：宿主把 `llm_chat` / `http_get` 作为 FFI 能力授予 VM；外层 LLM 通过 `sericite_exec` 在 VM 里写代码，其中包括**自己再开一个 LLM 会话**（用授予的 `llm_chat`），内层 LLM 的 `sericite_exec` 则落在更深一层 VM 里。内层不再授予网络能力，递归链条终止。

外层 VM 内的模型如果不确定 OpenAI 兼容 API 的 tools schema 写法，可以用授予的 `http_get` 抓取协议说明（默认指向 Kimi 开放平台的 tool_calls 指南，Markdown 纯文本，见 `main.py` 的 `API_HELP_URL`）——顺带展示了 VM 内获取外部资源的能力。

## 运行

```bash
uv sync --extra demo
# 配置 .env：API_URL / API_KEY / API_MODEL（OpenAI 兼容端点）
uv run --extra demo demo/main.py
```

没有真实 API 时，用本地桩服务器验证全链路（桩按固定脚本扮演 AI，中间层会话循环的 sericite 代码由桩给出）：

```bash
uv run --extra demo demo/mock_server.py &          # 监听 127.0.0.1:8399
API_URL=http://127.0.0.1:8399/v1/chat/completions API_KEY=mock API_MODEL=mock \
  uv run --extra demo demo/main.py
```

## 依赖说明

`requests` 与 `python-dotenv` 是 **demo 的依赖，不是 sericite 引擎的依赖**——引擎本体零依赖。它们声明在 `pyproject.toml` 的 `[project.optional-dependencies] demo` 中，只有显式 `--extra demo` 才会安装。

## 文件

- `main.py`：宿主程序（会话循环、工具分发、VM 创建与文件收种、`llm_chat` / `http_get` FFI 的定义与双向规整）。
- `mock_server.py`：本地桩，用于无真实 API 时验证。

提示词（外层 PROMPT1 / 内层 PROMPT_INNER）在 `main.py` 中。关键区分：外层提示词教模型用 `llm_chat` 实现递归会话；内层提示词不提递归能力，链条因此停下。
