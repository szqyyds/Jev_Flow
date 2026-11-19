"""Per-agent tool catalog: built-ins plus explicitly allowed MCP tools."""
from __future__ import annotations

import json
import re

from agent_core import SPECS, ToolSpec, ToolError, build_args, execute_tool, validate


class ToolRegistry:
    def __init__(self, include_builtins=True):
        self.specs = dict(SPECS) if include_builtins else {}
        self.bindings = {}

    def register_mcp(self, server, tool, client):
        from jsonschema import Draft202012Validator
        name = f"mcp_{server}_{tool.name}"
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name) or name in self.specs:
            raise ValueError(f"工具名称重复或不符合路由命名规则：{name}")
        schema = tool.inputSchema
        Draft202012Validator.check_schema(schema)
        if schema.get("type") != "object":
            raise ValueError(f"{name} 参数 Schema 必须为 object")
        def check_refs(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in ("$ref", "$dynamicRef") and not str(value).startswith('#'):
                        raise ValueError("工具 Schema 不允许外部引用")
                    check_refs(value)
            elif isinstance(node, list):
                for value in node:
                    check_refs(value)
        check_refs(schema)
        self.specs[name] = ToolSpec(name, (tool.description or tool.name)[:350],
                                    tuple(schema.get("required", [])), True, schema)
        self.bindings[name] = (client, tool.name)

    def match(self, text):
        for name in self.bindings:
            if re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", text):
                return name
        # Natural-language shortcuts for the bundled server only.
        shortcuts = [
            ("search_files", ("搜索文件内容", "搜索项目", "搜索代码")),
            ("read_text", ("读取文件", "读文件", "读取 ", "打开文件")),
            ("list_directory", ("列出文件", "列目录", "列出目录", "有哪些文件")),
            ("text_stats", ("文本统计", "统计文本")),
            ("current_time", ("现在几点", "当前时间", "今天几号", "当前日期")),
        ]
        for tool, phrases in shortcuts:
            name = f"mcp_local_{tool}"
            if name in self.bindings and any(p in text for p in phrases):
                return name
        return None

    def build_args(self, tool, text, session):
        if tool not in self.bindings:
            return build_args(tool, text, session)
        args, sources = {}, {}
        # JSON is a generic, explicit argument format for any discovered tool.
        # Multiple turns can supply additional fields; later values win.
        decoder = json.JSONDecoder()
        position = 0
        parsed_json = False
        while True:
            position = text.find('{', position)
            if position < 0:
                break
            try:
                value, length = decoder.raw_decode(text[position:])
            except ValueError:
                position += 1
                continue
            position += length
            if isinstance(value, dict):
                parsed_json = True
                args.update(value)
                sources.update({k: "user_explicit_json" for k in value})
        if '{' in text and not parsed_json:
            raise ToolError("参数 JSON 无法解析，请使用完整的 JSON 对象")
        name = tool.removeprefix("mcp_local_") if tool.startswith("mcp_local_") else ""
        if not args and name == "read_text":
            matches = re.findall(r'[`"“]([^`"”]+)[`"”]|([\w./-]+\.[A-Za-z0-9]+)', text)
            if matches:
                args["path"] = next(part for part in matches[-1] if part)
        elif not args and name == "search_files":
            match = re.search(r"(?:搜索文件内容|搜索项目|搜索代码)\s*[:：]?\s*(.+)", text)
            if match:
                args["query"] = match.group(1).strip(' `"“”')
        elif not args and name == "text_stats":
            match = re.search(r"(?:文本统计|统计文本)\s*[:：]\s*(.+)", text, re.S)
            if match:
                args["text"] = match.group(1)
        elif not args and name == "current_time":
            match = re.search(r"\b(?:[A-Za-z_]+/[A-Za-z_/]+|UTC)\b", text)
            if match:
                args["timezone"] = match.group()
        for key in args:
            sources.setdefault(key, "user_explicit")
        return args, sources

    def validate(self, tool, args, sources):
        if tool not in self.bindings:
            return validate(tool, args, sources)
        from jsonschema import Draft202012Validator
        schema = self.specs[tool].input_schema
        errors = []
        for key in schema.get("required", []):
            if key not in args:
                errors.append(key)
        for key, value in args.items():
            if key not in sources:
                errors.append(f"{key} 缺少输入来源")
            if key not in schema.get("properties", {}):
                errors.append(f"未知参数 {key}")
            if key in schema.get("required", []) and value == "":
                errors.append(f"{key} 不能为空")
        for error in Draft202012Validator(schema).iter_errors(args):
            if error.validator != "required":
                location = '.'.join(str(p) for p in error.path) or "参数"
                errors.append(f"{location} 不符合 {error.validator} 约束")
        return sorted(set(errors))

    def execute(self, name, args):
        if name not in self.specs:
            raise ToolError("未注册的工具")
        if name not in self.bindings:
            return execute_tool(name, args)
        # Validate again at the execution boundary, including direct CLI calls.
        errors = self.validate(name, args, {k: "explicit_call" for k in args})
        if errors:
            raise ToolError("参数校验失败：" + "、".join(errors))
        client, original_name = self.bindings[name]
        return client.call(original_name, args)
