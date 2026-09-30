# 自我学习生活多 Agent 管理平台 — 方案与技术栈

项目目录：`E:\AI\aicoding\daily-lesson-agent`  
产品名：**自我学习生活多 Agent 管理平台**。

产品：自学目标 → Supervisor 分派 → Life/Study 领域 Agent → Budget 裁剪 → Synthesizer 今日板；晚间 Evidence → Reviewer 复盘。

需求见 [REQUIREMENTS.md](REQUIREMENTS.md)；实现契约 [IMPLEMENTATION.md](IMPLEMENTATION.md)；改造目标 [PLAN.md](PLAN.md)。

## GitHub 对照

- [LangGraph](https://github.com/langchain-ai/langgraph)：状态图，可追责、可限轮次。
- [CrewAI](https://github.com/crewAIInc/crewAI) / [AutoGen](https://github.com/microsoft/autogen)：本版不上。
- [MCP](https://github.com/modelcontextprotocol)：工具层形态对齐，本地 JSON 占位。

主框架用 **LangGraph**（要可回放，不是群聊）。

## 企业全景砍留（演示边界）

| 本版落地 | 本版砍掉 |
|----------|----------|
| FastAPI 产品壳 + AI Gateway 薄层（`/api/plan` `/api/review`） | SSO / IM / 多端 |
| LangGraph Runtime + **真 LLM Supervisor** | Query Router / Memory Manager |
| 领域 Agent（Life / Study / Budget）+ Skill 工具子集 | 真 Gmail / Jira / OA |
| Tool Gateway（`invoke_tool`）+ MCP 形态本地源 | 真 MCP Server / Connector |
| SqliteSaver Checkpoint + agent_logs 轨迹 | Workflow / HITL / Eval 平台 |

## 协议（分层）

- MCP：Agent 调工具（本版 Tool Gateway + 本地 JSON，可换真 Server）
- A2A：本版不上
- HTTP：对外 REST；逐节点进度由前端状态文案模拟

## 技术栈

- 语言：Python 3.10+
- 编排：LangGraph + langchain-openai（DeepSeek `deepseek-chat`）
- 服务：FastAPI + Uvicorn
- 前端：侧栏四分区轻页面
- 数据库：SQLite + `langgraph-checkpoint-sqlite`（`thread_id`=plan-/review-日期）
- 外部能力：`read_life_context` / `list_interviews` / `apply_time_budget`
- 硬砍：Redis、Docker、向量库、登录

## 结构

```text
浏览器（今日 / 目标 / 历史 / Agent 轨迹）
        --HTTP--> FastAPI --> LangGraph
                         |      supervisor → {life ∥ study} → budget → synthesizer
                         |      evidence_agent → reviewer
                       SQLite   Tool Gateway（context.json / interviews.json）
```

## 节点定位

| 角色 | 实现 |
|------|------|
| supervisor | LLM：分派单；条件边决定是否走 life_agent |
| life_agent | LLM + 工具环（最多 3 轮，确定性兜底） |
| study_agent | LLM：拆 study，故意略超预算 |
| budget_agent | LLM + `apply_time_budget`（life 不砍） |
| synthesizer | LLM：今日板 `summary` |
| evidence_agent | 工具环取 `post_notes` / `notes` |
| reviewer | LLM：学习复盘 + 面试（表现复盘 vs 准备提示） |

## 给创始人讲选型

1. 图不是群聊：可回放、可限步、条件边真分派。  
2. Supervisor + Tool Gateway + MCP 形态 = 企业编排核心的 Demo 切片。  
3. SSO / RAG / HITL / Eval 故意砍，演示时对照「企业全景砍留」表说明边界。
