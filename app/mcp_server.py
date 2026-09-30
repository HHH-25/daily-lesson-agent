"""本地 MCP stdio Server：把 app.tools 里的四个函数暴露成 MCP tools。"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.server.fastmcp import FastMCP

from app.tools import (
    ANALYZE_INTERVIEWS_SPEC,
    APPLY_BUDGET_SPEC,
    LIST_INTERVIEWS_SPEC,
    READ_LIFE_SPEC,
    apply_time_budget as _apply_time_budget,
    analyze_interviews as _analyze_interviews,
    list_interviews as _list_interviews,
    read_life_context as _read_life_context,
)

mcp = FastMCP("daily-lesson-tools", log_level="ERROR")


@mcp.tool(description=READ_LIFE_SPEC["description"])
def read_life_context() -> dict[str, Any]:
    return _read_life_context()


@mcp.tool(description=LIST_INTERVIEWS_SPEC["description"])
def list_interviews(date: str) -> dict[str, Any]:
    return {"interviews": _list_interviews(date)}


@mcp.tool(description=APPLY_BUDGET_SPEC["description"])
def apply_time_budget(
    hours_left: float,
    life: list[dict],
    study: list[dict],
) -> dict[str, Any]:
    return _apply_time_budget(hours_left, life, study)


@mcp.tool(description=ANALYZE_INTERVIEWS_SPEC["description"])
def analyze_interviews() -> dict[str, Any]:
    return _analyze_interviews()


if __name__ == "__main__":
    mcp.run(transport="stdio")
