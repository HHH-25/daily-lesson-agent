"""FastAPI 入口：API + 产品壳。"""
from __future__ import annotations

from datetime import date as date_cls
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.graph import run_analyze, run_plan, run_review
from app.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    ContextCreateRequest,
    GoalCreateRequest,
    GoalCreateResponse,
    InterviewCreateRequest,
    InterviewPatchRequest,
    PlanRequest,
    PlanResponse,
    ReviewRequest,
    ReviewResponse,
    TasksPatchRequest,
    TasksPatchResponse,
    ToolCallItem,
)
from app.tools import (
    DEFAULT_STAGE,
    STAGES,
    analyze_interviews,
    append_context_item,
    append_interview,
    list_all_interviews,
    normalize_stage,
    today_context_bundle,
    update_interview,
)

load_dotenv()

app = FastAPI(
    title="自我学习生活多 Agent 管理平台",
    version="0.4.0",
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(exist_ok=True)


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()


@app.post("/api/plan", response_model=PlanResponse)
def api_plan(body: PlanRequest) -> PlanResponse:
    goal = db.get_goal(body.goal_id)
    if not goal:
        raise HTTPException(404, f"goal_id={body.goal_id} not found; run scripts/seed.py")

    day = db.get_or_create_day(body.date, body.goal_id, body.hours_left)
    day_id = int(day["id"])

    result = run_plan(goal=goal["title"], date=body.date, hours_left=body.hours_left)

    plan = result.get("plan") or []
    life = result.get("life") or []
    all_trimmed = result.get("trimmed_plan") or []
    trimmed = [t for t in all_trimmed if t.get("status") != "cut" and t.get("type") != "life"]
    life_persisted = [t for t in all_trimmed if t.get("type") == "life"]
    cut = [t for t in all_trimmed if t.get("status") == "cut"]

    persisted = db.insert_tasks(day_id, all_trimmed)
    # 按顺序回填 id（all_trimmed 与 persisted 同序）；trimmed/life_persisted/cut/life 都是它的引用，自动带 id
    for t, p in zip(all_trimmed, persisted):
        t["id"] = p["id"]

    tool_calls: list[ToolCallItem] = []
    for tc in result.get("tool_calls") or []:
        tool_calls.append(
            ToolCallItem(
                tool=tc["tool"],
                input=tc.get("input"),
                output=tc.get("output", ""),
                agent=tc.get("agent") or "",
                via=tc.get("via") or "local",
            )
        )

    for log in result.get("logs") or []:
        db.add_agent_log(day_id, log["node"], log["kind"], log["content"])

    return PlanResponse(
        day_id=day_id,
        hours_left=body.hours_left,
        assignments=result.get("assignments") or {},
        summary=result.get("summary") or "",
        plan=plan,
        life=life_persisted or life,
        trimmed_plan=trimmed,
        cut=cut,
        tool_calls=tool_calls,
    )


@app.patch("/api/tasks", response_model=TasksPatchResponse)
def api_tasks(body: TasksPatchRequest) -> TasksPatchResponse:
    updated = db.mark_tasks_done(body.task_ids, done=body.done)
    return TasksPatchResponse(updated=updated)


@app.post("/api/review", response_model=ReviewResponse)
def api_review(body: ReviewRequest) -> ReviewResponse:
    day = db.get_day(body.day_id)
    if not day:
        raise HTTPException(404, f"day_id={body.day_id} not found")

    goal = db.get_goal(int(day["goal_id"]))
    tasks = db.list_tasks_for_day(body.day_id)
    done_items = [t["id"] for t in tasks if t["status"] == "done" and t.get("type") == "study"]
    trimmed_plan = [
        {
            "id": t["id"],
            "title": t["title"],
            "estimate_hours": t["estimate_hours"],
            "priority": t["priority"],
            "type": t.get("type", "study"),
            "status": t["status"],
            "cut_reason": t.get("cut_reason") or "",
        }
        for t in tasks
    ]

    tool_summary = ""
    for log in db.list_agent_logs(body.day_id):
        if log["kind"] == "tool_result":
            tool_summary = log["content"]
            break

    result = run_review(
        goal=goal["title"] if goal else "",
        date=day["date"],
        hours_left=float(day["hours_left"]),
        trimmed_plan=trimmed_plan,
        done_items=done_items,
        tool_summary=tool_summary,
        interview_notes=body.interview_notes or "",
    )

    review = result.get("review") or ""
    tomorrow = result.get("tomorrow") or ""
    interview_review = result.get("interview_review") or ""
    db.save_review(body.day_id, review, tomorrow, interview_review)

    review_logs = []
    for log in result.get("logs") or []:
        db.add_agent_log(body.day_id, log["node"], log["kind"], log["content"])
        if log["node"] in ("evidence_agent", "reviewer"):
            review_logs.append(log)

    return ReviewResponse(
        review=review,
        tomorrow=tomorrow,
        interview_review=interview_review,
        logs=review_logs,
    )


@app.post("/api/analyze", response_model=AnalyzeResponse)
def api_analyze(body: AnalyzeRequest) -> AnalyzeResponse:
    goal = db.get_goal(body.goal_id)
    if not goal:
        raise HTTPException(404, f"goal_id={body.goal_id} not found; run scripts/seed.py")

    today = date_cls.today().isoformat()
    result = run_analyze(goal=goal["title"], date=today)

    tool_calls: list[ToolCallItem] = []
    for tc in result.get("tool_calls") or []:
        tool_calls.append(
            ToolCallItem(
                tool=tc["tool"],
                input=tc.get("input"),
                output=tc.get("output", ""),
                agent=tc.get("agent") or "",
                via=tc.get("via") or "local",
            )
        )

    # 确保有今日 day，才能把求职分析轨迹落 agent_logs（day_id NOT NULL）
    day = db.get_day_by_date(today)
    if not day:
        day = db.get_or_create_day(today, body.goal_id, 0.0)
    for log in result.get("logs") or []:
        db.add_agent_log(int(day["id"]), log["node"], log["kind"], log["content"])

    stats = result.get("stats") or {}
    if not stats:
        stats = analyze_interviews()

    return AnalyzeResponse(
        stats=stats,
        analysis=result.get("analysis") or "",
        suggestions=result.get("suggestions") or "",
        summary=result.get("summary") or "",
        assignments=result.get("assignments") or {},
        tool_calls=tool_calls,
    )


@app.post("/api/goals", response_model=GoalCreateResponse)
def api_create_goal(body: GoalCreateRequest) -> GoalCreateResponse:
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(400, "title 不能为空")
    gid = db.create_goal(title=title, description=body.description or "", status="active")
    goal = db.get_goal(gid)
    assert goal is not None
    return GoalCreateResponse(id=int(goal["id"]), title=goal["title"], status=goal["status"])


@app.post("/api/interviews")
def api_create_interview(body: InterviewCreateRequest) -> dict:
    stage = normalize_stage(body.stage) if body.stage else DEFAULT_STAGE
    if body.stage and body.stage.strip() and body.stage.strip() not in STAGES:
        # 非法 stage 仍默认，但保留请求可被 normalize
        stage = normalize_stage(body.stage)
    item = {
        "date": body.date,
        "time": body.time,
        "company": body.company,
        "role": body.role or "",
        "duration_hours": float(body.duration_hours),
        "notes": body.notes or "",
        "post_notes": body.post_notes or "",
        "stage": stage or DEFAULT_STAGE,
        "insights": body.insights or "",
    }
    return append_interview(item)


@app.patch("/api/interviews")
def api_patch_interview(body: InterviewPatchRequest) -> dict:
    patch: dict = {}
    if body.stage is not None:
        if body.stage.strip() and body.stage.strip() not in STAGES:
            raise HTTPException(400, f"stage 须为其一：{', '.join(STAGES)}")
        patch["stage"] = normalize_stage(body.stage)
    if body.insights is not None:
        patch["insights"] = body.insights
    if body.role is not None:
        patch["role"] = body.role
    if body.notes is not None:
        patch["notes"] = body.notes
    if body.post_notes is not None:
        patch["post_notes"] = body.post_notes
    if body.duration_hours is not None:
        patch["duration_hours"] = float(body.duration_hours)
    if not patch:
        raise HTTPException(400, "无更新字段")
    try:
        return update_interview(body.date, body.time, body.company, patch)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/context")
def api_create_context(body: ContextCreateRequest) -> dict:
    if body.type == "meeting":
        payload = {
            "time": body.time,
            "title": body.title,
            "duration_hours": float(body.duration_hours or 1.0),
        }
    elif body.type == "block":
        payload = {"time": body.time, "note": body.note}
    else:
        payload = str(body.text).strip()
    return append_context_item(body.type, payload)


@app.get("/api/journal")
def api_journal() -> dict:
    goals = [
        {
            "id": g["id"],
            "title": g["title"],
            "description": g.get("description") or "",
            "status": g["status"],
        }
        for g in db.list_goals()
    ]
    days = db.list_days_with_details()
    today = date_cls.today().isoformat()
    if days:
        today = days[0]["date"]
    context = today_context_bundle(today)
    interviews = list_all_interviews()
    stats = analyze_interviews()
    return {
        "goals": goals,
        "days": days,
        "context": context,
        "interviews": interviews,
        "interview_stats": stats,
    }


@app.get("/", response_model=None, response_class=HTMLResponse)
def index() -> HTMLResponse:
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
