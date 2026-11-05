# 09 · Agent 编排：执行循环、日志与中断确认

> 对应提示词 7。代码：`app/agent/engine.py`（530 行）、`app/agent/tools.py`、`app/agent/prompts.py`。
> 三个典型轨迹是**真实跑出来的**，不是手写示意（trace 脚本见本文 §5）。

## 1. 框架选择：为什么是"自己写的 5 轮循环"

提示词里推荐用 Harness。这里的结论是：**保留 Harness 的工程约束（工具 schema、轮次上限、逐步日志、人类确认），但不引入外部 Agent 框架的运行时**。

| 候选 | 不选的理由 |
| --- | --- |
| LangChain / LangGraph | 一个 `create_react_agent` 要拖进 30+ 依赖与自己的状态机抽象；我们要的循环只有 60 行，调试时反而要读框架源码 |
| LlamaIndex Agent | 同上，且它把"检索"和"工具"绑在一起，我们的检索层已有更贴合的 RRF + 门禁实现 |
| 直接调厂商 Assistants API | 会话状态在别人服务器上，审计与脱敏做不到 |
| **自研循环（本项目）** | 60 行核心逻辑、零额外依赖、离线可跑（`HeuristicBrain`）、每轮都能写日志、能被单测直接驱动 |

**Harness 的真正价值不在框架，而在它强制你想清楚的四件事**，这四件事这个项目都做了：

| Harness 的约束 | 本项目落点 |
| --- | --- |
| 工具必须有 JSON Schema | `Tool.schema()` → OpenAI `tools` 格式（`tool_schemas()`） |
| 循环必须有上限 | `settings.max_agent_rounds = 5` |
| 每一步必须可观测 | 每轮写 `agent_logs`（工具名/参数/耗时/结果摘要） |
| 有副作用的动作必须可中断 | `create_draft` 前 `confirm_required` 强制中断 |

## 2. 执行循环

```
                       ┌──────────────────────────────┐
        question ─────→│ 1. 组装系统提示词（DB 模板+身份） │
                       │ 2. 载入最近 6 轮历史            │
                       └──────────────┬───────────────┘
                                      ▼
                        ┌──────── 轮次 round_no < 5 ────────┐
                        │                                  │
                        ▼                                  │
             ┌── 有 LLM Key ──┐     ┌── 无 Key ──┐          │
             │ LLMBrain       │     │ HeuristicBrain │     │
             │ 真 FunctionCall│     │ 确定性规则     │     │
             └───────┬────────┘     └──────┬──────┘          │
                     └────────┬────────────┘                 │
                              ▼                              │
                   模型给出 tool_calls + 文本                  │
                              │                              │
              ┌───────────────┼────────────────┐             │
              ▼               ▼                ▼             │
       无 tool_calls      create_draft      其他工具          │
       （直接回答）      （中断等确认）     run_tool()        │
              │               │                │             │
              │               ▼                ▼             │
              │      写 pending 消息      结果回填 messages     │
              │      yield confirm_required    └──────┬──────┘
              │      return                          │ 记日志
              ▼                                      └──→ 回到循环顶部
        yield token 流 → citations → done
```

循环终止的四种情况：① 模型不再要求调工具（正常结束，最常见）；② 用完 5 轮（用已有工具结果 `_compose()` 兜底成文）；③ 请求 `create_draft`（中断等人工确认）；④ LLM 异常（降级为 `_degraded_answer`：只把检索到的条文摆出来，明确说"请人工判断"，绝不假装模型答过）。

### 2.1 双大脑：为什么无 Key 也能演示

```python
brain = LLMBrain() if settings.has_llm else HeuristicBrain()
```

`HeuristicBrain` 用正则判断意图（`query` / `draft` / `validate` / `compliance`），再按固定顺序发工具调用。它不是"降级的假 Agent"，而是**一颗可预测的对照大脑**：评测用例要校验"工具调用顺序"，用确定性大脑才能稳定复现；用真模型则同一问题两次可能差一个工具。

两颗粒大脑共用同一套工具、同一套闸门、同一张日志表——所以升级到真模型不需要改任何业务代码。

### 2.2 每轮的日志

`agent_logs` 一行 = 一次工具调用（或一次 LLM 决策）：

| 字段 | 示例 | 用途 |
| --- | --- | --- |
| `round_no` | `2` | 第几轮（`0` 表示"确认后补执行"的建单动作） |
| `tool_name` | `query_expense_standard` | 调了哪个工具 |
| `args_json` | `{"city":"北京","days":1.0}`（发票号已打码为 `8765****`） | 参数，`app/privacy.py` 脱敏后落库 |
| `result_summary` | `{"ok": true, "city": "北京", "tier": "一线"…}`（≤240 字） | 结果摘要，防日志表膨胀 |
| `duration_ms` | `1` | 耗时，用于找"哪个工具慢" |

这张表支撑三件事：① 提示词 7 要求的"每轮决策记录"；② `docs/10` 的评测证据；③ 换模型后对比"工具选择是否变好"。

## 3. `create_draft` 的强制中断

```python
if name == "create_draft":
    pending = {"args": args, "validated": self.ctx.state.get("validated", {})}
    self._save("system", "待员工确认的报销单草稿",
               meta={"pending_draft": pending, "status": "pending"})
    yield ev("tool_call", name=name, args=args)
    yield ev("confirm_required", draft=preview)   # 含 total / approval_chain
    yield ev("done", meta={"pending": True, ...})
    return                                        # ← 关键：不执行工具
```

三个细节：

1. **`validated` 必须一起落盘**。确认是另一个 HTTP 请求，`ToolContext` 早没了；不把已校验发票号存进消息，确认时就等于绕过闸门。
2. **`confirm_required` 之后不再产出 token**。否则模型可能一边说"已为您建单"一边等确认，员工会以为已成功。
3. **确认接口拒绝时返回 `{"accepted": false, "message": "已取消，未生成报销单"}`**，并把那条 pending 消息的状态改为 `rejected`，不允许"取消后再点一次确认"偷偷建单。

## 4. 三个典型轨迹（实跑输出）

### 4.1 轨迹 a：只查制度，不碰工具 2–4

> 问："出差上海3天住宿标准是多少"

```
round 1  tool_call       search_policy {"query": "出差上海3天住宿标准是多少"}
         tool_result     ok=true  命中条文（含 2.2 住宿标准、2.1 城市分级）
round 2  tool_call       query_expense_standard {"city": "上海", "days": 3.0}
         tool_result     ok=true  tier=一线, level_group=普通员工, 住宿上限 500/晚, 伙食 100/天
         answer          "上海属一线城市，普通员工住宿上限 500 元/晚；伙食补助 100 元/天，
                          3 天合计 300 元（制度 2.2 / 2.4）"
         citations       3 条
```

**关键点**：`validate_invoice` / `create_draft` 一次都没调。评测用例 `P01` 与 `M01` 就断言这个"不该调的别调"。

### 4.2 轨迹 b：完整建单链路

> 问："帮我报上个月去北京的差旅，发票号 87654321，金额 480 元，日期 2026-10-20"

```
round 1  tool_call       search_policy {"query": "北京 差旅 报销 标准 流程"}
round 2  tool_call       query_expense_standard {"city": "北京", "days": 1.0}
round 3  tool_call       validate_invoice {"invoice_no":"87654321","amount":480.0,
                                          "invoice_date":"2026-10-20","category":"其他",...}
         tool_result     ok=true, issues=[]        ← 通过，写入 state["validated"]
round 3  tool_call       create_draft {...}        ← 被拦截，不执行
         confirm_required 合计 ¥480.00
                          审批链路：直属主管审批 → 财务审核 → 出纳付款（制度 4.1）
         done (pending=true)
--- 员工点"确认生成" → POST /api/chat/confirm ---
         round 0  create_draft  → draft_id=7, 合计 480.00
```

**两个易错点**：① `search_policy` 的 query 在 draft 意图下是"北京 差旅 报销 标准 流程"（系统拼的），不是原句——因为建单场景要的是流程条文；② 日志里 `create_draft` 出现在 `round 3`（请求时）与 `round 0`（确认后执行时）两条，`round 0` 是刻意标记"人工确认后补执行"，方便审计区分。

### 4.3 轨迹 c：只校验发票给结论

> 问："这个发票能报吗，发票号 24681357，金额 300 元，日期 2026-10-15"

```
round 1  tool_call       validate_invoice {"invoice_no":"24681357","amount":300.0,
                                          "invoice_date":"2026-10-15","category":"其他"}
         tool_result     ok=true, issues=[]
         answer          "这张发票可以报销：抬头/税号、90 天时效、科目均通过校验。"
```

**关键点**：不建单、不查住宿标准。`category` 落到"其他"是因为原句没说科目——这里故意不猜（猜"住宿费"会连带出住宿超标判断，属于编造）。

## 5. 轨迹是怎么抓的（可复现）

```python
from app import db
from app.agent.engine import AgentEngine

conn = db.init_db(temp_db_path)
user = db.query_one(conn, "SELECT * FROM users WHERE username=?", ("zhangwei",))
sid = db.execute(conn, "INSERT INTO sessions (user_id,title) VALUES (?,?)", (user["id"], "trace")).lastrowid

for event in AgentEngine(conn, user, sid).run("出差上海3天住宿标准是多少"):
    print(event["type"], event.get("name"), event.get("args"))

# 每轮日志
db.query_all(conn, "SELECT round_no,tool_name,duration_ms FROM agent_logs WHERE session_id=?", (sid,))
```

跑法：`%TEMP%\trace.py` 或直接 `python -c`，不需要起 Web 服务、不需要 API Key。

## 6. 与提示词要求的对照

| 提示词 7 的要求 | 实现 | 证据 |
| --- | --- | --- |
| 选一种框架，实现"决策→执行→回填→再决策"，最多 5 轮 | 自研循环 + `max_agent_rounds=5` | `engine.py` 主循环；`docs/10` 无一条用例触顶 |
| 每轮决策写日志表（工具名/参数/耗时/结果摘要） | `agent_logs` + `mask_args()` 脱敏 | `GET /admin/logs` 页面 |
| `create_draft` 前强制中断，草稿流式发员工确认 | `confirm_required` + `pending_draft` 消息 | `scripts/smoke_e2e.py` 30/30 |
| 3 条典型轨迹示例 | §4（真实输出） | 可用 §5 脚本复现 |
| 代码配中文注释 | 全部模块 | — |
