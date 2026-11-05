"""业务规则引擎：把制度里的"可判定规则"变成代码。

核心立场（写进需求文档的一条硬约定）：
    **金额、比例、时效、审批门槛由这里判定，不由 LLM 判定。**
LLM 只负责解释"为什么超标、依据哪一条"，避免模型把 500 说成 600 这类事故。
所有规则都标注了制度条款编号，便于回答里回链原文。
"""
from __future__ import annotations

from datetime import date, datetime

# ---------------------------------------------------------------- 城市分级（制度 2.1）

_CITY_TIER = {
    "北京": "一线", "上海": "一线", "广州": "一线", "深圳": "一线",
    "成都": "新一线", "杭州": "新一线", "武汉": "新一线", "西安": "新一线",
    "南京": "新一线", "重庆": "新一线", "天津": "新一线", "苏州": "新一线",
}


def city_tier(city: str) -> str:
    """未列举的城市一律按"其他城市"，与制度 2.1 的兜底口径一致。"""
    return _CITY_TIER.get((city or "").strip(), "其他")


# ---------------------------------------------------------------- 住宿标准（制度 2.2）

_LODGING = {
    "总监及以上": {"一线": 800, "新一线": 650, "其他": 500},
    "经理": {"一线": 600, "新一线": 500, "其他": 400},
    "普通员工": {"一线": 500, "新一线": 400, "其他": 300},
}

LEVEL_ORDER = ["实习生", "普通员工", "经理", "总监及以上"]


def level_group(job_level: str, is_intern: bool = False) -> str:
    """把职级编码（M3/P5…）映射到制度里的三档人群。"""
    if is_intern:
        return "实习生"
    level = (job_level or "").strip().upper()
    if level.startswith("M"):
        try:
            num = int(level[1:])
        except ValueError:
            return "普通员工"
        return "总监及以上" if num >= 3 else "经理"
    if "总监" in (job_level or ""):
        return "总监及以上"
    if "经理" in (job_level or ""):
        return "经理"
    return "普通员工"


def lodging_cap(city: str, job_level: str = "P5", is_intern: bool = False) -> dict:
    """返回某城市 + 某职级的每晚上限，以及该结论的制度依据。"""
    group = level_group(job_level, is_intern)
    tier = city_tier(city)
    base = _LODGING["普通员工"][tier]
    if group == "实习生":
        # 制度 6.4：实习生按普通员工标准的 80% 执行
        cap = round(base * 0.8)
        return {
            "city": city, "tier": tier, "level_group": "实习生（普通员工 80%）",
            "cap_per_night": cap, "clause": "制度 2.2 + 6.4",
            "rule": f"{city}（{tier}）实习生住宿上限 {cap} 元/晚（普通员工 {base} 元的 80%）",
        }
    cap = _LODGING[group][tier]
    return {
        "city": city, "tier": tier, "level_group": group,
        "cap_per_night": cap, "clause": "制度 2.2",
        "rule": f"{city}（{tier}）{group}住宿上限 {cap} 元/晚（含税）",
    }


# ---------------------------------------------------------------- 伙食补助（制度 2.4）

_MEAL = {"一线": 100, "新一线": 80, "其他": 60}


def meal_allowance(city: str, days: float = 1, round_trip_same_day: bool = False) -> dict:
    tier = city_tier(city)
    daily = _MEAL[tier]
    effective_days = days * 0.5 if round_trip_same_day else days
    total = round(daily * effective_days)
    return {
        "city": city, "tier": tier, "daily": daily,
        "days": effective_days, "total": total, "clause": "制度 2.4",
        "rule": f"{city}（{tier}）伙食补助 {daily} 元/天，{effective_days:g} 天合计 {total} 元",
    }


# ---------------------------------------------------------------- 时效（制度 1.1）

DEADLINE_DAYS = 90


def days_since(value: str | date) -> int | None:
    """容忍多种日期写法，解析失败返回 None。"""
    if isinstance(value, date):
        return (date.today() - value).days
    raw = (value or "").strip().replace("/", "-").replace(".", "-")
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y%m%d"):
        try:
            return (date.today() - datetime.strptime(raw, fmt).date()).days
        except ValueError:
            continue
    return None


def deadline_check(invoice_date: str) -> dict:
    elapsed = days_since(invoice_date)
    if elapsed is None:
        return {"ok": False, "elapsed_days": None, "clause": "制度 1.1",
                "reason": f"无法解析日期「{invoice_date}」，请用 YYYY-MM-DD 格式"}
    remain = DEADLINE_DAYS - elapsed
    ok = 0 <= elapsed <= DEADLINE_DAYS
    return {
        "ok": ok, "elapsed_days": elapsed, "remain_days": remain, "clause": "制度 1.1",
        "reason": "" if ok else (
            f"距费用发生已 {elapsed} 天，超过 {DEADLINE_DAYS} 天时效，原则上不予受理"
            if elapsed > DEADLINE_DAYS else "费用发生日期晚于今天，请核对日期"
        ),
    }


# ---------------------------------------------------------------- 审批门槛（制度 4.1）

def approval_chain(amount: float) -> dict:
    amount = round(float(amount or 0), 2)
    nodes = ["直属主管审批"]
    if amount >= 5000:
        nodes.append("部门负责人审批（单笔 ≥ 5000）")
    nodes.append("财务审核")
    if amount >= 20000:
        nodes.append("财务总监审批（单笔 ≥ 20000）")
    nodes.append("出纳付款")
    return {
        "amount": amount,
        "nodes": nodes,
        "clause": "制度 4.1",
        "rule": " → ".join(nodes),
    }


# ---------------------------------------------------------------- 发票校验（制度 3.2 / 3.3）

VALID_CATEGORIES = ["住宿费", "交通费", "伙食补助", "其他"]
COMPANY_NAME = "某某科技有限公司"
COMPANY_TAX_ID = "91110108MA01ABCD2X"


def validate_invoice_fields(
    invoice_no: str,
    amount: float,
    invoice_date: str,
    category: str = "其他",
    merchant: str = "",
    title: str = "",
    tax_id: str = "",
) -> dict:
    """逐条校验发票，返回问题清单（每条都带制度依据），不做"是否通过"的模糊判断。"""
    issues: list[dict] = []

    if not (invoice_no or "").strip():
        issues.append({"code": "INVOICE_NO_MISSING", "clause": "制度 3.2", "message": "发票号码缺失，无法查重"})
    if amount is None or float(amount) <= 0:
        issues.append({"code": "AMOUNT_INVALID", "clause": "制度 3.2", "message": "金额必须大于 0"})

    if title and title.strip() != COMPANY_NAME:
        issues.append({
            "code": "TITLE_MISMATCH", "clause": "制度 3.2",
            "message": f"发票抬头为「{title}」，必须是公司全称「{COMPANY_NAME}」",
        })
    if tax_id and tax_id.strip() != COMPANY_TAX_ID:
        issues.append({
            "code": "TAX_ID_MISMATCH", "clause": "制度 3.2",
            "message": f"纳税人识别号「{tax_id}」与公司税号不一致",
        })

    deadline = deadline_check(invoice_date)
    if not deadline["ok"]:
        issues.append({"code": "OVER_DEADLINE", "clause": "制度 1.1", "message": deadline["reason"]})

    if category not in VALID_CATEGORIES:
        issues.append({
            "code": "CATEGORY_UNKNOWN", "clause": "制度 5.1",
            "message": f"费用类型「{category}」不在科目对照表内，可选：{'/'.join(VALID_CATEGORIES)}",
        })

    banned = ["礼品", "烟酒", "娱乐", "罚款", "滞纳金"]
    if any(word in (merchant or "") + (title or "") for word in banned):
        issues.append({
            "code": "NON_REIMBURSABLE", "clause": "制度 3.3",
            "message": "礼品、烟酒、娱乐消费、罚款、滞纳金不得报销",
        })

    return {
        "ok": not issues,
        "issues": issues,
        "checked": {
            "invoice_no": invoice_no, "amount": amount, "invoice_date": invoice_date,
            "category": category, "merchant": merchant,
        },
        "clauses": sorted({i["clause"] for i in issues}) or ["制度 3.2"],
    }
