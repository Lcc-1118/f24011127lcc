# -*- coding: utf-8 -*-
"""流程图：改造前 / 改造后（自包含 HTML + PNG 供文档嵌入）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import charts
from facts import META

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "docs" / "05-流程图"
ASSETS = BASE / "_assets"
OUT.mkdir(parents=True, exist_ok=True)

AS_IS = [
    ("提交出差申请", "OA 填写目的地、日期、预算；主管审批", "出差人", "normal",
     "审批通过后无标准提示，员工不知道住宿上限"),
    ("消费并垫付", "交通、住宿、餐饮全部个人垫款", "出差人", "normal",
     "平均垫款 4,200 元，回款 9.4 天"),
    ("收集发票", "酒店、高铁、打车、会议注册；抬头税号靠商家", "出差人", "pain",
     "抬头/税号错误率约 18%，当场发现不了"),
    ("手工填单", "在 OA 逐项录入，金额、科目、附件手工填", "出差人", "pain",
     "单均 28 分钟，跨 4 个系统；超标不自知"),
    ("直属主管审批", "看事由与金额，难核对标准", "主管", "normal",
     "主管也记不住分档标准，往往直接通过"),
    ("财务初审", "核对发票、标准、查重、审批链", "财务", "pain",
     "单均 11.5 分钟；63% 退单在此发生"),
    ("退回补件", "电话/企微告知问题，员工重填重交", "财务 ↔ 员工", "pain",
     "退回通知无具体条款；平均 1.7 次/单"),
    ("财务复核与付款", "复核金额、科目、预算；出纳付款", "财务", "normal",
     "无预审清单，重复劳动；付款排期固定"),
    ("归档", "影像归档 5 年，按发票号可查", "财务", "normal",
     "查重靠 Excel，跨表比对 20 分钟/天"),
]

TO_BE = [
    ("对话提问", "员工在企业微信/浏览器问“上海住宿多少”“这张票能报吗”", "员工", "ai",
     "30 秒内给出条款、版本与金额计算"),
    ("制度与标准解释", "混合检索 + 引用 + 弃答；现行版本 V3.2", "AI 助手", "ai",
     "回答带条款号；无依据明确说“未找到”"),
    ("发票预检", "抬头/税号/查重/时效/科目/标准六项代码判定", "规则引擎", "ai",
     "逐条问题 + 修改建议；单票 < 300ms"),
    ("草稿生成", "多轮补齐要素，生成费用明细、审批链路、附件清单", "AI 助手", "ai",
     "填单 28 → 12 分钟；缺失要素主动追问"),
    ("人工确认（闸门）", "员工核对金额/审批人/附件，确认后才落库", "员工", "human",
     "未确认 0 条落库；误建单成本前置拦截"),
    ("提交与审批", "草稿复制/推送到 OA（本期不真实提交）", "员工 → 主管", "normal",
     "审批链路随单展示；≥5,000 提示部门负责人"),
    ("财务审核", "按预审问题清单复核，重点看例外项", "财务", "human",
     "单均 11.5 → 6 分钟；审核责任不变"),
    ("付款", "复核通过后进入付款排期", "财务/出纳", "normal",
     "闭环 9.4 → 5.5 天（目标）"),
    ("归档与审计", "提案留痕：条款版本、工具调用、确认人、改动", "系统", "normal",
     "可重建每次建议；发票号掩码展示"),
]


def html_page(title, subtitle, steps, kpis, note):
    rows = []
    for i, (name, desc, actor, kind, anno) in enumerate(steps, 1):
        cls = {"normal": "", "ai": " ai", "human": " human", "pain": " pain"}[kind]
        tag = {"normal": "", "ai": "AI", "human": "人工", "pain": "痛点"}[kind]
        rows.append(f"""
      <li class="step{cls}">
        <div class="no">{i}</div>
        <div class="body">
          <div class="head"><span class="name">{name}</span><span class="actor">{actor}</span>{f'<span class="tag">{tag}</span>' if tag else ''}</div>
          <div class="desc">{desc}</div>
        </div>
        <div class="anno">{anno}</div>
      </li>""")
    kpi_html = "".join(
        f'<div class="kpi"><div class="v">{v}</div><div class="l">{l}</div><div class="s">{s}</div></div>'
        for v, l, s in kpis)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · {META['client_short']}报销助手</title>
<style>
  :root {{ --navy:#1F3B6B; --blue:#2E6DA4; --teal:#2A9D8F; --amber:#E9A23B; --red:#C0504D;
           --line:#D5DEE9; --bg:#F6F8FB; --ink:#222; --grey:#6E6E6E; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif; }}
  .page {{ max-width:1080px; margin:0 auto; padding:44px 40px 60px; background:#fff;
           box-shadow:0 2px 18px rgba(31,59,107,.06); }}
  .eyebrow {{ color:var(--blue); font-size:13px; letter-spacing:.18em; }}
  h1 {{ font-size:30px; color:var(--navy); margin:6px 0 6px; }}
  .sub {{ color:var(--grey); font-size:14px; }}
  .rule {{ height:3px; background:var(--navy); margin:18px 0 22px; }}
  .kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:18px 0 26px; }}
  .kpi {{ background:#EEF3FA; border-top:3px solid var(--navy); padding:12px 14px; }}
  .kpi .v {{ font-size:22px; font-weight:700; color:var(--navy); }}
  .kpi .l {{ font-size:12px; margin-top:2px; }}
  .kpi .s {{ font-size:11px; color:var(--grey); margin-top:2px; }}
  ol.flow {{ list-style:none; margin:0; padding:0; }}
  .step {{ display:grid; grid-template-columns:58px 1fr 260px; gap:14px; position:relative;
           background:#fff; border:1px solid var(--line); border-radius:8px; padding:12px 14px;
           margin-bottom:26px; }}
  .step::after {{ content:""; position:absolute; left:42px; bottom:-26px; height:26px;
                  border-left:2px solid var(--line); }}
  .step:last-child::after {{ display:none; }}
  .step .no {{ width:34px; height:34px; border-radius:50%; background:var(--navy); color:#fff;
               display:flex; align-items:center; justify-content:center; font-weight:700; }}
  .step .head {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
  .step .name {{ font-size:16px; font-weight:700; color:var(--navy); }}
  .step .actor {{ font-size:11px; color:var(--grey); background:#F0F3F7; padding:1px 8px; border-radius:10px; }}
  .step .tag {{ font-size:11px; color:#fff; padding:1px 8px; border-radius:10px; background:var(--blue); }}
  .step .desc {{ font-size:13px; color:#444; margin-top:6px; line-height:1.6; }}
  .step .anno {{ font-size:12px; color:var(--grey); border-left:2px dashed var(--line); padding-left:12px;
                 display:flex; align-items:center; line-height:1.6; }}
  .step.ai {{ border-color:var(--blue); background:#F7FBFF; }}
  .step.ai .tag {{ background:var(--blue); }}
  .step.ai .anno {{ color:var(--blue); border-color:#BBD3EA; }}
  .step.human {{ border-color:var(--amber); background:#FFFBF3; }}
  .step.human .tag {{ background:var(--amber); }}
  .step.human .anno {{ color:#A9700F; border-color:#F0D6A8; }}
  .step.pain {{ border-color:var(--red); background:#FFF8F8; }}
  .step.pain .tag {{ background:var(--red); }}
  .step.pain .anno {{ color:var(--red); border-color:#EBC3C3; }}
  .legend {{ display:flex; gap:18px; font-size:12px; color:var(--grey); margin:4px 0 18px; }}
  .dot {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; }}
  .note {{ background:#F5F7FA; border-left:4px solid var(--navy); padding:12px 16px;
           font-size:12px; color:#555; line-height:1.7; margin-top:26px; }}
  @media print {{ body {{ background:#fff; }} .page {{ box-shadow:none; padding:0; }} }}
  @media (max-width:760px) {{ .step {{ grid-template-columns:48px 1fr; }} .anno {{ grid-column:2; border-left:none; padding-left:0; }} .kpis {{ grid-template-columns:repeat(2,1fr); }} }}
</style>
</head>
<body>
<div class="page">
  <div class="eyebrow">{META['client']} · {META['project']}</div>
  <h1>{title}</h1>
  <div class="sub">{subtitle}</div>
  <div class="rule"></div>
  <div class="kpis">{kpi_html}</div>
  <div class="legend">
    <span><i class="dot" style="background:#fff;border:1px solid #D5DEE9"></i>人工流程</span>
    <span><i class="dot" style="background:#2E6DA4"></i>AI 介入</span>
    <span><i class="dot" style="background:#E9A23B"></i>人工确认 / 责任点</span>
    <span><i class="dot" style="background:#C0504D"></i>痛点环节</span>
  </div>
  <ol class="flow">{''.join(rows)}
  </ol>
  <div class="note">{note}</div>
</div>
</body>
</html>"""


def build():
    as_is = html_page(
        "改造前：一张单要过九道手",
        "现状流程（As-Is）· 9 个节点 · 平均 9.4 天闭环 · 退单率 21.7%",
        AS_IS,
        [("21.7%", "退单率", "63% 与发票有关"), ("11.5 分钟", "单均审核", "复杂单 30 分钟"),
         ("28 分钟", "员工填单", "跨 4 个系统"), ("9.4 天", "平均闭环", "垫款 4,200 元")],
        "读图说明：红色节点是“问题发生地”，不是“责任归属”。退单、超标、抬头错误都发生在提交前，"
        "但直到财务初审才被发现，这是现状流程最大的结构性浪费。典型事件 EV-2026-10-28-SH（张伟，5,600 元）"
        "正是沿着第 3→4→6→7 步走了 3 次：抬头错、税号错、住宿超标，12 天才到账。",
    )
    (OUT / "01-改造前流程.html").write_text(as_is, encoding="utf-8")

    to_be = html_page(
        "改造后：提交前就把问题解决",
        "目标流程（To-Be）· AI 预检前置 · 两处人工闸门 · 不改变审批责任",
        TO_BE,
        [("≤ 8%", "目标退单率", "现状 21.7%"), ("≤ 6 分钟", "单均审核", "现状 11.5 分钟"),
         ("≤ 12 分钟", "员工填单", "现状 28 分钟"), ("≤ 5.5 天", "目标闭环", "现状 9.4 天")],
        "读图说明：蓝色为 AI 介入点，橙色为人工确认/责任点。系统只产出草稿，不向 OA/ERP 真实提交；"
        "员工确认与财务审核是两道不可绕过的闸门。",
    )
    (OUT / "02-改造后流程.html").write_text(to_be, encoding="utf-8")

    charts.flow_diagram(
        ASSETS / "flow-as-is.png",
        "改造前流程（As-Is）",
        "9 个节点 · 退单集中在财务初审 · 平均 9.4 天闭环",
        [{"title": t, "desc": d, "actor": a, "kind": k, "note": n} for t, d, a, k, n in AS_IS],
        source="来源：财务共享中心访谈（f00/f01/f30）与 OA 流程说明",
        height=1060,
    )
    charts.flow_diagram(
        ASSETS / "flow-to-be.png",
        "改造后流程（To-Be）",
        "AI 预检前置 + 两处人工闸门 · 不改变审批责任",
        [{"title": t, "desc": d, "actor": a, "kind": k, "note": n} for t, d, a, k, n in TO_BE],
        source="来源：本项目需求定义（FR-01~FR-08）与试点方案",
        height=1060,
    )
    print("flows ok")


if __name__ == "__main__":
    build()
