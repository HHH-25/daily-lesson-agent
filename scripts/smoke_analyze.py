"""求职分析 smoke：工具单元 + /api/analyze（有 Key 才跑 LLM）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from fastapi.testclient import TestClient

load_dotenv(ROOT / ".env")

from app.main import app
from app.tools import STAGES, analyze_interviews, list_all_interviews, migrate_interviews_file


def main() -> None:
    fails: list[str] = []

    # 迁移缺 stage + 清 SmokeCo（幂等）
    migrate_interviews_file(default_stage="一面", drop_companies={"SmokeCo"})

    stats = analyze_interviews(as_of="2026-09-30")
    required = {
        "total",
        "by_stage",
        "active",
        "recent_7d",
        "recent_30d",
        "advance_rate",
        "top_roles",
    }
    if not required.issubset(stats.keys()):
        fails.append(f"analyze_interviews missing keys: {required - set(stats.keys())}")
    if not isinstance(stats["by_stage"], dict):
        fails.append("by_stage not dict")
    for s in STAGES:
        if s not in stats["by_stage"]:
            fails.append(f"by_stage missing {s}")
    if stats["total"] < 1:
        fails.append("total < 1 after seed/migrate")
    if not (0 <= float(stats["advance_rate"]) <= 1):
        fails.append(f"advance_rate out of range: {stats['advance_rate']}")
    for iv in list_all_interviews():
        if not iv.get("stage"):
            fails.append(f"interview missing stage: {iv.get('company')}")
        if iv.get("company") == "SmokeCo":
            fails.append("SmokeCo still present")

    client = TestClient(app)

    # 端点形状：缺 goal → 404（无需 LLM）
    r404 = client.post("/api/analyze", json={"goal_id": 999999})
    if r404.status_code != 404:
        fails.append(f"/api/analyze missing goal expected 404, got {r404.status_code}")

    j = client.get("/api/journal").json()
    if "interviews" not in j or "interview_stats" not in j:
        fails.append("journal missing interviews / interview_stats")
    if not j.get("goals"):
        fails.append("no goals; run seed.py")

    # upsert + PATCH stage（无需 LLM）
    upsert = client.post(
        "/api/interviews",
        json={
            "date": "2026-10-03",
            "time": "10:00",
            "company": "AnalyzeSmoke",
            "role": "SWE",
            "duration_hours": 1.0,
            "stage": "网申",
            "insights": "smoke",
        },
    )
    if upsert.status_code != 200:
        fails.append(f"POST interviews failed: {upsert.status_code} {upsert.text}")
    else:
        body = upsert.json()
        if body.get("stage") != "网申":
            fails.append(f"upsert stage={body.get('stage')}")
        patched = client.patch(
            "/api/interviews",
            json={
                "date": "2026-10-03",
                "time": "10:00",
                "company": "AnalyzeSmoke",
                "stage": "一面",
            },
        )
        if patched.status_code != 200 or patched.json().get("stage") != "一面":
            fails.append(f"PATCH stage failed: {patched.status_code} {patched.text}")
        # 清掉 smoke 面试，保持演示干净
        from app.tools import _load_interviews_raw, _save_interviews

        items = [
            i
            for i in _load_interviews_raw()
            if i.get("company") != "AnalyzeSmoke"
        ]
        _save_interviews(items)

    key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    analyze_result = None
    if key and j.get("goals"):
        goal_id = j["goals"][0]["id"]
        res = client.post("/api/analyze", json={"goal_id": goal_id})
        if res.status_code != 200:
            fails.append(f"/api/analyze LLM call failed: {res.status_code} {res.text[:300]}")
        else:
            analyze_result = res.json()
            for field in ("stats", "analysis", "suggestions", "tool_calls"):
                if field not in analyze_result:
                    fails.append(f"analyze response missing {field}")
            tools = {t.get("tool") for t in (analyze_result.get("tool_calls") or [])}
            if "analyze_interviews" not in tools:
                fails.append(f"tool_calls missing analyze_interviews: {tools}")
            asg = analyze_result.get("assignments") or {}
            if not asg.get("interview"):
                fails.append(f"assignments.interview not true: {asg}")
            if not (analyze_result.get("analysis") or "").strip():
                fails.append("analysis empty")
    else:
        print("SKIP /api/analyze LLM (no DEEPSEEK_API_KEY)")

    print("=== analyze_interviews stats ===")
    print(stats)
    if analyze_result:
        print("=== /api/analyze ===")
        print(
            {
                "assignments": analyze_result.get("assignments"),
                "stats_total": (analyze_result.get("stats") or {}).get("total"),
                "analysis_len": len(analyze_result.get("analysis") or ""),
                "suggestions_len": len(analyze_result.get("suggestions") or ""),
                "tools": [t.get("tool") for t in (analyze_result.get("tool_calls") or [])],
            }
        )

    if fails:
        print("FAIL:")
        for f in fails:
            print(" -", f)
        raise SystemExit(1)
    print("PASS smoke_analyze")


if __name__ == "__main__":
    main()
