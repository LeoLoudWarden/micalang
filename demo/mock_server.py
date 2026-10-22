"""本地 OpenAI 兼容桩服务器：无真实 API 时端到端验证 demo 全链路。

按固定脚本应答：外层会话（系统提示词不含"内部会话"）依次下达
读文档 → 练习 → 执行中间层会话代码；内层会话执行一段练习代码后收尾。
中间层会话循环的 sericite 代码由桩服务器"扮演 AI"给出，是正确可运行的。

运行：uv run --extra demo demo/mock_server.py（监听 127.0.0.1:8399）
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

MIDDLE_CODE = '''prompt_inner = read_file("/prompt_inner.txt")
doc = read_file("/sericite语言特性速览.md")

tools = [
    {"type": "function", "function": {"name": "sericite_exec", "description": "执行 sericite 代码", "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "file_read", "description": "读文件", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "file_write", "description": "写文件", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "file_list", "description": "列目录", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
]

messages = [{"role": "system", "content": prompt_inner}, {"role": "user", "content": "开始"}]

final = "(无结果)"
done = False
rounds = 0
while not done and rounds < 20:
    rounds = rounds + 1
    resp = llm_chat({"messages": messages, "tools": tools})
    messages = messages + [resp["assistant_message"]]
    calls = resp["tool_calls"]
    if len(calls) == 0:
        final = resp["content"]
        done = True
    else:
        for call in calls:
            name = call["name"]
            args = call["args"]
            result = "(未知工具)"
            if name == "sericite_exec":
                sub = VM(args["code"])
                sub.write_file("/sericite语言特性速览.md", doc)
                try:
                    result = sub.run()
                except:
                    result = "(内层 sericite_exec 执行出错)"
            if name == "file_read":
                try:
                    result = read_file(args["path"])
                except file_not_found:
                    result = "(文件不存在)"
            if name == "file_write":
                write_file(args["path"], args["content"])
                result = "(已写入)"
            if name == "file_list":
                result = ""
                for n in list_dir(args["path"]):
                    result = result + n + "\\n"
                if result == "":
                    result = "(空)"
            print("[内层工具]", name, "->", result)
            messages = messages + [{"role": "tool", "tool_call_id": call["id"], "content": result}]

print("内层最终回答:", final)
'''

PRACTICE_CODE = """print("外层练习: fib")
a = 0
b = 1
for i in range(6):
    t = a + b
    a = b
    b = t
    print(a)
"""

INNER_PRACTICE_CODE = """print("内层练习: 写读文件")
write_file("/inner_note.txt", "来自最内层 VM 的笔记")
print(read_file("/inner_note.txt"))
for k in {"c": 3, "a": 1}:
    print("key:", k)
"""


def _tool_call(index: int, name: str, args: dict) -> dict:
    return {
        "id": f"call_{index}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def _reply(content: str = "", tool_calls: list | None = None) -> dict:
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


class Handler(BaseHTTPRequestHandler):
    outer_turn = 0
    inner_turn = 0

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        system = ""
        if body.get("messages"):
            system = body["messages"][0].get("content") or ""
        if "内部会话" in system:
            payload = self._inner_reply()
        else:
            payload = self._outer_reply()
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _outer_reply(self) -> dict:
        type(self).outer_turn += 1
        turn = type(self).outer_turn
        if turn == 1:
            return _reply(tool_calls=[_tool_call(1, "file_read", {"path": "/sericite语言特性速览.md"})])
        if turn == 2:
            return _reply(tool_calls=[_tool_call(2, "sericite_exec", {"code": PRACTICE_CODE})])
        if turn == 3:
            return _reply(tool_calls=[_tool_call(3, "sericite_exec", {"code": MIDDLE_CODE})])
        return _reply(content="外层收尾：练习完成，内层会话也跑完了。")

    def _inner_reply(self) -> dict:
        type(self).inner_turn += 1
        turn = type(self).inner_turn
        if turn == 1:
            return _reply(tool_calls=[_tool_call(101, "sericite_exec", {"code": INNER_PRACTICE_CODE})])
        return _reply(content="内层练习完成：写了文件、读了回来、遍历了字典。")

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 8399), Handler)
    print("mock server on http://127.0.0.1:8399/v1/chat/completions")
    server.serve_forever()
