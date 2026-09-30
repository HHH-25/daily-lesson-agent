# 自我学习生活多 Agent 管理平台 — 需求分析

项目目录：`E:\AI\aicoding\daily-lesson-agent`  
产品名：**自我学习生活多 Agent 管理平台**。  
技术栈见同目录 [DESIGN.md](DESIGN.md)。

面向创始人许元鸿的 24 小时作业，不是给他们公司做产品。题目是「给你自己用、自己定义」。

## 已对齐

- 场景：以自学为主；生活只留「今晚前还剩几小时」作约束，不管运动、饮食、琐事。
- 形态：FastAPI + 轻前端，看起来像小平台，而不是 Streamlit 单页。
- 目录：`E:\AI\aicoding\daily-lesson-agent`。不改办公那份制度问答 Demo。
- 交付节奏：**先跑通主链路，再补「调用外部能力」**，外部能力写进交付范围，不写成「明确不做」。
- 模型：DeepSeek（`deepseek-chat`），key 走 `.env` 的 `DEEPSEEK_API_KEY`。
- 生活层：占用（会议/面试）作为不可砍任务进课表；面试支持「流水账输入 → 结构化复盘 → 补点接明日计划」。

## 他在考什么

- 你会不会自己定产品，而不是等需求
- 会不会把一天做不完的「平台」收成一条主链路
- 交出来的东西能不能演示、能不能讲设计

所以不做用户系统、不做 App、不做企业后台。做一个你自己每天能打开的小工具。

## 产品定义

**本平台**：给自己用的自学节律工具。你有一个中期学习目标（例如把多 Agent / LangGraph 练到手），系统每天只给你「今天能完成的课」，晚上根据完成情况改明天的计划。

「生活」怎么体现：不问运动、饮食、琐事清单，只问 **今晚前还剩几小时**。计划必须按剩余时间砍任务，避免一天排 8 小时学习。这就是生活约束，也和工作里的办公 Demo 明显不同。

## 交付分两步（都要交，顺序固定）

### 第一步：主链路（先做）

目标 → planner 拆今日课 → timekeeper 按剩余时间砍课 → 勾选完成 → reviewer 复盘并改明日建议；落库；页面可演示协作轨迹。

### 第二步：外部能力（主链路通了立刻做）

Agent 必须能 **调用图外的能力**，不能只靠 Prompt 空想。本作业落地为：

- 至少 1 个可演示的外部能力调用（例如读本地生活上下文/日程片段，或公开 HTTP 接口），在协作轨迹里能看到「调了什么、返回了什么」。
- 优先做成可替换形态：图内用 Tool 调用；结构上按 MCP 工具层预留，方便以后换成日历、笔记等 MCP Server。
- **不是**「明确不做」；只是排在主链路之后，避免一上来堆协议。

「自主学习」落地为：晚间复盘后改明日计划（可结合外部上下文），**不是**真模型持续训练。

## 本版硬砍（有空也不塞进明晚主演示）

这些会冲淡作业主线，明晚演示不讲、不做完整产品化：

- 注册登录、权限、多租户
- 手机端、微信提醒、完整日历同步产品
- 向量库 / RAG（避免被看成办公 Demo 换皮）
- 真模型持续训练 / 自训练团队

## 四个节点（一张 LangGraph）

```text
用户：目标 + 剩余时间
        │
        ▼
   coordinator  调度
        │
        ├─ planner      拆今日任务（可调用外部上下文工具）
        ├─ timekeeper   按剩余时间砍课
        └─ reviewer     晚间复盘并改明日建议
        │
        ▼
   今日课表 + 复盘
```

| 节点 | 职责 | 和办公 Demo 的差别 |
| --- | --- | --- |
| coordinator | 按进度分派，规则兜底 | 结构可类似，场景不同 |
| planner | 把中期目标拆成今日 3–5 条可执行任务 | 不是查制度；可吃外部上下文 |
| timekeeper | 对照剩余时间删减/降优先级，防止排满 | 「生活」约束，可讲的点 |
| reviewer | 根据勾选完成情况写复盘，并给出明日调整 | 「自我学习」，不是制度冲突检测 |

> 实现边界：`planner`/`reviewer` 是仅有的两个 LLM 节点；`timekeeper` 纯规则；`coordinator` = LangGraph 条件边，不是 LLM 调用。

共享状态：`goal`、`hours_left`、`plan`、`trimmed_plan`、`done_items`、`review`、`tomorrow`、`logs`、外部工具返回摘要。

跨天数据落库（SQLite，见 DESIGN），打开页面还能看到昨天，才像「管理」而不是单次问答。

## 页面与接口（产品侧）

产品壳：侧栏「今日 / 目标 / 历史 / Agent 轨迹」四分区。

- 今日：预算 + 课表（学习可勾选 / 生活占用不可砍）+ 外部能力 callout（已读取生活上下文 / 面试日程）
- 目标：目标列表
- 历史：近 7 天完成率 + 每日课表 + 昨日复盘→今日衔接
- Agent 轨迹：四角色卡片 + 工具调用往返
- 面试：流水账输入 → 结构化面试复盘 → 补点接明日计划

- `GET /api/journal` 读目标、近日课表、今日上下文
- `POST /api/plan` 跑 planner + timekeeper（含两个工具调用）
- `POST /api/review` 跑 reviewer（可带 `interview_notes`）
- `PATCH /api/tasks` 勾选完成

## 验收（明晚「跑通」= 这条链路）

从空库开始，一条命令走到头：

```bash
uvicorn app.main:app --reload
python scripts/seed.py          # 种 1 个目标
curl -X POST localhost:8000/api/plan    -d '{"goal_id":1,"date":"2026-09-30","hours_left":3.5}'
curl -X PATCH localhost:8000/api/tasks  -d '{"task_ids":[1,2],"done":true}'
curl -X POST localhost:8000/api/review  -d '{"day_id":1}'
curl localhost:8000/api/journal
```

每一步返回能看到：plan 被 timekeeper 按 3.5h 砍过；tasks 勾选落库；review 给出明日建议；journal 里 `agent_logs` 含外部工具调用记录（调了什么、返回什么）。这条通了 = 主链路 MVP 完成。

字段级 schema 与接口示例 JSON 见 [IMPLEMENTATION.md](IMPLEMENTATION.md)（给 Cursor 的实现契约）。

## 交付物

1. 可运行说明：`uvicorn` 起服务（本机演示；仓库推 GitHub）
2. 一次真实运行记录（生成课表含外部调用 + 复盘）
3. 一页说明：为什么这么拆、先做了什么、外部能力怎么接、故意砍了哪些产品化项

## 给创始人讲时的一句话

四个角色是基本盘。作业验证两件事：计划必须过「今天还剩多少时间」；复盘必须能改明天；并且 Agent 会调用外部能力拿真实上下文，而不是只聊天。
