"""Agent 工具集（Function Calling 规范）+ 业务约束。

六个工具，按"权限由小到大"排列：
    search_policy           只读，查制度条文
    query_expense_standard   只读，算差旅标准/额度
    request_invoice_info     只读，向员工索要素材（不落库）
    validate_invoice         只读，校验发票合规
    create_draft             写库，生成报销单草稿 ← 唯一有副作用的工具
    list_my_drafts           只读，查自己的草稿

调用顺序约束（**由代码强制，不只写在提示词里**）：
    create_draft 要求 ctx.state["validated"] 里存在通过校验的发票号；
    未调用 validate_invoice 或校验未通过时，create_draft 直接返回错误。
    提示词只做"第一道引导"，真正的闸门在 validate 之后。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..db import json_dumps
from ..rag.index import get_index
from . import rules


@dataclass
class ToolContext:
    conn: Any
    user: dict[str, Any]
    session_id: int
    state: dict[str, Any] = field(default_factory=dict)  # 会话级中间态：已校验发票、引用片段等

    @property
    def citations(self) -> list[dict[str, Any]]:
        return self.state.setdefault("citations", [])

    def add_citations(self, rows: list[dict[str, Any]]) -> None:
        seen = {(c["doc"], c["heading"], c["text"][:40]) for c in self.citations}
        for row in rows:
            key = (row["doc"], row["heading"], row["text"][:40])
            if key not in seen:
                seen.add(key)
                self.citations.append(row)


# ---------------------------------------------------------------- 工具实现

def _tool_search_policy(ctx: ToolContext, query: str = "", **_kw: Any) -> dict[str, Any]:
    index = get_index(ctx.conn)
    hits = index.search(query or "", top_k=3)
    ctx.add_citations(hits)
    return {
        "ok": True,
        "query": query,
        "hits": [
            {"heading": h["heading"], "doc": h["doc"], "doc_title": h.get("doc_title"),
             "score": h["score"], "text": h["text"][:300]}
            for h in hits
        ],
        "hint": "只能依据 hits 里的条文回答；hits 为空则回答'制度中未找到'。",
    }


def _tool_query_expense_standard(
    ctx: ToolContext,
    city: str = "",
    job_level: str = "",
    days: float = 1,
    same_day_return: bool = False,
    **_kw: Any,
) -> dict[str, Any]:
    level = job_level or ctx.user.get("job_level", "P5")
    is_intern = str(level).upper().startswith("S") or "实习" in str(level)
    lodging = rules.lodging_cap(city, level, is_intern)
    meal = rules.meal_allowance(city, days=days, round_trip_same_day=same_day_return)
    # 把依据条文一并检索出来，方便回答里给引用
    hits = get_index(ctx.conn).search(f"{city} 住宿标准 伙食补助 {lodging['level_group']}", top_k=2)
    ctx.add_citations(hits)
    return {
        "ok": True,
        "city": city,
        "job_level": level,
        "level_group": lodging["level_group"],
        "tier": lodging["tier"],
        "lodging_cap_per_night": lodging["cap_per_night"],
        "meal_daily": meal["daily"],
        "meal_total": meal["total"],
        "days": meal["days"],
        "rule_text": f"{lodging['rule']}；{meal['rule']}",
        "clauses": [lodging["clause"], meal["clause"]],
    }


def _tool_request_invoice_info(
    ctx: ToolContext,
    missing_fields: list[str] | None = None,
    question: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    fields = missing_fields or ["发票号码", "发票金额", "开票日期"]
    return {
        "ok": True,
        "need_user_input": True,
        "ask": question or f"请补充：{'、'.join(fields)}",
        "fields": fields,
        "note": "这是向员工索要素材，不是错误；拿到后继续 validate_invoice。",
    }


def _tool_validate_invoice(
    ctx: ToolContext,
    invoice_no: str = "",
    amount: float = 0,
    invoice_date: str = "",
    category: str = "其他",
    merchant: str = "",
    title: str = "",
    tax_id: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    result = rules.validate_invoice_fields(
        invoice_no=invoice_no, amount=amount, invoice_date=invoice_date,
        category=category, merchant=merchant, title=title, tax_id=tax_id,
    )
    # 发票查重（制度 3.2）：同一发票号在本系统内不得重复报销
    dup = ctx.conn.execute(
        "SELECT d.id, d.status FROM draft_items i JOIN drafts d ON d.id = i.draft_id"
        " WHERE i.invoice_no = ? AND i.invoice_no <> ''",
        (invoice_no,),
    ).fetchall()
    if dup:
        result["ok"] = False
        result["issues"].append({
            "code": "DUPLICATE_INVOICE", "clause": "制度 3.2",
            "message": f"发票号 {invoice_no} 已被报销单 #{dup[0]['id']} 使用，疑似重复报销",
        })

    if result["ok"]:
        validated = ctx.state.setdefault("validated", {})
        validated[invoice_no] = result
    hits = get_index(ctx.conn).search("发票抬头 税号 报销时效 不得报销", top_k=2)
    ctx.add_citations(hits)
    result["clauses"] = sorted({*(i["clause"] for i in result["issues"]), "制度 3.2"})
    return result


def _tool_create_draft(
    ctx: ToolContext,
    trip_city: str = "",
    trip_start: str = "",
    trip_end: str = "",
    items: list[dict[str, Any]] | None = None,
    note: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    items = items or []
    # ---- 闸门：所有明细的发票都必须先通过 validate_invoice ----
    validated: dict[str, Any] = ctx.state.get("validated", {})
    unvalidated = [i.get("invoice_no") for i in items if i.get("invoice_no") not in validated]
    if items and unvalidated:
        return {
            "ok": False,
            "error_code": "VALIDATION_REQUIRED",
            "error": f"发票 {unvalidated} 未通过 validate_invoice 校验，禁止直接建单（制度 4.1 第 2 步：系统自动预审）。",
            "next": "先逐张调用 validate_invoice，通过后再建单。",
        }
    if not items:
        return {"ok": False, "error_code": "EMPTY_ITEMS", "error": "明细为空，至少需要一条费用明细"}

    total = round(sum(float(i.get("amount") or 0) for i in items), 2)
    cur = ctx.conn.execute(
        "INSERT INTO drafts (user_id, session_id, status, trip_city, trip_start, trip_end, total_amount, note)"
        " VALUES (?,?, 'draft', ?,?,?,?,?)",
        (ctx.user["id"], ctx.session_id, trip_city, trip_start, trip_end, total, note),
    )
    draft_id = cur.lastrowid
    ctx.conn.executemany(
        "INSERT INTO draft_items (draft_id, category, amount, invoice_no, invoice_date, merchant, note)"
        " VALUES (?,?,?,?,?,?,?)",
        [
            (draft_id, i.get("category", "其他"), float(i.get("amount") or 0),
             i.get("invoice_no", ""), i.get("invoice_date", ""), i.get("merchant", ""), i.get("note", ""))
            for i in items
        ],
    )
    ctx.conn.commit()

    chain = rules.approval_chain(total)
    return {
        "ok": True,
        "draft_id": draft_id,
        "total_amount": total,
        "status": "draft",
        "approval_chain": chain["nodes"],
        "clause": chain["clause"],
        "message": f"已生成草稿 #{draft_id}，合计 {total:.2f} 元，待员工确认后提交",
    }


def _tool_list_my_drafts(ctx: ToolContext, **_kw: Any) -> dict[str, Any]:
    rows = ctx.conn.execute(
        "SELECT id, status, trip_city, trip_start, trip_end, total_amount, created_at"
        " FROM drafts WHERE user_id = ? ORDER BY id DESC LIMIT 10",
        (ctx.user["id"],),
    ).fetchall()
    return {"ok": True, "drafts": [dict(r) for r in rows], "count": len(rows)}


# ---------------------------------------------------------------- 注册表

@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., dict[str, Any]]
    side_effect: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


TOOLS: dict[str, Tool] = {
    "search_policy": Tool(
        name="search_policy",
        description="检索公司报销制度条文。任何关于标准、流程、时限的结论都必须先调用它。返回 top3 条文片段。",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "自然语言检索词，例如「北京 住宿标准」"}},
            "required": ["query"],
        },
        handler=_tool_search_policy,
    ),
    "query_expense_standard": Tool(
        name="query_expense_standard",
        description="按城市与职级计算差旅标准（每晚上限、伙食补助合计）。涉及金额时必须用它，不要自己算。",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "出差城市，如 北京"},
                "job_level": {"type": "string", "description": "职级，如 P5/M1；缺省用当前用户职级"},
                "days": {"type": "number", "description": "出差天数，默认 1"},
                "same_day_return": {"type": "boolean", "description": "是否当天往返（伙食补助按半天）"},
            },
            "required": ["city"],
        },
        handler=_tool_query_expense_standard,
    ),
    "request_invoice_info": Tool(
        name="request_invoice_info",
        description="向员工索要缺失的报销素材（发票号、金额、开票日期等）。缺字段时先问，禁止编造占位值。",
        parameters={
            "type": "object",
            "properties": {
                "missing_fields": {"type": "array", "items": {"type": "string"}, "description": "缺失字段中文名"},
                "question": {"type": "string", "description": "要问员工的话"},
            },
            "required": ["missing_fields"],
        },
        handler=_tool_request_invoice_info,
    ),
    "validate_invoice": Tool(
        name="validate_invoice",
        description="校验发票是否可报销：抬头/税号、90 天时效、重复报销、科目是否允许。建单前必须全部通过。",
        parameters={
            "type": "object",
            "properties": {
                "invoice_no": {"type": "string"},
                "amount": {"type": "number"},
                "invoice_date": {"type": "string", "description": "YYYY-MM-DD"},
                "category": {"type": "string", "enum": ["住宿费", "交通费", "伙食补助", "其他"]},
                "merchant": {"type": "string"},
                "title": {"type": "string", "description": "发票抬头"},
                "tax_id": {"type": "string", "description": "纳税人识别号"},
            },
            "required": ["invoice_no", "amount", "invoice_date"],
        },
        handler=_tool_validate_invoice,
    ),
    "create_draft": Tool(
        name="create_draft",
        description="生成报销单草稿（有副作用）。前置条件：明细中的发票都已通过 validate_invoice。",
        parameters={
            "type": "object",
            "properties": {
                "trip_city": {"type": "string"},
                "trip_start": {"type": "string"},
                "trip_end": {"type": "string"},
                "note": {"type": "string"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string"},
                            "amount": {"type": "number"},
                            "invoice_no": {"type": "string"},
                            "invoice_date": {"type": "string"},
                            "merchant": {"type": "string"},
                            "note": {"type": "string"},
                        },
                        "required": ["category", "amount", "invoice_no", "invoice_date"],
                    },
                },
            },
            "required": ["trip_city", "items"],
        },
        handler=_tool_create_draft,
        side_effect=True,
    ),
    "list_my_drafts": Tool(
        name="list_my_drafts",
        description="查看当前员工最近的报销单草稿。",
        parameters={"type": "object", "properties": {}},
        handler=_tool_list_my_drafts,
    ),
}


def tool_schemas() -> list[dict[str, Any]]:
    return [t.schema() for t in TOOLS.values()]


def run_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """执行一个工具，返回 (结果, 耗时毫秒)。异常被兜住，转成结构化错误交给模型自愈。"""
    tool = TOOLS.get(name)
    if tool is None:
        return {"ok": False, "error_code": "UNKNOWN_TOOL", "error": f"未注册的工具：{name}"}, 0

    started = time.perf_counter()
    try:
        result = tool.handler(ctx, **(args or {}))
    except TypeError as exc:
        result = {"ok": False, "error_code": "BAD_ARGUMENTS", "error": f"参数不合法：{exc}"}
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error_code": "TOOL_FAILED", "error": f"工具执行失败：{exc}"}
    duration_ms = int((time.perf_counter() - started) * 1000)
    return result, duration_ms


def summarize(result: dict[str, Any], limit: int = 240) -> str:
    """日志用的结果摘要：不存全量，避免日志表被大文本撑爆。"""
    text = json.dumps(result, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


__all__ = ["TOOLS", "ToolContext", "run_tool", "tool_schemas", "summarize", "json_dumps"]
