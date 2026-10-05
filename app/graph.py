"""LangGraph：Supervisor 主图 + Evidence 复盘副图 + 求职分析图；SqliteSaver checkpointer。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from app.nodes import (
    budget_agent,
    evidence_agent,
    interview_analyst,
    interview_qa,
    life_agent,
    reviewer,
    study_agent,
    supervisor,
    synthesizer,
)
from app.state import DayState

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "daily.db"

_checkpointer: SqliteSaver | None = None
_conn: sqlite3.Connection | None = None
_plan_graph = None
_review_graph = None
_analyze_graph = None
_ask_graph = None


def get_checkpointer() -> SqliteSaver:
    global _checkpointer, _conn
    if _checkpointer is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _checkpointer = SqliteSaver(_conn)
        _checkpointer.setup()
    return _checkpointer


def _route_after_supervisor(
    state: DayState,
) -> list[Literal["life_agent", "study_agent"]]:
    assignments = state.get("assignments") or {}
    if assignments.get("life"):
        return ["life_agent", "study_agent"]
    return ["study_agent"]


def build_plan_graph():
    g = StateGraph(DayState)
    g.add_node("supervisor", supervisor)
    g.add_node("life_agent", life_agent)
    g.add_node("study_agent", study_agent)
    g.add_node("budget_agent", budget_agent)
    g.add_node("synthesizer", synthesizer)

    g.add_edge(START, "supervisor")
    g.add_conditional_edges(
        "supervisor",
        _route_after_supervisor,
        ["life_agent", "study_agent"],
    )
    g.add_edge("life_agent", "budget_agent")
    g.add_edge("study_agent", "budget_agent")
    g.add_edge("budget_agent", "synthesizer")
    g.add_edge("synthesizer", END)
    return g.compile(checkpointer=get_checkpointer())


def build_review_graph():
    g = StateGraph(DayState)
    g.add_node("evidence_agent", evidence_agent)
    g.add_node("reviewer", reviewer)
    g.add_edge(START, "evidence_agent")
    g.add_edge("evidence_agent", "reviewer")
    g.add_edge("reviewer", END)
    return g.compile(checkpointer=get_checkpointer())


def build_analyze_graph():
    """求职分析：supervisor → interview_analyst → synthesizer。"""
    g = StateGraph(DayState)
    g.add_node("supervisor", supervisor)
    g.add_node("interview_analyst", interview_analyst)
    g.add_node("synthesizer", synthesizer)
    g.add_edge(START, "supervisor")
    g.add_edge("supervisor", "interview_analyst")
    g.add_edge("interview_analyst", "synthesizer")
    g.add_edge("synthesizer", END)
    return g.compile(checkpointer=get_checkpointer())


def build_ask_graph():
    """面经问答：interview_qa 单节点（本地检索工具，不经 MCP）。"""
    g = StateGraph(DayState)
    g.add_node("interview_qa", interview_qa)
    g.add_edge(START, "interview_qa")
    g.add_edge("interview_qa", END)
    return g.compile(checkpointer=get_checkpointer())


def get_plan_graph():
    global _plan_graph
    if _plan_graph is None:
        _plan_graph = build_plan_graph()
    return _plan_graph


def get_review_graph():
    global _review_graph
    if _review_graph is None:
        _review_graph = build_review_graph()
    return _review_graph


def get_analyze_graph():
    global _analyze_graph
    if _analyze_graph is None:
        _analyze_graph = build_analyze_graph()
    return _analyze_graph


def get_ask_graph():
    global _ask_graph
    if _ask_graph is None:
        _ask_graph = build_ask_graph()
    return _ask_graph


def _empty_state(**overrides: object) -> DayState:
    base: DayState = {
        "goal": "",
        "date": "",
        "hours_left": 0.0,
        "mode": "plan",
        "assignments": {},
        "plan": [],
        "life": [],
        "trimmed_plan": [],
        "done_items": [],
        "review": "",
        "tomorrow": "",
        "yesterday_hint": "",
        "interview_review": "",
        "interview_notes": "",
        "interview_evidence": {},
        "stats": {},
        "analysis": "",
        "suggestions": "",
        "summary": "",
        "ask_query": "",
        "ask_mode": "",
        "ask_chunks": [],
        "ask_answer": "",
        "logs": [],
        "tool_summary": "",
        "tool_calls": [],
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def run_plan(goal: str, date: str, hours_left: float) -> DayState:
    graph = get_plan_graph()
    yesterday_hint = ""
    try:
        from app import db as _db

        for d in _db.list_days_with_details(limit=7):
            if str(d.get("date", "")) >= date:
                continue
            rev = d.get("review") or {}
            hint = (rev.get("tomorrow") or "").strip()
            if hint:
                yesterday_hint = hint
                break
    except Exception:  # noqa: BLE001
        yesterday_hint = ""
    initial = _empty_state(
        goal=goal,
        date=date,
        hours_left=hours_left,
        mode="plan",
        yesterday_hint=yesterday_hint,
    )
    config = {"configurable": {"thread_id": f"plan-{date}"}}
    return graph.invoke(initial, config=config)  # type: ignore[return-value]


def run_review(
    goal: str,
    date: str,
    hours_left: float,
    trimmed_plan: list[dict],
    done_items: list[int],
    tool_summary: str = "",
    interview_notes: str = "",
) -> DayState:
    graph = get_review_graph()
    config = {"configurable": {"thread_id": f"review-{date}"}}
    initial = _empty_state(
        goal=goal,
        date=date,
        hours_left=hours_left,
        mode="plan",
        life=[t for t in trimmed_plan if t.get("type") == "life"],
        trimmed_plan=trimmed_plan,
        done_items=done_items,
        interview_notes=interview_notes or "",
        tool_summary=tool_summary,
    )
    return graph.invoke(initial, config=config)  # type: ignore[return-value]


def run_analyze(goal: str, date: str | None = None) -> DayState:
    """求职分析入口：supervisor → interview_analyst → synthesizer。"""
    from datetime import date as date_cls

    graph = get_analyze_graph()
    run_date = date or date_cls.today().isoformat()
    initial = _empty_state(
        goal=goal,
        date=run_date,
        hours_left=0.0,
        mode="analyze",
    )
    config = {"configurable": {"thread_id": f"analyze-{run_date}"}}
    return graph.invoke(initial, config=config)  # type: ignore[return-value]


def run_ask(query: str) -> DayState:
    from datetime import date as date_cls
    from app.rag import classify_ask_mode

    graph = get_ask_graph()
    q = (query or "").strip()
    run_date = date_cls.today().isoformat()
    initial = _empty_state(
        date=run_date,
        hours_left=0.0,
        mode="ask",
        ask_query=q,
        ask_mode=classify_ask_mode(q),
    )
    config = {"configurable": {"thread_id": f"ask-{run_date}"}}
    return graph.invoke(initial, config=config)  # type: ignore[return-value]
