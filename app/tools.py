"""Tool Gateway：StructuredTool + invoke_tool（MCP 形态本地源）。"""
from __future__ import annotations

import json
import time
from collections import Counter
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
CONTEXT_PATH = ROOT / "data" / "context.json"
INTERVIEWS_PATH = ROOT / "data" / "interviews.json"

STAGES = ("网申", "笔试", "一面", "二面", "HR面", "谈薪", "Offer", "已拒")
DEFAULT_STAGE = "一面"
TERMINAL_STAGES = frozenset({"Offer", "已拒"})
ADVANCE_STAGES = frozenset({"一面", "二面", "HR面", "谈薪", "Offer"})

READ_LIFE_SPEC: dict[str, Any] = {
    "name": "read_life_context",
    "description": "读取本地生活上下文 data/context.json（会议/占用/备注）。",
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}

LIST_INTERVIEWS_SPEC: dict[str, Any] = {
    "name": "list_interviews",
    "description": "读取 data/interviews.json 并按日期过滤当日面试。",
    "input_schema": {
        "type": "object",
        "properties": {"date": {"type": "string", "description": "YYYY-MM-DD"}},
        "required": ["date"],
    },
}

APPLY_BUDGET_SPEC: dict[str, Any] = {
    "name": "apply_time_budget",
    "description": "按剩余小时硬裁剪：life 永不砍，study 按 priority 升序裁剪。",
    "input_schema": {
        "type": "object",
        "properties": {
            "hours_left": {"type": "number"},
            "life": {"type": "array"},
            "study": {"type": "array"},
        },
        "required": ["hours_left", "life", "study"],
    },
}

ANALYZE_INTERVIEWS_SPEC: dict[str, Any] = {
    "name": "analyze_interviews",
    "description": "纯规则聚合 interviews.json：总量、按阶段、进行中、近7/30天、推进率、热门岗位。",
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}


def normalize_stage(stage: Optional[str]) -> str:
    s = (stage or "").strip()
    if s in STAGES:
        return s
    return DEFAULT_STAGE


def normalize_interview(item: dict[str, Any]) -> dict[str, Any]:
    """补齐 stage / insights，不改写磁盘（调用方决定是否落盘）。"""
    out = dict(item)
    out["stage"] = normalize_stage(out.get("stage"))
    if "insights" not in out or out.get("insights") is None:
        out["insights"] = ""
    else:
        out["insights"] = str(out.get("insights") or "")
    return out


def read_life_context() -> dict[str, Any]:
    if not CONTEXT_PATH.exists():
        return {"meetings": [], "blocks": [], "notes": []}
    data = json.loads(CONTEXT_PATH.read_text(encoding="utf-8"))
    return {
        "meetings": data.get("meetings") or [],
        "blocks": data.get("blocks") or [],
        "notes": data.get("notes") or [],
    }


def _load_interviews_raw() -> list[dict]:
    if not INTERVIEWS_PATH.exists():
        return []
    data = json.loads(INTERVIEWS_PATH.read_text(encoding="utf-8"))
    return list(data.get("interviews") or [])


def _save_interviews(items: list[dict]) -> None:
    INTERVIEWS_PATH.parent.mkdir(parents=True, exist_ok=True)
    INTERVIEWS_PATH.write_text(
        json.dumps({"interviews": items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_all_interviews() -> list[dict]:
    return [normalize_interview(i) for i in _load_interviews_raw()]


def list_interviews(date: str) -> list[dict]:
    return [i for i in list_all_interviews() if str(i.get("date", "")) == date]


def migrate_interviews_file(
    *,
    default_stage: str = DEFAULT_STAGE,
    drop_companies: Optional[set[str]] = None,
) -> dict[str, Any]:
    """为缺 stage 的条目补默认值；可选清理演示垃圾公司。返回变更摘要。"""
    drop_companies = drop_companies or set()
    if not INTERVIEWS_PATH.exists():
        return {"migrated": 0, "dropped": 0, "total": 0}
    raw = _load_interviews_raw()
    migrated = 0
    dropped = 0
    kept: list[dict] = []
    for item in raw:
        company = str(item.get("company") or "")
        if company in drop_companies:
            dropped += 1
            continue
        out = dict(item)
        if not (out.get("stage") or "").strip():
            out["stage"] = default_stage
            migrated += 1
        else:
            out["stage"] = normalize_stage(out.get("stage"))
        if "insights" not in out or out.get("insights") is None:
            out["insights"] = ""
        kept.append(out)
    _save_interviews(kept)
    return {"migrated": migrated, "dropped": dropped, "total": len(kept)}


def analyze_interviews(as_of: Optional[str] = None) -> dict[str, Any]:
    """纯规则聚合：total / by_stage / active / recent_7d·30d / advance_rate / top_roles。"""
    items = list_all_interviews()
    if as_of:
        today = datetime.strptime(as_of, "%Y-%m-%d").date()
    else:
        today = date_cls.today()

    by_stage = {s: 0 for s in STAGES}
    role_counter: Counter[str] = Counter()
    recent_7d = 0
    recent_30d = 0
    advance = 0
    active = 0

    for iv in items:
        stage = normalize_stage(iv.get("stage"))
        by_stage[stage] = by_stage.get(stage, 0) + 1
        if stage not in TERMINAL_STAGES:
            active += 1
        if stage in ADVANCE_STAGES:
            advance += 1
        role = str(iv.get("role") or "").strip() or "(未填岗位)"
        role_counter[role] += 1
        try:
            d = datetime.strptime(str(iv.get("date", "")), "%Y-%m-%d").date()
        except ValueError:
            continue
        delta = (today - d).days
        if 0 <= delta <= 7:
            recent_7d += 1
        if 0 <= delta <= 30:
            recent_30d += 1

    total = len(items)
    advance_rate = round(advance / total, 4) if total else 0.0
    top_roles = [
        {"role": role, "count": count}
        for role, count in role_counter.most_common(5)
    ]
    return {
        "total": total,
        "by_stage": by_stage,
        "active": active,
        "recent_7d": recent_7d,
        "recent_30d": recent_30d,
        "advance_rate": advance_rate,
        "top_roles": top_roles,
    }


def apply_time_budget(
    hours_left: float, life: list[dict], study: list[dict]
) -> dict[str, Any]:
    """硬裁剪：life 永不砍；study 按 priority 升序累加，超预算标 cut。"""
    hours_left = float(hours_left)
    life_out: list[dict] = []
    for t in life or []:
        life_out.append(
            {
                "title": t.get("title", "生活占用"),
                "estimate_hours": float(t.get("estimate_hours", 1.0)),
                "priority": 0,
                "type": "life",
                "status": "pending",
                "cut_reason": "",
            }
        )
    life_sum = sum(float(t["estimate_hours"]) for t in life_out)
    remaining = hours_left - life_sum

    sorted_study = sorted(study or [], key=lambda x: int(x.get("priority", 99)))
    kept: list[dict] = []
    cut: list[dict] = []
    used = 0.0
    cutting = remaining <= 0

    for t in sorted_study:
        est = float(t.get("estimate_hours", 0))
        item = {
            "title": t.get("title", "学习任务"),
            "estimate_hours": est,
            "priority": int(t.get("priority", 3)),
            "type": "study",
        }
        if cutting or used + est > remaining + 1e-9:
            cutting = True
            item["status"] = "cut"
            item["cut_reason"] = "超出剩余时间"
            cut.append(item)
        else:
            used += est
            item["status"] = "pending"
            item["cut_reason"] = ""
            kept.append(item)

    return {
        "kept": life_out + kept,
        "cut": cut,
        "remaining": remaining,
        "life_sum": life_sum,
        "study_used": used,
    }


class _ListInterviewsArgs(BaseModel):
    date: str = Field(description="YYYY-MM-DD")


class _ApplyBudgetArgs(BaseModel):
    hours_left: float = Field(description="今晚剩余小时预算")
    life: list[dict] = Field(default_factory=list, description="生活占用任务")
    study: list[dict] = Field(default_factory=list, description="学习任务")


def _tool_read_life_context() -> str:
    return json.dumps(read_life_context(), ensure_ascii=False)


def _tool_list_interviews(date: str) -> str:
    return json.dumps({"interviews": list_interviews(date)}, ensure_ascii=False)


def _tool_apply_time_budget(
    hours_left: float, life: list[dict], study: list[dict]
) -> str:
    return json.dumps(
        apply_time_budget(hours_left, life, study), ensure_ascii=False
    )


def _tool_analyze_interviews() -> str:
    return json.dumps(analyze_interviews(), ensure_ascii=False)


READ_LIFE_TOOL = StructuredTool.from_function(
    func=_tool_read_life_context,
    name="read_life_context",
    description=READ_LIFE_SPEC["description"],
)

LIST_INTERVIEWS_TOOL = StructuredTool.from_function(
    func=_tool_list_interviews,
    name="list_interviews",
    description=LIST_INTERVIEWS_SPEC["description"],
    args_schema=_ListInterviewsArgs,
)

APPLY_BUDGET_TOOL = StructuredTool.from_function(
    func=_tool_apply_time_budget,
    name="apply_time_budget",
    description=APPLY_BUDGET_SPEC["description"],
    args_schema=_ApplyBudgetArgs,
)

ANALYZE_INTERVIEWS_TOOL = StructuredTool.from_function(
    func=_tool_analyze_interviews,
    name="analyze_interviews",
    description=ANALYZE_INTERVIEWS_SPEC["description"],
)

LIFE_TOOLS = [READ_LIFE_TOOL, LIST_INTERVIEWS_TOOL]
BUDGET_TOOLS = [APPLY_BUDGET_TOOL]
EVIDENCE_TOOLS = [LIST_INTERVIEWS_TOOL]
ANALYZE_TOOLS = [ANALYZE_INTERVIEWS_TOOL]

TOOL_REGISTRY: dict[str, Any] = {
    "read_life_context": lambda args: read_life_context(),
    "list_interviews": lambda args: {"interviews": list_interviews(str(args.get("date", "")))},
    "apply_time_budget": lambda args: apply_time_budget(
        float(args.get("hours_left", 0)),
        list(args.get("life") or []),
        list(args.get("study") or []),
    ),
    "analyze_interviews": lambda args: analyze_interviews(
        as_of=str(args["as_of"]) if args and args.get("as_of") else None
    ),
}


def invoke_tool(
    name: str,
    args: Optional[dict] = None,
    *,
    agent: str = "",
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    """统一 Tool Gateway：审计字段 + 超时占位。"""
    args = args or {}
    started = time.monotonic()
    if name not in TOOL_REGISTRY:
        raise ValueError(f"unknown tool: {name}")
    # 本地同步工具；timeout_s 为 Gateway 形态占位
    _ = timeout_s
    data = TOOL_REGISTRY[name](args)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    output = json.dumps(data, ensure_ascii=False)
    return {
        "tool": name,
        "input": args if args else None,
        "output": output,
        "data": data,
        "agent": agent,
        "elapsed_ms": elapsed_ms,
    }


def build_life_tasks(context: dict, interviews: list[dict]) -> list[dict]:
    """从工具结果构造 life 任务。"""
    life: list[dict] = []
    for m in context.get("meetings") or []:
        title = f"{m.get('time', '')} {m.get('title', '会议')}".strip()
        life.append(
            {
                "title": title,
                "estimate_hours": float(m.get("duration_hours", 1.0)),
                "priority": 0,
                "type": "life",
                "status": "pending",
            }
        )
    for iv in interviews:
        title = (
            f"{iv.get('time', '')} 面试·{iv.get('company', '')}"
            f"（{iv.get('role', '')}）"
        ).strip()
        life.append(
            {
                "title": title,
                "estimate_hours": float(iv.get("duration_hours", 1.0)),
                "priority": 0,
                "type": "life",
                "status": "pending",
            }
        )
    return life


def today_context_bundle(date: str) -> dict[str, Any]:
    ctx = read_life_context()
    interviews = list_interviews(date)
    return {
        "meetings": ctx.get("meetings") or [],
        "blocks": ctx.get("blocks") or [],
        "notes": ctx.get("notes") or [],
        "interviews": interviews,
    }


def append_interview(item: dict[str, Any]) -> dict[str, Any]:
    """按 date+time+company upsert 一条面试到 interviews.json，返回落盘项。"""
    item = normalize_interview(item)
    items = _load_interviews_raw()
    key = (str(item.get("date")), str(item.get("time")), str(item.get("company")))
    updated = False
    for i, existing in enumerate(items):
        ek = (
            str(existing.get("date")),
            str(existing.get("time")),
            str(existing.get("company")),
        )
        if ek == key:
            merged = dict(existing)
            merged.update(item)
            items[i] = normalize_interview(merged)
            item = items[i]
            updated = True
            break
    if not updated:
        items.append(item)
    _save_interviews(items)
    return item


def update_interview(
    date: str,
    time: str,
    company: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """按 date+time+company 更新字段（尤其 stage / insights）。"""
    items = _load_interviews_raw()
    key = (str(date), str(time), str(company))
    for i, existing in enumerate(items):
        ek = (
            str(existing.get("date")),
            str(existing.get("time")),
            str(existing.get("company")),
        )
        if ek == key:
            merged = dict(existing)
            for k, v in patch.items():
                if v is not None:
                    merged[k] = v
            merged = normalize_interview(merged)
            items[i] = merged
            _save_interviews(items)
            return merged
    raise KeyError(f"interview not found: {date} {time} {company}")


def append_context_item(kind: str, payload: Any) -> dict[str, Any]:
    """按 type 追加进 context.json：meeting|block 推对象，note 推字符串。"""
    CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONTEXT_PATH.exists():
        data = json.loads(CONTEXT_PATH.read_text(encoding="utf-8"))
    else:
        data = {"meetings": [], "blocks": [], "notes": []}
    meetings = list(data.get("meetings") or [])
    blocks = list(data.get("blocks") or [])
    notes = list(data.get("notes") or [])
    if kind == "meeting":
        meetings.append(payload)
    elif kind == "block":
        blocks.append(payload)
    elif kind == "note":
        notes.append(payload)  # 字符串，非对象
    else:
        raise ValueError(f"unknown context type: {kind}")
    data = {"meetings": meetings, "blocks": blocks, "notes": notes}
    CONTEXT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"type": kind, "item": payload}


def peek_has_life_load(date: str) -> bool:
    """系统预览：当日是否有会议/面试/占用（不经 Agent 工具环）。"""
    bundle = today_context_bundle(date)
    return bool(
        bundle.get("meetings") or bundle.get("interviews") or bundle.get("blocks")
    )
