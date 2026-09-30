# 多 Agent 真编排改造计划（企业全景砍版）

> 状态：已实现（smoke PASS）  
> 产品：自我学习生活多 Agent 管理平台  
> 目录：`E:\AI\aicoding\daily-lesson-agent`  
> 对照需求/选型：[REQUIREMENTS.md](REQUIREMENTS.md) / [DESIGN.md](DESIGN.md)

## 0. 为什么要改

当前实现是直线流水线，**不像多 Agent，也不好意思给创始人演示**：

- `coordinator` 只是 LangGraph 边，**不是 LLM**
- 工具在 `planner` 里**写死调用**，Agent 不会「看情况决定调不调」
- `timekeeper` 是纯算术砍时间
- 面试流水账要用户手贴，交互尴尬

目标：一次「生成今日」能讲清 **Supervisor 分派 → 领域 Agent 调工具 → 汇总收口**；轨迹页可回放。

---

## 1. 企业级全景 → 本版砍留

参考企业办公 Agent 分层（Web → Gateway → SSO → Runtime → Router/Memory/RAG → Supervisor → 领域 Agent → Skill → Tool Gateway → MCP/Connector → Workflow/HITL → Checkpoint → Eval）。

### 1.1 本版落地（演示讲「已经有」）

| 企业层 | 本版怎么落地 |
|--------|----------------|
| Web / App | FastAPI + 侧栏产品壳（今日 / 目标 / 历史 / Agent 轨迹） |
| AI Gateway | 薄一层：统一 `/api/plan`、`/api/review`，集中错误包装；鉴权位先空 |
| Agent Runtime | LangGraph 状态图 |
| Supervisor | **真 LLM** 总控：理解目标 → 输出分派单 |
| 领域 Agent | Life（≈ Calendar）、Study（≈ Document）、Budget；复盘 Evidence |
| Skill | 每个 Agent 只绑自己的工具子集 |
| Tool Gateway | `app/tools.py` 统一 `invoke_tool`：审计 log、超时占位 |
| MCP 形态 | `read_life_context` / `list_interviews` / `apply_time_budget`（可换真 MCP Server） |
| Checkpoint | 已有 `SqliteSaver`，`thread_id=date` |
| Final Response | synthesizer 汇总今日板 |
| Observability | `agent_logs` + Agent 轨迹页 |

### 1.2 本版砍掉（演示讲「企业要补」）

| 企业层 | 为什么砍 |
|--------|----------|
| IM / 多端 | 与排课无关 |
| Identity / SSO | 单人本地 Demo |
| Query Router | 场景单一，Supervisor 直接吃请求 |
| Memory Manager | 用 SQLite 日计划代替长期记忆 |
| RAG / Hybrid / Reranker | 无私域库；面试 notes 直接读 JSON |
| 真 Gmail / Jira / OA | 换成生活/学习域本地数据 |
| 真 MCP Server / Connector | 本地 JSON 占位 |
| Workflow + Risk + HITL | 无审批/打款等高风险动作 |
| Evaluation 平台 | 用 `capture_sample_run` 冒烟代替 |

演进对齐（面试话术）：Workflow/RPA → LLM 只会说 → RAG+工具 → Tool Gateway/MCP → Graph Runtime → **Supervisor 多 Agent**。本 Demo 主打：**Supervisor + Graph Runtime + MCP 形态工具**；Workflow/HITL 只口头对齐。

---

## 2. 目标运行时

领域仍是「自学 + 生活占用」，不改成假办公系统。

### 2.1 生成今日（主图）

```text
START
  → supervisor（LLM：读目标/日期/预算 → 出分派单）
        │
        ├─ 条件边（按 assignments）：有会议/面试才派 life_agent，否则跳过
        │        → life_agent（LLM + 工具环）
        └─ 并行 → study_agent（LLM：拆学习任务）
        │
        ▼  两路汇合
  → budget_agent（LLM + apply_time_budget：life 不砍、study 按优先级裁剪）
  → synthesizer（LLM：今日板 + 协作说明）
  → END
```

> 关键：supervisor 输出**策略**（note/brief，注入下游 prompt）；**路由由数据决定**——条件边按 `peek_has_life_load`（有无会议/面试）决定 life_agent 是否走，不让 LLM 决定「有没有会议」。
>
> 延迟：主图串 5 次 LLM 调用，靠 life ∥ study 并行 + UI 逐节点进度（见 3.4）两条一起缓解。

| 角色 | 对企业映射 | 职责 | 工具（Skill 子集） |
|------|------------|------|-------------------|
| **supervisor** | Supervisor | 读目标/日期/预算 → 出策略（note/study_brief/life_brief，注入下游）；**路由由数据（有无会议）决定** | 无 |
| **life_agent** | Calendar Agent | 与 study_agent **并行**；自己决定调日历/面试工具 | `read_life_context`、`list_interviews`（最多 3 轮，带确定性兜底） |
| **study_agent** | Document/学习 Agent | 按 supervisor 分派 + life 结果拆学习任务（故意略超预算） | 无（吃上游 state） |
| **budget_agent** | 预算/约束 Agent | 调 `apply_time_budget` 拿硬裁剪，再写理由 | `apply_time_budget`（life 不砍、study 按优先级） |
| **synthesizer** | Final Response | 今日板 + 一句话说明；面试 notes 从 JSON 自动读 | 无 |

### 2.2 晚间复盘（副图）

```text
START → evidence_agent（工具环） → reviewer（LLM） → END
```

- `evidence_agent`：调 `list_interviews` 取当日面试，读 `post_notes`（面试后流水账，可空）
- **语义拆清**：`notes` 是面试**前准备**信息，`post_notes` 才是面试**后表现**；「表现复盘（答得咋样、卡点）」只能来自 `post_notes` 或用户输入，不能拿准备信息冒充
- UI 文本框改为「覆盖备注（可选）」；空则用工具读到的 `post_notes`；两者都空 → 只出「面试准备提示」，不出「表现复盘」

---

## 3. 实现清单

### 3.1 状态 — `app/state.py`

新增：

- `assignments`：supervisor 策略 JSON——**note/brief 注入下游 prompt**；life_agent 是否走由数据（peek_has_life_load）决定，不是 LLM 路由
- `summary`：synthesizer 文案

保留：`life` / `plan` / `trimmed_plan` / `logs` / `tool_calls` 等；轨迹仍落 `agent_logs`。

### 3.2 工具网关 — `app/tools.py`

- 现有两工具包成 LangChain `StructuredTool`
- 新增 `apply_time_budget(hours_left, life, study) → {kept, cut, remaining}`
- 统一 `invoke_tool(name, args)`：写审计字段（仿 Gateway）
- **确定性兜底**：工具环里 LLM 没调工具 / 参数错时，直接读文件、直接算裁剪，不赌 LLM 听话

### 3.3 节点与图 — `app/nodes.py` / `app/graph.py`

- 抽 `_run_tool_loop(agent_name, system, user, tools)`，内含确定性兜底
- 删除「planner 开头硬调两工具」
- `timekeeper` 叙事升级为 `budget_agent`（规则进工具）
- `build_plan_graph`：`supervisor → {life_agent ∥ study_agent} → budget_agent → synthesizer`；supervisor 出**条件边**决定 life_agent 是否走
- `build_review_graph`：`evidence_agent → reviewer`
- checkpointer 保留

### 3.4 API / UI

- `/api/plan` 响应增加 `assignments`、`summary`、更完整 `tool_calls`
- Agent 轨迹：Supervisor 分派 → 各 Agent 工具往返 → Synthesizer
- 文案：「不可砍」→「固定安排（时间紧时只砍学习）」
- 今日顶栏显示 synthesizer 摘要
- 复盘框：可选覆盖备注
- **逐节点进度**：生成今日会串 5 次 LLM，UI 按节点回显「Supervisor 已分派 → Life Agent 正在读日程…」，把等待变成多 agent 在干活的观感

### 3.5 文档与验收

- 更新 `DESIGN.md` / `IMPLEMENTATION.md` / `README.md`：取消「coordinator 不是 LLM / timekeeper 纯规则」
- `DESIGN.md` 增加本节「企业全景砍留」精简表
- `scripts/capture_sample_run.py` 真断言：
  - `assignments` 非空
  - `tool_calls` 含 life 工具且来自 `life_agent`
  - 含 `apply_time_budget`
  - `summary` 非空
  - 复盘在空 `interview_notes` 时仍能从 JSON notes 产出 `interview_review`

---

## 4. 演示话术（约 30 秒）

1. 企业完整图有 Gateway / SSO / RAG / HITL / Eval——我们**故意砍**，聚焦编排核心。  
2. 点「生成今日」→ **Supervisor 分派** → Life Agent **自己调工具** → Study → Budget **经 Tool Gateway 形态裁剪** → Synthesizer 收口。  
3. Checkpoint 可回放；轨迹页即 Observability 雏形。  
4. 工具是 MCP 形态本地源，换真日历 / MCP Server 不改 Agent 叙事。

---

## 5. 明确不做

- SSO、RAG、真 MCP Server、Jira/OA Connector  
- Workflow 审批流 / Risk / HITL UI  
- Memory Manager、Evaluation 平台  
- CrewAI / A2A（继续同进程 LangGraph）  
- 不改成 React；侧栏五分区（今日 / 目标 / 求职 / 历史 / Agent 轨迹）  
- 不上 Redis / Docker / 向量库 / 登录  

---

## 6. 实现 Todos

1. [x] 扩展 DayState；tools 包成 StructuredTool（Tool Gateway 形态）+ `apply_time_budget`；`interviews.json` 加 `post_notes`
2. [x] 重写 nodes/graph：Supervisor 条件路由 → {life ∥ study} → budget → synthesizer；复盘 evidence → reviewer；工具环带确定性兜底
3. [x] API 带回 assignments/summary；轨迹与文案按新编排展示；生成今日逐节点进度
4. [x] 更新 DESIGN / IMPLEMENTATION / README；`capture_sample_run` 真断言 PASS

---

## 7. 验收标准（演示前）

- [x] 点一次「生成今日」，轨迹可见 Supervisor 分派文案；**有面试才走 life_agent 的面试分支（条件边生效）**  
- [x] life_agent 至少出现一轮 tool_call / tool_result（非写死脚本感）  
- [x] budget_agent 调用 `apply_time_budget`，学习任务有 cut、生活占用仍在  
- [x] synthesizer 产出可读「今日板说明」，页面顶部能看到  
- [x] 复盘：有 `post_notes` 时产出「表现复盘」；只有 `notes` 时只给「准备提示」，不冒充表现复盘  
- [x] 生成今日期间 UI 逐节点显示进度，不是干等  
- [x] 能用本节「砍留表」给创始人讲企业全景 vs Demo 边界  

---

## 8. 追加：数据录入（已完成）

用户要能自己加目标/会议/面试，不能再靠 seed 写死。

- [x] `POST /api/goals` → goals 表；`POST /api/interviews` → interviews.json；`POST /api/context` → context.json（按 type）
- [x] 前端：目标页「＋添加目标」；今日页「＋面试」「＋会议/占用」表单，添加后刷新 journal
- [x] `seed.py` 改幂等：文件不存在才写
- [x] 面试/会议走 JSON、目标走 DB；别给面试建表
- [x] 目标多选：加目标后刷新目标下拉，今日「生成今日」用选中的 `goal_id`（别写死第一条）
- [x] `context` 的 `notes` 是字符串数组，`type=note` 要 push 字符串，别当对象写坏
- [x] 表单旁写「下次生成今日才进课表」，别让用户以为点完就进固定安排
- [x] 暂不做「手加学习任务」：学习仍由 Study Agent 拆，保持「Agent 拆课」主线；要加另开一笔
- 契约见 IMPLEMENTATION.md §7「数据录入」+ §8  

---

## 9. 追加：求职分析（已完成）

- [x] `interviews.json` 加 `stage`（网申/笔试/一面/二面/HR面/谈薪/Offer/已拒）+ `insights`（手写公司面经）
- [x] 新工具 `analyze_interviews`：纯规则聚合（total / by_stage / active / recent_7d·30d / advance_rate / top_roles）
- [x] 新 agent `interview_analyst`（LLM + 工具环）出复盘 + 建议
- [x] `POST /api/analyze`：supervisor → interview_analyst → synthesizer（supervisor 真三域分派）
- [x] 前端第 5 分区「求职」：面试列表（stage 标签）+ 投递统计 + 「生成求职复盘」
- [x] 不做完整投递 CRM（拖拽看板）+ 面经 RAG（爬公开语料）
- 契约见 IMPLEMENTATION.md §5/§6/§7/§8  

---

## 10. 追加：强化 supervisor 策略输出（已完成）

- [x] supervisor prompt 让 `study_brief` 输出**具体策略**（例：「今天有面试，学习收窄到约 Xh，优先补 [面试卡点]」），别空泛「按目标拆学习任务」
- [x] `note` 一句话点出今日关键约束（有面试 → 面试优先；无 → 专注学习）
- [x] brief 可见进轨迹（supervisor/output 已有）——演示时指着 supervisor 输出讲「它判断今天面试优先」
- 契约见 IMPLEMENTATION.md §6  
