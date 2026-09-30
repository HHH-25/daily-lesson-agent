"""Supervisor / 领域 Agent / synthesizer / evidence / reviewer 节点。"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from app.state import DayState
from app.tools import (
    ANALYZE_TOOLS,
    BUDGET_TOOLS,
    EVIDENCE_TOOLS,
    LIFE_TOOLS,
    build_life_tasks,
    invoke_tool,
    peek_has_life_load,
    today_context_bundle,
)

load_dotenv()


def get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model="deepseek-chat",
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
        temperature=0.2,
    )


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if m:
            return json.loads(m.group(1))
        raise


def _log(node: str, kind: str, content: Any) -> dict:
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return {"node": node, "kind": kind, "content": content}


def _tc_public(call: dict) -> dict:
    return {
        "tool": call["tool"],
        "input": call.get("input"),
        "output": call.get("output", ""),
        "agent": call.get("agent") or "",
    }


def _run_tool_loop(
    agent_name: str,
    system: str,
    user: str,
    tools: list,
    *,
    max_rounds: int = 3,
    fallback: Optional[Callable[[], list[dict]]] = None,
) -> tuple[str, list[dict], list[dict]]:
    """LLM 工具环；失败或未调工具时走确定性兜底。返回 (final_text, tool_calls, logs)。"""
    logs: list[dict] = []
    tool_calls_out: list[dict] = []
    tool_map = {t.name: t for t in tools}
    llm = get_llm().bind_tools(tools)
    messages: list = [SystemMessage(content=system), HumanMessage(content=user)]
    logs.append(_log(agent_name, "input", user))

    final_text = ""
    called_any = False

    for _ in range(max_rounds):
        resp = llm.invoke(messages)
        messages.append(resp)
        raw_calls = getattr(resp, "tool_calls", None) or []
        if not raw_calls:
            final_text = resp.content if isinstance(resp.content, str) else str(resp.content or "")
            break

        called_any = True
        for tc in raw_calls:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
            args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {}) or {}
            call_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", name)
            if not isinstance(args, dict):
                args = {}
            try:
                gateway = invoke_tool(name, args, agent=agent_name)
            except Exception as exc:  # noqa: BLE001
                # 参数错：尝试 StructuredTool 自带 invoke，再不行记错误
                try:
                    tool = tool_map[name]
                    raw = tool.invoke(args)
                    data = json.loads(raw) if isinstance(raw, str) else raw
                    gateway = {
                        "tool": name,
                        "input": args or None,
                        "output": raw if isinstance(raw, str) else json.dumps(data, ensure_ascii=False),
                        "data": data,
                        "agent": agent_name,
                    }
                except Exception:  # noqa: BLE001
                    gateway = {
                        "tool": name,
                        "input": args or None,
                        "output": json.dumps({"error": str(exc)}, ensure_ascii=False),
                        "data": {"error": str(exc)},
                        "agent": agent_name,
                    }
                    logs.append(_log(agent_name, "error", f"tool {name}: {exc}"))

            tool_calls_out.append(_tc_public(gateway))
            logs.append(
                _log(
                    agent_name,
                    "tool_call",
                    {"tool": gateway["tool"], "input": gateway["input"], "agent": agent_name},
                )
            )
            logs.append(
                _log(
                    agent_name,
                    "tool_result",
                    {"tool": gateway["tool"], "output": gateway["data"], "agent": agent_name},
                )
            )
            messages.append(
                ToolMessage(content=gateway["output"], tool_call_id=call_id)
            )
    else:
        # 用尽轮次仍在调工具：再要一次纯文本
        resp = get_llm().invoke(messages)
        final_text = resp.content if isinstance(resp.content, str) else str(resp.content or "")

    if not called_any and fallback:
        for gateway in fallback():
            tool_calls_out.append(_tc_public(gateway))
            logs.append(
                _log(
                    agent_name,
                    "tool_call",
                    {
                        "tool": gateway["tool"],
                        "input": gateway["input"],
                        "agent": agent_name,
                        "fallback": True,
                    },
                )
            )
            logs.append(
                _log(
                    agent_name,
                    "tool_result",
                    {
                        "tool": gateway["tool"],
                        "output": gateway["data"],
                        "agent": agent_name,
                        "fallback": True,
                    },
                )
            )
        if not final_text:
            final_text = "(deterministic fallback)"

    logs.append(_log(agent_name, "output", final_text or "(empty)"))
    return final_text, tool_calls_out, logs


# study_brief 禁止的空泛旧默认/模板句（IMPLEMENTATION §6 / PLAN §10）
_VAGUE_STUDY_BRIEF_MARKERS = (
    "按目标拆学习任务",
    "故意略超可用预算以便演示裁剪",
    "拆学习任务",
    "按目标拆",
)


def _estimate_study_budget(hours_left: float, peek: dict) -> float:
    """用预览估固定占用后的剩余学习小时。"""
    life_hint = build_life_tasks(
        {"meetings": peek.get("meetings") or [], "blocks": [], "notes": []},
        peek.get("interviews") or [],
    )
    life_sum = sum(float(t["estimate_hours"]) for t in life_hint)
    return max(0.5, round(float(hours_left) - life_sum, 1))


def _collect_kadian_hints(peek: dict, *, date: str) -> list[str]:
    """从面试 notes / post_notes / insights 抽卡点线索（当日优先，再看近期）。"""
    from app.tools import list_all_interviews

    hints: list[str] = []
    seen: set[str] = set()

    def _push(text: str) -> None:
        t = (text or "").strip()
        if not t or t in seen:
            return
        seen.add(t)
        hints.append(t)

    today_ivs = list(peek.get("interviews") or [])
    today_keys = {
        (str(i.get("date", "")), str(i.get("time", "")), str(i.get("company", "")))
        for i in today_ivs
    }
    recent = [i for i in list_all_interviews() if str(i.get("date", "")) <= date]
    # 当日优先，再按日期倒序补近期
    extras = [
        i
        for i in recent
        if (str(i.get("date", "")), str(i.get("time", "")), str(i.get("company", "")))
        not in today_keys
    ]
    ordered = today_ivs + sorted(extras, key=lambda x: str(x.get("date", "")), reverse=True)
    for iv in ordered:
        company = str(iv.get("company") or "").strip()
        prefix = f"{company}：" if company else ""
        for field in ("post_notes", "notes", "insights"):
            val = (iv.get(field) or "").strip()
            if val:
                _push(f"{prefix}{val}")
        if len(hints) >= 4:
            break
    return hints


def _yesterday_tomorrow_hint(date: str, state: DayState) -> str:
    """昨日/近期复盘的 tomorrow；优先 state.yesterday_hint，否则查 DB。"""
    val = state.get("yesterday_hint")
    if isinstance(val, str) and val.strip():
        return val.strip()
    try:
        from app import db as _db

        for d in _db.list_days_with_details(limit=7):
            if str(d.get("date", "")) >= date:
                continue
            rev = d.get("review") or {}
            hint = (rev.get("tomorrow") or "").strip()
            if hint:
                return hint
    except Exception:  # noqa: BLE001
        pass
    return ""


def _is_vague_study_brief(brief: str) -> bool:
    text = (brief or "").strip()
    if not text or len(text) < 12:
        return True
    return any(m in text for m in _VAGUE_STUDY_BRIEF_MARKERS)


def _fallback_study_brief(
    *,
    has_interview: bool,
    has_meeting: bool,
    study_budget: float,
    kadian: list[str],
    goal: str,
) -> str:
    """确定性具体策略：面试/会议 + 剩余学时 + 优先卡点。"""
    focus = (kadian[0] if kadian else "").strip()
    if "：" in focus:
        focus = focus.split("：", 1)[-1].strip()
    # 截短卡点，避免 brief 过长
    if len(focus) > 40:
        focus = focus[:40].rstrip("，。；;,. ") + "…"
    if not focus:
        focus = f"围绕「{goal}」的最薄可演示点" if goal else "目标里最薄可演示点"

    if has_interview:
        return (
            f"今天有面试，学习收窄到约 {study_budget:.1f}h，优先补 [{focus}]；"
            "不扩新主题，拆出可口述/可跑通的小练习。"
        )
    if has_meeting:
        return (
            f"今天有会议占用，学习收窄到约 {study_budget:.1f}h，优先补 [{focus}]；"
            "会议前后只做短块，故意略超预算以便 Budget 演示裁剪。"
        )
    return (
        f"今日无固定占用，学习预算约 {study_budget:.1f}h，专注补 [{focus}]；"
        "任务总量故意略超预算以便演示裁剪。"
    )


def _fallback_note(*, has_interview: bool, has_meeting: bool, study_budget: float) -> str:
    if has_interview:
        return f"今日面试优先；学习收窄到约 {study_budget:.1f}h"
    if has_meeting:
        return f"今日有会议占用；学习收窄到约 {study_budget:.1f}h"
    return "今日无固定占用，专注学习"


def _normalize_note(note: str, *, has_interview: bool, has_meeting: bool, study_budget: float) -> str:
    text = (note or "").strip()
    if not text:
        return _fallback_note(
            has_interview=has_interview, has_meeting=has_meeting, study_budget=study_budget
        )
    # 有面试却未点出 → 用确定性 note（演示「面试优先」）
    if has_interview and ("面试" not in text):
        return _fallback_note(
            has_interview=True, has_meeting=has_meeting, study_budget=study_budget
        )
    return text


def _needs_study_brief_fallback(
    brief: str, *, has_interview: bool, has_meeting: bool
) -> bool:
    if _is_vague_study_brief(brief):
        return True
    text = (brief or "").strip()
    # 否认当日事实 → 兜底
    if has_interview and ("无面试" in text or "没有面试" in text):
        return True
    if has_meeting and ("无会议" in text) and ("面试" not in text):
        # 仅会议日却写无会议；有面试时允许侧重面试
        return True
    if "收窄到约" not in text and "约 " not in text and "h" not in text.lower():
        # 缺少学时预算信号
        return True
    if "优先" not in text and "补" not in text:
        return True
    return False


def supervisor(state: DayState) -> dict:
    """真 LLM 总控：输出 assignments。mode=analyze 时分派求职域；否则 life/study。"""
    if (state.get("mode") or "plan") == "analyze":
        return _supervisor_analyze(state)

    date = state["date"]
    hours_left = float(state["hours_left"])
    goal = state["goal"]
    peek = today_context_bundle(date)
    has_life = peek_has_life_load(date)
    interviews = list(peek.get("interviews") or [])
    meetings = list(peek.get("meetings") or [])
    has_interview = bool(interviews)
    has_meeting = bool(meetings)
    study_budget = _estimate_study_budget(hours_left, peek)
    kadian = _collect_kadian_hints(peek, date=date)
    yesterday_hint = _yesterday_tomorrow_hint(date, state)

    interview_fact = (
        "；".join(
            f"{iv.get('time','')} {iv.get('company','')}（{iv.get('stage','')}）"
            for iv in interviews
        )
        or "无"
    )
    meeting_fact = (
        "；".join(f"{m.get('time','')} {m.get('title','')}" for m in meetings) or "无"
    )

    prompt = f"""你是自我学习生活多 Agent 平台的 Supervisor。
根据目标与今日预算，输出**具体策略**分派单 JSON（不要 markdown）。

中期目标：{goal}
日期：{date}
今晚总预算：{hours_left}h
预估固定占用后学习预算：约 {study_budget:.1f}h

【不可否认的当日事实】
- 面试：{len(interviews)} 场 → {interview_fact}
- 会议：{len(meetings)} 场 → {meeting_fact}
- 占用块：{len(peek.get('blocks') or [])} 条
预览摘要：{json.dumps(peek, ensure_ascii=False)}
卡点线索（来自 notes/post_notes/insights，可空）：{json.dumps(kadian, ensure_ascii=False)}
昨日/近期复盘建议（可空）：{yesterday_hint or "（无）"}

输出：
{{
  "life": true/false,
  "study": true,
  "interview": false,
  "note": "一句话点出今日关键约束",
  "life_brief": "给 Life Agent 的指令（无生活占用则空）",
  "study_brief": "给 Study Agent 的具体策略"
}}

规则：
1. 有会议或面试时 life 必须为 true；study 恒为 true；本入口 interview 为 false。
2. note：有面试 → 必须写「面试优先」；仅有会议 →「会议占用、学习收窄」；都无 →「专注学习」。一句话即可。
3. study_brief 必须具体，禁止空泛「按目标拆学习任务」。须包含：
   - 是否有面试/会议（与上面事实一致，禁止写「无面试」如果事实有面试）；
   - 学习收窄到约 {study_budget:.1f}h；
   - 有卡点线索时写「优先补 [卡点]」（可综合昨日建议）。
   好例子：「今天有面试，学习收窄到约 {study_budget:.1f}h，优先补 [状态合并卡点]」。
4. life_brief：有占用时交代读哪些会议/面试；无则空字符串。
"""
    logs = [_log("supervisor", "input", prompt)]
    llm = get_llm()
    resp = llm.invoke(prompt)
    raw = resp.content if isinstance(resp.content, str) else str(resp.content)
    logs.append(_log("supervisor", "output", raw))

    try:
        data = _extract_json(raw)
        if not isinstance(data, dict):
            data = {}
    except json.JSONDecodeError:
        data = {}
        logs.append(_log("supervisor", "error", f"JSON 解析失败：{raw[:200]}"))

    note = _normalize_note(
        str(data.get("note") or ""),
        has_interview=has_interview,
        has_meeting=has_meeting,
        study_budget=study_budget,
    )

    life_brief = str(data.get("life_brief") or "").strip()
    if has_life and not life_brief:
        life_brief = "读取会议与面试，产出 life 任务"
    if not has_life:
        life_brief = ""

    study_brief = str(data.get("study_brief") or "").strip()
    if _needs_study_brief_fallback(
        study_brief, has_interview=has_interview, has_meeting=has_meeting
    ):
        logs.append(
            _log(
                "supervisor",
                "error",
                f"study_brief 空泛或不符事实，启用确定性兜底：{(study_brief or '（空）')[:100]}",
            )
        )
        study_brief = _fallback_study_brief(
            has_interview=has_interview,
            has_meeting=has_meeting,
            study_budget=study_budget,
            kadian=kadian or ([yesterday_hint] if yesterday_hint else []),
            goal=str(goal or ""),
        )

    assignments = {
        "life": bool(has_life),
        "study": True,
        "interview": False,
        "note": note,
        "life_brief": life_brief,
        "study_brief": study_brief,
        "study_budget_hours": study_budget,
    }
    # 强制对齐预览，保证条件边演示可靠
    assignments["life"] = bool(has_life)

    logs.append(_log("supervisor", "output", {"assignments": assignments}))
    return {"assignments": assignments, "logs": logs, "tool_calls": []}


def _supervisor_analyze(state: DayState) -> dict:
    """求职入口：分派 interview=true（三域之一）。"""
    goal = state.get("goal") or ""
    prompt = f"""你是自我学习生活多 Agent 平台的 Supervisor（求职分析入口）。
中期目标：{goal}
请输出分派单 JSON（不要 markdown）：
{{
  "life": false,
  "study": false,
  "interview": true,
  "note": "一句话说明为何做求职复盘",
  "interview_brief": "给 Interview Analyst 的指令（看哪些阶段/节奏/建议）"
}}
规则：本入口必须 interview=true，life/study=false。
"""
    logs = [_log("supervisor", "input", prompt)]
    llm = get_llm()
    resp = llm.invoke(prompt)
    raw = resp.content if isinstance(resp.content, str) else str(resp.content)
    logs.append(_log("supervisor", "output", raw))

    try:
        data = _extract_json(raw)
        if not isinstance(data, dict):
            data = {}
    except json.JSONDecodeError:
        data = {}
        logs.append(_log("supervisor", "error", f"JSON 解析失败：{raw[:200]}"))

    assignments = {
        "life": False,
        "study": False,
        "interview": True,
        "note": str(data.get("note") or "进入求职域：聚合投递与面试数据并给建议"),
        "interview_brief": str(
            data.get("interview_brief")
            or "调用 analyze_interviews，复盘投递节奏、卡点阶段与下一步补点"
        ),
    }
    logs.append(_log("supervisor", "output", {"assignments": assignments}))
    return {"assignments": assignments, "logs": logs, "tool_calls": []}


def interview_analyst(state: DayState) -> dict:
    """求职分析：调 analyze_interviews → 产出 analysis + suggestions。"""
    assignments = state.get("assignments") or {}
    brief = assignments.get("interview_brief") or "分析投递与面试数据，给出复盘与建议。"
    goal = state.get("goal") or ""

    def fallback() -> list[dict]:
        return [invoke_tool("analyze_interviews", {}, agent="interview_analyst")]

    system = (
        "你是 Interview Analyst（求职分析）。必须先调用工具 analyze_interviews 获取统计，"
        "再基于统计输出 JSON："
        '{"analysis":"近期面试复盘…","suggestions":"下一步建议…"}。'
        "不要自己编造数字；数字以工具结果为准。"
    )
    user = (
        f"中期目标：{goal}\n"
        f"Supervisor 分派：{brief}\n"
        f"分派说明：{assignments.get('note', '')}\n"
        "请先调 analyze_interviews，再写复盘与建议。"
    )

    final_text, tool_calls, logs = _run_tool_loop(
        "interview_analyst",
        system,
        user,
        ANALYZE_TOOLS,
        max_rounds=3,
        fallback=fallback,
    )

    stats: dict = {}
    for tc in tool_calls:
        if tc["tool"] == "analyze_interviews":
            try:
                stats = json.loads(tc["output"]) if isinstance(tc["output"], str) else tc["output"]
            except json.JSONDecodeError:
                stats = {}

    if not stats:
        call = invoke_tool("analyze_interviews", {}, agent="interview_analyst")
        tool_calls.append(_tc_public(call))
        logs.append(_log("interview_analyst", "tool_call", {"tool": call["tool"], "fallback": True}))
        logs.append(_log("interview_analyst", "tool_result", {"tool": call["tool"], "output": call["data"]}))
        stats = call["data"]

    analysis = ""
    suggestions = ""
    try:
        data = _extract_json(final_text) if final_text and not final_text.startswith("(") else {}
        if isinstance(data, dict):
            analysis = str(data.get("analysis") or "")
            suggestions = str(data.get("suggestions") or "")
    except (json.JSONDecodeError, TypeError, ValueError):
        analysis = ""
        suggestions = ""

    if not analysis:
        analysis = (
            f"共投递 {stats.get('total', 0)} 家，进行中 {stats.get('active', 0)}；"
            f"近 7 天 {stats.get('recent_7d', 0)} / 近 30 天 {stats.get('recent_30d', 0)}；"
            f"进入面试阶段占比 {float(stats.get('advance_rate') or 0):.0%}。"
            f"阶段分布：{json.dumps(stats.get('by_stage') or {}, ensure_ascii=False)}。"
        )
    if not suggestions:
        suggestions = (
            "对照 by_stage 找卡点阶段，优先补一面高频题；"
            "有 insights 的公司复盘面经，下一周保持投递节奏。"
        )

    logs.append(
        _log(
            "interview_analyst",
            "output",
            {"analysis": analysis, "suggestions": suggestions, "stats_total": stats.get("total")},
        )
    )
    return {
        "stats": stats,
        "analysis": analysis,
        "suggestions": suggestions,
        "tool_calls": tool_calls,
        "logs": logs,
    }


def life_agent(state: DayState) -> dict:
    """并行：LLM + 工具环读日历/面试，产出 life 任务。"""
    date = state["date"]
    assignments = state.get("assignments") or {}
    brief = assignments.get("life_brief") or "读取生活上下文与面试日程，产出固定安排任务。"

    system = (
        "你是 Life Agent（日历/面试）。必须调用工具 read_life_context 与 list_interviews，"
        "再根据结果用 JSON 总结：{\"life_titles\":[...]}。不要编造日程。"
    )
    user = f"日期：{date}\nSupervisor 分派：{brief}\n请先调工具再总结。"

    def fallback() -> list[dict]:
        return [
            invoke_tool("read_life_context", {}, agent="life_agent"),
            invoke_tool("list_interviews", {"date": date}, agent="life_agent"),
        ]

    _, tool_calls, logs = _run_tool_loop(
        "life_agent", system, user, LIFE_TOOLS, max_rounds=3, fallback=fallback
    )

    # 从 tool_calls 取最新结果；若仍空再兜底读文件
    context: dict = {"meetings": [], "blocks": [], "notes": []}
    interviews: list = []
    got_life = False
    got_iv = False
    for tc in tool_calls:
        if tc["tool"] == "read_life_context":
            try:
                context = json.loads(tc["output"]) if isinstance(tc["output"], str) else tc["output"]
                got_life = True
            except json.JSONDecodeError:
                pass
        if tc["tool"] == "list_interviews":
            try:
                payload = json.loads(tc["output"]) if isinstance(tc["output"], str) else tc["output"]
                interviews = payload.get("interviews") if isinstance(payload, dict) else payload
                interviews = interviews or []
                got_iv = True
            except json.JSONDecodeError:
                pass

    if not got_life:
        call = invoke_tool("read_life_context", {}, agent="life_agent")
        tool_calls.append(_tc_public(call))
        logs.append(_log("life_agent", "tool_call", {"tool": call["tool"], "fallback": True}))
        logs.append(_log("life_agent", "tool_result", {"tool": call["tool"], "output": call["data"]}))
        context = call["data"]
    if not got_iv:
        call = invoke_tool("list_interviews", {"date": date}, agent="life_agent")
        tool_calls.append(_tc_public(call))
        logs.append(_log("life_agent", "tool_call", {"tool": call["tool"], "fallback": True}))
        logs.append(_log("life_agent", "tool_result", {"tool": call["tool"], "output": call["data"]}))
        interviews = call["data"].get("interviews") or []

    life = build_life_tasks(context, interviews)
    tool_summary = json.dumps(
        {"context": context, "interviews": interviews, "life_count": len(life)},
        ensure_ascii=False,
    )
    return {
        "life": life,
        "tool_summary": tool_summary,
        "tool_calls": tool_calls,
        "logs": logs,
    }


def study_agent(state: DayState) -> dict:
    """并行：按 supervisor 分派拆 study 任务（故意略超预算）。"""
    assignments = state.get("assignments") or {}
    brief = str(assignments.get("study_brief") or "").strip()
    note = assignments.get("note") or ""
    hours_left = float(state["hours_left"])
    # life 可能尚未汇合；用预览估占用，避免并行竞态
    peek = today_context_bundle(state["date"])
    life_hint = build_life_tasks(
        {"meetings": peek.get("meetings") or [], "blocks": [], "notes": []},
        peek.get("interviews") or [],
    )
    remaining_hint = _estimate_study_budget(hours_left, peek)
    if assignments.get("study_budget_hours") is not None:
        try:
            remaining_hint = float(assignments["study_budget_hours"])
        except (TypeError, ValueError):
            pass
    if not brief or _is_vague_study_brief(brief):
        brief = _fallback_study_brief(
            has_interview=bool(peek.get("interviews")),
            has_meeting=bool(peek.get("meetings")),
            study_budget=remaining_hint,
            kadian=_collect_kadian_hints(peek, date=state["date"]),
            goal=str(state.get("goal") or ""),
        )

    prompt = f"""你是 Study Agent。必须严格按 Supervisor 的 study_brief 拆学习任务，不要另起空泛主题。

中期目标：{state['goal']}
日期：{state['date']}
今晚总预算：{hours_left}h
Supervisor note：{note}
【必须执行的 study_brief】：{brief}
预估固定占用（勿重复安排）：{json.dumps(life_hint, ensure_ascii=False)}
可用学习预算约：{remaining_hint:.1f}h

要求：
1. 只输出 JSON：{{"tasks":[{{"title":"...","estimate_hours":1.0,"priority":1}}]}}
2. 只输出学习任务；不要会议/面试。
3. priority：1 最高；brief 里点名的卡点必须是 priority=1。
4. 任务标题要能看出在落实 study_brief（收窄学时 + 优先卡点）。
5. estimate_hours 总和必须严格大于 {remaining_hint:.1f}（演示裁剪）。
6. 不要 markdown 外解释。
"""
    logs = [_log("study_agent", "input", prompt)]
    llm = get_llm()
    resp = llm.invoke(prompt)
    raw = resp.content if isinstance(resp.content, str) else str(resp.content)
    logs.append(_log("study_agent", "output", raw))

    try:
        data = _extract_json(raw)
    except json.JSONDecodeError:
        data = {}
        logs.append(_log("study_agent", "error", f"JSON 解析失败：{raw[:200]}"))

    if isinstance(data, dict) and "tasks" in data:
        plan_raw = data["tasks"]
    elif isinstance(data, list):
        plan_raw = data
    else:
        plan_raw = []

    plan: list[dict] = []
    for i, t in enumerate(plan_raw):
        plan.append(
            {
                "title": str(t.get("title", f"学习任务{i+1}")),
                "estimate_hours": float(t.get("estimate_hours", 1.0)),
                "priority": int(t.get("priority", i + 1)),
                "type": "study",
            }
        )

    # 兜底：确保略超预算
    if not plan or sum(float(t["estimate_hours"]) for t in plan) <= remaining_hint:
        plan = [
            {"title": "读 LangGraph 状态合并与 reducer", "estimate_hours": 1.0, "priority": 1, "type": "study"},
            {"title": "手写 Supervisor 条件边小练习", "estimate_hours": 1.5, "priority": 2, "type": "study"},
            {"title": "整理 Tool Gateway 笔记", "estimate_hours": 1.0, "priority": 3, "type": "study"},
        ]

    return {"plan": plan, "logs": logs, "tool_calls": []}


def budget_agent(state: DayState) -> dict:
    """调 apply_time_budget，再写裁剪理由。"""
    hours_left = float(state["hours_left"])
    life = list(state.get("life") or [])
    study = list(state.get("plan") or [])
    assignments = state.get("assignments") or {}

    def fallback() -> list[dict]:
        return [
            invoke_tool(
                "apply_time_budget",
                {"hours_left": hours_left, "life": life, "study": study},
                agent="budget_agent",
            )
        ]

    system = (
        "你是 Budget Agent。必须调用 apply_time_budget 获取硬裁剪结果，"
        "然后用一句话说明为何砍掉某些学习任务。life 任务永不砍。"
    )
    user = (
        f"hours_left={hours_left}\n"
        f"life={json.dumps(life, ensure_ascii=False)}\n"
        f"study={json.dumps(study, ensure_ascii=False)}\n"
        f"Supervisor：{assignments.get('note', '')}\n"
        "请调用 apply_time_budget。"
    )

    final_text, tool_calls, logs = _run_tool_loop(
        "budget_agent",
        system,
        user,
        BUDGET_TOOLS,
        max_rounds=2,
        fallback=fallback,
    )

    budget_data = None
    for tc in tool_calls:
        if tc["tool"] == "apply_time_budget":
            try:
                budget_data = json.loads(tc["output"]) if isinstance(tc["output"], str) else tc["output"]
            except json.JSONDecodeError:
                budget_data = None

    if not budget_data:
        call = invoke_tool(
            "apply_time_budget",
            {"hours_left": hours_left, "life": life, "study": study},
            agent="budget_agent",
        )
        tool_calls.append(_tc_public(call))
        logs.append(_log("budget_agent", "tool_call", {"tool": call["tool"], "fallback": True}))
        logs.append(_log("budget_agent", "tool_result", {"tool": call["tool"], "output": call["data"]}))
        budget_data = call["data"]

    kept = list(budget_data.get("kept") or [])
    cut = list(budget_data.get("cut") or [])

    # LLM 理由：给 cut 项补 cut_reason（若仍是默认）
    reason = (final_text or "").strip()
    if reason and not reason.startswith("("):
        for item in cut:
            if item.get("cut_reason") in ("", "超出剩余时间"):
                item["cut_reason"] = f"超出剩余时间；{reason[:80]}"

    all_items = kept + cut
    logs.append(
        _log(
            "budget_agent",
            "output",
            {
                "remaining": budget_data.get("remaining"),
                "kept_count": len(kept),
                "cut_count": len(cut),
                "note": reason[:200],
            },
        )
    )
    return {
        "trimmed_plan": all_items,
        "life": [t for t in kept if t.get("type") == "life"],
        "tool_calls": tool_calls,
        "logs": logs,
    }


def synthesizer(state: DayState) -> dict:
    """汇总今日板，或求职分析收口报告。"""
    assignments = state.get("assignments") or {}
    if assignments.get("interview") or (state.get("mode") or "") == "analyze":
        return _synthesizer_analyze(state)

    life = [t for t in (state.get("trimmed_plan") or []) if t.get("type") == "life"]
    pending = [
        t
        for t in (state.get("trimmed_plan") or [])
        if t.get("type") != "life" and t.get("status") != "cut"
    ]
    cut = [t for t in (state.get("trimmed_plan") or []) if t.get("status") == "cut"]
    hours_left = state.get("hours_left")

    prompt = f"""你是 Synthesizer。把多 Agent 产出收成「今日板」一句话说明（中文，2–4 句）。
不要 JSON，不要 markdown。

Supervisor 分派：{json.dumps(assignments, ensure_ascii=False)}
预算：{hours_left}h
固定安排：{json.dumps(life, ensure_ascii=False)}
保留学习：{json.dumps(pending, ensure_ascii=False)}
砍掉学习：{json.dumps(cut, ensure_ascii=False)}
"""
    logs = [_log("synthesizer", "input", prompt)]
    llm = get_llm()
    resp = llm.invoke(prompt)
    summary = resp.content if isinstance(resp.content, str) else str(resp.content)
    summary = summary.strip()
    if not summary:
        life_h = sum(float(t.get("estimate_hours", 0)) for t in life)
        study_h = sum(float(t.get("estimate_hours", 0)) for t in pending)
        summary = (
            f"今日学习预算约 {max(0, float(hours_left or 0) - life_h):.1f}h；"
            f"固定安排 {len(life)} 项（{life_h:.1f}h），保留学习 {len(pending)} 项（{study_h:.1f}h），"
            f"裁剪 {len(cut)} 项。{assignments.get('note', '')}"
        )
    logs.append(_log("synthesizer", "output", summary))
    return {"summary": summary, "logs": logs, "tool_calls": []}


def _synthesizer_analyze(state: DayState) -> dict:
    """求职域：把 stats/analysis/suggestions 收成可读报告。"""
    assignments = state.get("assignments") or {}
    stats = state.get("stats") or {}
    analysis = state.get("analysis") or ""
    suggestions = state.get("suggestions") or ""

    prompt = f"""你是 Synthesizer（求职分析收口）。把 Interview Analyst 的产出收成可读「求职复盘报告」（中文，3–6 句）。
不要 JSON，不要 markdown。保留关键数字，语气务实。

Supervisor 分派：{json.dumps(assignments, ensure_ascii=False)}
统计：{json.dumps(stats, ensure_ascii=False)}
复盘：{analysis}
建议：{suggestions}
"""
    logs = [_log("synthesizer", "input", prompt)]
    llm = get_llm()
    resp = llm.invoke(prompt)
    summary = resp.content if isinstance(resp.content, str) else str(resp.content)
    summary = (summary or "").strip()
    if not summary:
        summary = f"{analysis}\n建议：{suggestions}".strip()
    logs.append(_log("synthesizer", "output", summary))
    return {
        "summary": summary,
        "analysis": analysis,
        "suggestions": suggestions,
        "stats": stats,
        "logs": logs,
        "tool_calls": [],
    }


def evidence_agent(state: DayState) -> dict:
    """复盘前置：list_interviews 取 post_notes / notes。"""
    date = state["date"]

    def fallback() -> list[dict]:
        return [invoke_tool("list_interviews", {"date": date}, agent="evidence_agent")]

    system = (
        "你是 Evidence Agent。必须调用 list_interviews 获取当日面试证据。"
        "区分 notes（面试前准备）与 post_notes（面试后表现）。"
    )
    user = f"日期：{date}。请调用 list_interviews。"

    _, tool_calls, logs = _run_tool_loop(
        "evidence_agent",
        system,
        user,
        EVIDENCE_TOOLS,
        max_rounds=2,
        fallback=fallback,
    )

    interviews: list = []
    for tc in tool_calls:
        if tc["tool"] == "list_interviews":
            try:
                payload = json.loads(tc["output"]) if isinstance(tc["output"], str) else tc["output"]
                interviews = payload.get("interviews") if isinstance(payload, dict) else payload
                interviews = interviews or []
            except json.JSONDecodeError:
                pass

    if not tool_calls:
        call = invoke_tool("list_interviews", {"date": date}, agent="evidence_agent")
        tool_calls.append(_tc_public(call))
        interviews = call["data"].get("interviews") or []

    post_parts = []
    prep_parts = []
    for iv in interviews:
        company = iv.get("company", "")
        if (iv.get("post_notes") or "").strip():
            post_parts.append(f"{company}: {iv['post_notes'].strip()}")
        if (iv.get("notes") or "").strip():
            prep_parts.append(f"{company}: {iv['notes'].strip()}")

    evidence = {
        "interviews": interviews,
        "post_notes": "；".join(post_parts),
        "prep_notes": "；".join(prep_parts),
    }
    logs.append(_log("evidence_agent", "output", evidence))
    return {
        "interview_evidence": evidence,
        "tool_calls": tool_calls,
        "logs": logs,
    }


def reviewer(state: DayState) -> dict:
    """学习复盘 + 面试复盘（表现来自 post_notes/覆盖备注；仅 notes 则准备提示）。"""
    trimmed = state.get("trimmed_plan") or []
    done_ids = set(state.get("done_items") or [])
    user_override = (state.get("interview_notes") or "").strip()
    evidence = state.get("interview_evidence") or {}
    post_notes = user_override or (evidence.get("post_notes") or "").strip()
    prep_notes = (evidence.get("prep_notes") or "").strip()

    done_titles: list[str] = []
    pending_titles: list[str] = []
    cut_titles: list[str] = []
    life_titles: list[str] = []
    for t in trimmed:
        status = t.get("status", "pending")
        tid = t.get("id")
        title = t.get("title", "")
        typ = t.get("type", "study")
        if typ == "life":
            life_titles.append(title)
            continue
        if status == "cut":
            cut_titles.append(title)
        elif tid is not None and tid in done_ids:
            done_titles.append(title)
        elif status == "done":
            done_titles.append(title)
        else:
            pending_titles.append(title)

    if post_notes:
        interview_block = (
            f"面试后表现材料（用户覆盖或 post_notes）：{post_notes}\n"
            "interview_review 必须写「表现复盘」：问题清单 / 亮点 / 卡点 / 下次准备；"
            "并把要补的点写进 tomorrow。"
        )
        mode = "performance"
    elif prep_notes:
        interview_block = (
            f"仅有面试前准备信息 notes：{prep_notes}\n"
            "interview_review 只写「准备提示」，禁止写成表现复盘（不要假装知道答得怎样）。"
            "tomorrow 可写准备建议。"
        )
        mode = "prep"
    else:
        interview_block = "无面试材料：interview_review 必须输出空字符串 \"\"。"
        mode = "none"

    prompt = f"""你是自学日课复盘教练。

中期目标：{state.get('goal', '')}
今日日期：{state.get('date', '')}
剩余时间预算：{state.get('hours_left', '')}
已完成学习：{done_titles or ['（无）']}
未完成学习：{pending_titles or ['（无）']}
被砍学习：{cut_titles or ['（无）']}
固定安排：{life_titles or ['（无）']}
{interview_block}

要求：
1. 只输出 JSON：{{"review":"...","tomorrow":"...","interview_review":"..."}}
2. review：学习复盘。
3. tomorrow：明日可执行建议；有面试卡点则写入。
4. 模式={mode}。
"""
    logs = [_log("reviewer", "input", prompt)]
    llm = get_llm()
    resp = llm.invoke(prompt)
    raw = resp.content if isinstance(resp.content, str) else str(resp.content)
    logs.append(_log("reviewer", "output", raw))

    try:
        data = _extract_json(raw)
    except json.JSONDecodeError:
        data = {}
        logs.append(_log("reviewer", "error", f"JSON 解析失败：{raw[:200]}"))

    if not isinstance(data, dict):
        data = {}
    review = str(data.get("review", raw))
    tomorrow = str(data.get("tomorrow", ""))
    interview_review = str(data.get("interview_review", ""))
    if mode == "none":
        interview_review = ""

    return {
        "review": review,
        "tomorrow": tomorrow,
        "interview_review": interview_review,
        "logs": logs,
        "tool_calls": [],
    }
