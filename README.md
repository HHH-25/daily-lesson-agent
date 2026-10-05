# 自我学习生活多 Agent 管理平台

自学目标 → **Supervisor 分派** → Life/Study 并行 → Budget（`apply_time_budget`）→ Synthesizer 今日板；晚间 Evidence → Reviewer；求职 **Interview Analyst**（`analyze_interviews`）→ 复盘建议。

设计说明：[说明.md](说明.md)。

## 启动

```bash
cd E:\AI\aicoding\daily-lesson-agent
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # 填入 DEEPSEEK_API_KEY
# 开发期换 schema 时删旧库：
del data\daily.db data\daily.db-shm data\daily.db-wal 2>nul
python scripts\seed.py
uvicorn app.main:app --reload
```

浏览器：<http://localhost:8000>  
侧栏：今日 / 目标 / **求职** / 历史 / Agent 轨迹。

**三入口**：生成今日课 · 晚间复盘 · 求职复盘（Supervisor 分派 `interview=true`）。

**数据录入**：目标页可「＋添加目标」；今日页「＋面试」加公司。求职页可手写后记，或 **上传 PDF/Word 整份面经入库**（问答会检索）。

首次面经语义检索会下载 `BAAI/bge-small-zh-v1.5`（无新 API key）。国内可设 `HF_ENDPOINT`。`RAG_SKIP_EMBED=1` 时只走关键词。向量库存 `data/chroma/`，不进 Git。

## Smoke

```bash
python scripts\seed.py
python scripts\smoke_analyze.py
python scripts\smoke_ask.py
python scripts\capture_sample_run.py
```

快速验录入 + 统计工具（无需 DeepSeek）：

```bash
python scripts\smoke_analyze.py
```

或手动 curl（服务已启动时）：

```bash
curl -X POST localhost:8000/api/interviews -H "Content-Type: application/json" -d "{\"date\":\"2026-10-01\",\"time\":\"15:00\",\"company\":\"XX\",\"role\":\"…\",\"duration_hours\":1.0,\"stage\":\"一面\",\"insights\":\"\"}"
curl -X PATCH localhost:8000/api/interviews -H "Content-Type: application/json" -d "{\"date\":\"2026-10-01\",\"time\":\"15:00\",\"company\":\"XX\",\"stage\":\"二面\"}"
curl -X POST localhost:8000/api/analyze -H "Content-Type: application/json" -d "{\"goal_id\":1}"
curl localhost:8000/api/journal
```

真实记录：[traces/sample_run.md](traces/sample_run.md)。

## 为什么这么拆

| 角色 | 实现 |
| --- | --- |
| supervisor | LLM 总控：分派单驱动条件边 + 下游 prompt（life / study / **interview**） |
| life_agent | 工具环：`read_life_context` + `list_interviews`（MCP 优先，失败回退本地） |
| study_agent | LLM：拆 study（略超预算） |
| budget_agent | `apply_time_budget`：固定安排不砍，只砍学习 |
| interview_analyst | `analyze_interviews` 纯规则统计 → 复盘 + 建议 |
| synthesizer | 今日板 / 求职报告 `summary` |
| evidence → reviewer | 取证 + 复盘（表现复盘 vs 准备提示） |

**工具层**：真本地 MCP stdio（`python -m app.mcp_server`，工作目录为项目根，`data/*.json` 可解析）；Agent 优先经 langchain-mcp-adapters 调 MCP，失败/超时回退本地函数。不要 Zapier/Gmail。
