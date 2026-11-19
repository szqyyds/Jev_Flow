"""Real, bounded local tools exposed over MCP stdio (stdout is protocol only)."""
from __future__ import annotations

import argparse
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

server = FastMCP("Jev local tools", log_level="WARNING")
ROOT = Path.cwd().resolve()
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
SKIP = {".git", ".venv", "node_modules", "__pycache__", ".aws", ".ssh", ".codex"}


def checked(path: str) -> Path:
    candidate = Path(path)
    candidate = candidate if candidate.is_absolute() else ROOT / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError("路径必须位于配置的工作目录内")
    # Exclude hidden/configuration credentials through both names and symlinks.
    for p in (candidate, resolved):
        if any(part.startswith('.') and part not in ('.', '..') for part in p.relative_to(ROOT).parts):
            raise ValueError("不读取隐藏目录或隐藏文件")
        if p.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}:
            raise ValueError("不读取密钥文件")
    return resolved


@server.tool(annotations=READ_ONLY)
def list_directory(path: str = ".") -> dict[str, Any]:
    """列出工作目录中的文件和子目录；path 是相对路径，默认当前工作目录。"""
    folder = checked(path)
    if not folder.is_dir():
        raise ValueError("路径不是目录")
    entries = []
    for item in sorted(folder.iterdir(), key=lambda p: p.name):
        try:
            safe = checked(str(item))
        except ValueError:
            continue
        if safe.name in SKIP:
            continue
        entries.append({"name": item.name, "type": "directory" if safe.is_dir() else "file"})
        if len(entries) == 200:
            break
    return {"path": path, "entries": entries, "limit": 200, "source": "LOCAL_FILESYSTEM"}


@server.tool(annotations=READ_ONLY)
def read_text(path: str) -> dict[str, Any]:
    """读取工作目录内 UTF-8 文本文件，最多返回 10000 字符。必须提供 path。"""
    file = checked(path)
    if not file.is_file() or file.stat().st_size > 1_000_000:
        raise ValueError("只读取 1 MB 以内的普通文本文件")
    with file.open(encoding="utf-8") as stream:
        content = stream.read(10001)
    return {"path": path, "text": content[:10000], "truncated": len(content) > 10000,
            "source": "LOCAL_FILESYSTEM"}


@server.tool(annotations=READ_ONLY)
def search_files(query: str, path: str = ".") -> dict[str, Any]:
    """在工作目录文本文件中搜索字面关键词 query，可指定子目录 path。返回文件、行号和片段。"""
    if not query.strip():
        raise ValueError("搜索词不能为空")
    folder = checked(path)
    if not folder.is_dir():
        raise ValueError("搜索路径必须是目录")
    matches, visited = [], 0
    for base, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in SKIP
                         and not (Path(base) / d).is_symlink())
        for name in sorted(files):
            visited += 1
            if visited > 300:
                return {"matches": matches, "truncated": True, "source": "LOCAL_FILESYSTEM"}
            try:
                file = checked(str(Path(base) / name))
                if not file.is_file() or file.stat().st_size > 200_000:
                    continue
                lines = file.read_text(encoding="utf-8").splitlines()
            except (ValueError, OSError, UnicodeError):
                continue
            for number, line in enumerate(lines, 1):
                if query.casefold() in line.casefold():
                    matches.append({"path": str(file.relative_to(ROOT)), "line": number, "text": line[:250]})
                    if len(matches) >= 30:
                        return {"matches": matches, "truncated": True, "source": "LOCAL_FILESYSTEM"}
    return {"matches": matches, "truncated": False, "source": "LOCAL_FILESYSTEM"}


@server.tool(annotations=READ_ONLY)
def text_stats(text: str) -> dict[str, Any]:
    """统计用户提供文本的字符数、中文字符数、英文单词数和行数。"""
    if len(text) > 10000:
        raise ValueError("文本不能超过 10000 字符")
    return {"characters": len(text), "chinese_characters": len(re.findall(r"[\u4e00-\u9fff]", text)),
            "english_words": len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text)),
            "lines": len(text.splitlines()), "source": "LOCAL_COMPUTATION"}


@server.tool(annotations=READ_ONLY)
def current_time(timezone: str = "Asia/Shanghai") -> dict[str, Any]:
    """查询当前实际日期和时间；timezone 使用 IANA 时区，例如 Asia/Shanghai 或 UTC。"""
    return {"datetime": datetime.now(ZoneInfo(timezone)).isoformat(timespec="seconds"),
            "timezone": timezone, "source": "SYSTEM_CLOCK"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    ROOT = Path(parser.parse_args().root).resolve(strict=True)
    server.run(transport="stdio")
