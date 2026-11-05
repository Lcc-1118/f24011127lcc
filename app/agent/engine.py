"""Agent 执行循环：模型决策 → 执行工具 → 结果回填 → 再决策。

最多 5 轮（`MAX_AGENT_ROUNDS`），每轮的工具调用都写 `agent_logs`，
便于阶段 5 做"哪些工具最常被调用 / 哪一步最慢 / 哪些单子被反复驳回"的分析。

两种"大脑"，共用同一套循环与安全闸门：
1. **LLMBrain**：真实 Function Calling（配置了 LLM_API_KEY 时启用）；
2. **HeuristicBrain**：确定性规则大脑，用于无 Key 环境演示全链路。
   它不是"假的模型"，而是把决策写死成规则，保证评测/演示可复现、零成本。

无论哪种大脑，`create_draft` 都不会被执行 —— 引擎会中断并发出
`confirm_required` 事件，等员工在前端点"确认"后才真正落库。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Iterator

from ..config import settings
from ..db import get_prompt, json_dumps
from ..llm import LLMError, LLMReply, build_client
from ..privacy import mask_args
from ..rag.index import get_index
from . import rules
from .prompts import build_agent_system_prompt, build_llm_history, format_tool_result, render_citations
from .tools import TOOLS, ToolContext, run_tool, summarize, tool_schemas

STEP_LIMIT = 5  # 硬上限，避免模型死循环烧 token


# ---------------------------------------------------------------- 事件

def ev(kind: str, **payload: Any) -> dict[str, Any]:
    return {"type": kind, **payload}


# ---------------------------------------------------------------- 大脑

class HeuristicBrain:
    """确定性决策大脑：按意图 + 已执行步骤推进，保证演示与评测可复现。"""

    mode = "offline"

    _CITY_RE = re.compile(
        "|".join(sorted(rules._CITY_TIER.keys(), key=len, reverse=True)) + r"|[一-龥]{2,3}市?"
    )
    _INVOICE_RE = re.compile(r"(?<!\d)(\d{8,20})(?!\d)")
    _AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*元")
    _DATE_RE = re.compile(r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})")
    _DAYS_RE = re.compile(r"(\d+)\s*天")

    def decide(self, question: str, steps: list[dict[str, Any]], user: dict[str, Any]) -> LLMReply:
        ran = [s["tool"] for s in steps]
        q = question

        intent = self._intent(q)

        # ---- 违规意图：不给"绕路方案"，只给合规指引 ----
        if intent == "compliance":
            if "search_policy" not in ran:
                return self._call(
                    "search_policy",
                    {"query": "票据真实 虚开 拆单 代开 不得报销 报销原则"},
                )
            res = self._last_result(steps, "search_policy") or {}
            return LLMReply(content=AgentEngine._compliance_answer(q, res.get("hits") or []))

        if intent == "validate":
            if "validate_invoice" not in ran:
                invoice = self._invoice_args(q, user)
                if not invoice.get("invoice_no") or not invoice.get("amount"):
                    # 只问"这张发票能报吗"却没给号码/金额：先要素材，不拿空数据去跑校验。
                    # 已经问过一次就不再重复问（否则会空转到轮次上限），交给兜底成文输出追问。
                    if "request_invoice_info" in ran:
                        return LLMReply(content=None)
                    return self._call(
                        "request_invoice_info",
                        {"missing_fields": ["发票号码", "发票金额（元）", "开票日期（YYYY-MM-DD）", "费用科目"],
                         "question": "请把这张发票的号码、金额、开票日期和费用科目发我，我来校验能不能报。"},
                    )
                return self._call("validate_invoice", invoice)
            return LLMReply(content=None)  # 交给上层收尾

        if intent == "draft":
            city = self._city(q) or "北京"
            if "search_policy" not in ran:
                return self._call("search_policy", {"query": f"{city} 差旅 报销 标准 流程"})
            if "query_expense_standard" not in ran:
                return self._call(
                    "query_expense_standard",
                    {"city": city, "days": self._days(q)},
                )
            invoice = self._invoice_args(q, user)
            if not invoice.get("invoice_no") or not invoice.get("amount"):
                if "request_invoice_info" not in ran:
                    return self._call(
                        "request_invoice_info",
                        {"missing_fields": ["发票号码", "发票金额（元）", "开票日期（YYYY-MM-DD）"],
                         "question": "请补充这张发票的号码、金额和开票日期，我再帮你校验并生成草稿。"},
                    )
                return LLMReply(content=None)
            if "validate_invoice" not in ran:
                return self._call("validate_invoice", invoice)
            last = self._last_result(steps, "validate_invoice")
            if last and not last.get("ok"):
                return LLMReply(content=None)  # 校验失败：不再建单，直接给结论
            if "create_draft" not in ran:
                return self._call(
                    "create_draft",
                    {
                        "trip_city": city,
                        "trip_start": invoice.get("invoice_date", ""),
                        "trip_end": invoice.get("invoice_date", ""),
                        "note": "由对话生成",
                        "items": [
                            {
                                "category": invoice.get("category", "住宿费"),
                                "amount": invoice["amount"],
                                "invoice_no": invoice["invoice_no"],
                                "invoice_date": invoice.get("invoice_date", ""),
                                "merchant": invoice.get("merchant", ""),
                            }
                        ],
                    },
                )
            return LLMReply(content=None)

        # ---- 默认：制度问答 ----
        if "search_policy" not in ran:
            return self._call("search_policy", {"query": q})
        city = self._city(q)
        asks_money = bool(re.search(r"住宿|补助|标准|多少|上限|额度|几晚", q))
        if city and asks_money and "query_expense_standard" not in ran:
            return self._call("query_expense_standard", {"city": city, "days": self._days(q)})
        return LLMReply(content=None)

    # -- 意图识别
    def _intent(self, q: str) -> str:
        if re.search(
            r"多开|虚开|拆单|拆成|重开|另开|不要发票|凑票|代开|开大|开高|换科目|改科目|"
            r"不走审批|跳过审批|绕过审批|不审批直接",
            q,
        ):
            return "compliance"
        if re.search(r"帮我报|帮我提交|报销上个|提交报销|建单|报一下|发起报销|生成报销单", q):
            return "draft"
        # 发票校验意图必须同时满足"指向某张票"+"在问能不能报"，否则"发票抬头是什么"
        # 这类制度问题会被误判成校验，进而去要发票号，答非所问。
        if re.search(r"这个发票|这张发票|该发票|(?:这|那)(?:张|个)(?:发票|小票|票|呢)|校验|审核一下", q) or (
            re.search(r"能报吗|能不能报|可以报吗|能不能报账", q)
            and re.search(r"发票|小票|票据|这个|那个|\d{8,20}", q)
            and not re.search(r"标准|规定|要求|流程|时效|怎么算|是多少|多少|哪些|材料|多久|几天", q)
        ):
            return "validate"
        return "query"

    def _city(self, q: str) -> str:
        for city in sorted(rules._CITY_TIER.keys(), key=len, reverse=True):
            if city in q:
                return city
        match = re.search(r"(?:去|到)([一-龥]{2,4}?)(?:出差|开会|培训|拜访)", q)
        return match.group(1) if match else ""

    def _days(self, q: str) -> float:
        match = self._DAYS_RE.search(q)
        return float(match.group(1)) if match else 1.0

    def _invoice_args(self, q: str, user: dict[str, Any]) -> dict[str, Any]:
        match_no = self._INVOICE_RE.search(q)
        invoice_no = match_no.group(1) if match_no else ""
        amount_match = self._AMOUNT_RE.search(q)
        date_match = self._DATE_RE.search(q)
        category = "住宿费" if "住宿" in q or "酒店" in q else ("交通费" if re.search(r"机票|高铁|打车|车票", q) else "其他")
        return {
            "invoice_no": invoice_no,
            "amount": float(amount_match.group(1)) if amount_match else 0,
            "invoice_date": (date_match.group(1).replace("/", "-").replace(".", "-") if date_match else ""),
            "category": category,
            "merchant": "",
            "title": settings.company_name if "抬头" in q else "",
        }

    @staticmethod
    def _call(name: str, args: dict[str, Any]) -> LLMReply:
        return LLMReply(content=None, tool_calls=[{"id": f"call_{name}", "name": name, "arguments": args}])

    @staticmethod
    def _last_result(steps: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
        for step in reversed(steps):
            if step["tool"] == name:
                return step["result"]
        return None


class LLMBrain:
    """真实模型大脑：Function Calling，供应商可替换。mode = 'llm'。"""

    mode = "llm"

    def __init__(self) -> None:
        self.client = build_client()

    def decide(
        self,
        messages: list[dict[str, Any]],
    ) -> LLMReply:
        if self.client is None:
            raise LLMError("未配置 LLM_API_KEY")
        return self.client.chat(messages, tools=tool_schemas())


# ---------------------------------------------------------------- 引擎

class AgentEngine:
    def __init__(self, conn: Any, user: Any, session_id: int) -> None:
        self.conn = conn
        self.user = dict(user)
        self.session_id = session_id
        self.ctx = ToolContext(conn=conn, user=self.user, session_id=session_id)

    # -- 历史
    def history(self, limit_rounds: int | None = None) -> list[Any]:
        rounds = limit_rounds or settings.history_rounds
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE session_id = ? AND role IN ('user','assistant')"
            " ORDER BY id DESC LIMIT ?",
            (self.session_id, rounds * 2),
        ).fetchall()
        return list(reversed(rows))

    # -- 落库
    def _save(self, role: str, content: str, citations: Any = None, meta: Any = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO messages (session_id, role, content, citations, agent_meta) VALUES (?,?,?,?,?)",
            (
                self.session_id,
                role,
                content,
                json_dumps(citations) if citations else None,
                json_dumps(meta) if meta else None,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def _log_tool(self, round_no: int, name: str, args: dict[str, Any], result: dict[str, Any], ms: int) -> None:
        self.conn.execute(
            "INSERT INTO agent_logs (session_id, round_no, tool_name, args_json, result_summary, duration_ms)"
            " VALUES (?,?,?,?,?,?)",
            (self.session_id, round_no, name, json_dumps(mask_args(args)), summarize(result), ms),
        )
        self.conn.commit()

    # ---------------------------------------------------------------- 主循环
    def run(self, question: str) -> Iterator[dict[str, Any]]:
        """执行一次完整的对话轮次，逐条 yield SSE 事件。"""
        started = time.perf_counter()
        self._save("user", question)

        # 会话标题：首句前 20 字
        self.conn.execute(
            "UPDATE sessions SET updated_at = datetime('now','localtime'),"
            " title = CASE WHEN title = '新会话' THEN ? ELSE title END WHERE id = ?",
            (question[:20], self.session_id),
        )
        self.conn.commit()

        system_prompt = build_agent_system_prompt(get_prompt(self.conn, "system_agent"), self.user)
        use_llm = settings.has_llm
        brain: Any = LLMBrain() if use_llm else HeuristicBrain()
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(build_llm_history(self.history()))

        steps: list[dict[str, Any]] = []
        answer: str | None = None
        round_no = 0

        yield ev("status", message="正在检索制度…")

        for round_no in range(1, STEP_LIMIT + 1):
            try:
                if use_llm:
                    reply = brain.decide(messages)
                else:
                    reply = brain.decide(question, steps, self.user)
            except LLMError as exc:
                # 模型故障 → 降级为"仅检索结果 + 提示人工判断"，不让入口整体不可用
                yield ev("status", message="模型服务暂不可用，已降级为制度检索模式")
                hits = get_index(self.conn).search(question, top_k=3)
                self.ctx.add_citations(hits)
                answer = self._degraded_answer(hits, str(exc))
                break

            if reply.content:
                answer = reply.content
                break

            if not reply.tool_calls:
                answer = None
                break

            for call in reply.tool_calls:
                name = call["name"]
                args = call.get("arguments") or {}

                if use_llm:
                    messages.append({
                        "role": "assistant",
                        "content": reply.content,
                        "tool_calls": [{
                            "id": call.get("id") or f"call_{name}_{round_no}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                        }],
                    })

                # ---- 建单前强制中断 ----
                if name == "create_draft":
                    pending = {
                        "args": args,
                        "validated": self.ctx.state.get("validated", {}),
                    }
                    self._save(
                        "system",
                        "待员工确认的报销单草稿",
                        meta={"pending_draft": pending, "status": "pending"},
                    )
                    yield ev("tool_call", name=name, args=args)
                    # 金额与审批链路由后端算好再下发：前端只负责渲染，不重复实现业务规则
                    items = args.get("items") or []
                    total = round(sum(float(i.get("amount") or 0) for i in items), 2)
                    chain = rules.approval_chain(total)
                    preview = dict(args)
                    preview["total"] = total
                    preview["approval_chain"] = chain["nodes"]
                    preview["approval_rule"] = chain["rule"]
                    preview["approval_clause"] = chain["clause"]
                    yield ev("confirm_required", draft=preview)
                    yield ev(
                        "done",
                        message_id=None,
                        meta={"rounds": round_no, "mode": brain.mode, "pending": True,
                              "latency_ms": int((time.perf_counter() - started) * 1000)},
                    )
                    return

                yield ev("tool_call", name=name, args=args)
                result, ms = run_tool(self.ctx, name, args)
                self._log_tool(round_no, name, args, result, ms)
                steps.append({"tool": name, "args": args, "result": result})
                yield ev(
                    "tool_result",
                    name=name,
                    ok=bool(result.get("ok")),
                    summary=summarize(result, 160),
                )

                if use_llm:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id") or f"call_{name}_{round_no}",
                        "content": format_tool_result(name, result),
                    })

        if answer is None:
            # 轮次耗尽或大脑没有给出文本 → 用已收集到的工具结果兜底成文
            answer = self._compose(question, steps)

        yield ev("status", message="正在生成回答…")
        for chunk in _chunks(answer, 6):
            yield ev("token", text=chunk)

        citations = self.ctx.citations[:3]
        yield ev("citations", items=[
            {"doc": c["doc"], "doc_title": c.get("doc_title"), "heading": c["heading"],
             "text": c["text"][:180], "version": c.get("version"), "score": c["score"]}
            for c in citations
        ])

        meta = {
            "rounds": round_no,
            "mode": brain.mode,
            "tools": [s["tool"] for s in steps],
            "model": settings.llm_model if use_llm else "heuristic",
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
        message_id = self._save("assistant", answer, citations=citations, meta=meta)
        yield ev("done", message_id=message_id, meta=meta)

    # ---------------------------------------------------------------- 兜底成文
    def _compose(self, question: str, steps: list[dict[str, Any]]) -> str:
        """把工具结果整理成人话（离线模式主路径；在线模式下模型已给文本）。"""
        if not steps:
            hits = get_index(self.conn).search(question, top_k=3)
            if not hits:
                return "制度中未找到相关规定。建议联系财务部（分机 8001）确认后再提交报销。"
            self.ctx.add_citations(hits)
            return self._extractive(hits)

        names = [s["tool"] for s in steps]
        last = steps[-1]["result"]
        # 索要素材优先：员工还没给发票信息时，最该看到的是"要什么"，
        # 而不是先甩一段住宿标准（那样用户会以为流程已经走完了）。
        if last.get("need_user_input"):
            return last.get("ask") or "还需要一些信息才能继续。"
        validate = next((s["result"] for s in reversed(steps) if s["tool"] == "validate_invoice"), None)
        if validate is not None:
            return self._validation_answer(validate)
        if "query_expense_standard" in names:
            std = next(s["result"] for s in reversed(steps) if s["tool"] == "query_expense_standard")
            return self._standard_answer(std)
        if last.get("ok") and last.get("draft_id"):
            return f"已生成报销单草稿 #{last['draft_id']}，合计 {last['total_amount']:.2f} 元。审批链路：{last.get('approval_chain')}"
        hits = self.ctx.citations or get_index(self.conn).search(question, top_k=3)
        return self._extractive(hits)

    @staticmethod
    def _compliance_answer(question: str, hits: list[dict[str, Any]]) -> str:
        """违规意图的固定回答口径：明确拒绝 + 给出合规做法 + 引用条文，绝不编造变通规则。"""
        lines = [
            "这个做法不合规，我不能帮你这样报。",
            "制度要求票据真实、抬头与税号准确、内容与实际业务一致，禁止虚开、拆单、代开或"
            "以其他名目变通报销；被查实属于违规报销，按员工手册处理。",
        ]
        if hits:
            lines.append("依据条文：")
            for i, hit in enumerate(hits[:2], start=1):
                text = re.sub(r"\s+", "", hit["text"])[:120]
                lines.append(f"{i}. 《{hit.get('doc_title') or hit['doc']}》{hit['heading']}：{text}…")
        lines.append(
            "如果是发票丢失、金额超标或审批卡住，请走正式渠道：发票丢失按制度 6.1 补办证明；"
            "金额超标的需部门负责人与财务共同审批；审批流程见制度 4.1。"
            "需要我帮你查具体条款可以直接问。"
        )
        return "\n".join(lines)

    @staticmethod
    def _extractive(hits: list[dict[str, Any]]) -> str:
        if not hits:
            return "制度中未找到相关规定。"
        lines = ["根据制度条文："]
        for i, hit in enumerate(hits[:2], start=1):
            text = re.sub(r"\s+", "", hit["text"])[:160]
            lines.append(f"{i}. 《{hit.get('doc_title') or hit['doc']}》{hit['heading']}：{text}…")
        return "\n".join(lines)

    @staticmethod
    def _standard_answer(std: dict[str, Any]) -> str:
        return (
            f"{std['city']}属于{std['tier']}，你是{std['level_group']}，"
            f"住宿上限 {std['lodging_cap_per_night']} 元/晚（含税），"
            f"伙食补助 {std['meal_daily']} 元/天，{std['days']:g} 天合计 {std['meal_total']} 元。\n"
            f"依据：{'、'.join(std['clauses'])}。超额部分需个人承担；"
            f"因会议或展会涨价超标的，需上传房价截图并由部门负责人与财务共同审批，单次上浮不超过 50%。"
        )

    @staticmethod
    def _validation_answer(result: dict[str, Any]) -> str:
        if result.get("ok"):
            return (
                "这张发票校验通过，可以报销。\n"
                "下一步我会生成报销单草稿，请你确认金额与明细后再提交。"
            )
        lines = ["这张发票暂时不能报销，问题如下："]
        for i, issue in enumerate(result.get("issues", []), start=1):
            lines.append(f"{i}. {issue['message']}（依据 {issue['clause']}）")
        lines.append("建议补齐材料后重新提交，或联系财务部确认。")
        return "\n".join(lines)

    @staticmethod
    def _degraded_answer(hits: list[dict[str, Any]], reason: str) -> str:
        body = AgentEngine._extractive(hits)
        return (
            f"（模型服务暂不可用：{reason}。以下为制度检索结果，请人工判断。）\n\n{body}"
        )

    # ---------------------------------------------------------------- 草稿确认
    def confirm(self, accepted: bool, reason: str = "") -> dict[str, Any]:
        """员工点"确认/取消"后执行。校验态从待确认消息里恢复，保证闸门不被绕过。"""
        row = self.conn.execute(
            "SELECT * FROM messages WHERE session_id = ? AND role = 'system'"
            " AND agent_meta LIKE '%pending_draft%' ORDER BY id DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "没有待确认的草稿"}

        meta = json.loads(row["agent_meta"])
        if meta.get("status") != "pending":
            return {"ok": False, "error": "该草稿已处理过"}

        payload = meta["pending_draft"]
        meta["status"] = "confirmed" if accepted else "rejected"
        self.conn.execute(
            "UPDATE messages SET agent_meta = ? WHERE id = ?", (json_dumps(meta), row["id"])
        )
        self.conn.commit()

        if not accepted:
            text = reason or "已取消，本次没有生成报销单。"
            self._save("assistant", text, meta={"mode": "confirm", "accepted": False})
            return {"ok": True, "accepted": False, "message": text}

        # 恢复校验态：pending 里保存的是"已通过 validate_invoice 的发票号"
        self.ctx.state["validated"] = payload.get("validated", {})
        result, ms = run_tool(self.ctx, "create_draft", payload["args"])
        self._log_tool(0, "create_draft", payload["args"], result, ms)

        if not result.get("ok"):
            text = f"生成草稿失败：{result.get('error', '未知错误')}"
            self._save("assistant", text, meta={"mode": "confirm", "accepted": True, "ok": False})
            return {"ok": False, "error": result.get("error"), "message": text}

        text = (
            f"已生成报销单草稿 **#{result['draft_id']}**，合计 {result['total_amount']:.2f} 元。\n"
            f"审批链路：{' → '.join(result['approval_chain'])}。\n"
            "你可以在「我的草稿」里查看，确认无误后提交。"
        )
        self._save("assistant", text, meta={
            "mode": "confirm", "accepted": True, "ok": True,
            "draft_id": result["draft_id"], "tools": ["create_draft"],
        })
        return {
            "ok": True,
            "accepted": True,
            "draft_id": result["draft_id"],
            "total_amount": result.get("total_amount"),
            "approval_chain": result.get("approval_chain"),
            "message": text,
        }


def _chunks(text: str, size: int) -> Iterator[str]:
    for i in range(0, len(text), size):
        yield text[i : i + size]


def find_pending(conn: Any, session_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT agent_meta FROM messages WHERE session_id = ? AND role = 'system'"
        " AND agent_meta LIKE '%pending_draft%' ORDER BY id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    meta = json.loads(row["agent_meta"])
    return meta if meta.get("status") == "pending" else None


__all__ = ["AgentEngine", "HeuristicBrain", "LLMBrain", "STEP_LIMIT", "find_pending", "render_citations"]
