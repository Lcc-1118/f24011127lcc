# src · 源码与归档说明

> 对应任务要求：`src: 1、问答助手归档；2、MVP 项目：工程架构、Agent 框架、知识库，至少 3 处人工确认/审核点`

## 目录

```
src/
├── README.md                      # 本文件（总览与索引）
├── 01-问答助手归档/
│   ├── README.md                  # 归档说明：v0.9 单文件原型的设计与局限
│   └── 报销问答助手-v0.9-归档.zip  # 阶段 1 原型（可独立运行）
└── 02-MVP项目/
    ├── README.md                  # 工程架构 / Agent 框架 / 知识库 / 人工闸门
    └── 验收自查.md                 # 对照需求与验收项的源码级自查
```

## 一、问答助手归档（阶段 1）

阶段 1 的目标不是“做产品”，而是回答一个技术问题：**在企业报销制度这种小语料上，纯检索能不能撑住问答？**

结论：能撑住“查标准”，撑不住“算金额、验发票、建单”。因此阶段 2 演进为“规则 + Agent + 确认闸门”的 MVP。

归档内容：

| 文件 | 说明 |
|---|---|
| `prototype/reimbursement_assistant.py` | 单文件命令行原型：切片、BM25、引用、交互问答 |
| `quickstart.py` | 零依赖一键演示（控制台 3 场景） |
| `evals/cases.json` + `run_eval.py` | 30 条评测用例与跑分脚本 |

> 归档 zip 内保留原始代码与运行说明，不依赖 FastAPI，`python quickstart.py` 即可复现。

## 二、MVP 项目（阶段 2）

MVP 代码位于仓库根目录（`app/`、`run.py`、`data/`、`evals/`、`scripts/`），本目录只放“怎么读、怎么验”的说明，不复制代码，避免双份维护。

| 主题 | 文档 | 代码入口 |
|---|---|---|
| 工程架构 | `02-MVP项目/README.md` 第 1 章 | `app/main.py`、`app/web/` |
| Agent 框架 | 第 2 章 | `app/agent/engine.py`、`tools.py`、`prompts.py` |
| 知识库 | 第 3 章 | `app/rag/index.py`、`app/db.py` |
| 人工确认/审核点 | 第 4 章 | `engine.confirm`、`admin_routes`、`audit_flags` |
| 验收自查 | `验收自查.md` | `evals/`、`scripts/`、`Test/` |

## 三、快速验证

```bash
python quickstart.py                      # 控制台 3 场景，零依赖
python evals/run_eval.py                  # 30 条评测，当前 30/30
python run.py --port 8000                 # Web 版
python scripts/smoke_e2e.py --base http://127.0.0.1:8000   # 31 项冒烟
```

当前结果：评测 30/30、冒烟 31/31、场景测试 55/56（1 个 P2 缺陷，见 `Test/`）。
