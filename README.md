# Jev Flow

[中文](#中文) | [English](#english)

## 中文

Jev Flow 是一个用于演示多轮、多步骤工具调用的 Agent。Jev 根据当前任务状态选择下一步动作与工具；DeepSeek 为所选工具提取参数，并在收集到足够信息后生成自然语言回答。Runtime 负责会话状态、参数校验、MCP 调用、失败处理和 Trace。

### 功能

- **迭代决策循环**：每次工具执行后，将结果加入任务状态，再由 Jev 决定继续调用、追问或结束。
- **MCP 工具接入**：通过三个 stdio MCP Server 接入 9 个工具。
- **联网天气**：通过 Open-Meteo 查询地点和天气预报；查询结果包含来源及时间。
- **多轮会话与恢复**：SQLite 保存会话、工具观察和未完成任务，可在重启后恢复。
- **显式长期记忆**：用户可以保存、查看和删除偏好；例如把居住城市作为未指定城市天气查询的默认值。
- **参数与执行约束**：按 MCP 输入 Schema 校验参数，限制循环步数和重复调用，并保留失败任务。
- **可观测性**：JSONL Trace 记录决策、校验、工具结果和服务用量（若服务返回）。
- **离线结果查看**：`/result` 展示已保存结果，不调用模型；`/result details` 展示原始详情。

### 工具

| MCP Server | 工具 | 用途 |
|---|---|---|
| `weather` | `search_location` | 搜索地点及坐标 |
| `weather` | `weather_forecast` | 查询坐标和日期对应的天气预报 |
| `local` | `list_directory` | 列出工作目录内容 |
| `local` | `read_text` | 读取工作目录中的 UTF-8 文本文件 |
| `local` | `search_files` | 按字面关键词搜索文件内容 |
| `local` | `text_stats` | 统计文本 |
| `local` | `current_time` | 查询指定时区的系统时间 |
| `utility` | `calculate` | 安全计算算术表达式 |
| `utility` | `date_offset` | 日期加减计算 |

旧版内置演示工具仍可使用 `--engine legacy`；其中天气、订单、汇率数据是固定演示数据。默认多步骤模式使用 MCP 工具。

### 架构

```text
用户消息 / 会话记忆 / 工具观察
                  ↓
         Jev 选择动作与工具
                  ↓
       DeepSeek 按 Schema 提取参数
                  ↓
       Runtime 校验并调用 MCP 工具
                  ↓
          保存观察并返回 Jev
                  ↓
      Jev 选择继续、追问或结束
                  ↓
       DeepSeek 整理最终回答
```

Jev 负责有限选项决策；DeepSeek 负责参数提取和自然语言生成。单次用户任务可能触发多轮模型及工具请求。当前没有经过对照实验验证 Token 或成本节省。

### 环境要求

- Python 3.10 或更高版本（建议 Python 3.12）
- Jev API Key：`JEVMODEL_API_KEY` 或 `TYPESAFE_API_KEY`
- DeepSeek API Key：`DEEPSEEK_API_KEY`
- MCP Python SDK 与 JSON Schema 依赖
- 联网天气由 Open-Meteo 提供。它是天气预报，不是实况观测；服务连接失败时程序不会用演示数据替代。

### 安装与运行

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-mcp.txt
export JEVMODEL_API_KEY="your-jev-key"
export DEEPSEEK_API_KEY="your-deepseek-key"
python app.py --mode jev --generator deepseek --user me --session default --trace traces-loop.jsonl
```


### 常用命令

| 命令 | 用途 |
|---|---|
| `/help` | 查看命令帮助 |
| `/tools` | 查看工具 |
| `/memory` | 查看当前用户的长期记忆 |
| `/remember 名称=内容` | 保存一项记忆 |
| `/forget 名称` | 删除一项记忆 |
| `/new` | 开启新会话并保留偏好 |
| `/session ID` | 切换或恢复会话 |
| `/reset` | 清空当前会话和待办任务 |
| `/retry` | 允许重试待完成任务 |
| `/result` | 查看已有结果，无需调用模型 |
| `/result details` | 查看完整结果和来源信息 |
| `/exit` | 退出，数据自动保存 |

示例：

```text
/remember 居住地=北京
查询一下今天的天气
我住在哪里
```


### 项目结构

```text
app.py                    CLI 入口
loop_agent.py             Jev 决策循环与 Agent Runtime
loop_cli.py               多轮终端交互
mcp_client.py             stdio MCP 客户端与生命周期
mcp_servers/              本地、天气及通用工具 MCP Server
mcp.loop.json             V2 默认 MCP 配置
tool_registry.py           工具注册、Schema 校验与分发
memory_store.py           SQLite 会话和用户记忆
memory_context.py         记忆查询及默认值规则
network_io.py              有限网络重试
terminal_view.py           终端格式化
traces-loop.jsonl          运行时生成的 Trace（不应提交）
```

### 已知限制

- 默认串行执行工具，不提供并行调度。
- 参数由生成模型提取；Schema 校验格式，但不能证明参数语义正确。
- 长期记忆需要用户显式保存；没有自动记忆提取或向量检索。
- 会话与记忆保存在本地 SQLite，目前未加密。
- 日期解析和地点消歧依赖模型及工具结果。
- 双模型设计的准确率、延迟和成本尚未与单模型基线进行系统评测。
- 天气等在线功能受网络、第三方服务及 API 配额影响。

## English

Jev Flow is a Python Agent demo for multi-turn, multi-step tool use. Jev selects the next action and tool from the current task state. DeepSeek extracts arguments for the selected tool and writes the final natural-language response. The runtime manages conversation state, schema validation, MCP calls, failures, and traces.

### Features

- **Iterative decisions:** After each tool call, its result is added to task state and Jev decides whether to call another tool, ask the user, or finish.
- **MCP integration:** Nine tools are exposed through three stdio MCP servers.
- **Live weather forecasts:** Open-Meteo provides location search and forecasts with source and retrieval time.
- **Multi-turn persistence:** SQLite stores conversations, tool observations, and pending tasks so they can be resumed after restart.
- **Explicit long-term memory:** Users can save, inspect, and delete preferences. A saved residence can be used as the default city for weather queries when no city is specified.
- **Validation and execution limits:** MCP input schemas are validated; loop steps and duplicate calls are bounded, and failed tasks are preserved.
- **Observability:** JSONL traces record decisions, validation, tool results, and provider usage when available.
- **Offline result inspection:** `/result` displays saved observations without calling a model; `/result details` shows raw details.

### Tools

| MCP server | Tools | Purpose |
|---|---|---|
| `weather` | `search_location` | Search for locations and coordinates |
| `weather` | `weather_forecast` | Retrieve a forecast for coordinates and dates |
| `local` | `list_directory` | List entries in the configured workspace |
| `local` | `read_text` | Read UTF-8 text files in the workspace |
| `local` | `search_files` | Search file contents by literal keyword |
| `local` | `text_stats` | Analyze basic text statistics |
| `local` | `current_time` | Read system time in a requested time zone |
| `utility` | `calculate` | Safely evaluate arithmetic expressions |
| `utility` | `date_offset` | Add or subtract days from a date |

The previous built-in demo tools remain available with `--engine legacy`; their weather, order, and exchange-rate data are fixtures. The default multi-step engine uses MCP tools.

### Architecture

```text
User message / session memory / tool observations
                         ↓
                 Jev selects action and tool
                         ↓
             DeepSeek extracts schema-based arguments
                         ↓
               Runtime validates and calls MCP tool
                         ↓
                  Save observation and return to Jev
                         ↓
            Jev continues, asks, or finishes
                         ↓
                  DeepSeek writes final answer
```

Jev handles finite-choice decisions. DeepSeek handles argument extraction and natural-language generation. One user task can trigger multiple model and tool requests. Token or cost savings have not been established by a controlled comparison.

### Requirements

- Python 3.10+ (Python 3.12 recommended)
- Jev API key: `JEVMODEL_API_KEY` or `TYPESAFE_API_KEY`
- DeepSeek API key: `DEEPSEEK_API_KEY`
- MCP Python SDK and JSON Schema dependencies
- Live forecasts use Open-Meteo. They are forecasts, not current observations. If the service is unreachable, the app reports the failure instead of substituting fixture data.

### Install and run

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-mcp.txt
export JEVMODEL_API_KEY="your-jev-key"
export DEEPSEEK_API_KEY="your-deepseek-key"
python app.py --mode jev --generator deepseek --user me --session default --trace traces-loop.jsonl
```


### Commands

| Command | Purpose |
|---|---|
| `/help` | Show command help |
| `/tools` | List tools |
| `/memory` | Show the current user's long-term memories |
| `/remember name=value` | Save a memory |
| `/forget name` | Delete a memory |
| `/new` | Start a new session while keeping preferences |
| `/session ID` | Switch to or restore a session |
| `/reset` | Clear the current session and pending task |
| `/retry` | Allow a pending task to retry |
| `/result` | View saved results without model calls |
| `/result details` | View full results and sources |
| `/exit` | Exit; data is saved automatically |

Example:

```text
/remember residence=Beijing
What's the weather today?
Where do I live?
```

### Project layout

```text
app.py                    CLI entry point
loop_agent.py             Jev decision loop and Agent runtime
loop_cli.py               Multi-turn terminal interface
mcp_client.py             stdio MCP client and lifecycle
mcp_servers/              Local, weather, and utility MCP servers
mcp.loop.json             Default V2 MCP configuration
tool_registry.py           Tool registration, schema validation, dispatch
memory_store.py           SQLite conversations and user memories
memory_context.py         Memory lookup and defaults
network_io.py              Bounded network retries
terminal_view.py           Terminal presentation
```

### Limitations

- Tools run serially; parallel scheduling is not implemented.
- Arguments are extracted by a generative model. Schema validation checks structure, not semantic correctness.
- Long-term memories are explicit; automatic extraction and vector search are not implemented.
- Local SQLite data is not encrypted.
- Date interpretation and location disambiguation depend on the model and tool results.
- The two-model design has not been systematically evaluated against a single-model baseline for accuracy, latency, or cost.
- Online features depend on network connectivity, third-party service availability, and API quotas.
