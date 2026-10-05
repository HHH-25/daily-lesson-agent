"""共享图状态 DayState。"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class DayState(TypedDict, total=False):
    goal: str
    date: str
    hours_left: float
    mode: str  # "plan" | "analyze"
    assignments: dict[str, Any]  # supervisor 分派单：驱动条件边 + 下游 prompt
    plan: list[dict]  # study_agent 输出的 study 任务
    life: list[dict]  # life_agent 输出的 life 任务
    trimmed_plan: list[dict]  # budget_agent 输出（life + study pending/cut）
    done_items: list[int]
    review: str
    tomorrow: str
    yesterday_hint: str  # 昨日复盘 tomorrow，供 supervisor 写 study_brief
    interview_review: str
    interview_notes: str  # 用户覆盖备注（可选）
    interview_evidence: dict[str, Any]  # evidence_agent：post_notes / prep_notes
    stats: dict[str, Any]  # analyze_interviews 统计
    analysis: str  # 求职复盘文案
    suggestions: str  # 求职建议
    summary: str  # synthesizer 今日板 / 求职报告收口
    ask_query: str
    ask_mode: str  # semantic | time
    ask_chunks: list[dict[str, Any]]
    ask_answer: str
    tool_summary: str
    logs: Annotated[list[dict], operator.add]
    tool_calls: Annotated[list[dict], operator.add]
