> 版本提示：本文描述 V1 单步 Runtime。V2 的多步骤循环、真实天气和 SQLite 记忆请先阅读 README 顶部；不要混用两版能力与测试结论。

# JevFlow 项目面试讲解稿

依据当前项目源码整理，2026-10-25。本文描述实现现状；“后续计划”不代表已经完成。之前的 27 项测试通过记录来自 MCP 扩展后的验证，本次主要为代码核对与讲解整理，未重新进行在线模型测试。

## 1. 90 秒项目介绍

> 我做的是一个支持 MCP 扩展的轻量级多工具 Agent，面向计算、查询和本地文件处理。核心工作流是用户输入、结构化路由、参数提取与校验、工具执行、结果生成。
>
> 我把决策和表达拆开：Jev 从预定义动作和已注册工具中作选择，Python Runtime 检查参数、维护待办状态并执行工具，DeepSeek 根据工具观察生成最终答复。这样每个阶段都有明确输入输出，可以独立排查和验证。
>
> 最初有计算器、天气、汇率、订单、知识和文档检索六个工具，其中计算器做真实计算，另外五个使用本地模拟数据或示例语料。后来接入官方 MCP Python SDK，通过 stdio 连接独立服务进程，新增目录查看、文本读取、文件搜索、文本统计和时区时间五个实际本地工具。
>
> 主要解决了三个工程问题：模型动作和工具选择不一致时如何安全追问；如何通过配置、工具发现和命名映射接入新工具；如何在同步 CLI 中复用异步 MCP 会话并处理超时、错误和退出清理。项目有 JSONL Trace，以及包含真实 MCP 子进程调用的 27 项测试。它目前是可运行工程原型，尚未实现通用自主规划和长期记忆。

面试时先讲这段，随后根据面试官的问题展开，不需要上来逐个背函数。

## 2. 项目解决什么问题

只有模型调用并不能构成可靠的工具执行系统。至少要明确：有哪些工具、什么时候调用、参数从哪里来、缺参数怎么办、失败如何归类、结果如何追踪。

本项目把这些职责落到代码中。模型提供有限选项决策，运行时保留最终执行控制；工具以统一目录暴露，新增 MCP 服务可以通过配置接入。离线模式提供可复现的规则基线，在线模式验证模型参与路由的可行性。

“专用决策模型可能减少路由成本或延迟”是设计动机和后续实验假设。目前没有公平对照数据，不能声称 Jev 已经比通用 LLM 更快、更准或更便宜。

## 3. 文件结构与职责

| 文件 | 职责 |
|---|---|
| [app.py](/Users/shanziqi/program/jev_agent_demo/app.py) | 命令行参数、加载 MCP 配置、交互会话、直接工具调用、资源退出管理 |
| [agent_core.py](/Users/shanziqi/program/jev_agent_demo/agent_core.py) | 内置工具、参数提取、Jev/离线路由、运行时、Session、DeepSeek 和 Trace |
| [tool_registry.py](/Users/shanziqi/program/jev_agent_demo/tool_registry.py) | 统一工具目录、MCP 注册、参数 Schema 校验、名称映射和执行分发 |
| [mcp_client.py](/Users/shanziqi/program/jev_agent_demo/mcp_client.py) | SDK 会话、服务进程连接、分页发现、队列调用、超时与清理 |
| [mcp_servers/local_tools.py](/Users/shanziqi/program/jev_agent_demo/mcp_servers/local_tools.py) | 独立 MCP 服务与五个实际本地工具 |
| [mcp.json](/Users/shanziqi/program/jev_agent_demo/mcp.json) | 服务启动命令、参数、超时和允许工具列表 |
| [evaluate.py](/Users/shanziqi/program/jev_agent_demo/evaluate.py) | 16 条样本的单轮动作与工具选择评测，不是完整任务成功率评测 |
| [tests/test_agent.py](/Users/shanziqi/program/jev_agent_demo/tests/test_agent.py) | 15 项核心运行时、模拟 API 响应和会话测试 |
| [tests/test_mcp.py](/Users/shanziqi/program/jev_agent_demo/tests/test_mcp.py) | 12 项实际 MCP 子进程集成与边界测试 |

这是职责分层，当前并没有拆成多个部署服务。Agent 主程序和 MCP Server 是不同进程，但它们不属于多 Agent 协作。

## 4. 一次请求完整经过哪些步骤

```text
启动：读取配置 → 启动 MCP 服务 → initialize → tools/list → 工具注册

请求：用户输入 + 待办状态
             ↓
      JevRouter / OfflineRouter
             ↓
       动作 + 候选工具
             ↓
       参数提取 + 来源记录
             ↓
    必填字段 / JSON Schema 校验
       ↙                    ↘
  缺参数：追问          完整且允许：执行工具
  保存 pending_query          ↓
                         observations
                              ↓
                      模板 / DeepSeek
                              ↓
                    最终答复 + Trace
```

`Agent.run()` 编排一轮请求。大部分请求执行一个工具；订单规则场景可以执行两个。没有不断观察结果、自动生成新计划的通用 while-loop Planner。

`--tool` 是独立调试入口：用户显式指定工具和 JSON 参数，经过校验直接调用，不经过 Jev 和答案生成模型。这可以区分“工具连不上”和“模型没选对工具”。

## 5. 六个内置工具怎样定义

[ToolSpec](/Users/shanziqi/program/jev_agent_demo/agent_core.py:21) 使用冻结的 dataclass 表示工具描述：

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    required: tuple[str, ...]
    read_only: bool = True
    input_schema: dict = field(default_factory=dict)
```

例如订单定义为 `ToolSpec("order", "根据明确订单号查询演示订单", ("order_id",))`。描述和必填参数告诉路由层工具的用途；具体执行在 `execute_tool()`；参数提取在 `build_args()`。冻结描述对象不意味着工具执行天然安全，仍需要实际校验。

| 工具 | 必填参数 | 如何执行 | 真实程度 |
|---|---|---|---|
| calculator | expression | 解析数学表达式的 AST，递归计算白名单节点 | 真实计算 |
| weather | city | 在 DEMO_WEATHER 中查询，支持北京、上海、广州、深圳 | 固定模拟天气 |
| exchange | amount、from_currency、to_currency | 按固定汇率计算，支持 USD→CNY、EUR→USD | 计算真实，汇率是假定值 |
| order | order_id | 在 DEMO_ORDERS 中查询 A123、B456 的状态和预计送达时间 | 模拟订单，无数据库 |
| knowledge | query | 在 KNOWLEDGE 卡片中按主题包含关系匹配 | 本地示例知识 |
| document | query | 在 DOCUMENTS 字典中匹配示例文档摘录 | 不读取真实文件，也不是向量检索 |

计算器支持数字常量、一元正负号，以及加、减、乘、除、幂运算。拒绝函数调用、变量和任意 Python 语句；限制递归深度、幂运算规模和结果范围，处理除零、溢出等异常。代码不使用 `eval()`。

参数提取器对中文乘除做简单标准化，利用正则提取算式。执行器能处理的 AST 表达式与自然语言提取器能识别的表达式不是完全等价的，目前自然语言提取能力有限。

`knowledge` 与 `document` 的区别在示例数据组织和检索用途，检索算法都很简单。不能把它们写成已经实现 Embedding、向量数据库、RAG、重排或法规数据库。

## 6. 五个实际本地 MCP 工具

服务端通过 `FastMCP` 和 `@server.tool` 暴露函数。SDK 从函数名称、docstring、参数类型和默认值生成工具描述与输入 Schema，并负责 MCP 协议消息。函数返回 `dict[str, Any]`，以便 SDK 提供结构化结果。

| 工具 | 参数 | 实现 | 主要限制 |
|---|---|---|---|
| list_directory | path="." | Path.iterdir，排序、过滤、返回名字和类型 | 返回最多 200 项 |
| read_text | path 必填 | UTF-8 打开普通文本文件 | 文件≤1,000,000 字节；返回最多 10,000 字符 |
| search_files | query 必填；path="." | os.walk 遍历，casefold 后做字面包含匹配 | 最多检查约 300 个文件；单文件≤200,000 字节；最多 30 条匹配；片段≤250 字符 |
| text_stats | text 必填 | len、中文字符正则、英文单词正则、splitlines | 输入≤10,000 字符 |
| current_time | timezone="Asia/Shanghai" | datetime.now(ZoneInfo(timezone)) | 需要有效 IANA 时区；返回 ISO 时间 |

`read_text` 多读取一个字符判断是否截断，返回 `truncated` 标记。搜索不跟随目录符号链接，会跳过隐藏目录、虚拟环境和依赖目录等。文件权限边界由统一 `checked()` 检查。

文本统计不是 tokenizer，不是模型 Token 数；中文统计主要覆盖 `\u4e00-\u9fff`，不是完整 Unicode 汉字分类；英文统计也不是完整 NLP 分词。

时间来源是操作系统时钟，不是联网授时服务。文件工具读取实际工作目录，它和内置 document 工具完全不同。

## 7. Jev 和 DeepSeek 分别干什么

[JevRouter.route()](/Users/shanziqi/program/jev_agent_demo/agent_core.py:309) 把当前请求、最近订单号、工具描述、必填参数与 Schema 组成 state，在同一次 HTTP 请求中询问两个 Choice：

- action：direct_answer、tool_call、request_info、cannot_answer。
- tool：已注册的工具名，外加 none。

示意响应：

```json
{
  "answers": {
    "action": {"choice": "tool_call", "confidence": 0.93},
    "tool": {"choice": "order", "confidence": 0.90}
  }
}
```

数值仅用于说明格式。两个问题共享 state，但不是先选动作后有条件地选工具，因此可能出现 action=request_info、tool=none 等不一致组合。

Jev 不负责生成工具参数。参数由本地规则或用户给出的 JSON 提供。Jev 不拿到工具执行权，也不直接访问文件进程。

DeepSeek 只接收用户请求和工具 observations，用于组织最终答复，没有传入工具执行接口。没有启用 DeepSeek 时使用本地模板；模板的直接回答能力很有限。缺参追问由程序生成，并不需要先调用 DeepSeek。

模型服务请求使用 urllib 和 JSON，而不是某个 Agent 框架；接入 MCP 的协议能力来自官方 SDK。JevModel 和 TypeSafe 是不同服务，Key 必须与 endpoint 配对。

## 8. 参数怎样提取，来源记录是什么意思

内置工具使用规则提取：城市在支持列表中匹配；金额与原币种用正则提取；订单号匹配一个字母加至少三位数字并转换为大写；知识与文档请求识别预设主题。

示意中间结果：

```json
{
  "args": {"order_id": "A123"},
  "provenance": {"order_id": "user_explicit"}
}
```

常见来源标签：

| 标签 | 含义 |
|---|---|
| user_explicit | 从用户文本规则提取 |
| user_explicit_normalized | 提取后做币种等标准化 |
| session_confirmed | 在满足上下文规则时使用最近订单号 |
| user_explicit_topic | 从预设主题中匹配用户文本 |
| user_explicit_json | 从用户显式 JSON 参数中读取 |
| planner_fixed_rule | 固定订单规则流程提供的检索主题 |

`session_confirmed` 是代码内部标签，不代表执行了用户身份认证或用户点击确认。它只表示会话已有订单号且当前文本满足相关词规则。

来源记录有助于排查参数是如何产生的，但不是密码学证据，也没有保存精确字符区间和语义证明。不能声称已经解决所有参数幻觉和越权问题。

MCP 参数优先读取显式 JSON；多个对象按出现顺序合并，同名字段以后者为准。附带工具另有文件名、搜索词、文本与时区的规则提取。第三方工具没有通用模型参数抽取，通常需要用户提供 JSON。

## 9. JSON Schema 校验和必填检查有什么区别

内置六工具主要检查必填字段、空值和来源，另有金额范围约束；它们尚未全部迁移到完整 JSON Schema。

MCP 工具在注册时检查 inputSchema 合法、顶层为 object，拒绝外部 Schema 引用。执行前用 Draft202012Validator 检查类型及 Schema 声明的约束，并额外检查必填字段、来源、空字符串和未知参数。

读取工具的简化 Schema：

```json
{
  "type": "object",
  "properties": {"path": {"type": "string"}},
  "required": ["path"]
}
```

`{}` 缺参数，`{"path":42}` 类型错误，`{"path":"README.md","unexpected":true}` 被项目的未知参数检查拦截。

字符串类型正确不代表路径合法；路径是否越界由服务端 checked() 再判断。Schema default 描述默认值，客户端不会自动补全任意默认参数；附带工具由 Python 函数默认值接收省略的可选字段。

注意当前兼容范围：校验器额外拒绝未出现在 properties 的字段，即使第三方 Schema 允许 additionalProperties，也不会自动放行。未来要支持开放字典参数需调整策略。

## 10. 缺参追问完整过程

[Session](/Users/shanziqi/program/jev_agent_demo/agent_core.py:135) 当前只有：

```python
pending_query: str | None
last_order_id: str | None
history: list[dict[str, str]]
```

第一轮“查询一下订单”：路由识别 order；build_args 没有找到订单号；validate 返回 order_id；运行时把原查询写入 pending_query，返回 request_info：“请补充：订单号（例如 A123）”。没有执行订单工具。

第二轮“A123”：Agent 发现 pending_query，将输入重建为：

```text
查询一下订单；用户补充：A123
```

之后重新路由、提取参数、校验；允许执行后查询模拟订单，更新 last_order_id，再生成答案。

MCP 读取文件同理：先说“读取文件”，再提供 README.md 或 `{"path":"README.md"}`。

history 只记录用户输入和状态，没有保存完整助手答复，而且不会整体传给 Jev 或 DeepSeek。因此准确表述是“任务型会话状态与缺参续办”，不能说“已实现通用长对话记忆”。

当前 pending_query 在合并时即被清空。如果补参过程中发生服务错误，需要更完善的任务恢复；如果用户突然切换话题，当前简单拼接也可能污染请求。`/reset` 可以手动清空状态，自动意图切换尚未实现。

## 11. 为什么之前查订单没有追问，后来怎么修

实际错误是“决策未指定可用工具”，根因是 Jev 返回 request_info，但 tool=none。旧运行时无法确定应该问哪个工具的参数，返回 review_needed，也没有记录待补充请求。

修复有两个部分：提示词明确要求“参数缺失时仍选择相关工具”；运行时增加有条件的追问恢复。

恢复条件：模型动作明确为 request_info、工具为空；本地规则能匹配已注册工具；代码校验确认确实缺字段。满足后只补上追问所需的工具上下文，记录 clarification_recovery 事件，不执行工具。

低置信度导致的 review、cannot_answer、无法匹配工具，以及参数已经齐全的冲突情形，都不通过这个分支强制执行。

Trace 保留原始 route，修复信息单独记录。因此 route.tool 可能仍是 null，但后面的 clarification_recovery 和 validate 显示运行时恢复了什么。这是为了保留模型原始行为。

面试答法：

> 我把“识别追问对象”和“授权执行”分开。规则只帮助提出明确的缺参问题，后续补参仍重新经过路由和校验，低置信度保护没有被绕过。

## 12. MCP 是什么，项目里谁是 Host、Client、Server

MCP 全称 Model Context Protocol，是让 AI 应用以统一协议对接工具和上下文的机制。理解本项目只需把握三方：Agent 主程序是 Host；每个 StdioClient 维护一个服务连接；local_tools.py 是提供工具的 Server。通信使用 JSON-RPC。

协议还定义 Resources 和 Prompts 等能力；本项目主要使用 Tools，没有实现所有 MCP 功能。参考 [MCP 架构规范](https://modelcontextprotocol.io/specification/2025-11-25/architecture)。

MCP 统一的是发现和调用接口，模型仍要选择工具，Host 仍要校验与控制执行。它不会自动解决参数提取、权限管理、业务正确性或 Agent 规划。

与模型 Function Calling 的关系：Function Calling 描述模型如何输出工具意图；MCP 描述应用如何连接工具服务。二者可以配合。本项目由 Jev 的 Choice 表示工具选择，没有使用 DeepSeek 的 tool_calls 作为路由方式。

stdio 下，Host 启动子进程，向其 stdin 写协议消息，从 stdout 读取响应；日志使用 stderr，不能污染 stdout。Streamable HTTP 面向 HTTP 连接，本项目没有实现。参考 [MCP 传输规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。

## 13. MCP 服务如何启动、发现、调用与关闭

MCPManager 读取 mcpServers 配置，展开 PYTHON、ROOT 与显式环境变量占位符，构建 StdioServerParameters。命令和参数分开传入，不通过 shell；cwd 为配置文件所在目录。

客户端先初始化，再分页调用 tools/list；根据 nextCursor 继续读取，检查重复游标。获得工具后，核对配置允许列表中的名字是否存在，注册描述和 Schema。未允许的工具不会放进路由目录。

调用示意，消息由 SDK 负责序列化和收发：

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {"name":"read_text", "arguments":{"path":"README.md"}}
}
```

id 仅为示意，非抓包记录。工具列表和调用分别对应 tools/list、tools/call；参考 [MCP Tools 规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)。

收到结果后检查 isError；存在 structuredContent 时优先使用，避免同时保留文本和结构化内容造成重复。序列化结果超过 16,000 字符时截断并标记。

app 用 ExitStack 管理生命周期，退出时反向关闭连接。关闭通过队列哨兵通知后台协程，再退出 SDK 的会话和传输上下文；SDK 负责子进程资源清理。不是每次调用都启动新进程。

“动态发现”在这里指启动时从服务获取描述，不是运行中监听工具变更并热更新。

## 14. 工具命名隔离如何实现

[register_mcp()](/Users/shanziqi/program/jev_agent_demo/tool_registry.py:15) 构造别名：

```text
mcp_{配置中的服务名}_{服务原始工具名}
```

例如 `local/read_text → mcp_local_read_text`。另一个服务也有 read_text 时，可以注册为 mcp_docs_read_text。

工具目录维护两张映射：

```python
specs["mcp_local_read_text"] = ToolSpec(...)
bindings["mcp_local_read_text"] = (local_client, "read_text")
```

模型选择别名，执行器查 bindings 找到正确连接，然后向该服务发送原始名称 read_text。不是把别名直接传给 MCP Server。

注册时校验名称格式、长度以及是否已存在；冲突就报错，不覆盖。下划线拼接不是数学上无碰撞的编码，例如不同拆分可能得到相同字符串，因此显式重复检查很必要。

每个 ToolRegistry 有自己的目录字典；添加一个 Agent 的 MCP 工具不会修改全局内置 SPECS。命名隔离解决分发歧义；不是文件权限、租户数据或操作系统隔离。

## 15. 为什么同时有线程、asyncio、Queue 和 Future

现有 CLI 和 Agent.run 是同步逻辑，MCP SDK 是异步上下文。为了复用持久连接，每个 MCP 服务用一个后台线程运行 asyncio.run(_serve())。

主线程把 `(工具名, 参数, Future)` 放进 Queue 并等待 Future；后台协程通过 asyncio.to_thread(queue.get) 取任务，await session.call_tool，最后设置 Future 的结果或异常。

```text
同步 CLI → Queue → 后台线程中的 async 会话 → MCP 子进程
    ↑                 Future 返回结果/异常
    └─────────────────────────────────────
```

会话的进入、使用和退出位于同一个协程中，避免把异步上下文分散到多个 asyncio.run 调用。asyncio.to_thread 让阻塞的 queue.get 不阻塞事件循环，SDK 的读写任务仍可运行。

当前每个服务在消费循环中串行 await 调用，CLI 也逐轮处理请求。虽然使用了线程和异步 SDK，但不能声称已经实现工具并行调度、多 Agent 或高并发后端。

默认 SDK 读取超时 15 秒，调用 Future 等待额外留 5 秒；关闭 join 留额外 10 秒。代码有等待上限，不等于可以绝对证明任意恶意子进程都被强制终止。超时不自动重试，避免未知执行结果下重复调用。

## 16. 保护措施的真实边界

| 检查位置 | 已做什么 | 不能据此声称什么 |
|---|---|---|
| 工具目录 | 名称校验、拒绝覆盖、配置允许列表 | 自动证明第三方工具只读 |
| 参数层 | 缺参、来源、MCP Schema 检查 | 完整语义和用户权限校验 |
| 本地文件服务 | resolve 后检查 ROOT 边界，检查原路径与目标路径，过滤隐藏路径和密钥后缀 | 操作系统沙箱、消除所有竞态或敏感信息泄漏 |
| 资源限制 | 文件大小、读取字符、搜索结果、MCP 输出与超时限制 | 完整资源配额系统 |
| 生成层 | 只给查询和观察，无工具执行接口，提示忽略工具文本指令 | 从根本上消除提示注入和答案幻觉 |

read_only_tools 是操作者配置的允许列表；readOnlyHint 是服务声明。它们都不会把任意服务强制变成只读。第三方 MCP 进程本身仍拥有启动用户的系统权限。

本地路径检查限制正常的路径穿越和指向根目录外的符号链接；resolve 与 open 之间仍存在潜在竞态。文件正文也可能含敏感信息，隐藏文件过滤不是内容脱敏。Trace 和生成器可能接触读取到的文本，应选择合适的目录和数据。

## 17. 状态、置信度与错误如何解释

| 运行状态 | 含义 |
|---|---|
| completed | 本轮完成 |
| request_info | 参数缺失或需要修正，保存待办并追问 |
| review_needed | 低置信度、工具选择缺失或语义冲突，阻止自动执行 |
| cannot_answer | 路由判定当前不支持或不应处理 |
| tool_error | 工具执行、MCP 调用、JSON 参数等错误 |
| system_error | 模型服务连接、认证、响应格式等错误 |

四动作是模型可选动作集合，六种状态是运行时结果集合，不要混为一谈。

当前 0.55 是演示阈值，未校准，不能解释为“正确率至少 55%”。动作置信信号不足会转 review；tool_call 还检查工具置信信号。缺少 confidence 时当前适配器尝试使用所选标签的 probability，二者语义未必完全相同，后续应分别记录和校准。

只返回 review_needed 不代表已经有人工审核平台。现在只是 CLI 提示，需要用户补充指令或重新发起；没有审批队列和恢复按钮。

服务认证和连接错误没有自动回退成离线路由，否则可能把在线实验变成规则结果。项目也没有通用自动重试框架。

## 18. 固定两步骤任务如何实现

输入“查订单 A123，如果没发货解释一下发货规则”时，先查询 order。当返回状态为未发货，且原请求满足“如果”和规则/解释等触发词时，由代码固定追加 knowledge 查询，参数为 `{"query":"发货规则"}`，来源为 planner_fixed_rule。

两次观察合并给生成器。第二步没有重新让 Jev 规划，也没有任务图搜索、失败补偿或动态循环，因此可称“固定规则的两步编排”，不能称“通用自主 Planner”或完整 ReAct。

## 19. Trace、测试与评测怎样讲

Trace 每轮追加一条 JSONL，包含 query、resolved_query、result；result 中有原始 route、events、status、latency_ms、answer，成功执行时有 observations，服务返回 usage 时也会附上。

事件可包含 route、clarification_recovery、validate、tool_result。可以据此区分模型选错工具、参数未提取、规则修复、工具报错等环节。总延迟通过 perf_counter 计算。

目前没有专门的路由/工具/生成阶段耗时、P95、计费聚合或完整日志脱敏。usage 来自路由器/生成器对象的 last_usage，异常轮次的旧值清理还需完善，不能当成严谨的逐请求成本账本。

27 项测试 = 15 项核心行为测试 + 12 项 MCP 集成测试。核心模型响应用 transport 注入，以便复现低置信度、字段缺失和追问恢复。MCP 测试会真实启动 SDK 服务子进程，在临时目录里读写测试文件，验证调用、Schema、越界路径、缺参补全、输出截断、超时和清理。

“27 项测试通过”表示对应工程用例通过，不表示 Jev 准确率 100%。MCP 路由测试的模型响应也是注入的，没有测线上自然语言选择能力。

16 条 smoke benchmark 每条新建 Session，统计动作一致率和工具选择指标；当前 evaluate.py 不加载 MCP 配置，也不评价最终答案正确性、跨轮任务完成或完整工具执行成功。甚至工具错误在某些条件下仍可能被计为路由正确，因此它不能当 Task Success Rate。

## 20. 可以展开讲的真实调试案例

案例一：持续 401。

错误最初只展示 HTTPError，缺少状态码和响应正文。完善诊断后发现认证失败，最终核对密钥发行服务，确认 JevModel Key 被发给了 TypeSafe endpoint。改成凭证与服务地址配对，并适配不同响应包装后，真实调用成功。期间 TLS EOF 是另一类连接问题，不应与认证问题混为一谈。

案例二：有查单意图却不追问。

通过运行时分支定位到 request_info 与 tool=none 的冲突，增加了不授权执行的追问恢复规则，使用两轮测试验证原查询被保留、补充订单号后能继续执行。

案例三：MCP 结果格式。

希望读取 structuredContent，但最初函数返回注解为泛化 dict，SDK 未产出预期结构化字段。改成 dict[str, Any] 后，实际子进程调用能获得结构化内容，并避免文本和结构化结果重复占用输出预算。

讲案例用“现象 → 证据 → 根因 → 改动 → 验证”，不把尝试次数多包装成复杂系统能力。

## 21. 高频追问的建议回答

**为什么不用一个 LLM 做所有事？**

我想把有限动作选择和自然语言生成分开，方便检查决策并单独评测。但这会增加服务依赖和潜在延迟，是否更优需要同工具、同数据集和同校验规则下对比，目前没有性能结论。

**是不是只套了两个 API？**

模型 API 是外部能力，工程工作在统一工具目录、会话续办、参数来源与 Schema 校验、MCP 生命周期、运行状态和 Trace。仍然是轻量原型，尚未覆盖生产级权限、并发、可观测性和恢复机制。

**为什么不用 LangChain/LangGraph？**

当前流程规模小，自定义 Runtime 能清楚呈现状态变化和边界，方便复现问题。未来任务图和状态持久化复杂后可以评估框架，不能说自行实现天然优于框架。

**MCP 为什么值得接？**

把工具提供方与 Agent 解耦，让描述和输入 Schema 通过协议发现，用同一执行分发接入多个服务。代价是连接生命周期、进程故障、协议兼容与信任边界需要管理。

**新工具是不是只改配置就完全能自然语言用了？**

配置能完成连接、发现和注册；Jev 能看到新增工具，但任意工具的自然语言参数提取尚未通用化。现在通用方式是显式 JSON，附带工具有专用规则。

**工具很多怎么办？**

当前适配器有明确数量上限，超出报错；还没实现工具检索、分组路由和延迟加载。未来可先选领域或服务器，再选候选工具，但要同步评测候选召回率，避免正确工具被提前筛掉。

**多轮记忆能记多久？**

状态只在当前进程 Session 中存在，退出即失去，没有长期记忆与会话恢复。history 也不是传给模型的完整上下文。

**只读允许列表能否防止恶意 Server 写文件？**

不能。它只限制调用入口，不限制服务进程权限；需要容器、系统沙箱或独立权限账户提供更强隔离。

**为什么失败后不自动重试？**

一次超时不一定意味着对方没执行。当前先返回明确错误，后续应根据幂等性和错误类型决定重试，对计费或写操作还要有幂等键、限次和退避策略。

## 22. 优先改进方向

1. 结构化待办任务：保存 task_id、tool、已确认参数、缺失参数和状态；识别取消、改口、话题切换，并保留错误后的恢复信息。
2. 通用参数提取：模型输出候选参数和证据片段，由程序核对来源、Schema 与业务约束，保留显式 JSON 调试入口。
3. 真实业务工具：接入有明确授权和数据来源的天气、订单或检索系统；单独维护网络失败和业务失败状态。
4. 工具规模扩展：候选工具召回、分层路由、上下文预算与服务健康状态；随后再做动态发现更新和远程 HTTP。
5. 任务级评测：覆盖真实歧义、跨轮补参、错误工具、参数错误和服务故障，测任务成功率、错误执行率、追问轮数、延迟和成本。
6. 更强执行边界：写操作确认、身份权限、审计脱敏、系统级进程隔离；明确接口重试和幂等策略。

面试时选择一两个方向讲清方案与验收标准即可，不要把所有后续功能写成已实现。

## 23. 现场演示顺序

先用离线模式证明工具链可用，再根据网络和账户状态展示在线路由。

```bash
.venv/bin/python app.py --mode offline --mcp-config mcp.json --trace traces-interview.jsonl
```

依次输入：

```text
/tools
查询一下订单
A123
读取文件
README.md
搜索文件内容 Jev
当前时间 UTC
/reset
exit
```

另开终端演示直接工具调用和测试：

```bash
.venv/bin/python app.py --mcp-config mcp.json --tool mcp_local_text_stats --args '{"text":"你好 Agent"}'
.venv/bin/python -m unittest discover -s tests -v
```

在线模式使用当前终端已配置的凭证：

```bash
.venv/bin/python app.py --mode jev --generator deepseek --mcp-config mcp.json
```

不要在演示中打印凭证。不要把离线规则结果说成 Jev 推理结果；如果服务繁忙，直接说明在线服务状态，并用离线演示解释同一个 Runtime 的执行路径。
