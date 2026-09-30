"""SQLite 建表 + CRUD。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "daily.db"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: Optional[sqlite3.Connection] = None) -> None:
    own = conn is None
    if own:
        conn = get_conn()
    assert conn is not None
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS goals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          title TEXT NOT NULL,
          description TEXT DEFAULT '',
          status TEXT NOT NULL DEFAULT 'active',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS days (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          date TEXT NOT NULL UNIQUE,
          goal_id INTEGER NOT NULL REFERENCES goals(id),
          hours_left REAL NOT NULL,
          status TEXT NOT NULL DEFAULT 'planned',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tasks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          day_id INTEGER NOT NULL REFERENCES days(id),
          title TEXT NOT NULL,
          estimate_hours REAL NOT NULL,
          priority INTEGER NOT NULL DEFAULT 3,
          type TEXT NOT NULL DEFAULT 'study',
          status TEXT NOT NULL DEFAULT 'pending',
          order_index INTEGER NOT NULL DEFAULT 0,
          cut_reason TEXT DEFAULT '',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS reviews (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          day_id INTEGER NOT NULL REFERENCES days(id),
          content TEXT NOT NULL,
          tomorrow TEXT NOT NULL,
          interview_review TEXT DEFAULT '',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS agent_logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          day_id INTEGER NOT NULL REFERENCES days(id),
          node TEXT NOT NULL,
          kind TEXT NOT NULL,
          content TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    if own:
        conn.close()


def create_goal(title: str, description: str = "", status: str = "active") -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO goals (title, description, status) VALUES (?, ?, ?)",
            (title, description, status),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_goal(goal_id: int) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_goals() -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, title, description, status, created_at FROM goals ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_or_create_day(date: str, goal_id: int, hours_left: float) -> dict:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM days WHERE date = ?", (date,)).fetchone()
        if row:
            conn.execute(
                "UPDATE days SET goal_id = ?, hours_left = ?, status = 'planned' WHERE id = ?",
                (goal_id, hours_left, row["id"]),
            )
            conn.execute("DELETE FROM tasks WHERE day_id = ?", (row["id"],))
            conn.execute("DELETE FROM reviews WHERE day_id = ?", (row["id"],))
            conn.execute("DELETE FROM agent_logs WHERE day_id = ?", (row["id"],))
            conn.commit()
            row = conn.execute("SELECT * FROM days WHERE id = ?", (row["id"],)).fetchone()
            return dict(row)
        cur = conn.execute(
            "INSERT INTO days (date, goal_id, hours_left) VALUES (?, ?, ?)",
            (date, goal_id, hours_left),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM days WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def get_day(day_id: int) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM days WHERE id = ?", (day_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_day_by_date(date: str) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM days WHERE date = ?", (date,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_tasks(day_id: int, tasks: list[dict]) -> list[dict]:
    conn = get_conn()
    try:
        out: list[dict] = []
        for i, t in enumerate(tasks):
            cur = conn.execute(
                """
                INSERT INTO tasks
                  (day_id, title, estimate_hours, priority, type, status, order_index, cut_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    day_id,
                    t["title"],
                    float(t["estimate_hours"]),
                    int(t.get("priority", 3)),
                    t.get("type", "study"),
                    t.get("status", "pending"),
                    t.get("order_index", i),
                    t.get("cut_reason") or "",
                ),
            )
            out.append(
                {
                    "id": int(cur.lastrowid),
                    "title": t["title"],
                    "estimate_hours": float(t["estimate_hours"]),
                    "priority": int(t.get("priority", 3)),
                    "type": t.get("type", "study"),
                    "status": t.get("status", "pending"),
                    "cut_reason": t.get("cut_reason") or "",
                    "order_index": t.get("order_index", i),
                }
            )
        conn.commit()
        return out
    finally:
        conn.close()


def mark_tasks_done(task_ids: list[int], done: bool = True) -> list[int]:
    """只更新 study 任务；life 不可勾选。"""
    conn = get_conn()
    try:
        status = "done" if done else "pending"
        updated: list[int] = []
        for tid in task_ids:
            cur = conn.execute(
                """
                UPDATE tasks SET status = ?
                WHERE id = ? AND type = 'study' AND status != 'cut'
                """,
                (status, tid),
            )
            if cur.rowcount:
                updated.append(tid)
        conn.commit()
        return updated
    finally:
        conn.close()


def list_tasks_for_day(day_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, day_id, title, estimate_hours, priority, type, status, order_index, cut_reason
            FROM tasks WHERE day_id = ? ORDER BY order_index, id
            """,
            (day_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_review(
    day_id: int, content: str, tomorrow: str, interview_review: str = ""
) -> dict:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM reviews WHERE day_id = ?", (day_id,))
        cur = conn.execute(
            """
            INSERT INTO reviews (day_id, content, tomorrow, interview_review)
            VALUES (?, ?, ?, ?)
            """,
            (day_id, content, tomorrow, interview_review or ""),
        )
        conn.execute("UPDATE days SET status = 'reviewed' WHERE id = ?", (day_id,))
        conn.commit()
        return {
            "id": int(cur.lastrowid),
            "day_id": day_id,
            "content": content,
            "tomorrow": tomorrow,
            "interview_review": interview_review or "",
        }
    finally:
        conn.close()


def get_review(day_id: int) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT content, tomorrow, interview_review, created_at
            FROM reviews WHERE day_id = ? ORDER BY id DESC LIMIT 1
            """,
            (day_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def add_agent_log(day_id: int, node: str, kind: str, content: Any) -> None:
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO agent_logs (day_id, node, kind, content) VALUES (?, ?, ?, ?)",
            (day_id, node, kind, content),
        )
        conn.commit()
    finally:
        conn.close()


def list_agent_logs(day_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT node, kind, content, created_at FROM agent_logs WHERE day_id = ? ORDER BY id",
            (day_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_days_with_details(limit: int = 14) -> list[dict]:
    conn = get_conn()
    try:
        days = conn.execute(
            "SELECT * FROM days ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()
        result: list[dict] = []
        for d in days:
            day_id = d["id"]
            tasks = conn.execute(
                """
                SELECT id, title, estimate_hours, priority, type, status, cut_reason, order_index
                FROM tasks WHERE day_id = ? ORDER BY order_index, id
                """,
                (day_id,),
            ).fetchall()
            review_row = conn.execute(
                """
                SELECT content, tomorrow, interview_review
                FROM reviews WHERE day_id = ? ORDER BY id DESC LIMIT 1
                """,
                (day_id,),
            ).fetchone()
            logs = conn.execute(
                "SELECT node, kind, content, created_at FROM agent_logs WHERE day_id = ? ORDER BY id",
                (day_id,),
            ).fetchall()
            entry: dict[str, Any] = {
                "date": d["date"],
                "day_id": day_id,
                "hours_left": d["hours_left"],
                "status": d["status"],
                "tasks": [dict(t) for t in tasks],
                "logs": [dict(l) for l in logs],
            }
            if review_row:
                entry["review"] = {
                    "content": review_row["content"],
                    "tomorrow": review_row["tomorrow"],
                    "interview_review": review_row["interview_review"] or "",
                }
            else:
                entry["review"] = None
            result.append(entry)
        return result
    finally:
        conn.close()
