"""CLI for the Jev multi-tool Agent, with optional MCP stdio servers."""
import argparse
import json
import os
from contextlib import ExitStack

from agent_core import Agent, OfflineRouter, Session, DeepSeekResponder, ToolError, jev_router_from_env
from tool_registry import ToolRegistry


def show_tools(registry):
    for name, spec in registry.specs.items():
        source = "MCP" if name in registry.bindings else "内置"
        print(f"{name} [{source}] 必填：{', '.join(spec.required) or '无'} — {spec.description}")
        if spec.input_schema:
            print("  Schema: " + json.dumps(spec.input_schema, ensure_ascii=False))


def run(args, parser, registry):
    if args.list_tools:
        show_tools(registry)
        return
    if args.tool:
        if args.tool not in registry.specs:
            parser.error("工具不存在，请先运行 --list-tools")
        try:
            arguments = json.loads(args.arguments)
            if not isinstance(arguments, dict):
                raise ValueError("--args 必须是 JSON 对象")
        except ValueError as exc:
            parser.error(str(exc))
        errors = registry.validate(args.tool, arguments, {k: "user_explicit_json" for k in arguments})
        if errors:
            raise ToolError("参数校验失败：" + "、".join(errors))
        print(json.dumps(registry.execute(args.tool, arguments), ensure_ascii=False, indent=2))
        return
    if args.mode == "jev":
        try:
            router = jev_router_from_env(min_confidence=args.confidence)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        router = OfflineRouter()
    responder = None
    if args.generator == "deepseek":
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not key:
            parser.error("DeepSeek 生成模式需要环境变量 DEEPSEEK_API_KEY")
        responder = DeepSeekResponder(api_key=key)
    agent = Agent(router, trace_path=args.trace or None, responder=responder, registry=registry)
    session = Session()

    if args.demo:
        cases = ["计算 18 + 24", "北京天气怎么样", "100 美元兑换人民币", "帮我查一下订单",
                 "A123", "查订单 A123，如果没发货解释一下发货规则", "搜索本地文档", "高处作业"]
        if registry.bindings:
            cases.extend(["列出文件", "读取 README.md", "搜索文件内容 Jev", "文本统计：你好 Agent", "当前时间"])
        for query in cases:
            out = agent.run(query, session)
            print(f"\n用户: {query}\n状态: {out['status']}\nAgent: {out['answer']}")
        return
    if args.query:
        out = agent.run(args.query, session)
        print(f"[{out['status']}] {out['answer']}")
        return
    print(f"Jev Agent / mode={args.mode} / 工具数={len(registry.specs)}（MCP {len(registry.bindings)}）。")
    print("天气/汇率/订单为模拟数据；MCP 按工具说明访问实际数据。输入 /tools 查看工具，/reset 清空会话，exit 退出。")
    while True:
        try:
            query = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if query.lower() in {"exit", "quit", "q"}:
            break
        if query == "/tools":
            show_tools(registry)
            continue
        if query == "/reset":
            session = Session()
            print("会话已清空。")
            continue
        if not query:
            continue
        out = agent.run(query, session)
        print(f"Agent [{out['status']}] ({out['latency_ms']}ms): {out['answer']}")


def main():
    parser = argparse.ArgumentParser(description="Jev + multi-tool Agent with optional MCP")
    parser.add_argument("--mode", choices=["offline", "jev"], default="jev")
    parser.add_argument("--query", help="执行一次请求")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--trace", default="")
    parser.add_argument("--confidence", type=float, default=0.55)
    parser.add_argument("--generator", choices=["template", "deepseek"], default="template")
    parser.add_argument("--mcp-config", help="包含 mcpServers 的 JSON 配置文件")
    parser.add_argument("--list-tools", action="store_true", help="列出工具和 Schema，无需模型 Key")
    parser.add_argument("--tool", help="直接调用指定工具，无需模型 Key")
    parser.add_argument("--args", dest="arguments", default="{}", help="--tool 的 JSON 参数")
    parser.add_argument("--engine", choices=["loop", "legacy"], default="loop", help="默认多轮多步骤引擎；legacy 保留旧演示")
    parser.add_argument("--user", default="default", help="本地用户档案ID，不是身份认证")
    parser.add_argument("--session", default="default", help="可恢复的会话ID")
    parser.add_argument("--memory-db", default=".agent_data/memory.sqlite3")
    parser.add_argument("--max-steps", type=int, default=12)
    args = parser.parse_args()
    if not 1 <= args.max_steps <= 30:
        parser.error("--max-steps 必须为 1–30")
    if args.engine == "loop":
        try:
            from loop_cli import run_loop
            run_loop(args, parser)
        except ModuleNotFoundError as exc:
            parser.exit(1, f"缺少依赖 {exc.name}；使用 .venv/bin/python 并安装 requirements-mcp.txt\n")
        except (ToolError, ValueError, OSError, KeyError) as exc:
            parser.exit(1, f"启动或工具调用失败：{exc}\n")
        return
    registry = ToolRegistry()
    try:
        with ExitStack() as stack:
            if args.mcp_config:
                try:
                    from mcp_client import MCPManager
                except ModuleNotFoundError:
                    parser.error("MCP 依赖未安装：请使用 .venv/bin/python，并安装 requirements-mcp.txt")
                stack.enter_context(MCPManager(args.mcp_config, registry))
            run(args, parser, registry)
    except (ToolError, ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"启动或工具调用失败：{exc}\n")


if __name__ == "__main__":
    main()
