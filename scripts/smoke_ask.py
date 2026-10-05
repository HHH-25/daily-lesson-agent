"""面经问答 smoke：分类 + 关键词兜底；不强制加载 embedding。有 Key 才打 /api/ask。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RAG_SKIP_EMBED"] = "1"

from dotenv import load_dotenv
from fastapi.testclient import TestClient

load_dotenv(ROOT / ".env")

from app.main import app
from app.rag import classify_ask_mode, keyword_retrieve, list_recent_chunks


def main() -> None:
    fails: list[str] = []

    if classify_ask_mode("总结最近面试") != "time":
        fails.append("time query not classified as time")
    if classify_ask_mode("哪些公司问了状态合并") != "semantic":
        fails.append("semantic query misclassified")

    recent = list_recent_chunks(5)
    if "chunks" not in recent:
        fails.append("list_recent_chunks missing chunks")

    kw = keyword_retrieve("状态合并", k=5)
    if kw.get("via") != "keyword":
        fails.append(f"keyword_retrieve via={kw.get('via')}")
    if not isinstance(kw.get("chunks"), list):
        fails.append("keyword chunks not list")

    client = TestClient(app)
    empty = client.post("/api/ask", json={"query": "  "})
    if empty.status_code != 400:
        fails.append(f"empty query expected 400, got {empty.status_code}")

    hist = client.get("/api/ask/history")
    if hist.status_code != 200 or "items" not in hist.json():
        fails.append("/api/ask/history shape")

    patch = client.patch(
        "/api/interviews",
        json={
            "date": "2026-09-30",
            "time": "14:00",
            "company": "讴谱科技",
            "post_notes": "问了状态合并，我答得含糊；reducer 举例没讲清。",
            "insights": "这家偏好问状态管理与工具调用",
        },
    )
    if patch.status_code not in (200, 404):
        fails.append(f"PATCH notes unexpected {patch.status_code} {patch.text[:200]}")

    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if key:
        r = client.post("/api/ask", json={"query": "哪些公司问了状态合并"})
        if r.status_code != 200:
            fails.append(f"/api/ask {r.status_code} {r.text[:300]}")
        else:
            body = r.json()
            if not (body.get("answer") or "").strip():
                fails.append("/api/ask empty answer")
            if body.get("mode") not in ("semantic", "time"):
                fails.append(f"bad mode {body.get('mode')}")
        r2 = client.post("/api/ask", json={"query": "总结最近面试"})
        if r2.status_code != 200:
            fails.append(f"time /api/ask {r2.status_code}")
        elif r2.json().get("mode") != "time":
            fails.append(f"time ask mode={r2.json().get('mode')}")

    # 主链路 journal 仍可用
    j = client.get("/api/journal")
    if j.status_code != 200 or "goals" not in j.json():
        fails.append("journal broken after ask smoke")

    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        raise SystemExit(1)
    print("PASS smoke_ask")
    if not key:
        print("(skipped /api/ask LLM; no DEEPSEEK_API_KEY)")


if __name__ == "__main__":
    main()
