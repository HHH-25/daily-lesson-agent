"""真实 smoke：断言 Supervisor 编排字段，写入 traces/sample_run.md。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.main import app


def main() -> None:
    client = TestClient(app)
    fails: list[str] = []

    j0 = client.get("/api/journal").json()
    if not j0.get("goals"):
        raise SystemExit("no goals; run python scripts/seed.py first")
    goal_id = j0["goals"][0]["id"]

    plan = client.post(
        "/api/plan",
        json={"goal_id": goal_id, "date": "2026-09-30", "hours_left": 3.5},
    ).json()

    assignments = plan.get("assignments") or {}
    if not assignments:
        fails.append("assignments empty")
    if not assignments.get("life"):
        fails.append("assignments.life should be true (meeting+interview day)")

    if not (plan.get("summary") or "").strip():
        fails.append("summary empty")

    if not plan.get("life"):
        fails.append("plan.life empty")
    if not plan.get("cut"):
        fails.append("plan.cut empty (budget should cut study)")

    tool_calls = plan.get("tool_calls") or []
    tools = {t["tool"] for t in tool_calls}
    life_tools = [
        t
        for t in tool_calls
        if t["tool"] in ("read_life_context", "list_interviews")
        and t.get("agent") == "life_agent"
    ]
    if not life_tools:
        fails.append(f"no life_agent tool_calls; tools={tools}")
    if "apply_time_budget" not in tools:
        fails.append(f"apply_time_budget missing; tools={tools}")
    budget_from = [t for t in tool_calls if t["tool"] == "apply_time_budget"]
    if budget_from and budget_from[0].get("agent") and budget_from[0]["agent"] != "budget_agent":
        fails.append(f"apply_time_budget agent={budget_from[0].get('agent')}")

    day_id = plan["day_id"]
    study_pending = [
        t["id"]
        for t in (plan.get("trimmed_plan") or [])
        if t.get("id") and t.get("type") != "life"
    ][:2]
    patch = client.patch(
        "/api/tasks",
        json={"task_ids": study_pending or [1], "done": True},
    ).json()

    # 空覆盖备注：应仍能从 JSON post_notes 产出 interview_review
    review_empty = client.post(
        "/api/review",
        json={"day_id": day_id, "interview_notes": ""},
    ).json()
    if not (review_empty.get("interview_review") or "").strip():
        fails.append("interview_review empty when interview_notes blank (expect post_notes)")
    if not (review_empty.get("tomorrow") or "").strip():
        fails.append("tomorrow empty on empty-notes review")

    # 带覆盖备注再跑一次（演示用户覆盖路径）
    review = client.post(
        "/api/review",
        json={
            "day_id": day_id,
            "interview_notes": "问了 LangGraph 状态合并，答得含糊",
        },
    ).json()
    if not review.get("interview_review"):
        fails.append("interview_review empty with override notes")

    journal = client.get("/api/journal").json()
    if not journal.get("context"):
        fails.append("journal.context empty")
    ctx = journal["context"]
    if not (ctx.get("meetings") or ctx.get("interviews")):
        fails.append("journal.context has no meetings/interviews")

    out = ROOT / "traces" / "sample_run.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    status = "PASS" if not fails else "FAIL"
    body = [
        "# 真实运行记录（v3 Supervisor）",
        "",
        f"**断言结果**：{status}",
        "",
        "## 失败项" if fails else "## 断言点",
        "",
    ]
    if fails:
        body.extend(f"- {f}" for f in fails)
    else:
        body.extend(
            [
                "- assignments 非空且 life=true",
                "- summary 非空",
                "- tool_calls 含 life_agent 的 read_life_context/list_interviews",
                "- tool_calls 含 apply_time_budget",
                "- plan.cut 非空；life 非空",
                "- 空 interview_notes 仍从 post_notes 产出 interview_review",
                "- journal.context 非空",
            ]
        )
    body.extend(
        [
            "",
            "## POST /api/plan",
            "",
            "```json",
            json.dumps(
                {
                    "day_id": plan.get("day_id"),
                    "hours_left": plan.get("hours_left"),
                    "assignments": plan.get("assignments"),
                    "summary": plan.get("summary"),
                    "life": plan.get("life"),
                    "trimmed_plan": plan.get("trimmed_plan"),
                    "cut": plan.get("cut"),
                    "tool_calls": plan.get("tool_calls"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            "```",
            "",
            "## PATCH /api/tasks",
            "",
            "```json",
            json.dumps(patch, ensure_ascii=False, indent=2),
            "```",
            "",
            "## POST /api/review（空备注）",
            "",
            "```json",
            json.dumps(review_empty, ensure_ascii=False, indent=2),
            "```",
            "",
            "## POST /api/review（覆盖备注）",
            "",
            "```json",
            json.dumps(review, ensure_ascii=False, indent=2),
            "```",
            "",
            "## GET /api/journal（摘要）",
            "",
            "```json",
            json.dumps(
                {
                    "goals": journal.get("goals"),
                    "context": journal.get("context"),
                    "days_count": len(journal.get("days") or []),
                },
                ensure_ascii=False,
                indent=2,
            ),
            "```",
            "",
        ]
    )
    out.write_text("\n".join(body), encoding="utf-8")
    print(status, "->", out)
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
