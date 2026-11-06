# -*- coding: utf-8 -*-
"""详细项目设计方案（docx）：架构、模块、数据、接口、人工闸门、部署、演进。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import charts
import docx_kit as dk
from facts import META, MILESTONES

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "docs" / "07-项目设计方案"
ASSETS = BASE / "_assets"
OUT.mkdir(parents=True, exist_ok=True)


def build_images():
    arch = charts.arch_diagram(
        ASSETS / "design-arch.png",
        "系统总体架构：四层、一库、四道闸门",
        "接入层 → 应用层 → 智能层 → 数据层；所有金额判定在规则层，所有建单在确认闸门之后",
        [
            ("接入层", charts.BLUE, ["浏览器（员工端）", "管理后台", "企业微信（预留）", "AD / SSO（预留）"]),
            ("应用层", charts.NAVY, ["FastAPI 路由", "SSE 流式输出", "Jinja2 模板", "鉴权与审计"]),
            ("智能层", charts.TEAL, ["Agent 循环（≤5 轮）", "6 个工具", "六条硬规则", "BM25+向量+RRF 检索"]),
            ("数据层", charts.AMBER, ["SQLite（10 表）", "制度原文与切片", "工具调用日志", "草稿与审计标记"]),
        ],
        source="说明：智能层不直接写库；所有写操作经由工具层与确认闸门",
        width=1150, height=700,
    )
    gate = charts.flow_diagram(
        ASSETS / "design-gate.png",
        "建单链路：四道闸门，缺一不可",
        "从“问一句”到“草稿落库”的完整路径",
        [
            {"title": "检索制度", "desc": "search_policy → Top3 片段 + 条款号", "actor": "工具", "kind": "ai", "note": "无依据则弃答"},
            {"title": "计算标准", "desc": "query_expense_standard：城市 × 职级 × 天数", "actor": "规则", "kind": "ai", "note": "金额不由模型计算"},
            {"title": "校验发票", "desc": "抬头/税号/时效/查重/科目/禁报名目", "actor": "规则", "kind": "ai", "note": "失败返回 error_code"},
            {"title": "代码闸门", "desc": "create_draft 检查本会话 validated 标记", "actor": "代码", "kind": "human", "note": "未校验 → VALIDATION_REQUIRED"},
            {"title": "人工确认", "desc": "前端 confirm_required 卡片，员工点确认", "actor": "员工", "kind": "human", "note": "未确认 0 条落库"},
            {"title": "落库并给审批链", "desc": "drafts + draft_items + 审批人清单", "actor": "系统", "kind": "normal", "note": "记录确认人与时间"},
        ],
        source="对应 FR-03/04/05/06 与 AC-03/04",
        width=1150, height=800,
    )
    return arch, gate


def build():
    doc = dk.new_document(footer_text="详细设计方案 · V1.0")
    arch, gate = build_images()

    dk.title_page(
        doc,
        "详 细 项 目 设 计 方 案",
        "规则可审计，回答有出处，建单有闸门",
        f"{META['client']} · 报销助手智能体系统 · 技术设计与实施方案",
        [("客户", META["client"]),
         ("项目", f"{META['project']}（{META['code']}）"),
         ("版本", f"{META['version']}"),
         ("日期", META["date"]),
         ("技术栈", "Python 3.11+ / FastAPI / SQLite / 原生 JS"),
         ("部署形态", "内网单机 · 8C16G · 无 GPU")],
        note="本方案与《需求分析方案》V1.0 配套，需求编号一一对应。",
    )
    dk.add_toc(doc)

    dk.h1(doc, "一、设计目标与原则")
    dk.para(doc, "本方案要交付的不是一个“会聊天的报销机器人”，而是一条**可审计的提交前预检链路**。"
                 "所有设计取舍都服从五条原则：")
    dk.table(doc,
             ["原则", "含义", "落地方式", "反面案例"],
             [["规则代码化", "金额、时效、标准、查重由代码判定", "app/agent/rules.py 唯一裁判", "让模型“估算”住宿上限"],
              ["回答可引用", "每句结论能指到条款与版本", "检索片段带 doc/section/clause", "只给结论不给依据"],
              ["建单有闸门", "有副作用的动作必须可中断", "confirm_required + 代码校验", "模型直接写库"],
              ["降级可用", "模型挂了系统不整体不可用", "HeuristicBrain / 条文兜底", "白屏或 500"],
              ["零依赖可复现", "无 Key、无外部服务也能演示", "标准库实现检索/规则/Agent", "必须装 30 个包才能跑"]],
             widths=[3.0, 6.2, 6.2, 4.6])
    dk.callout(doc, "一个关键取舍",
               "不引入 LangChain 等 Agent 框架。核心循环只有约 60 行，引入框架会增加 30+ 依赖与黑盒状态机，"
               "而本项目需要的四件事（工具 schema、轮次上限、逐步日志、可中断）都能用标准库表达。")

    dk.h1(doc, "二、总体架构")
    dk.h2(doc, "2.1 分层视图")
    dk.figure(doc, arch, "图 1  系统总体架构（四层）", 16.0)
    dk.table(doc,
             ["层", "职责", "关键文件", "不做什么"],
             [["接入层", "浏览器/后台/企微入口，登录态", "app/web/templates、static", "不承载业务规则"],
              ["应用层", "路由、SSE、鉴权、审计", "app/web/*.py、app/main.py", "不直接调模型"],
              ["智能层", "Agent 循环、工具、规则、检索", "app/agent/*、app/rag/index.py", "不直接写库（经工具）"],
              ["数据层", "SQLite、制度原文、日志", "app/db.py、data/", "不存明文口令"]],
             widths=[2.4, 7.0, 6.0, 4.6])
    dk.h2(doc, "2.2 技术选型")
    dk.table(doc,
             ["领域", "选型", "理由", "备选/演进"],
             [["Web 框架", "FastAPI + Uvicorn", "异步、SSE 原生、类型友好", "Flask（同步，SSE 需额外处理）"],
              ["模板/前端", "Jinja2 + 原生 JS", "无构建、无 CDN、内网可用", "Vue/React（增加构建链）"],
              ["检索", "BM25 + 哈希向量 + RRF", "零依赖、可解释、切片量级小", "FAISS/Milvus（二期）"],
              ["存储", "SQLite（WAL）", "单机、备份=拷贝、事务足够", "PostgreSQL（多实例时）"],
              ["模型", "OpenAI 兼容协议", "供应商可替换、无 SDK 锁定", "私有化 vLLM（二期）"],
              ["鉴权", "PBKDF2 + 手写 HS256 JWT", "标准库实现、可审计", "OIDC/AD SSO（预留）"]],
             widths=[3.0, 5.4, 7.2, 5.0])
    dk.h2(doc, "2.3 部署拓扑")
    dk.para(doc, "单台 8C16G 虚拟机，内网 443 反向代理到 127.0.0.1:8000；SQLite 与制度文件同机；"
                 "夜间 02:00 备份到 NAS；模型 API 出网经白名单；无 GPU、无消息队列、无 Redis。")
    dk.table(doc,
             ["组件", "规格", "数量", "说明"],
             [["应用 VM", "8 vCPU / 16GB / 200GB SSD", "1", "Ubuntu 22.04 或 Windows Server 2019+"],
              ["反向代理", "Nginx / IIS", "1", "TLS 终止、限流、静态缓存"],
              ["数据库", "SQLite 文件", "1", "WAL 模式，随应用部署"],
              ["备份", "NAS / 对象存储", "1", "每日全量 + 保留 30 天"],
              ["模型", "外部 API 或私有化", "—", "默认外部 API，可切离线模式"]],
             widths=[3.4, 6.6, 2.0, 8.0])

    dk.h1(doc, "三、模块设计")
    dk.h2(doc, "3.1 检索层（app/rag/index.py）")
    dk.para(doc, "制度文本按 500 字切片、50 字重叠，中文按 2-gram 分词；BM25 与 256 维哈希向量并行召回，"
                 "RRF（k=60）融合取 Top3。三道门禁保证“不乱答”：")
    dk.table(doc,
             ["门禁", "规则", "作用"],
             [["相关性门禁", "融合分数低于阈值 → 视为无依据", "避免硬凑条文"],
              ["域外词表", "命中健身房/宠物/礼金等 → 直接弃答", "覆盖制度外问题"],
              ["条块完整性", "切片必须带标题/小节/条款号", "保证引用可核对"]],
             widths=[3.4, 8.6, 5.0])
    dk.para(doc, "检索结果进入提示词时只保留“标题 + 小节 + 条款号 + 原文”，模型被要求在回答中引用；"
                 "引用卡由后端 `render_citations` 统一生成，避免模型编造来源。")
    dk.h2(doc, "3.2 规则层（app/agent/rules.py）")
    dk.para(doc, "规则层是唯一裁判，输出结构化结论（是否通过、金额、条款、解释），不生成自然语言长文。")
    dk.table(doc,
             ["规则", "判定内容", "边界值", "输出"],
             [["R1 时效", "费用发生日起 90 个自然日", "第 90 天可报，第 91 天拒绝", "deadline_ok + 剩余天数"],
              ["R2 住宿", "城市等级 × 职级三档", "超 1 元即超标；上浮 ≤50% 需特批", "cap、超标金额、自付金额"],
              ["R3 伙食", "一线 100 / 新一线 80 / 其他 60；当天往返 50%", "半天 = 50%，四舍五入到元", "allowance"],
              ["R4 发票要素", "抬头=公司全称、税号精确匹配", "错 1 位即不通过", "逐字段结论"],
              ["R5 查重", "发票号全局唯一", "跨用户、跨会话", "占用草稿号"],
              ["R6 审批链", "≥5,000 部门负责人；≥20,000 财务总监", "含等于；叠加不替代", "审批人列表"]],
             widths=[3.0, 6.6, 6.4, 4.8])
    dk.callout(doc, "为什么规则不用模型",
               "同样的输入必须得到同样的金额，且要能解释到条款。模型的温度参数、上下文长度、供应商升级都会改变输出；"
               "审计场景不能接受“上次算 500，这次算 520”。")
    dk.h2(doc, "3.3 工具层（app/agent/tools.py）")
    dk.table(doc,
             ["工具", "副作用", "参数（required）", "失败约定"],
             [["search_policy", "无", "query", "空结果 → 弃答"],
              ["query_expense_standard", "无", "city（job_level/days 可选）", "未知城市 → 追问问清"],
              ["request_invoice_info", "无", "fields", "不编造占位值"],
              ["validate_invoice", "无", "invoice_no/amount/invoice_date", "返回逐条问题"],
              ["create_draft", "写库", "trip_city/items[]", "未校验 → VALIDATION_REQUIRED"],
              ["list_my_drafts", "无", "limit", "仅本人"]],
             widths=[4.6, 2.0, 6.8, 5.0])
    dk.para(doc, "工具失败不抛异常，统一返回 `{ok:false, error_code, message, hint}`，让模型能读懂“下一步做什么”，"
                 "也让人能看懂“为什么没成”。")
    dk.h2(doc, "3.4 Agent 编排（app/agent/engine.py）")
    dk.para(doc, "自研 5 轮循环：组装提示词 → 载入最近 6 轮历史 → 模型给出 tool_calls → 执行/中断 → 回填结果 → 下一轮。"
                 "终止条件四种：不再调工具、用完 5 轮、请求建单（中断）、LLM 异常（降级）。")
    dk.para(doc, "双大脑设计：有 API Key 用 LLMBrain，无 Key 用 HeuristicBrain（确定性意图路由）。"
                 "两者共用工具、闸门、日志表，因此升级模型不改业务代码，评测也能稳定复现。")
    dk.h2(doc, "3.5 Web 与前端（app/web/）")
    dk.table(doc,
             ["页面/接口", "角色", "关键交互", "安全"],
             [["/chat", "员工", "SSE 流式、引用卡、确认卡、草稿", "登录 + 本人会话"],
              ["/drafts", "员工", "草稿列表、状态", "仅本人"],
              ["/admin/policies", "管理员", "上传/删除/重建切片", "二次确认 + 软删除"],
              ["/admin/prompts", "管理员", "编辑模板、版本 +1", "管理员守卫"],
              ["/admin/audit", "管理员", "按人/时间/标记筛选", "审计留痕"],
              ["/admin/logs", "管理员", "工具调用明细", "只读"]],
             widths=[4.6, 2.6, 7.2, 4.0])
    dk.para(doc, "前端不引入框架与 CDN：手写 SSE 解析（fetch + ReadableStream）、打字机效果、引用卡与确认卡，"
                 "保证内网离线可用、首屏无外部请求。")

    dk.h1(doc, "四、关键流程")
    dk.h2(doc, "4.1 建单链路与四道闸门")
    dk.figure(doc, gate, "图 2  建单链路：检索 → 计算 → 校验 → 代码闸门 → 人工确认 → 落库", 15.6)
    dk.para(doc, "第 3 步与第 4 步是两回事：`validate_invoice` 是“算”，`create_draft` 里的检查是“卡”。"
                 "即使模型被诱导跳过校验，代码闸门仍会返回 VALIDATION_REQUIRED；即使闸门被绕过，"
                 "前端没有员工确认也不会落库。三层防御分别对应模型、代码、人。")
    dk.h2(doc, "4.2 一次问答的 SSE 事件序列")
    dk.table(doc,
             ["顺序", "事件", "载荷", "前端行为"],
             [["1", "status", "阶段提示", "显示“正在检索制度…”"],
              ["2", "tool_call", "工具名/参数", "折叠显示工具条"],
              ["3", "tool_result", "结果摘要", "更新工具条状态"],
              ["4", "token", "增量文本", "打字机输出"],
              ["5", "citations", "条款/文档/片段", "渲染引用卡"],
              ["6", "confirm_required", "草稿预览", "渲染确认卡并禁用输入"],
              ["7", "done", "耗时/模式", "恢复输入"]],
             widths=[1.8, 4.2, 6.6, 6.0])
    dk.h2(doc, "4.3 知识库更新")
    dk.para(doc, "上传 → 解析（md/txt/pdf）→ 切片（500/50）→ 入库 → 重建索引 → 立即可检索。"
                 "同名文档自动把旧版置为 archived，检索只命中当前版；删除为软删除，切片立即失效，"
                 "但保留审计记录。")

    dk.h1(doc, "五、接口设计")
    dk.table(doc,
             ["方法", "路径", "角色", "说明"],
             [["GET", "/healthz", "公开", "健康检查"],
              ["POST", "/api/auth/login", "公开", "登录，写 httpOnly Cookie"],
              ["POST", "/api/chat/stream", "员工", "SSE 问答"],
              ["POST", "/api/chat/confirm", "员工", "确认/拒绝草稿"],
              ["GET", "/api/chat/sessions", "员工", "本人会话列表"],
              ["GET", "/api/drafts", "员工", "本人草稿"],
              ["POST", "/api/admin/policies", "管理员", "上传制度"],
              ["DELETE", "/api/admin/policies/{id}", "管理员", "删除（软删）"],
              ["POST", "/api/admin/policies/{id}/reindex", "管理员", "重建切片"],
              ["POST", "/api/admin/prompts", "管理员", "保存提示词（版本 +1）"],
              ["GET", "/api/admin/audit", "管理员", "审计查询"]],
             widths=[2.0, 8.0, 2.6, 6.0])

    dk.h1(doc, "六、数据模型")
    dk.para(doc, "SQLite 10 张表，零 ORM；所有金额以“分”为整数存储，避免浮点误差。")
    dk.table(doc,
             ["表", "用途", "关键字段", "索引/约束"],
             [["users", "账号与角色", "username, pwd_hash, role, job_level", "username 唯一"],
              ["sessions", "会话", "user_id, title, updated_at", "user_id+updated_at"],
              ["messages", "消息", "session_id, role, content, meta", "session_id+id"],
              ["policy_docs", "制度文档", "title, version, effective_date, status", "同名归档"],
              ["policy_chunks", "切片", "doc_id, ord, text, section", "doc_id+ord"],
              ["drafts", "报销单草稿", "user_id, trip_city, total, status", "user_id+created_at"],
              ["draft_items", "费用明细", "draft_id, category, amount, invoice_no", "draft_id"],
              ["agent_logs", "工具调用", "session_id, tool, args, ms, result", "session_id+id"],
              ["prompt_templates", "提示词", "key, content, version", "key 唯一"],
              ["audit_flags", "审计标记", "message_id, reason, status", "message_id"]],
             widths=[3.4, 3.6, 7.8, 4.4], font_size=8.4)
    dk.callout(doc, "金额与发票号的处理",
               "金额统一存整数分；发票号在库内明文（查重需要），在页面掩码（只显示前 2 后 4）；"
               "税号不落日志。这是“可审计”与“最小披露”的折中。")

    dk.h1(doc, "七、人工确认与审核点")
    dk.para(doc, "本系统有 5 处人工确认/审核点，其中 3 处为强制闸门（不可配置关闭）：")
    dk.table(doc,
             ["编号", "位置", "触发", "人工动作", "强制", "留痕"],
             [["H1", "员工端 · 建单", "create_draft 请求", "核对金额/明细/审批链后点“确认生成”", "是", "确认人、时间、草稿号"],
              ["H2", "管理端 · 删除制度", "点击删除", "二次弹窗确认；软删除", "是", "操作人、文档、时间"],
              ["H3", "管理端 · 审计", "命中风险关键词", "人工复核，标记“已核实/误报”", "是（高风险）", "复核人、结论"],
              ["H4", "管理端 · 提示词", "保存模板", "管理员确认发布，版本 +1", "否（管理员权限）", "版本、diff"],
              ["H5", "财务 · 最终审核", "草稿进入 OA 后", "审核员按预检清单复核并签批", "是（线下）", "OA 审批记录"]],
             widths=[1.4, 4.0, 4.2, 6.4, 3.2, 4.0], font_size=8.0)
    dk.para(doc, "H1 的实现细节：`create_draft` 到达时不执行，先写一条 status=pending 的助手消息，"
                 "推送 `confirm_required` 并结束本轮；确认接口读取 pending 中保存的“已通过校验的发票号”，"
                 "恢复校验态后再执行。重复确认会被拒绝，避免重复建单。")

    dk.h1(doc, "八、安全与合规设计")
    dk.table(doc,
             ["维度", "措施", "验证"],
             [["认证", "PBKDF2-SHA256（12 万次）+ httpOnly Cookie JWT（12h）", "登录/过期/篡改用例"],
              ["授权", "require_user / require_admin 双守卫；他人资源按 404", "角色越权用例"],
              ["注入", "参数化 SQL；前端转义；提示词注入按“普通文本”处理", "对抗用例"],
              ["隐私", "发票号/税号掩码；日志脱敏；不记录口令", "页面抽查"],
              ["传输", "内网 HTTPS；模型 API 白名单", "安全评审"],
              ["审计", "工具调用、确认、删除、提示词版本全留痕", "重建 20 条"],
              ["备份", "每日全量 + 30 天保留；恢复演练", "演练报告"]],
             widths=[2.6, 10.4, 5.0])

    dk.h1(doc, "九、性能与容量")
    dk.table(doc,
             ["指标", "目标", "设计手段", "实测/备注"],
             [["检索 P95", "< 200ms", "内存索引、500 字切片、Top3", "本地实测满足"],
              ["SSE 首字", "< 1s（离线）", "先推 status，再流式 token", "依赖模型时 1–3s"],
              ["单票预检", "< 300ms", "纯规则，无模型调用", "本地实测 < 50ms"],
              ["并发", "20 在线", "单 worker + 异步 IO；模型侧限流", "压测待执行"],
              ["数据量", "3 年 6.6 万单", "SQLite 索引 + 分页", "容量 < 2GB"],
              ["可用性", "99.5%", "健康检查 + 自动重启", "试点期监控"]],
             widths=[3.4, 4.4, 7.4, 4.0])

    dk.h1(doc, "十、实施计划与交付物")
    dk.table(doc,
             ["阶段", "周期", "关键任务", "里程碑"],
             [[m[0], m[1], m[2], m[3]] for m in MILESTONES],
             widths=[2.2, 2.6, 9.6, 4.4])
    dk.table(doc,
             ["交付物", "形式", "验收方式"],
             [["需求分析方案", "DOCX", "需求评审会签"],
              ["详细设计方案", "DOCX（本文）", "技术评审"],
              ["MVP 系统", "源代码 + 部署包", "冒烟 31 项 + 评测 30 条"],
              ["测试方案与报告", "MD + DOCX", "测试评审"],
              ["上线交付运行手册", "DOCX", "运维演练"],
              ["培训材料", "PPT/文档", "培训考核 ≥80 分"]],
             widths=[6.0, 5.0, 7.4])

    dk.h1(doc, "十一、演进路线")
    dk.table(doc,
             ["阶段", "能力", "触发条件"],
             [["一期（本期）", "问答、预检、草稿、确认、审计", "验收通过"],
              ["二期", "OCR 识别、税务查验、OA 单向推送", "试点退单率 ≤8% 且预算批准"],
              ["三期", "费用预测、预算预警、多组织多租户", "全公司推广后"],
              ["技术演进", "向量库、私有化模型、SSO", "数据量 >50 万切片或合规要求"]],
             widths=[3.6, 9.4, 5.0])

    path = OUT / "某某科技-报销助手-详细项目设计方案.docx"
    doc.save(path)
    print("design ok", path)


if __name__ == "__main__":
    build()
