# Demo: HTTP capability granting + multi-layer virtualized LLM code exec

[中文说明](README.zh-CN.md)

Shows Sericite's full closed loop: the host grants `llm_chat` / `http_get` to a VM as FFI capabilities; the outer LLM writes code in that VM via `sericite_exec`, including **starting another LLM session of its own** (using the granted `llm_chat`), whose `sericite_exec` lands in a yet deeper VM. The inner layer is granted no network capability, so the recursion terminates.

If the model in the outer VM is unsure about the tools schema of the OpenAI-compatible API, it can use the granted `http_get` to fetch the protocol documentation (by default the tool_calls guide on the Kimi open platform, plain Markdown text — see `API_HELP_URL` in `main.py`) — incidentally demonstrating a VM fetching external resources.

## Run

```bash
uv sync --extra demo
# configure .env: API_URL / API_KEY / API_MODEL (OpenAI-compatible endpoint)
uv run --extra demo demo/main.py
```

Without a real API, verify the whole chain against the local stub server (the stub plays the AI with a fixed script and supplies the sericite code for the intermediate session loop):

```bash
uv run --extra demo demo/mock_server.py &          # listens on 127.0.0.1:8399
API_URL=http://127.0.0.1:8399/v1/chat/completions API_KEY=mock API_MODEL=mock \
  uv run --extra demo demo/main.py
```

## Dependencies

`requests` and `python-dotenv` are **dependencies of the demo, not of the Sericite engine** — the engine itself has zero dependencies. They are declared in the `demo` extra of `pyproject.toml` and installed only with an explicit `--extra demo`.

## Files

- `main.py`: the host program (session loop, tool dispatch, VM creation and file harvesting, `llm_chat` / `http_get` FFI definitions with bidirectional normalization).
- `mock_server.py`: local stub for verification without a real API.

The prompts (outer `PROMPT1` / inner `PROMPT_INNER`) live in `main.py`. The key distinction: the outer prompt teaches the model to use `llm_chat` for a recursive session; the inner prompt never mentions that capability, so the chain stops.
