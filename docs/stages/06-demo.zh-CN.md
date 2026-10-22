# Stage 6：一个真实用例

上回说到，到 stage-5 为止，"执行不可信代码"终于从口号变成了有配额、有审查、有回归测试支撑的东西。城墙修好了，但它还从没见过真敌人。

回头看[设计说明](../design.zh-CN.md)第 4 节所说的话：Mica 的引擎不认识 LLM，但你可以在应用层将 Mica 引擎通过一个形如 mica_exec 的工具挂在某个 LLM 服务的工具列表里，Mica 引擎本身并不在意你会怎么用它。这句话在 stage-6 之前只是设计宣言，一行代码佐证都没有。

FFI 这根支柱也一样：`create_root(functions=...)` 造好了、测试齐了，可它从未有过一个真实的用户。

这一篇就是还愿的：造一个完整的 LLM 代码执行 demo，把前五篇建的所有东西——隔离、配额、FFI、VFS、嵌套 VM——放到真实场景里跑一遍。顺便，它还真的抓到了一个潜伏了一整个阶段的 bug，这个后面讲。

## 先立规矩：引擎一行不改

动手之前先立规矩，这条规矩是整个 demo 的立身之本：**所有 LLM 相关的东西都活在宿主应用层，引擎一行不改。**

`mica_exec`、`file_read`、`file_write`、`file_list` 四个工具全部由宿主实现；引擎只提供 `functions=` 这一个注入点。

为什么这条规矩这么重要？因为一旦为 LLM 场景在引擎里开任何一个特例，"场景选择了 Mica，Mica 的设计却不耦合场景"这句话就破产了。

这一篇写完可以验收：`mica/engine.py`、`mica/isa.py`、`mica/vfs.py` 在本阶段一行未动，整个阶段给引擎侧的改动只有编译器的 10 行（那 10 行的故事稍后讲）。换来的是 344 行宿主代码加 149 行离线桩支撑的三层递归虚拟化闭环——引擎做减法，应用做加法。

## demo 的结构：递归的不是代码，是提示词

demo 要演示的场景是递归的。整条链上有五个角色：

宿主 → LLM 外层会话 → root VM 里的 Mica 代码 → LLM 内层会话 → 子 VM

走一遍：

- 宿主把 `llm_chat` 和 `http_get` 两个函数授予 root VM；
- root VM 里跑的 Mica 代码用 `llm_chat` 发起一个**内层 LLM 会话**，给内层会话也配上 `mica_exec` 等四个工具；
- 内层模型再调 `mica_exec` 时，Mica 代码就创建一台**子 VM** 来执行。

注意 LLM 会话不算虚拟化层——它只负责传话。所以按虚拟化层数算，这条链是三层：宿主、root VM、子 VM。两层 LLM 会话夹在层与层之间，负责把能力接力下去。

递归怎么终止？靠"**不授予**"，我们可以这样推演：

- 宿主在创建 root VM 时，授予了它 VM() 函数 →→ root VM 获得了“造子 VM”的能力；
- root VM 在创建 子 VM 时，决定继续授予它 VM() 函数 →→ 子 VM 同样获得了“造孙 VM”的能力；
- 子 VM 在创建 孙 VM 时，决定不再授予 VM() 函数。
- 这时，孙 VM 在执行时如果尝试调用 VM()，它会发现自己的工具箱里根本没有这个函数 → 调用失败（甚至根本不会调用） → 递归终止。

这种方式的妙处在于，不需要维护一个"最大深度"，每一层各司其职，只负责执行授予的结果。至于套娃到哪一层停止，完全由应用层在传递能力时决定。看 demo 核心资产——外层提示词里写给模型的指令（节选；这里的"对方"指内层会话里的那个模型，外层模型要替它分发工具调用）：

> 对方调用 mica_exec 时：创建一个新 VM 执行（VM(code)），并先 write_file 把 `mica语言特性速览.md` 放进它的工作目录。**不要给这些 VM 授予任何 functions。**

（`demo/main.py:105-132`，`PROMPT1`）

而内层提示词 `PROMPT_INNER` 通篇没有"递归"、没有 `llm_chat` 字样，只让模型"随便做点简单的练习"。

两段提示词并排读，就是那句话的物证：**递归控制靠提示词**。引擎里没有任何"层级上限"的硬编码（`max_vm_depth` 只是配额兜底），链条在应用层自然终止——因为最深处那台 VM 创建时根本没拿到 `functions`，它想继续套娃也无能为力。

demo 中的 `mica_exec` 工具展示了如何利用 VFS 的生命周期。属于我的个人实现，不代表最佳实践。如果你在自己的项目中实现类似功能，可以参考这种方式：

```python
        if name == "mica_exec":
            code = args.get("code", "")
            engine = MicaEngine()
            try:
                vm = engine.create_root(
                    code,
                    functions={"llm_chat": llm_chat, "http_get": http_get},
                )
                for path, text in {**seed_files, **workspace}.items():
                    vm.write_file(path, text)
                output = vm.run()
            except MicaError as exc:
                # 编译/运行错误都作为工具结果喂回，让模型自己改代码重试
                return f"(mica error: {exc})"
            harvest(vm, workspace)
            return output[:MAX_RESULT_CHARS] or "(no output)"
```

（`demo/main.py:258-273`）

这段代码里有三个值得注意的点：

- 每次工具调用都新建引擎、新建 root VM；运行前往 VM 的文件系统里**预置**材料，跑完用 `harvest()` 把产出**收**回工作区——第三篇造的"运行前配置、运行后取回"，第一次承担了真实职责；
- 编译错、运行错不抛出，而是作为工具结果喂回模型，让它自己改代码重试——这是 agent 循环的标准做法，也让 stage-5 那套"宿主只见 MicaError"的收口在应用层兑了现；
- 末尾的 `MAX_RESULT_CHARS` 截断是工具结果的长度上限，防一次输出撑爆模型的上下文窗口。

关于 `harvest()` 的实现，为了避免在 VFS 中引入冗余的判定 API，我选择通过捕获特定的异常种类来驱动递归逻辑。：

```python
def harvest(vm, workspace: dict[str, str], prefix: str = "/") -> None:
    """把 VM 文件系统里的文件收回到工作区。"""
    for name in vm.list_dir(prefix):
        path = prefix.rstrip("/") + "/" + name
        try:
            workspace[path] = vm.read_file(path)
        except MicaError as exc:
            if exc.kind == "is_directory":
                harvest(vm, workspace, path)
            else:
                raise
```

（`demo/main.py:233-243`）

这里可以回看一个细节：原本为 try/except 设计的 `MicaError.kind` 字段（第三篇的伏笔），在应用层被自然地用作了控制流信号。这种错误分类的机制，在此时才真正体现出它的实用价值。

另外一个有趣的小细节是：`demo/main.py` 虽然导入了 `MicaLimits` 但实际并未调用。这在某种程度上验证了 stage-5 设定的默认配额已经足够覆盖绝大多数场景，应用层无需再进行繁琐的调参。

## 离线桩：让 demo 可以回归

真实 LLM API 不可复现、不可进 CI，而"多层虚拟化 + 工具分发 + 文件收种"这条全链路必须有个办法固定下来。所以 demo 配了一个 149 行的离线桩 `mock_server.py`：监听 `127.0.0.1:8399`，按固定脚本扮演 AI——该调工具时调工具，该写 Mica 代码时写 Mica 代码。

桩"扮演 AI"时给出的中间层会话循环，本身就是一份"正确的 Mica 代码长什么样"的参考答案：

```python
            if name == "mica_exec":
                sub = VM(args["code"])
                sub.write_file("/mica语言特性速览.md", doc)
                try:
                    result = sub.run()
                except:
                    result = "(内层 mica_exec 执行出错)"
```

（`demo/mock_server.py`，`MIDDLE_CODE` 节选）

裸 `except:`、字符串拼接代替方法、`VM(args["code"])` 不传 functions——全是语言约束的自然结果。链条在 `VM(args["code"])` 这一行终止：没有 functions，就没有下一层。

但要诚实地说清桩的边界：固定脚本只能证明**链路连通**，证明不了"模型能写出等价代码"。想要知道"LLM 是否会对 Mica 水土不服"，仍需实际的测试验证。

## 真实 API 验证

用真实 API 把 demo 复跑一遍后的答案如何呢？实测结论：LLM 对 Mica 的适应程度总体良好，"文档驱动编程"成立——模型会先 `file_read` 读语言介绍文档，再按其约束写代码，全链路（外层练习 → 递归开内层会话 → 内层练习 → 收尾）能完整跑通。

摩擦集中在 Mica 与 Python 的差异上，模型会习惯性写出 Mica 不支持的写法，如：
- 三元表达式——由于文档失误引入，后经过权衡决定加入语法特性中，下文展开。
- 字符串方法
- 切片等
但把这类错误作为工具结果喂回后，模型能自我修正重试。

最主要的出错原因与 Mica 无关，出在 openai-api 上，模型凭记忆手写 tools schema 时失误频繁。加了 `http_get` 兜底后，模型在写 tools schema 前会主动抓取协议文档再动手，之前"凭记忆手写 schema 导致 400"的摩擦消失；

小毛病还有：中文文件名、file_read 被误当 mica 内建函数等，都能自愈或已被工具层归一化兜底。简而言之，模型"按文档守规矩"的能力足够支撑这个 demo 形态，差异点靠错误反馈闭环兜住，适应性是够用的。

接上文，三元表达式怎么修？两个方向：

- 改文档把三元示例删掉——让文档迁就实现；
- 把三元表达式补进语言——让实现追上文档。

我选了后者：它是纯表达式、短路语义清晰、复用 if 的跳转逻辑即可实现、没有安全隐患。全部编译逻辑：

```python
        if isinstance(node, ast.IfExp):
            self.compile_expression(node.test, code)
            jump_false = self.emit(code, isa.JUMP_IF_FALSE, None, line)
            self.compile_expression(node.body, code)
            jump_end = self.emit(code, isa.JUMP, None, line)
            code.instrs[jump_false].arg = len(code.instrs)
            self.compile_expression(node.orelse, code)
            code.instrs[jump_end].arg = len(code.instrs)
            return
```

（`mica/compiler.py:642-650`；白名单侧只加了 `ast.IfExp` 一行）

和 if 语句同一套回填手法。`body` 与 `orelse` 只有一个会被求值。

对应的测试 `test_short_circuit` 与这段跳转结构互为镜像：它的断言里没有 "boom"——未选中分支的副作用必须不存在。

## 验证

```bash
python -m unittest discover -s tests   # 198 个用例
python demo/mock_server.py &           # 监听 127.0.0.1:8399
API_URL=http://127.0.0.1:8399/v1/chat/completions API_KEY=mock API_MODEL=mock \
  python demo/main.py
```

测试 195 → 198（新增 `TernaryTests` 三个用例：基本求值、短路、嵌套）。demo 依赖收在 `pyproject.toml` 的 `demo` extra 里，引擎本体保持零依赖。

## 留下了什么

也要说清这个 demo 不是什么：

- 它不是接口承诺——提示词与工具协议是应用层示例，引擎不为它们背书；
- 真实 API 下的表现依赖模型能力——外层任务，不大的开源模型就能完成；递归会话要求更高，`MAX_ROUNDS = 30` 是硬兜底；
- mock 桩只证明链路连通，前面说过了。

但回头看这六篇走过的路：从 749 行、只能把 `2` 和 `3` 拼成 `23` 的 AST 原型，到一个引擎零改动就能挂进 LLM 工具列表、三层递归虚拟化跑通的完整闭环——设计说明第一天写下的那些话，逐条都有了着落：

- 创建 VM 是第一类语句；
- 隔离是结构事实；
- 能力靠授予、逐层显式；
- 资源有配额、错误有分类；
- 调试内生。

每一条都从一个理念变成了一段可以 checkout 下来运行的代码。

至此，0.1.0 版本的语言与引擎内容全部齐了。但对我来说，这更像是一个起点而非终点。

我深信 Mica 的潜力还远未被完全挖掘，但一个真正稳健的隔离引擎需要长期的打磨与投入，因此我非常欢迎大家参与进来贡献代码，或就实现细节与我交流讨论。坦白说，目前的实现中仍有许多我不满意的细节，甚至还留着不少潜在的"窟窿"，但我认为最好的改进往往来自真实场景的碰撞。

既然雏形已成，就让它先在真实世界里跑跑看。

---

- tag：`stage-6`
- 测试：198 个用例全部通过
