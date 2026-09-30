"""种 goal + context.json + interviews.json（含 stage / insights / post_notes）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db
from app.tools import migrate_interviews_file

DATA = ROOT / "data"
CONTEXT = DATA / "context.json"
INTERVIEWS = DATA / "interviews.json"

CONTEXT_DATA = {
    "meetings": [
        {"time": "21:30", "title": "线上会议", "duration_hours": 1.0}
    ],
    "blocks": [{"time": "20:00", "note": "之后无整块时间"}],
    "notes": ["今晚早点收尾"],
}

# 干净演示数据：多阶段覆盖，方便求职分析统计
INTERVIEWS_DATA = {
    "interviews": [
        {
            "date": "2026-09-30",
            "time": "14:00",
            "company": "讴谱科技",
            "role": "多Agent工程师",
            "stage": "一面",
            "duration_hours": 1.5,
            "notes": "技术面，重点问 LangGraph",
            "post_notes": "问了状态合并，我答得含糊；reducer 举例没讲清。",
            "insights": "这家偏好问状态管理与工具调用，面试官是技术负责人",
        },
        {
            "date": "2026-09-28",
            "time": "10:00",
            "company": "星河智能",
            "role": "多Agent工程师",
            "stage": "二面",
            "duration_hours": 1.0,
            "notes": "系统面，准备 Supervisor 编排案例",
            "post_notes": "",
            "insights": "二面偏项目深挖，会追问为什么不用 CrewAI",
        },
        {
            "date": "2026-09-25",
            "time": "16:00",
            "company": "云帆科技",
            "role": "AI 应用工程师",
            "stage": "网申",
            "duration_hours": 0.5,
            "notes": "已投递，等笔试通知",
            "post_notes": "",
            "insights": "",
        },
        {
            "date": "2026-09-20",
            "time": "11:00",
            "company": "北极光",
            "role": "后端工程师",
            "stage": "已拒",
            "duration_hours": 1.0,
            "notes": "一面答了 RPC 与超时",
            "post_notes": "超时重试讲得乱",
            "insights": "偏基础后端，Agent 经验不是加分项",
        },
        {
            "date": "2026-09-15",
            "time": "15:00",
            "company": "澜起数科",
            "role": "多Agent工程师",
            "stage": "笔试",
            "duration_hours": 2.0,
            "notes": "在线笔试，含 Graph 设计题",
            "post_notes": "",
            "insights": "笔试会给半成品图让你补节点",
        },
    ]
}


def main() -> None:
    db.init_db()
    DATA.mkdir(parents=True, exist_ok=True)

    # 幂等：文件已存在则跳过写入，避免冲掉用户录入
    if CONTEXT.exists():
        print(f"skip {CONTEXT} (already exists)")
    else:
        CONTEXT.write_text(
            json.dumps(CONTEXT_DATA, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"wrote {CONTEXT}")

    if INTERVIEWS.exists():
        # 迁移缺 stage 的旧条目，并清掉 SmokeCo 演示垃圾
        summary = migrate_interviews_file(
            default_stage="一面",
            drop_companies={"SmokeCo"},
        )
        print(
            f"migrate {INTERVIEWS}: "
            f"migrated={summary['migrated']} dropped={summary['dropped']} "
            f"total={summary['total']}"
        )
    else:
        INTERVIEWS.write_text(
            json.dumps(INTERVIEWS_DATA, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"wrote {INTERVIEWS}")

    goals = db.list_goals()
    active = [g for g in goals if g["status"] == "active"]
    if active:
        g = active[0]
        print(f"goal already exists: id={g['id']} title={g['title']}")
    else:
        gid = db.create_goal(
            title="把多 Agent / LangGraph 练到手",
            description="自我学习生活多 Agent 管理平台：Supervisor 分派 → 领域 Agent → 预算裁剪 → 复盘。",
            status="active",
        )
        print(f"created goal id={gid}")
    print("seed ok")


if __name__ == "__main__":
    main()
