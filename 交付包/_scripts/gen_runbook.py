# -*- coding: utf-8 -*-
"""上线交付运行手册（docx）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import docx_kit as dk
from facts import META

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "docs" / "09-上线交付"
OUT.mkdir(parents=True, exist_ok=True)


def build():
    doc = dk.new_document(footer_text="上线交付运行手册 · V1.0")
    dk.title_page(
        doc,
        "上 线 交 付 运 行 手 册",
        "从一台空虚拟机到可用系统",
        f"{META['client']} · {META['project']} · 部署、运维、应急与验收",
        [("客户", META["client"]),
         ("项目", META["project"]),
         ("版本", "V1.0（交付版）"),
         ("日期", META["date"]),
         ("适用对象", "IT 运维 / 财务系统管理员 / 项目组"),
         ("部署形态", "内网单机 · 8C16G · 无 GPU")],
        note="本手册中的命令均已在交付环境验证；执行前请先阅读第 4 章“变更与回滚”。",
    )
    dk.add_toc(doc)

    dk.h1(doc, "一、文档说明")
    dk.h2(doc, "1.1 读者与用途")
    dk.table(doc,
             ["读者", "关注章节", "使用方式"],
             [["IT 运维", "3–7、10–12", "按第 5 章完成部署，按第 9 章接入监控"],
              ["财务系统管理员", "6–8", "账号、制度上传、日常巡检"],
              ["项目组", "1–2、13–15", "交付验收、培训与支持"],
              ["安全/审计", "3.4、8.4、12", "数据边界、日志与应急"]],
             widths=[4.4, 6.0, 7.6])
    dk.h2(doc, "1.2 术语")
    dk.table(doc,
             ["术语", "含义"],
             [["MVP", "最小可用版本：问答 + 发票预检 + 草稿确认 + 管理后台"],
              ["SSE", "Server-Sent Events，服务端流式推送"],
              ["RAG", "检索增强生成：先检索制度，再让模型基于片段回答"],
              ["闸门", "代码中不可绕过的检查点，如建单前的 validate_invoice 校验"],
              ["H1–H5", "五处人工确认/审核点，见《详细设计方案》第七章"]],
             widths=[3.0, 15.0])
    dk.h2(doc, "1.3 交付物清单")
    dk.table(doc,
             ["类别", "文件", "位置"],
             [["源代码", "app/、run.py、quickstart.py、requirements.txt", "项目根目录"],
              ["制度数据", "company_reimbursement.md（示例，需替换）", "data/policies/"],
              ["需求与设计", "需求分析方案、详细设计方案", "交付包/docs/06、07"],
              ["报价", "三档报价方案", "交付包/docs/08"],
              ["测试", "测试方案 + 5 份场景过程文档", "Test/"],
              ["本手册", "上线交付运行手册", "交付包/docs/09"]],
             widths=[3.4, 9.0, 5.6])

    dk.h1(doc, "二、系统概述")
    dk.para(doc, "系统是一个单机 Web 应用：员工在浏览器（或企业微信内置浏览器）里提问、验票、生成草稿；"
                 "管理员在后台维护制度与提示词、查看审计。后端 FastAPI + SQLite，检索与规则全部本地计算，"
                 "模型调用可配置、可关闭。")
    dk.table(doc,
             ["组件", "进程/文件", "端口", "说明"],
             [["Web 应用", "python run.py / uvicorn", "8000（可配）", "员工端 + 管理后台"],
              ["反向代理", "Nginx / IIS", "443", "TLS、限流、静态缓存"],
              ["数据库", "data/reimb.db（SQLite WAL）", "—", "文件级备份"],
              ["制度原文", "data/policies/*.md", "—", "启动时自动切片入库"],
              ["日志", "标准输出 / 应用日志", "—", "保留 180 天"]],
             widths=[3.6, 6.6, 3.4, 4.4])

    dk.h1(doc, "三、环境要求")
    dk.h2(doc, "3.1 硬件与操作系统")
    dk.table(doc,
             ["项目", "最低", "推荐", "说明"],
             [["CPU", "4 核", "8 核", "检索为内存计算，核数影响并发"],
              ["内存", "8 GB", "16 GB", "切片索引常驻内存"],
              ["磁盘", "100 GB", "200 GB SSD", "含 3 年单据与备份"],
              ["操作系统", "Windows Server 2019 / Ubuntu 20.04", "Ubuntu 22.04 LTS", "需长期支持版本"],
              ["GPU", "不需要", "不需要", "模型走 API 或离线规则"]],
             widths=[3.2, 5.4, 5.0, 4.4])
    dk.h2(doc, "3.2 软件依赖")
    dk.table(doc,
             ["软件", "版本", "用途", "检查命令"],
             [["Python", "3.11+", "运行时", "python --version"],
              ["pip", "23+", "依赖安装", "python -m pip --version"],
              ["FastAPI / Uvicorn", "requirements.txt 锁定", "Web 框架", "pip show fastapi"],
              ["Nginx / IIS", "任意稳定版", "反向代理与 TLS", "nginx -v"],
              ["浏览器", "Chrome/Edge 100+", "员工端", "—"]],
             widths=[4.0, 4.6, 5.0, 4.4])
    dk.h2(doc, "3.3 网络与端口")
    dk.bullets(doc, [
        "内网用户 → 443（反向代理）→ 127.0.0.1:8000（应用）。",
        "应用不需要公网入口；如需调用外部模型 API，仅放行 443 到供应商域名。",
        "数据库与制度文件不对外暴露；备份走内网 NAS。",
        "NTP 必须可用：JWT 过期与审计时间依赖系统时钟。",
    ], numbered=True)
    dk.h2(doc, "3.4 安全前置条件")
    dk.table(doc,
             ["检查项", "要求", "责任人"],
             [["数据分级", "确认为“内部”，不含个人敏感信息", "孙宁"],
              ["外采模型", "完成安全评估，签署数据处理协议", "孙宁/陈昊"],
              ["证书", "内网 CA 或公网证书，TLS 1.2+", "陈昊"],
              ["账号", "默认口令已修改，管理员单独账号", "李静"],
              ["备份", "NAS 空间 ≥ 500GB，权限仅运维可写", "陈昊"]],
             widths=[4.0, 9.6, 4.0])

    dk.h1(doc, "四、部署前准备")
    dk.h2(doc, "4.1 变更与回滚")
    dk.para(doc, "上线窗口为周四 20:00–22:00，回滚窗口 30 分钟。变更前必须：① 备份当前数据库与配置；"
                 "② 记录当前版本号与 commit；③ 通知财务值班人；④ 准备回滚脚本。")
    dk.h2(doc, "4.2 目录规划")
    dk.table(doc,
             ["路径", "内容", "权限"],
             [["/opt/reimburse/app", "源代码", "只读（应用账号）"],
              ["/opt/reimburse/data", "SQLite 与制度文件", "读写（应用账号）"],
              ["/opt/reimburse/backup", "本地备份（可选）", "读写（运维）"],
              ["/var/log/reimburse", "应用日志", "读写（应用账号）"],
              ["/etc/reimburse/.env", "环境变量（含密钥）", "600，仅应用账号"]],
             widths=[6.0, 7.4, 4.2])
    dk.h2(doc, "4.3 配置项")
    dk.table(doc,
             ["变量", "默认", "生产建议", "说明"],
             [["REIMB_DATA_DIR", "./data", "/opt/reimburse/data", "数据目录"],
              ["REIMB_DB_PATH", "data/reimb.db", "/opt/reimburse/data/reimb.db", "数据库路径"],
              ["JWT_SECRET", "dev-only-secret-change-me", "32 位随机串", "必须修改"],
              ["JWT_TTL_HOURS", "12", "8", "按安全要求缩短"],
              ["LLM_BASE_URL", "https://api.deepseek.com/v1", "按采购配置", "OpenAI 兼容"],
              ["LLM_MODEL", "deepseek-chat", "按合同", "模型名"],
              ["LLM_API_KEY", "空", "从密钥管理注入", "为空则离线模式"],
              ["LLM_TIMEOUT", "60", "30", "超时降级"],
              ["RETRIEVAL_TOP_K", "3", "3", "引用条数"],
              ["MAX_AGENT_ROUNDS", "5", "5", "工具轮次上限"]],
             widths=[4.6, 6.4, 4.6, 4.2], font_size=8.4)

    dk.h1(doc, "五、部署步骤（Linux）")
    dk.para(doc, "以下命令以 Ubuntu 22.04、应用账号 reimb 为例；Windows 部署见 5.4。")
    dk.h2(doc, "5.1 安装与初始化")
    dk.callout(doc, "Step 1 · 创建账号与目录",
               "sudo useradd -r -s /bin/bash reimb\n"
               "sudo mkdir -p /opt/reimburse/{app,data,backup} /var/log/reimburse\n"
               "sudo chown -R reimb:reimb /opt/reimburse /var/log/reimburse")
    dk.callout(doc, "Step 2 · 上传代码并安装依赖",
               "cd /opt/reimburse/app\n"
               "python3 -m venv .venv && source .venv/bin/activate\n"
               "pip install -r requirements.txt")
    dk.callout(doc, "Step 3 · 配置环境变量",
               "sudo install -m 600 .env.example /etc/reimburse/.env\n"
               "# 编辑 /etc/reimburse/.env：JWT_SECRET、REIMB_DATA_DIR、LLM_API_KEY")
    dk.callout(doc, "Step 4 · 初始化数据库与制度",
               "# 首次启动会自动建表、写入示例制度与账号\n"
               "python run.py --port 8000 --no-browser")
    dk.callout(doc, "Step 5 · 验证",
               "curl -s http://127.0.0.1:8000/healthz\n"
               "# 期望：{\"status\":\"ok\"}")
    dk.h2(doc, "5.2 注册为系统服务（systemd）")
    dk.callout(doc, "Step 6 · systemd 单元",
               "sudo tee /etc/systemd/system/reimburse.service >/dev/null <<'EOF'\n"
               "[Unit]\nDescription=Reimburse Copilot\nAfter=network.target\n\n"
               "[Service]\nUser=reimb\nWorkingDirectory=/opt/reimburse/app\n"
               "EnvironmentFile=/etc/reimburse/.env\n"
               "ExecStart=/opt/reimburse/app/.venv/bin/python run.py --port 8000 --no-browser\n"
               "Restart=always\nRestartSec=5\n\n"
               "[Install]\nWantedBy=multi-user.target\nEOF\n"
               "sudo systemctl daemon-reload && sudo systemctl enable --now reimburse")
    dk.h2(doc, "5.3 反向代理（Nginx）")
    dk.callout(doc, "Step 7 · Nginx 配置",
               "server {\n  listen 443 ssl;\n  server_name reimburse.corp.example.com;\n"
               "  ssl_certificate /etc/ssl/certs/reimburse.crt;\n"
               "  ssl_certificate_key /etc/ssl/private/reimburse.key;\n"
               "  client_max_body_size 20m;\n"
               "  location / {\n    proxy_pass http://127.0.0.1:8000;\n"
               "    proxy_http_version 1.1;\n    proxy_set_header Host $host;\n"
               "    proxy_buffering off;   # SSE 必须关闭缓冲\n  }\n}")
    dk.h2(doc, "5.4 Windows 部署")
    dk.para(doc, "解压到 D:\\reimburse\\app，安装 Python 3.11+ 后执行 pip install -r requirements.txt，"
                 "双击 start.bat 或运行 python run.py；用“任务计划程序”设置开机启动，"
                 "并将 8000 端口限制为本机访问，由 IIS ARR 反向代理。")
    dk.callout(doc, "重要",
               "SSE 必须关闭代理缓冲（proxy_buffering off / IIS responseBufferLimit=0），"
               "否则前端会“一次性蹦出全文”，失去流式效果。")

    dk.h1(doc, "六、初始化与账号")
    dk.h2(doc, "6.1 默认账号（首次登录后立即修改）")
    dk.table(doc,
             ["角色", "账号", "初始口令", "处理"],
             [["管理员", "admin", "admin123", "首次登录改密；生产禁用默认值"],
              ["员工", "zhangwei", "emp123", "改为真实员工；或接入 AD"]],
             widths=[3.0, 4.0, 4.0, 6.0])
    dk.callout(doc, "安全提示",
               "生产环境必须通过环境变量 SEED_ADMIN_PWD / SEED_EMPLOYEE_PWD 覆盖默认口令，"
               "并在首次登录后再次修改；默认口令仅用于演示环境。")
    dk.h2(doc, "6.2 制度入库")
    dk.para(doc, "将现行制度 Markdown/Word 放入 data/policies/ 或在管理后台上传；上传后自动切片并立即可检索。"
                 "同名文档自动把旧版置为 archived，检索只命中当前版。")
    dk.bullets(doc, [
        "文件名建议：company_reimbursement_v3.2_20250101.md",
        "正文必须含版本号、生效日期、归口部门；条款编号保留（如 2.2）。",
        "上传后在“知识库”页查看切片数与命中测试，确认 Top3 含正确条款。",
    ], numbered=True)
    dk.h2(doc, "6.3 提示词初始化")
    dk.para(doc, "管理后台 → 提示词模板，确认 system_qa 与 system_agent 为最新版本；保存即版本 +1，"
                 "下一轮对话立即生效。修改前先在测试环境验证 30 条评测集。")

    dk.h1(doc, "七、日常运行")
    dk.h2(doc, "7.1 启停与状态")
    dk.table(doc,
             ["操作", "命令", "说明"],
             [["启动", "sudo systemctl start reimburse", "开机自启已启用"],
              ["停止", "sudo systemctl stop reimburse", "先确认无进行中的建单"],
              ["重启", "sudo systemctl restart reimburse", "约 3 秒"],
              ["状态", "sudo systemctl status reimburse", "看 Active 与最近日志"],
              ["健康检查", "curl -s localhost:8000/healthz", "返回 status ok"],
              ["实时日志", "journalctl -u reimburse -f", "或 tail -f /var/log/reimburse/app.log"]],
             widths=[3.2, 8.0, 6.0])
    dk.h2(doc, "7.2 每日巡检（5 分钟）")
    dk.table(doc,
             ["时间", "检查项", "通过标准", "异常处理"],
             [["09:00", "健康检查", "HTTP 200", "重启；仍失败升级"],
              ["09:05", "磁盘使用率", "< 80%", "清理日志/扩容"],
              ["09:10", "备份任务", "昨夜备份存在且 > 1MB", "手动补备"],
              ["09:15", "错误日志", "无连续 5xx", "记录并排查"],
              ["17:30", "待确认草稿", "无超过 24h 未处理的 pending", "提醒员工/清理"]],
             widths=[2.4, 5.0, 5.6, 5.0])
    dk.h2(doc, "7.3 备份与恢复")
    dk.para(doc, "SQLite 用在线备份命令，避免直接拷贝正在写入的文件。保留 30 天，每周做一次恢复演练（沙箱）。")
    dk.callout(doc, "备份脚本（每日 02:00）",
               "sqlite3 /opt/reimburse/data/reimb.db \".backup '/opt/reimburse/backup/reimb-$(date +%F).db'\"\n"
               "find /opt/reimburse/backup -name 'reimb-*.db' -mtime +30 -delete\n"
               "# 同步到 NAS：rsync -az /opt/reimburse/backup/ nas:/backup/reimburse/")
    dk.callout(doc, "恢复步骤（30 分钟内）",
               "1) systemctl stop reimburse\n"
               "2) cp /opt/reimburse/backup/reimb-2026-11-05.db /opt/reimburse/data/reimb.db\n"
               "3) 校验：sqlite3 reimb.db 'PRAGMA integrity_check;'\n"
               "4) systemctl start reimburse && curl -s localhost:8000/healthz")
    dk.h2(doc, "7.4 日志与审计")
    dk.table(doc,
             ["日志", "位置", "保留", "内容"],
             [["应用日志", "/var/log/reimburse/app.log", "180 天", "请求、错误、启动"],
              ["工具调用", "agent_logs 表", "180 天", "工具名/参数/耗时/结果摘要"],
              ["审计标记", "audit_flags 表", "180 天", "风险关键词、复核结论"],
              ["访问日志", "Nginx access.log", "90 天", "IP、UA、状态码"]],
             widths=[3.6, 6.0, 3.0, 5.0])

    dk.h1(doc, "八、监控与告警")
    dk.table(doc,
             ["指标", "采集方式", "告警阈值", "级别"],
             [["可用性", "/healthz 每分钟", "连续 3 次失败", "P1"],
              ["5xx 比例", "Nginx 日志", "> 1%（5 分钟）", "P1"],
              ["P95 响应", "应用埋点", "> 3s（10 分钟）", "P2"],
              ["磁盘", "node_exporter/脚本", "> 85%", "P2"],
              ["备份失败", "备份脚本退出码", "非 0", "P1"],
              ["模型调用失败", "应用日志", "> 20%/小时", "P2（自动降级）"],
              ["待确认积压", "drafts 表", "> 50 条且 > 24h", "P3"]],
             widths=[3.6, 6.0, 5.0, 3.4])

    dk.h1(doc, "九、故障处理")
    dk.table(doc,
             ["现象", "可能原因", "处置", "升级"],
             [["页面 502", "应用未启动/崩溃", "systemctl status；看日志；重启", "30 分钟未恢复 → P1"],
              ["登录后立即退出", "JWT_SECRET 变更/时钟漂移", "统一密钥；校时", "P2"],
              ["回答“制度中未找到”变多", "索引未建/制度被删", "管理后台重建切片", "P2"],
              ["建单一直提示未校验", "会话状态丢失", "重新校验发票；查 agent_logs", "P3"],
              ["确认后无草稿", "重复确认/事务失败", "查 drafts 与日志；手工补录", "P2"],
              ["模型超时", "网络/配额", "自动降级；检查 API Key 与配额", "P2"],
              ["上传制度失败", "格式/大小/编码", "转 UTF-8；< 20MB；md/txt/pdf", "P3"],
              ["磁盘满", "日志/备份未清理", "清日志；迁移备份；扩容", "P1"],
              ["数据库损坏", "断电/磁盘故障", "停服 → 恢复备份 → integrity_check", "P1"],
              ["越权访问告警", "账号共享/攻击", "禁用账号；查审计；通知安全", "P1"]],
             widths=[4.4, 4.6, 6.6, 4.0], font_size=8.2)
    dk.h2(doc, "9.1 模型不可用时的降级")
    dk.para(doc, "系统检测到 LLM 超时/异常时，自动切换为“只给条文 + 请人工判断”，并在回答顶部标注“当前为离线模式”。"
                 "运维无需干预；恢复后下一轮自动回到模型模式。若长期不可用，检查 LLM_API_KEY 与网络白名单。")
    dk.h2(doc, "9.2 应急联系")
    dk.table(doc,
             ["级别", "定义", "响应", "联系人"],
             [["P1", "全站不可用/数据损坏", "15 分钟响应，2 小时恢复", "陈昊 + 项目经理"],
              ["P2", "核心功能受损/性能严重下降", "30 分钟响应，4 小时恢复", "运维值班"],
              ["P3", "单点问题/体验问题", "下一工作日", "实施顾问"]],
             widths=[2.0, 7.6, 5.4, 5.0])

    dk.h1(doc, "十、升级与回滚")
    dk.h2(doc, "10.1 升级步骤")
    dk.bullets(doc, [
        "备份数据库与当前代码目录（tar）。",
        "在预发环境跑 `python scripts/smoke_e2e.py` 与 `python evals/run_eval.py`，全绿再上线。",
        "停服 → 替换代码 → pip install -r requirements.txt → 启动。",
        "验证 healthz、登录、问答、建单确认、后台 5 个页面。",
        "观察 30 分钟错误日志，无异常则关闭变更单。",
    ], numbered=True)
    dk.h2(doc, "10.2 回滚步骤")
    dk.para(doc, "停服 → 还原上一版代码目录 → 还原数据库备份（仅当本次有 schema 变更）→ 启动 → 验证。"
                 "全程目标 30 分钟内；回滚后 24 小时内提交根因分析。")

    dk.h1(doc, "十一、上线检查清单（Go-Live）")
    dk.table(doc,
             ["#", "检查项", "标准", "负责人", "结果"],
             [["1", "环境与端口", "443→8000 通，证书有效", "陈昊", "☐"],
              ["2", "配置与密钥", "JWT_SECRET 已改，API Key 注入", "陈昊", "☐"],
              ["3", "数据库", "初始化成功，integrity_check ok", "陈昊", "☐"],
              ["4", "制度", "V3.2 已入库，Top3 命中正确", "李静", "☐"],
              ["5", "账号", "默认口令已改，角色正确", "李静", "☐"],
              ["6", "冒烟", "31/31 通过", "测试", "☐"],
              ["7", "评测", "30/30，弃答 100%", "测试", "☐"],
              ["8", "备份", "手动备份成功，恢复演练通过", "陈昊", "☐"],
              ["9", "监控", "健康检查与告警接入", "陈昊", "☐"],
              ["10", "培训", "2 场完成，考核 ≥80 分", "实施", "☐"],
              ["11", "回滚包", "上一版代码与 DB 备份就绪", "陈昊", "☐"],
              ["12", "值班表", "上线首周 5×8 值班确认", "项目经理", "☐"]],
             widths=[1.2, 5.2, 7.4, 3.0, 2.2], align="center", font_size=8.4)

    dk.h1(doc, "十二、培训与支持")
    dk.table(doc,
             ["对象", "内容", "时长", "考核"],
             [["员工", "怎么问、怎么看引用、怎么确认草稿", "30 分钟", "3 题实操"],
              ["财务审核员", "问题清单、误判反馈、例外处理", "45 分钟", "5 个案例"],
              ["管理员", "制度上传、切片、提示词、审计", "60 分钟", "上传+回滚演练"],
              ["运维", "部署、备份、故障、回滚", "90 分钟", "独立恢复一次"]],
             widths=[3.4, 8.0, 3.0, 4.2])
    dk.para(doc, "支持期：中档 3 个月、高档 6 个月，5×8 响应；紧急问题走 P1 通道。")

    dk.h1(doc, "十三、附录")
    dk.h2(doc, "13.1 命令速查")
    dk.table(doc,
             ["目的", "命令"],
             [["启动（开发）", "python run.py --port 8000"],
              ["仅控制台演示", "python quickstart.py"],
              ["冒烟测试", "python scripts/smoke_e2e.py --base http://127.0.0.1:8000"],
              ["评测", "python evals/run_eval.py"],
              ["重建索引", "管理后台 → 知识库 → 重建切片"],
              ["查看日志", "journalctl -u reimburse -n 200 --no-pager"]],
             widths=[5.0, 11.6])
    dk.h2(doc, "13.2 交付确认")
    dk.table(doc,
             ["角色", "姓名", "签字", "日期"],
             [["客户项目负责人", "李静", "", ""],
              ["客户 IT", "陈昊", "", ""],
              ["乙方项目经理", "", "", ""],
              ["乙方实施顾问", "", "", ""]],
             widths=[5.4, 4.0, 4.4, 4.0])

    path = OUT / "某某科技-报销助手-上线交付运行手册.docx"
    doc.save(path)
    print("runbook ok", path)


if __name__ == "__main__":
    build()
