# MVP 项目说明：工程架构 / Agent 框架 / 知识库 / 人工闸门

> 版本：V1.0　|　代码位置：仓库根目录（本文件只做导读，不复制代码）
> 运行：`python run.py --port 8000`（Web）或 `python quickstart.py`（控制台）

## 1. 工程架构

### 1.1 分层

```
┌──────────────────────────────────────────────────────────────┐
│ 接入层  浏览器 / 企业微信（预留） / AD·SSO（预留）              │
├──────────────────────────────────────────────────────────────┤
│ 应用层  FastAPI 路由 · SSE · Jinja2 模板 · 鉴权 · 审计          │
│         app/main.py, app/web/{deps,auth,chat,admin}_routes.py  │
├──────────────────────────────────────────────────────────────┤
│ 智能层  Agent 循环 · 6 个工具 · 六条硬规则 · 混合检索            │
│         app/agent/{engine,tools,rules,prompts}.py              │
│         app/rag/index.py                                       │
├──────────────────────────────────────────────────────────────┤
│ 数据层  SQLite（10 表） · 制度原文/切片 · 工具日志 · 审计标记     │
│         app/db.py, data/policies/                              │
└──────────────────────────────────────────────────────────────┘
```

### 1.2 目录与职责

| 路径 | 职责 | 不做什么 |
|---|---|---|
| `run.py` | 启动器：依赖自检、端口、自动开浏览器 | 不含业务逻辑 |
| `app/main.py` | FastAPI 应用、异常处理、静态资源 | 不直接调模型 |
| `app/config.py` | 环境变量/.env、路径、阈值 | 不写死密钥 |
| `app/db.py` | 建表、种子数据、切片重建、10 张表 | 不做检索排序 |
| `app/security.py` | PBKDF2 口令、HS256 JWT | 不存会话 |
| `app/privacy.py` | 日志脱敏（发票号/手机号/身份证） | 不改业务数据 |
| `app/llm.py` | OpenAI 兼容客户端（chat/stream/tools） | 不做业务判断 |
| `app/rag/index.py` | 切片、分词、BM25、哈希向量、RRF、门禁 | 不生成最终文案 |
| `app/agent/rules.py` | 六条硬规则，唯一裁判 | 不写自然语言长文 |
| `app/agent/tools.py` | 6 个工具 + JSON Schema + 建单闸门 | 不直接返回 HTML |
| `app/agent/engine.py` | 5 轮循环、SSE 事件、确认中断、降级 | 不绕过工具写库 |
| `app/agent/prompts.py` | 系统提示词组装 | 不写死业务数字 |
| `app/web/*` | 路由、鉴权、页面 | 不重复实现规则 |
| `evals/` | 30 条评测 + 跑分 | 不依赖 Web 服务 |
| `scripts/` | 冒烟、检索基线 | 不修改生产数据 |

### 1.3 关键设计取舍

1. **不引入 Agent 框架**：核心循环约 60 行，需要的是“工具 schema、轮次上限、逐步日志、可中断”，标准库足够；框架会带来 30+ 依赖与黑盒状态。
2. **规则与模型分离**：金额/时效/标准/查重由 `rules.py` 判定；模型只解释与引导。理由是可审计、可复现。
3. **顺序约束写成代码**：`create_draft` 自己检查本会话是否通过 `validate_invoice`，不依赖提示词“请先校验”。
4. **双大脑**：有 Key 用 `LLMBrain`，无 Key 用 `HeuristicBrain`（确定性意图路由），共用工具、闸门、日志，保证演示与评测可复现。
5. **零第三方依赖优先**：检索、规则、Agent、SQLite 全标准库；FastAPI 只用于 Web 层。

## 2. Agent 框架

### 2.1 循环

```
组装 system prompt（DB 模板 + 身份/职级）
  → 载入最近 6 轮历史
  → 轮次 < 5：
      模型给出 tool_calls / 文本
      ├─ 无 tool_calls → 流式输出 → citations → done
      ├─ create_draft → 写 pending 消息 → confirm_required → 中断
      └─ 其他工具 → 执行 → 回填 → 写 agent_logs → 下一轮
  → 5 轮用尽 → 用已有结果兜底成文
  → LLM 异常 → 降级为“条文 + 请人工判断”
```

### 2.2 六个工具

| 工具 | 副作用 | 作用 |
|---|---|---|
| `search_policy` | 无 | 检索制度 Top3（含标题/小节/条款） |
| `query_expense_standard` | 无 | 城市 × 职级 × 天数 → 住宿上限、伙食补助 |
| `request_invoice_info` | 无 | 缺字段时追问，禁止编造占位值 |
| `validate_invoice` | 无 | 抬头/税号/时效/查重/科目/禁报名目 |
| `create_draft` | **写库** | 生成草稿 + 明细 + 审批链路 |
| `list_my_drafts` | 无 | 查看本人最近草稿 |

### 2.3 失败约定

工具失败不抛异常，统一返回 `{ok:false, error_code, message, hint}`。例：

```json
{"ok": false, "error_code": "VALIDATION_REQUIRED",
 "message": "以下发票尚未通过校验：87654321",
 "hint": "请先调用 validate_invoice"}
```

这样模型知道“下一步做什么”，人也看得懂“为什么没成”。

### 2.4 可观测

每轮写 `agent_logs`：session_id、轮次、工具名、参数、耗时、结果摘要。管理后台 `/admin/logs` 可按会话查看。

## 3. 知识库

| 环节 | 实现 |
|---|---|
| 入库 | 后台上传 md/txt/pdf；≤ 8MB；解析失败明确报错 |
| 切片 | 500 字、50 字重叠，保留标题/小节 |
| 分词 | 中文 2-gram + 英文/数字词 |
| 召回 | BM25 + 256 维哈希向量，RRF（k=60）融合 Top3 |
| 门禁 | 相关性阈值、域外词表、条块完整性 |
| 版本 | `policy_docs.version/effective_date/status`；同名上传旧版归档 |
| 更新 | 上传后立即重建索引，下一轮问答生效 |
| 删除 | 软删除，切片立即失效，审计留痕 |

**为什么不引入向量数据库**：切片量级 < 1 万，哈希向量 + BM25 的召回已满足，且零部署成本；当切片 > 50 万或需要多语言语义检索时再评估。

## 4. 人工确认与审核点（5 处）

| 编号 | 位置 | 触发 | 人工动作 | 强制 | 留痕 |
|---|---|---|---|---|---|
| H1 | 员工端建单 | `create_draft` 请求 | 核对金额/明细/审批链，点“确认生成” | 是 | 确认人、时间、草稿号 |
| H2 | 管理端删除制度 | 点击删除 | 二次弹窗确认；软删除 | 是 | 操作人、文档、时间 |
| H3 | 管理端审计 | 命中风险关键词 | 人工复核，标记“已核实/误报” | 是（高风险） | 复核人、结论 |
| H4 | 管理端提示词 | 保存模板 | 管理员确认发布，版本 +1 | 否（管理员权限） | 版本、diff |
| H5 | 财务最终审核 | 草稿进入 OA 后 | 审核员按预检清单复核签批 | 是（线下） | OA 审批记录 |

### 4.1 H1 的实现（最关键）

```
create_draft 请求
  → 不执行；写一条 status=pending 的 system 消息
  → SSE 推 confirm_required（含草稿预览）
  → 本轮结束
员工点“确认生成”
  → POST /api/chat/confirm {session_id, accepted}
  → 读取 pending 中保存的“已通过校验的发票号”
  → 恢复 ctx.state["validated"]
  → 执行 create_draft → 写 drafts + draft_items
  → 返回 draft_id、金额、审批链
重复确认 → 400 “该草稿已处理过”
未确认 → 数据库 0 条草稿
```

对应代码：`app/agent/engine.py::AgentEngine.confirm`、`app/web/chat_routes.py::confirm_draft`、`app/web/static/chat.js`（确认卡）。

## 5. 权限模型

| 能力 | 员工 | 管理员 |
|---|---|---|
| 问答 / 验票 / 建自己的草稿 | ✓ | ✓ |
| 看他人会话 | ✕（404） | ✓ |
| 改已确认金额 | ✕ | ✕（只能标记/备注） |
| 上传/删除制度、改提示词、审计 | ✕（403） | ✓ |

实现：`require_user` / `require_admin` 双守卫；JWT 存 httpOnly Cookie（12h）；越权按 404，不泄露存在性。

## 6. 验证入口

| 命令 | 结果 |
|---|---|
| `python evals/run_eval.py` | 30/30 |
| `python scripts/smoke_e2e.py --base http://127.0.0.1:8000` | 31/31 |
| `python Test/_scripts/run_tests.py --base http://127.0.0.1:8124` | 55/56（1 个 P2） |
| `python Test/_scripts/bench.py` | 首字 P95 27ms（离线） |
