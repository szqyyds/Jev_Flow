"""Deterministic arithmetic and date operations via MCP."""
import sys
from pathlib import Path
from datetime import date, timedelta
from typing import Any
from mcp.server.fastmcp import FastMCP
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_core import safe_calculate
server = FastMCP('Utilities', log_level='WARNING')


@server.tool()
def calculate(expression: str) -> dict[str, Any]:
    """计算明确的四则运算或幂表达式，AST 白名单执行，不执行 Python 代码。"""
    if len(expression) > 500:
        raise ValueError('表达式过长')
    return {'value': safe_calculate(expression), 'source': 'LOCAL_COMPUTATION'}


@server.tool()
def date_offset(start_date: str, days: int) -> dict[str, Any]:
    """给 YYYY-MM-DD 日期加减整数天数，返回日期和星期（周一为1）；用于相对日期计算。"""
    if abs(days) > 3660:
        raise ValueError('偏移不得超过3660天')
    result = date.fromisoformat(start_date) + timedelta(days=days)
    return {'date': result.isoformat(), 'weekday': result.isoweekday(), 'source': 'LOCAL_COMPUTATION'}


if __name__ == '__main__':
    server.run(transport='stdio')
