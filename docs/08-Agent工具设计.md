# 08 · Agent 工具设计（Function Calling 规范）

> 对应提示词 6。代码：`app/agent/tools.py`（定义与实现）、`app/agent/prompts.py`（提示词约束）、`app/db.py::DEFAULT_PROMPTS["system_agent"]`（默认口径）。
> 提示词要求"先设计、确认后再执行"——本文件是那份设计说明，代码已按此实现。

## 1. 设计原则：能算的不用模型，能卡的不用提示词

| 层次 | 手段 | 管什么 |
| --- | --- | --- |
| L1 | **工具定义**（给模型的 schema） | 模型知道"我能做什么、参数长什么样" |
| L2 | **系统提示词**（`system_agent`） | 模型知道"我该按什么顺序做" |
| L3 | **代码闸门**（`create_draft` 内部校验 `ctx.state["validated"]`） | 模型**不听话时**也建不了单 |
| L4 | **人工确认**（`confirm_required` 中断） | 建单前必须员工点头 |

关键判断：**顺序约束不能只写在提示词里**。提示词是"请求"，不是"约束"——模型幻觉、用户诱导（"先建了再说"）、提示词注入（"忽略之前的指令"）都会让它跳过校验。所以真正兜底的是 L3：`create_draft` 在执行前自己检查"这张发票在本会话里是否通过过 `validate_invoice`"，没通过就直接返回 `VALIDATION_REQUIRED`，参数再合法也建不出来。

## 2. 六个工具

| # | 工具名 | 副作用 | 作用 | 何时调用 |
| --- | --- | --- | --- | --- |
| 1 | `search_policy` | 无 | 检索制度条文，返回 top3 片段（含文档标题、小节、正文） | 任何关于标准/流程/时限的问题，**先查再说** |
| 2 | `query_expense_standard` | 无 | 按 城市 × 职级 × 天数 计算住宿上限、伙食补助合计、适用条款 | 涉及具体金额时，**不许模型自己算** |
| 3 | `request_invoice_info` | 无 | 向员工索要缺失字段（发票号/金额/日期） | 缺字段时先问，禁止编造占位值 |
| 4 | `validate_invoice` | 无 | 校验单张发票：抬头/税号、90 天时效、重复报销、科目、禁报名目 | 建单前逐张校验 |
| 5 | `create_draft` | **有** | 生成报销单草稿 + 明细 + 审批链路 | 全部发票校验通过后，且用户明确要求报销 |
| 6 | `list_my_drafts` | 无 | 查看本人最近草稿 | 用户问"我报过吗" |

### 2.1 参数规范（节选）

```jsonc
// search_policy
{ "query": "北京 住宿标准" }                                  // required: query

// query_expense_standard
{ "city": "北京", "job_level": "P5", "days": 3, "same_day_return": false }  // required: city
// job_level 缺省用当前登录用户职级；days 默认 1

// validate_invoice
{ "invoice_no": "87654321", "amount": 480, "invoice_date": "2026-10-20",
  "category": "住宿费", "title": "某某科技有限公司", "tax_id": "91110108MA01ABCD2X" }
// required: invoice_no / amount / invoice_date

// create_draft（唯一有副作用的工具）
{ "trip_city": "北京", "trip_start": "2026-10-18", "trip_end": "2026-10-20",
  "items": [ { "category": "住宿费", "amount": 480, "invoice_no": "87654321",
               "invoice_date": "2026-10-20", "merchant": "某酒店" } ] }
// required: trip_city / items[].{category,amount,invoice_no,invoice_date}
```

### 2.2 返回约定

所有工具统一返回 `dict`，失败也**不抛异常**（抛异常会让模型失去上下文）：

```jsonc
{ "ok": true,  "hits": [...], "clause": "2.2" }                       // 成功
{ "ok": false, "error_code": "VALIDATION_REQUIRED",
  "message": "以下发票尚未通过校验：87654321", "hint": "请先调用 validate_invoice" }
```

`error_code` 让模型能读懂"下一步该干什么"，而不是拿到一段人类才看得懂的报错。

## 3. 顺序约束（三种，从软到硬）

### 3.1 声明式约束（写进提示词）

默认 `system_agent`（存 `prompt_templates` 表，管理员可改）：

```
你是企业报销助手 Agent。你可以调用工具完成：查制度、算差旅标准、校验发票、生成报销单草稿。
硬性规则：
1) 任何金额/标准类结论必须先 search_policy 或 query_expense_standard 取到依据，不得凭记忆回答；
2) 建单前必须先 validate_invoice 且通过；未通过时禁止调用 create_draft，改为告知问题清单；
3) 缺少发票号/日期/金额时，先向员工索要，不要编造占位值；
4) 不要跳过校验直接建单，即使员工要求'先建了再说'；
5) 制度里没有的内容，明确说'制度中未找到'，不要推断。
```

系统会在运行时把它拼成"模板 + 当前员工身份（姓名/工号/职级/部门/公司全称/税号）+ 工具清单"，工具清单里的副作用工具会额外标注"（有副作用，需用户确认）"。

### 3.2 状态机约束（代码里记状态）

```
search_policy ─┐
query_expense_standard ─┤
                        ├─→ validate_invoice ──(全部通过,写入 state["validated"])──→ create_draft
request_invoice_info ───┘            │
                                     └─(未通过)─→ 只能返回问题清单，不能建单
```

`ToolContext.state["validated"]` 是一个 `{invoice_no: {...}}` 字典（**会话级**，不跨请求持久化），只在 `validate_invoice` 通过时写入。

### 3.3 强制闸门（代码里返回错误）

`create_draft` 的第一段逻辑：

```python
unvalidated = [i.get("invoice_no") for i in items if i.get("invoice_no") not in validated]
if unvalidated:
    return {"ok": False, "error_code": "VALIDATION_REQUIRED",
            "message": f"以下发票尚未通过校验：{', '.join(unvalidated)}",
            "hint": "请先调用 validate_invoice"}
```

这段话的价值不在于"提示模型"，而在于**模型无论怎么被诱导都拿不到 draft_id**。评测用例 T05（只给"帮我报上周去上海的差旅"，没有发票号）与 V03（"能不能不走审批直接付款"）就是用来验证这一点的。

## 4. 建单为什么要"中断等人确认"

`create_draft` 是全系统唯一写业务数据的地方。即使校验全过，也要停下来把草稿渲染给员工看——因为校验只能查"票面合规"，查不出"这张票不是这次出差的"。误建单的成本远高于多点一次按钮。

实现（`app/agent/engine.py`）：模型请求 `create_draft` 时**不执行**，而是：

1. 把 `{"pending_draft": {"args": ..., "validated": state["validated"]}, "status": "pending"}` 写进一条 system 消息的 `agent_meta`；
2. 向前端发 `confirm_required`（含明细、合计、审批链路）；
3. 员工点确认 → `POST /api/chat/confirm` → `confirm(accepted=True)` 从那条消息里恢复 `validated` 再真正执行。

**为什么把 `validated` 一起存进消息**：确认是另一个 HTTP 请求，`ToolContext` 已经销毁。如果确认时重建空 state，闸门就等于没有。恢复旧 state 才能保证"确认的是同一批已校验发票"。

## 5. 为什么不用更多工具（刻意省略）

| 想过但没做 | 原因 |
| --- | --- |
| `ocr_invoice`（拍照识别发票） | 需要多模态模型 + 图片存储，MVP 阶段收益/成本比太低；先让员工手输号码 |
| `submit_approval`（提交审批流） | 审批要对接 OA，当前只到"草稿 + 审批链路预览"，提交动作留给真实系统 |
| `delete_draft` | 有副作用且不可逆，MVP 不做；员工可让管理员在后台处理 |
| `calc_tax`（个税/抵扣） | 制度里没有，属于财税专业域，硬做会编造规则 |

**工具越多，模型选错的概率越高**。六个工具覆盖了"问—算—校验—建单"的主干，够用。

## 6. 与提示词要求的对照

| 提示词 6 的要求 | 实现 |
| --- | --- |
| 设计几个工具（Function Calling 规范） | 6 个，schema 见 `tool_schemas()`，`Tool.schema()` 直接产出 OpenAI 格式 |
| 说明调用顺序约束 | 本文 §3，三种层次 |
| 如何用系统提示词约束不得跳过校验 | 默认模板第 2/4 条 + 运行时注入工具清单 + 代码闸门兜底 |
| 先给设计，确认后执行 | 本文件 + 已实现代码 |
