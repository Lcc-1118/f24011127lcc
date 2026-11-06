# -*- coding: utf-8 -*-
"""报价方案（低/中/高三档）+ 投入产出测算（docx）。所有数字由同一模型计算。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import charts
import docx_kit as dk
from facts import META, TIERS

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "docs" / "08-报价方案"
ASSETS = BASE / "_assets"
OUT.mkdir(parents=True, exist_ok=True)

# 全公司口径的年化收益（已乘折算系数，元）
GROSS = {
    "财务审核工时": 183_150,
    "员工填单与咨询": 384_800,
    "退单返工": 141_920,
}
# 折算后计入的收益（75% / 40% / 60%）
BENEFIT = 183_150 * 0.75 + 384_800 * 0.40 + 141_920 * 0.60
# 首年只覆盖试点人数；第 2 年起按全公司推广，能力系数体现档位差异
COVERAGE = {"low": 0.08, "mid": 0.15, "high": 1.0}
CAPABILITY = {"low": 0.55, "mid": 0.90, "high": 1.05}
RAMP = [0.60, 0.85, 0.95]


def money(v):
    return f"{v:,.0f}"


def calc(tier):
    build = tier["days"] * tier["rate"]
    maint = build * tier["maintain"] + (15_000 if tier["key"] == "high" else 0)
    cov = COVERAGE[tier["key"]]
    cap = CAPABILITY[tier["key"]]
    base = BENEFIT * cap
    # 首年只覆盖试点人数；第 2/3 年公司级推广
    flows = [base * cov * RAMP[0], base * RAMP[1], base * RAMP[2]]
    cum = -build - maint
    payback = None
    for i, f in enumerate(flows, 1):
        prev = cum
        cum += f - maint
        if payback is None and cum >= 0:
            used = (0 - prev) / (cum - prev)
            payback = (i - 1 + used) * 12
    return {
        "build": build, "maint": maint, "total": build + maint,
        "flows": flows, "cum": cum, "roi": cum / (build + maint),
        "payback": payback, "cov": cov,
    }


def build():
    stats = {t["key"]: calc(t) for t in TIERS}

    doc = dk.new_document(footer_text="报价方案 · V1.0")
    dk.title_page(
        doc,
        "项 目 报 价 方 案",
        "三档交付，按效果扩展",
        f"{META['client']} · {META['project']} · 低 / 中 / 高三档报价与投入产出测算",
        [("客户", META["client"]),
         ("项目", META["project"]),
         ("版本", "V1.0（商务版）"),
         ("日期", META["date"]),
         ("报价有效期", "60 天"),
         ("币种/税率", "人民币 / 含 6% 增值税")],
        note="本报价为方案演练报价，最终以双方签署的合同与工作说明书（SOW）为准。",
    )
    dk.add_toc(doc)

    dk.h1(doc, "一、报价总览")
    dk.para(doc, "三档报价不是“功能打折”，而是三种交付范围与风险承担方式。低档验证“能不能用”，"
                 "中档验证“值不值得推广”，高档解决“全公司怎么用”。建议从**中档**起步：它覆盖全部 P0 + P1 能力，"
                 "足以支撑 3 个月试点的全部验收指标。")
    dk.table(doc,
             ["档位", "定位", "人天", "单价（元/人天）", "实施费（元）", "首年运维（元）", "首年总投入（元）"],
             [[t["name"], t["scope"].split("，")[0], str(t["days"]), money(t["rate"]),
               money(stats[t["key"]]["build"]), money(stats[t["key"]]["maint"]),
               money(stats[t["key"]]["total"])] for t in TIERS],
             widths=[4.2, 4.0, 1.6, 3.0, 3.2, 3.0, 3.2], font_size=8.0)
    dk.callout(doc, "报价口径",
               "单价含需求、设计、开发、测试、部署、培训与 3 个月护航（高档 6 个月）；"
               "不含客户侧硬件、网络、第三方模型 API 费用（按实际用量另计，试点期预计 < 3,000 元/月）。")

    dk.h1(doc, "二、三档范围对比")
    dk.table(doc,
             ["能力 / 服务", "低档", "中档", "高档"],
             [["制度问答（RAG + 引用 + 弃答）", "✓", "✓", "✓"],
              ["发票六项硬规则预检", "✓", "✓", "✓"],
              ["草稿生成 + 人工确认", "✓", "✓", "✓"],
              ["审批链路与超标解释", "—", "✓", "✓"],
              ["退单归因看板", "—", "✓", "✓"],
              ["合规审计与人工复核", "—", "✓", "✓"],
              ["提示词版本管理", "基础", "完整", "完整"],
              ["AD/SSO 对接", "—", "预研", "实施"],
              ["票据 OCR / 税务查验", "—", "接口预留", "1 类票测试"],
              ["费用预测原型", "—", "—", "✓"],
              ["试点范围", "50 人 / 1 部门", "180 人 / 3 部门", "1,200 人 / 全公司"],
              ["培训与护航", "1 场 / 1 个月", "2 场 / 3 个月", "4 场 / 6 个月"],
              ["交付文档", "需求 + 手册", "全套 6 类", "全套 + 推广包"]],
             widths=[8.4, 3.2, 3.2, 3.2], align="center", font_size=8.6)

    dk.h1(doc, "三、人天与角色构成")
    dk.table(doc,
             ["角色", "低档", "中档", "高档", "单价（元/人天）"],
             [["项目经理", "8", "14", "18", "3,200"],
              ["解决方案架构师", "10", "18", "26", "3,600"],
              ["后端工程师", "22", "38", "54", "2,600"],
              ["前端工程师", "8", "16", "22", "2,400"],
              ["测试工程师", "10", "16", "22", "2,200"],
              ["实施/培训顾问", "10", "16", "26", "2,200"],
              ["合计", "68", "118", "168", "综合 2,600"]],
             widths=[5.0, 2.6, 2.6, 2.6, 4.0], align="center")
    dk.para(doc, "综合单价 2,600 元/人天为加权结果；实际结算按角色单价 × 实际人天，超出部分走变更单。")

    dk.h1(doc, "四、投入产出测算")
    dk.h2(doc, "4.1 收益口径")
    dk.para(doc, "收益只计算可被台账验证的三项，且全部打折计入，避免“把节省的工时全部当现金”：")
    dk.table(doc,
             ["收益项", "计算口径", "全公司年化（元）", "折算系数", "计入（元）"],
             [["财务审核工时", "22,200 单 × 5.5 分钟 × 90 元/时", money(GROSS["财务审核工时"]), "75%", money(GROSS["财务审核工时"] * 0.75)],
              ["员工填单与咨询", "22,200 单 × 16 分钟 × 65 元/时", money(GROSS["员工填单与咨询"]), "40%", money(GROSS["员工填单与咨询"] * 0.40)],
              ["退单返工", "22,200 × 13.7% × 35 分钟 × 80 元/时", money(GROSS["退单返工"]), "60%", money(GROSS["退单返工"] * 0.60)]],
             widths=[4.0, 7.8, 3.6, 2.2, 3.4], font_size=8.4)
    dk.para(doc, f"三项加总，**全公司满负荷年化收益约 {money(BENEFIT)} 元**（已按 75%/40%/60% 折算）。未计入：员工满意度、"
                 "合规风险下降、审计抽检效率、垫款资金成本——这些难以直接货币化，作为附加价值。")
    dk.h2(doc, "4.2 三年现金流与回收期")
    rows = []
    mid_s = stats["mid"]
    mid_total, mid_pay, mid_roi = mid_s["total"], mid_s["payback"] or 0, mid_s["roi"] * 100
    for t in TIERS:
        s = stats[t["key"]]
        rows.append([
            t["name"],
            money(s["total"]),
            money(s["flows"][0]), money(s["flows"][1]), money(s["flows"][2]),
            f"{s['roi']*100:.0f}%",
            ("三年内未回收" if s["payback"] is None else f"{s['payback']:.1f} 个月"),
        ])
    dk.table(doc,
             ["档位", "首年投入", "第 1 年收益", "第 2 年收益", "第 3 年收益", "三年 ROI", "静态回收期"],
             rows, widths=[4.2, 3.2, 3.2, 3.2, 3.2, 2.4, 3.4], font_size=8.2)
    dk.para(doc, "说明：首年只覆盖试点人数（低档 50 人 / 中档 180 人 / 高档全公司），收益按 60% 采纳率折算；"
                 "第 2 年按全公司推广、85% 采纳；第 3 年 95%。能力系数体现档位差异（低 0.55 / 中 0.90 / 高 1.05）。"
                 "三档三年 ROI 接近，差别在**风险、速度与可推广性**，不在“谁更便宜”。")
    dk.callout(doc, "建议",
               f"中档首年投入约 {mid_total/1e4:.1f} 万元，第 1 年只覆盖 3 个试点部门，第 2 年起按全公司推广测算，"
               f"约 {mid_pay:.1f} 个月回本、三年 ROI 约 {mid_roi:.0f}%。"
               "若只做低档，省下约 15 万元，但拿不到审批链路、退单归因与审计能力，试点结论不足以支撑推广决策。",
               fill="FFF7E6", accent="E9A23B")

    # ROI 图（中档）
    mid = stats["mid"]
    charts.bar_chart(
        ASSETS / "quote-roi.png",
        "中档三年现金流：投入 vs 收益",
        "单位：万元；首年含实施 + 运维，第 2–3 年仅运维",
        ["首年", "第 2 年", "第 3 年"],
        {"投入": [mid["total"] / 1e4, mid["maint"] / 1e4, mid["maint"] / 1e4],
         "收益": [mid["flows"][0] / 1e4, mid["flows"][1] / 1e4, mid["flows"][2] / 1e4]},
        ylabel="万元",
        value_fmt="{:.1f}",
        source=f"口径：全公司年化收益 {BENEFIT/1e4:.1f} 万元 × 覆盖比例 × 采纳率；不含模型 API 费用",
        height=520,
    )
    dk.figure(doc, ASSETS / "quote-roi.png", "图 1  中档三年现金流（保守口径）", 15.2)

    dk.h2(doc, "4.3 敏感性")
    dk.table(doc,
             ["变量", "基准", "-20%", "+20%", "对回收期影响"],
             [["退单率下降", "13.7pp", "11.0pp", "16.4pp", "±2.4 个月"],
              ["员工采纳率", "60%/年", "48%", "72%", "±4.1 个月"],
              ["单均审核节省", "5.5 分钟", "4.4 分钟", "6.6 分钟", "±1.8 个月"],
              ["模型 API 成本", "3,000 元/月", "2,400", "3,600", "可忽略"]],
             widths=[4.4, 4.0, 3.6, 3.6, 4.0])

    dk.h1(doc, "五、付款与商务条款")
    dk.table(doc,
             ["节点", "比例", "金额（中档）", "触发条件"],
             [["合同签订", "30%", "92,040 元", "SOW 签署、项目启动"],
              ["MVP 验收", "40%", "122,720 元", "冒烟 31/31 + 评测 30/30"],
              ["试点上线", "20%", "61,360 元", "3 部门上线、培训完成"],
              ["终验", "10%", "30,680 元", "验收清单 P0 全通过"]],
             widths=[4.0, 2.0, 4.4, 8.2])
    dk.bullets(doc, [
        "报价含 6% 增值税；差旅费按实际发生另计，需事先书面同意。",
        "首年运维含 5×8 响应、月度巡检、2 次规则修订；不含新功能开发。",
        "需求变更按 2,600 元/人天结算，单次 < 2 人天由项目经理现场消化。",
        "源代码与知识产权归客户所有；乙方保留通用组件复用权。",
        "验收争议以《需求分析方案》AC-01~AC-20 为准。",
    ], numbered=True)

    dk.h1(doc, "六、为什么是这个价")
    dk.para(doc, "同类企业报销 AI 项目市场报价通常在 40–80 万元（含集成）。本报价低于区间下限，原因是："
                 "① 复用已验证的 MVP 代码（检索、规则、Agent、确认闸门已跑通）；"
                 "② 不做 OCR 与 OA 双向集成，避免最大成本项；③ 单机部署，无中间件与云资源。"
                 "低价不等于低质：省下的是“重复造轮子”，不是“测试与文档”。")
    dk.table(doc,
             ["成本项", "本方案", "市场常见方案", "差异原因"],
             [["OCR/查验", "0", "8–15 万", "本期不做，接口预留"],
              ["OA 双向集成", "0", "10–20 万", "只对齐字段，不写回"],
              ["中间件/云资源", "0", "3–8 万/年", "单机 SQLite"],
              ["模型 API", "按量", "按量", "试点期 < 3,000 元/月"],
              ["实施与培训", "含", "另计", "含在报价内"]],
             widths=[5.0, 4.0, 5.0, 5.0])

    dk.h1(doc, "七、报价假设与除外")
    dk.bullets(doc, [
        "客户在 W1 内提供制度全文、近 12 个月脱敏单据、组织架构与审批人清单。",
        "客户提供 1 台 8C16G 虚拟机、内网域名与证书，并配合完成安全评估。",
        "试点部门指定 1 名接口人（0.5 FTE），负责需求确认与用户答疑。",
        "不含硬件采购、网络改造、AD 域改造、第三方软件许可。",
        "不含因客户制度冲突导致的额外条款梳理（超过 20 人天另计）。",
        "报价有效期 60 天；汇率与税率变化按合同约定调整。",
    ], numbered=True)

    path = OUT / "某某科技-报销助手-报价方案（低中高三档）.docx"
    doc.save(path)
    for k, s in stats.items():
        print(k, "build", s["build"], "total", round(s["total"]), "roi", round(s["roi"] * 100, 1),
              "payback", None if s["payback"] is None else round(s["payback"], 1))
    print("quote ok", path)


if __name__ == "__main__":
    build()
