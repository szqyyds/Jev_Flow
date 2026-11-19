"""Persistent MCP stdio sessions, owned and closed by a single async task."""
from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import sys
import threading
from concurrent.futures import Future
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_core import ToolError


class StdioClient:
    def __init__(self, parameters: StdioServerParameters, timeout: float = 15):
        self.parameters, self.timeout = parameters, timeout
        self.jobs = queue.Queue()
        self.ready = Future()
        self.closed = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        try:
            self.tools = self.ready.result(timeout=timeout + 5)
        except Exception:
            self.close()
            raise ToolError("MCP 初始化失败，请检查服务命令和 stderr 日志") from None

    def _run(self):
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            if not self.ready.done():
                self.ready.set_exception(exc)
        finally:
            self.closed = True
            while not self.jobs.empty():
                job = self.jobs.get_nowait()
                if job is not None and not job[2].done():
                    job[2].set_exception(ToolError("MCP 会话已关闭"))

    async def _serve(self):
        async with stdio_client(self.parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=self.timeout)) as session:
                await session.initialize()
                tools, cursor, seen = [], None, set()
                while True:
                    page = await session.list_tools(cursor=cursor)
                    tools.extend(page.tools)
                    cursor = page.nextCursor
                    if not cursor:
                        break
                    if cursor in seen or len(tools) > 1000:
                        raise ToolError("MCP tools/list 分页异常")
                    seen.add(cursor)
                self.ready.set_result(tools)
                while True:
                    job = await asyncio.to_thread(self.jobs.get)
                    if job is None:
                        break
                    name, arguments, future = job
                    try:
                        result = await session.call_tool(name, arguments=arguments)
                        if result.isError:
                            detail = " ".join(c.text for c in result.content if c.type == "text")[:1000]
                            raise ToolError(f"MCP 工具失败：{detail}")
                        payload = result.model_dump(by_alias=True, mode="json", exclude_none=True)
                        if "structuredContent" in payload:
                            payload = payload["structuredContent"]
                        serialized = json.dumps(payload, ensure_ascii=False)
                        if len(serialized) > 16000:
                            payload = {"text": serialized[:16000], "truncated": True}
                        future.set_result({"source": "MCP", "result": payload})
                    except Exception as exc:
                        future.set_exception(ToolError(f"MCP 调用失败：{exc}"))

    def call(self, name: str, arguments: dict) -> dict:
        if self.closed or not self.thread.is_alive():
            raise ToolError("MCP 会话已关闭，请重新启动程序")
        future = Future()
        self.jobs.put((name, arguments, future))
        try:
            return future.result(timeout=self.timeout + 5)
        except TimeoutError:
            raise ToolError("MCP 工具调用超时，未自动重试") from None

    def close(self):
        if self.thread.is_alive():
            self.jobs.put(None)
            self.thread.join(timeout=self.timeout + 10)
        self.closed = True


class MCPManager:
    def __init__(self, config_path: str, registry):
        self.path = Path(config_path).resolve()
        self.registry = registry
        self.clients = []

    def __enter__(self):
        try:
            config = json.loads(self.path.read_text(encoding="utf-8"))
            for name, entry in config["mcpServers"].items():
                if entry.get("disabled", False):
                    continue
                if not re.fullmatch(r"[a-z][a-z0-9_]{0,19}", name):
                    raise ValueError("MCP 服务名只支持小写字母、数字和下划线，最多20字符")
                allowed = entry.get("read_only_tools", [])
                if not allowed:
                    raise ValueError(f"{name} 缺少 read_only_tools 工具允许列表")
                def expand(value):
                    def replace(match):
                        variable = match.group(1)
                        if variable == "PYTHON":
                            return sys.executable
                        if variable == "ROOT":
                            return str(self.path.parent)
                        if variable not in os.environ:
                            raise ValueError(f"缺少 MCP 环境变量：{variable}")
                        return os.environ[variable]
                    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)
                params = StdioServerParameters(
                    command=expand(entry["command"]),
                    args=[expand(a) for a in entry.get("args", [])],
                    cwd=str(self.path.parent),
                    env={k: expand(v) for k, v in entry.get("env", {}).items()},
                )
                timeout = float(entry.get("timeout", 15))
                if not 0 < timeout <= 120:
                    raise ValueError("MCP timeout 应在 0 到 120 秒之间")
                client = StdioClient(params, timeout)
                self.clients.append(client)
                available = {t.name for t in client.tools}
                if set(allowed) - available:
                    raise ValueError(f"{name} 配置的工具不存在：{sorted(set(allowed) - available)}")
                for tool in client.tools:
                    if tool.name in allowed:
                        self.registry.register_mcp(name, tool, client)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *exc):
        for client in reversed(self.clients):
            client.close()
