# 自我学习生活多 Agent 管理平台：实现契约

> 目标：明晚主链路跑通 + 一个「像平台」的产品壳。本文是唯一实现基准，字段/接口/验收以这里为准，避免 Cursor 猜形状。需求与选型理由见 [REQUIREMENTS.md](REQUIREMENTS.md) / [DESIGN.md](DESIGN.md)。

## 0. 范围定稿（v3 Supervisor）

主链路：目标 → **supervisor 分派** → life∥study → budget 砍课 → synthesizer → 勾选 → evidence → reviewer。

- **外部能力** = Tool Gateway 四工具（本地文件，结构对齐 MCP）：`read_life_context` + `list_interviews` + `apply_time_budget` + `analyze_interviews`。**不做天气**；真 HTTP 留以后。
- **任务分两类**：`study`（学习，可砍）/ `life`（固定安排：会议、面试，时间紧时只砍学习）。
- **面试复盘**：`post_notes` 或用户覆盖备注 → 表现复盘；仅有 `notes` → 准备提示；都空 → `interview_review=""`。
- **产品壳**：侧栏「今日 / 目标 / 求职 / 历史 / Agent 轨迹」五分区。

## 1. 依赖（requirements.txt）

```
langgraph
langchain-core
langchain-openai
langgraph-checkpoint-sqlite
fastapi
uvicorn[standard]
python-dotenv
```

Python 3.10+。无 Redis / 向量库 / 登录。

## 2. 模型配置（写死 DeepSeek）

```python
ChatOpenAI(
    model="deepseek-chat",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
    temperature=0.2,
)
```

`.env.example`：`DEEPSEEK_API_KEY=sk-xxxx`。
`.gitignore` 至少：`.env`、`data/*.db*`、`__pycache__/`、`.venv/`、`*.pyc`、`key-deepseek.txt`。

## 3. 目录结构

```text
app/
  main.py        # FastAPI 入口 + 路由
  state.py       # DayState（TypedDict；logs/tool_calls 可累加）
  graph.py       # LangGraph 主图/副图 + SqliteSaver
  nodes.py       # supervisor / life / study / budget / synthesizer / evidence / reviewer
  db.py          # 建表 + CRUD
  tools.py       # StructuredTool + invoke_tool（Tool Gateway）
  schemas.py     # Pydantic 模型
  static/index.html   # 产品壳（侧栏五分区 + 逐节点进度）
scripts/
  seed.py        # 种 goal + context.json / interviews.json（含 post_notes）
  capture_sample_run.py
data/
  context.json       # 生活上下文（会议/占用/备注）
  interviews.json    # 面试日程（notes / post_notes）
  daily.db           # 运行时生成，gitignore
```

## 4. 数据表（SQLite）

```sql
CREATE TABLE goals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',      -- active | done（MVP 单 active）
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE days (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL UNIQUE,
  goal_id INTEGER NOT NULL REFERENCES goals(id),
  hours_left REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'planned',     -- planned | reviewed
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day_id INTEGER NOT NULL REFERENCES days(id),
  title TEXT NOT NULL,
  estimate_hours REAL NOT NULL,
  priority INTEGER NOT NULL DEFAULT 3,        -- 1 最高
  type TEXT NOT NULL DEFAULT 'study',         -- 'study' | 'life'
  status TEXT NOT NULL DEFAULT 'pending',     -- pending | done | cut
  order_index INTEGER NOT NULL DEFAULT 0,
  cut_reason TEXT DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day_id INTEGER NOT NULL REFERENCES days(id),
  content TEXT NOT NULL,                       -- 学习复盘
  tomorrow TEXT NOT NULL,                      -- 明日建议（含面试补点）
  interview_review TEXT DEFAULT '',            -- 面试复盘（无面试则为空）
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE agent_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day_id INTEGER NOT NULL REFERENCES days(id),
  node TEXT NOT NULL,                          -- supervisor | life_agent | study_agent | budget_agent | synthesizer | evidence_agent | reviewer
  kind TEXT NOT NULL,                          -- input | output | tool_call | tool_result | error
  content TEXT NOT NULL,                       -- JSON 字符串
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

> `tasks` / `reviews` 加了列，旧 `data/daily.db` 不会被 `CREATE TABLE IF NOT EXISTS` 迁移——**开发期直接删 `data/daily.db` 重跑 seed**。

## 5. 外部能力工具（`tools.py`）

工具按 MCP 形态：`name / description / input_schema / 返回 JSON`；统一经 `invoke_tool`（审计 agent / elapsed_ms）。换真 MCP Server 时不动图内调用点。

### `read_life_context` — 读 `data/context.json`

```json
{
  "meetings": [ { "time": "21:30", "title": "线上会议", "duration_hours": 1.0 } ],
  "blocks":    [ { "time": "20:00", "note": "之后无整块时间" } ],
  "notes":     ["今晚早点收尾"]
}
```

返回 `{"meetings": [...], "blocks": [...], "notes": [...]}`。

### `list_interviews(date)` — 读 `data/interviews.json`，过滤当日

```json
{
  "interviews": [
    { "date": "2026-09-30", "time": "14:00", "company": "XX公司",
      "role": "多Agent工程师", "stage": "一面", "duration_hours": 1.5,
      "notes": "技术面，重点问 LangGraph",
      "post_notes": "问了状态合并，我答得含糊…",
      "insights": "这家偏好问状态管理与工具调用，面试官是技术负责人" }
  ]
}
```

- `stage`：投递阶段，枚举 `网申 / 笔试 / 一面 / 二面 / HR面 / 谈薪 / Offer / 已拒`
- `notes`：面试**前**准备信息；`post_notes`：面试**后**流水账；`insights`：你手写的公司面经（可空）
- 「表现复盘」只能来自 `post_notes` 或用户输入

返回当日面试列表。

### `apply_time_budget(hours_left, life, study)` — 硬裁剪工具（纯规则）

返回 `{kept, cut, remaining}`：life 任务永不砍；study 任务按 priority 升序累加，超过 `hours_left - sum(life)` 的标 cut。由 `budget_agent` 调用，LLM 只负责写理由、规则进工具。

### `analyze_interviews()` — 求职数据分析（纯规则聚合）

读 `interviews.json` 全量，返回统计 JSON：
- `total`：投递总数
- `by_stage`：各阶段数量（网申/笔试/一面/二面/HR面/谈薪/Offer/已拒）
- `active`：进行中（非 Offer/已拒）数量
- `recent_7d` / `recent_30d`：近 7/30 天新增投递与面试数
- `advance_rate`：进入面试阶段（一面及以后）占比
- `top_roles`：投最多的岗位

由 `interview_analyst` 调用；纯规则聚合，不用 LLM 算数。

## 6. 图与节点（Supervisor 模式）

共享状态 `DayState`：`goal, date, hours_left, assignments, plan, life, trimmed_plan, done_items, review, tomorrow, interview_review, summary, logs, tool_summary`。

### plan 入口（主图）

```text
supervisor → { life_agent ∥ study_agent } → budget_agent → synthesizer
```

- **supervisor（LLM，策略）**：读 goal + hours_left + 上下文 → 输出 `assignments`：`note`/`study_brief`/`life_brief`（**策略判断**，注入下游 prompt）。**路由由数据决定**：条件边按 `peek_has_life_load`（有无会议/面试）决定 life_agent 是否走，不让 LLM 决定「有没有会议」。
  - `study_brief` 要具体到策略：例如「今天有面试，学习收窄到约 1.5h，优先补状态合并卡点」；不要空泛的「按目标拆学习任务」
  - 空泛检测：若 LLM 返回含「按目标拆学习任务」等模板句或过短，用确定性兜底重写（面试/会议 + 剩余学时 + 卡点线索）
  - 卡点线索来自当日/近期面试的 `notes` / `post_notes` / `insights`，以及昨日复盘 `tomorrow`（若可取）
- **life_agent（LLM + 工具环）**：调 `read_life_context` + `list_interviews`（最多 3 轮，带确定性兜底），产出 life 任务（type=`life`）。与 study_agent **并行**。
- **study_agent（LLM）**：按 supervisor 的 **具体** `study_brief` 拆 study 任务（type=`study`，故意略超可用预算，方便演示砍课）；brief 点名的卡点必须 priority=1。
- **budget_agent（LLM + 工具）**：调 `apply_time_budget(hours_left, life, study)` 拿硬裁剪（life 永不砍、study 按 priority 升序砍），再写裁剪理由。
- **synthesizer（LLM）**：合并各 agent 产出 → `summary`（今日板 + 一句话说明）。

### review 入口（副图）

```text
evidence_agent → reviewer
```

- **evidence_agent（工具环）**：调 `list_interviews` 读当日面试的 `post_notes`（可空）；带确定性兜底。
- **reviewer（LLM）**：输入 goal、done/pending/cut 拆分、`post_notes`（或用户覆盖备注）→ 输出 `{"review","tomorrow","interview_review"}`：
  - `post_notes` 非空 → `interview_review` 出**表现复盘**（问题/亮点/卡点/下次准备），补点写进 `tomorrow`
  - 只有 `notes`（面试前准备）→ 只给**准备提示**，不冒充表现复盘
  - 都没有 → `interview_review` 为 `""`

### 求职分析（第三个入口，`POST /api/analyze`）

```text
supervisor → interview_analyst → synthesizer
```

- **supervisor** 分派到「求职」域：`assignments` 记为 `{"interview": true, ...}`（真路由——不是学习就是求职）
- **interview_analyst（LLM + 工具环）**：调 `analyze_interviews`（纯规则统计）→ 输出「近期面试复盘 + 建议」（投递节奏、卡在哪个阶段、下一步补什么）
- **synthesizer（LLM）**：把分析结果收口成可读的求职复盘报告

supervisor 由此真正跨三域：**life（日程）/ study（学习）/ interview（求职）**。

## 7. API 契约

### `POST /api/plan`

请求：`{ "goal_id": 1, "date": "2026-09-30", "hours_left": 3.5 }`

响应：

```json
{
  "day_id": 1,
  "hours_left": 3.5,
  "assignments": { "life": true, "study": true, "note": "今天有会议+面试，学习收窄" },
  "summary": "今天学习预算 2.0h，21:30 会议 + 14:00 面试为固定占用…",
  "plan":   [ { "title": "读 LangGraph 状态合并", "estimate_hours": 1.0, "priority": 1 } ],
  "life":   [ { "title": "21:30 线上会议", "estimate_hours": 1.0, "priority": 0, "type": "life" } ],
  "trimmed_plan": [ { "title": "读 LangGraph 状态合并", "estimate_hours": 1.0, "priority": 1, "status": "pending", "type": "study" } ],
  "cut":    [ { "title": "手写 state graph", "estimate_hours": 2.0, "priority": 3, "status": "cut", "cut_reason": "超出剩余时间", "type": "study" } ],
  "tool_calls": [
    { "tool": "read_life_context", "input": null, "output": "…" },
    { "tool": "list_interviews", "input": { "date": "2026-09-30" }, "output": "…" },
    { "tool": "apply_time_budget", "input": { "hours_left": 3.5 }, "output": "…" }
  ]
}
```

### `PATCH /api/tasks`

请求：`{ "task_ids": [1, 2], "done": true }` → 响应 `{ "updated": [1, 2] }`

守卫：只更新 `type='study'` 的任务；`life` 任务不可勾选完成。

### `POST /api/review`

请求：`{ "day_id": 1, "interview_notes": "…" }`（`interview_notes` 可选，是**覆盖备注**；空则用 `interviews.json` 的 `post_notes`）

响应：

```json
{
  "review": "今天 3 条里完成 2 条…",
  "tomorrow": "1) 提前未完成项… 2) 重读 state 合并（面试卡点）…",
  "interview_review": "问题：LangGraph 状态合并… 亮点：… 卡点：… 下次：…",
  "logs": [ { "node": "reviewer", "kind": "output", "content": "{...}" } ]
}
```

### `GET /api/journal`

响应：`{ "goals": [...], "days": [...], "context": { "meetings": [...], "blocks": [...], "notes": [...], "interviews": [...] } }`

- `context` 是「今日」的外部上下文，供产品壳的「已读取生活上下文 / 面试日程」callout 与今日页汇总使用。
- 近 7 天完成率由前端从 `days[].tasks[]` 算：`完成率 = done / (done + pending)`（不含 cut 与 life）。

### 数据录入（用户自己输入）

目标进 DB；会议/面试进 JSON（保持「Agent 读外部源」叙事，下次生成今日即吃到）。

#### `POST /api/goals` — 添加目标

请求：`{ "title": "学 Rust", "description": "…" }` → 响应 `{ "id": 2, "title": "学 Rust", "status": "active" }`

#### `POST /api/interviews` — 添加/更新面试（upsert 进 `interviews.json`）

请求：`{ "date": "2026-10-01", "time": "15:00", "company": "XX", "role": "…", "duration_hours": 1.0, "notes": "…", "post_notes": "", "stage": "一面", "insights": "" }` → 返回落盘那条。缺 `stage` 时默认 `一面`。同 `date+time+company` 再次 POST 会 upsert。

#### `PATCH /api/interviews` — 更新阶段等字段

请求：`{ "date": "…", "time": "…", "company": "…", "stage": "二面", "insights": "…" }` → 按三元组定位更新。

#### `POST /api/context` — 添加会议/占用/备注（追加进 `context.json`）

请求按 `type`：
- `{ "type": "meeting", "time": "21:30", "title": "线上会议", "duration_hours": 1.0 }` → push 对象进 `meetings`
- `{ "type": "block", "time": "20:00", "note": "之后无整块时间" }` → push 对象进 `blocks`
- `{ "type": "note", "text": "今晚早点收尾" }` → push **字符串**进 `notes`（`notes` 是字符串数组，不是对象，别写坏结构）

> `seed.py` 改**幂等**：文件不存在才写，否则每次重跑都会冲掉用户加的数据。

#### `POST /api/analyze` — 求职分析

请求：`{ "goal_id": 1 }` → 响应：

```json
{
  "stats": { "total": 12, "by_stage": { "网申": 4, "一面": 3, "二面": 1, "已拒": 4 }, "advance_rate": 0.33, "recent_7d": 3 },
  "analysis": "近期投递 12 家，一面通过率…建议…",
  "suggestions": "…",
  "tool_calls": [ { "tool": "analyze_interviews", "agent": "interview_analyst", "output": "…" } ]
}
```

## 8. 产品壳前端（`static/index.html`）

**侧栏五分区**，替代现在的三个 Tab：

1. **今日**
   - 顶部：日期 + 「今晚还剩 Xh」输入 + 生成今日课；synthesizer `summary` 横幅
   - 外部能力 callout：「已读取生活上下文（1 会议 · 1 面试）」——由 `journal.context` 驱动
   - 课表分两段：**学习任务**（可勾选完成）+ **固定安排**（会议/面试，时间紧时只砍学习）
   - 汇总行：`学习 2.0h + 占用 2.5h = 4.5h` 对照预算 `3.5h`，超预算部分高亮
2. **目标**：目标列表 + 「＋添加目标」表单（标题/描述）→ `POST /api/goals`；添加后**刷新目标下拉**，今日页「生成今日」用当前选中的 `goal_id`（别写死第一条）
3. **求职**（第 5 分区）：面试列表（带 `stage` 标签，可 PATCH 改阶段）+ 投递统计 + 「生成求职复盘」→ `POST /api/analyze` → 复盘 + 建议 + 工具轨迹（`tool_calls` 直接回显，不依赖 day_id）
4. **历史**：近 7 天完成率（每日一条百分比）+ 每日课表 + 「昨日复盘 → 今日」衔接
5. **Agent 轨迹**：Supervisor / Life / Study / Budget / Synthesizer / Interview Analyst / Evidence→Reviewer 卡片 + 工具往返时间线
6. 生成今日：状态栏逐节点进度文案
7. 文案：「固定安排（时间紧时只砍学习）」替代「不可砍」
8. **数据录入**：今日页「＋面试」「＋会议/占用」按钮 → 小表单（面试含 stage/insights）→ `POST /api/interviews` / `POST /api/context`；添加后刷新 journal。**表单旁写一句「下次生成今日才进课表」**，别让用户以为点完就出现在「固定安排」里

**复盘**（今日页底部）：可选「覆盖备注」；空则用 `post_notes` → 学习复盘 / 面试复盘 / 明日建议。

## 9. 验收 smoke

```bash
uvicorn app.main:app --reload
python scripts/seed.py
python scripts/capture_sample_run.py
```

断言点（`capture_sample_run.py` 真断言）：
- `assignments` 非空且当日有会议/面试时 `life=true`
- `summary` 非空
- `tool_calls` 含 `life_agent` 的 life 工具，且含 `apply_time_budget`
- `cut` 非空、`life` 含会议/面试
- 空 `interview_notes` 仍能从 JSON `post_notes` 产出 `interview_review`
- `journal.context` 非空

## 10. 交付物

1. `README.md`：启动 + smoke 命令。
2. 一次真实运行记录（`traces/sample_run.md`，真断言）。
3. 一页说明：为什么这么拆、先做什么、外部能力怎么接、砍了哪些。
