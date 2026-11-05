# 报销助手智能体系统（Reimbursement Agent）

企业报销制度问答 + 发票校验 + 报销单草稿生成的智能体 MVP。
面向两类角色：**员工**（查制度、问"这张票能不能报"、发起报销）与**管理员**（维护制度知识库、调提示词、看审计）。

- 后端：FastAPI（异步 + SSE 流式），Python 3.11+
- 检索：BM25 + 哈希向量 + RRF 融合，带**相关性门禁**与**域外弃答**
- 智能体：自研 5 轮工具循环（Function Calling），**建单前强制人工确认**
- 存储：SQLite（10 张表），零 ORM
- 依赖极少：FastAPI 只用于 Web 层；检索/规则/Agent/SQLite 全部标准库

> 仓库地址：<https://github.com/Lcc-1118/f24011127lcc>（main 分支，60 个项目文件）
> 如果只想看"能跑起来"的证据：`python quickstart.py`，30 秒出结果，不需要装任何包、不需要 API Key。

## 一分钟跑起来

**Windows 用户最省事**：进到项目文件夹，双击这两个文件即可（脚本会自己找可用的 Python）：

| 双击 | 作用 |
| --- | --- |
| `demo.bat` | 控制台演示 3 个场景，不需要装任何包、不需要 API Key |
| `start.bat` | 启动 Web 版（员工端 + 管理后台），自动打开浏览器 |

命令行等价写法：

```bash
# 1) 控制台演示：零第三方依赖、不需要 API Key
python quickstart.py

# 2) Web 版（员工端 + 管理后台）
python -m pip install -r requirements.txt
python run.py                 # 自动打开 http://127.0.0.1:8000
```

> 注意：如果在 Windows 上敲 `python` 弹的是微软商店窗口（说明 PATH 里只有商店占位符，没有真 Python），
> 就用 `demo.bat` / `start.bat`，或者在命令行里写完整路径，例如
> `"D:\应用\DSH Desktop\resources\office-runtime\primary-runtime\dependencies\python\python.exe" run.py`。

账号：

| 角色 | 账号 | 密码 |
| --- | --- | --- |
| 管理员（财务 李静） | `admin` | `admin123` |
| 员工（研发 张伟） | `zhangwei` | `emp123` |

## 目录结构

```
f24011127lcc/
├── demo.bat                   # ★ Windows 双击：控制台演示（零依赖）
├── start.bat                  # ★ Windows 双击：启动 Web 版
├── quickstart.py              # 一键演示（控制台 3 场景 / --web 起服务 / --interactive 交互）
├── run.py                     # Web 启动器（依赖自检、--reset、自动开浏览器）
├── requirements.txt
├── app/
│   ├── config.py              # 配置（环境变量 / .env）
│   ├── db.py                  # SQLite 建表、种子数据、切片重建
│   ├── security.py            # pbkdf2 口令 + 手写 HS256 JWT
│   ├── privacy.py             # 日志脱敏（发票号 / 手机号 / 身份证）
│   ├── llm.py                 # OpenAI 兼容客户端（chat / stream / tools）
│   ├── main.py                # FastAPI 应用与异常处理
│   ├── rag/index.py           # 切片、分词、BM25、哈希向量、RRF、查询改写、弃答门禁
│   ├── agent/
│   │   ├── rules.py           # 硬规则：住宿/伙食/时效/审批/发票校验（代码判定）
│   │   ├── tools.py           # 6 个工具 + Function Calling schema + 建单闸门
│   │   ├── prompts.py         # 提示词组装
│   │   └── engine.py          # Agent 循环、SSE 事件、确认中断、降级
│   └── web/
│       ├── deps.py auth_routes.py chat_routes.py admin_routes.py
│       ├── templates/         # 11 个 Jinja2 模板（无前端框架、无 CDN）
│       └── static/            # style.css + chat.js（手写 SSE 解析、打字机、引用卡）
├── data/policies/             # 制度原文（Markdown）
├── docs/                      # 01–12 全套设计/评测/上线文档
├── evals/                     # 30 条评测用例 + 评测脚本 + 报告
├── scripts/
│   ├── smoke_e2e.py           # 端到端冒烟（30 项检查）
│   └── recall_report.py       # 检索基线（recall@3 / MRR / 弃答准确率）
└── prototype/                 # 阶段 1 单文件命令行原型
```

## 验证结果（都是实跑数字）

| 项目 | 命令 | 结果 |
| --- | --- | --- |
| 评测用例 | `python evals/run_eval.py` | **30/30 通过（100%）** |
| 端到端冒烟 | `python scripts/smoke_e2e.py` | **30/30 通过** |
| 检索基线 | `python scripts/recall_report.py` | recall@3 **95.8%**、recall@5 **100%**、MRR **0.969**、域外弃答 **100%** |

## 三个设计取舍（值得先看）

1. **硬规则不用模型判定**：90 天时效、住宿三档、审批门槛、发票抬头/税号，全部是 `app/agent/rules.py` 的代码判定。模型只负责"把条文讲清楚"，不负责"算得对"。
2. **顺序约束不靠提示词**：`create_draft` 执行前自己检查"这张发票在本会话是否通过过 `validate_invoice`"，没通过直接返回 `VALIDATION_REQUIRED`。提示词只是提醒，闸门在代码里。
3. **建单必须人工确认**：`create_draft` 请求到达时不执行，先落一条 `pending` 消息并向前端推 `confirm_required`；确认时才恢复校验态执行。误建单的成本远高于多点一次按钮。

## 文档索引

| 文档 | 内容 | 对应要求 |
| --- | --- | --- |
| [01](docs/01-需求评审-十个关键问题.md) | 需求评审：10 个关键问题 × 答案导致的架构差异 | 提示词 1 |
| [02](docs/02-一页纸需求清单.md) | 一页纸需求清单（六条硬规则、范围、验收标准） | 提示词 1 |
| [03](docs/03-架构选型与技术方案.md) | 四层架构、目录、10 张表 DDL、第一周目标 | 提示词 2 |
| [04](docs/04-阶段1检索基线报告.md) | 检索基线：recall/MRR/弃答，三道门禁 | 提示词 3 |
| [05](docs/05-阶段1原型说明.md) | 单文件原型说明与复用映射 | 提示词 3 |
| [06](docs/06-员工端接口与前端设计.md) | SSE 接口、事件表、前端交互 | 提示词 4 |
| [07](docs/07-管理后台设计.md) | 6 个页面 + 接口清单 + 权限模型 | 提示词 5 |
| [08](docs/08-Agent工具设计.md) | 6 个工具 schema、顺序约束、提示词约束 | 提示词 6 |
| [09](docs/09-Agent编排说明.md) | 5 轮循环、日志、确认中断、3 条真实轨迹 | 提示词 7 |
| [10](docs/10-评测报告.md) | 30 条用例、结果、抓到的 2 个真 bug | 提示词 8 |
| [11](docs/11-上线检查清单.md) | 5 维度 × 5 项，含负责人/验证方法/通过标准 | 提示词 9 |
| [12](docs/12-项目结构与交付说明.md) | 交付对照、推送说明、后续路线 | 提示词 10 |

## 配置（可选）

复制 `.env.example` 为 `.env`：

```ini
# 不配也能跑：系统自动切"规则大脑 + 检索兜底"模式，演示与评测都在该模式下 100% 通过
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-xxxx
JWT_SECRET=请改成随机串
```

## 许可

MIT。制度文本为示例数据（`company_reimbursement.md` V3.2），非真实企业制度。
