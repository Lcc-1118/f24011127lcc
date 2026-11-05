"""管理后台：制度知识库、提示词模板、对话审计（服务端渲染，类 SaaS 风格）。

权限模型很简单但必须严格：
- 所有 /admin 页面：未登录 → 302 登录页；已登录但非管理员 → 403 页面；
- 所有 /api/admin 接口：未登录 → 401；非管理员 → 403（由 require_admin 统一处理）。
页面与接口走两套守卫，是因为浏览器期望 HTML，脚本期望 JSON。
"""
from __future__ import annotations

import io
import json
import re
from datetime import date
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from app.agent.engine import STEP_LIMIT
from app.config import settings
from app.db import get_conn, rebuild_chunks
from app.web.deps import ApiError, optional_user, render

router = APIRouter(prefix="/admin")

# 疑似违规关键词：只用于"提示人工复核"，绝不自动判定（制度判定由代码规则 + 人工确认）
SUSPICIOUS = [
    "不要发票", "不开发票", "多开", "虚开", "拆单", "凑票", "补开",
    "换个科目", "走别的科目", "报成", "绕过审批", "先报了再说",
]


# ------------------------------------------------------------------ 守卫
def _admin_page(request: Request) -> Response | None:
    """页面级守卫：返回 None 表示放行，否则返回一个响应（重定向或 403）。"""
    user = optional_user(request)
    if user is None:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)
    if user["role"] != "admin":
        return render(request, request.app.state.templates, "forbidden.html", {}, status_code=403)
    return None


def _tpl(request: Request):
    return request.app.state.templates


# ------------------------------------------------------------------ 仪表盘
@router.get("")
@router.get("/")
def dashboard(request: Request) -> Response:
    if (resp := _admin_page(request)) is not None:
        return resp

    conn = get_conn()
    try:
        def one(sql: str, params: tuple = ()) -> int:
            row = conn.execute(sql, params).fetchone()
            return int(row[0]) if row else 0

        stats = {
            "users": one("SELECT COUNT(*) FROM users"),
            "sessions": one("SELECT COUNT(*) FROM sessions"),
            "messages": one("SELECT COUNT(*) FROM messages WHERE role = 'assistant'"),
            "drafts": one("SELECT COUNT(*) FROM drafts"),
            "docs": one("SELECT COUNT(*) FROM policy_docs WHERE status = 'active'"),
            "chunks": one("SELECT COUNT(*) FROM policy_chunks"),
            "flags": one("SELECT COUNT(*) FROM audit_flags WHERE status = 'open'"),
            "tool_calls": one("SELECT COUNT(*) FROM agent_logs"),
        }
        per_tool = [
            dict(r) for r in conn.execute(
                "SELECT tool_name, COUNT(*) AS n, ROUND(AVG(duration_ms), 1) AS avg_ms"
                " FROM agent_logs GROUP BY tool_name ORDER BY n DESC"
            ).fetchall()
        ]
        recent = [
            dict(r) for r in conn.execute(
                "SELECT l.*, s.user_id, u.display_name FROM agent_logs l"
                " JOIN sessions s ON s.id = l.session_id JOIN users u ON u.id = s.user_id"
                " ORDER BY l.id DESC LIMIT 12"
            ).fetchall()
        ]
        pending_drafts = one("SELECT COUNT(*) FROM drafts WHERE status = 'draft'")
    finally:
        conn.close()

    return render(request, _tpl(request), "admin_dashboard.html", {
        "stats": stats,
        "per_tool": per_tool,
        "recent": recent,
        "pending_drafts": pending_drafts,
        "model_mode": settings.llm_model if settings.has_llm else "离线规则引擎（未配置 LLM_API_KEY）",
        "max_rounds": STEP_LIMIT,
    })


# ------------------------------------------------------------------ 制度知识库
@router.get("/policies")
def policies_page(request: Request, msg: str = "") -> Response:
    if (resp := _admin_page(request)) is not None:
        return resp

    conn = get_conn()
    try:
        docs = [
            dict(r) for r in conn.execute(
                "SELECT d.*, (SELECT COUNT(*) FROM policy_chunks c WHERE c.doc_id = d.id) AS n_chunks"
                " FROM policy_docs d ORDER BY d.id DESC"
            ).fetchall()
        ]
    finally:
        conn.close()

    return render(request, _tpl(request), "admin_policies.html", {"docs": docs, "msg": msg})


@router.post("/policies")
async def upload_policy(
    request: Request,
    file: UploadFile = File(...),
    version: str = Form("V1.0"),
    title: str = Form(""),
    effective_date: str = Form(""),
) -> Response:
    """上传制度文件 → 解析文本 → 切片 → 入库。

    支持 .md / .txt / .pdf：PDF 是报销制度最常见的真实形态，用 pypdf 抽文本；
    扫描件（图片型 PDF）抽不出文本时明确报错，而不是入库一份空文档。
    """
    if (resp := _admin_page(request)) is not None:
        return resp

    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise ApiError("E_FILE_TOO_LARGE", "文件超过 8MB，请拆分后再上传", 413)

    filename = file.filename or "upload.md"
    text = _extract_text(filename, raw)
    if len(text.strip()) < 20:
        raise ApiError(
            "E_EMPTY_DOCUMENT",
            "没有从文件中解析出文本：扫描件 PDF 需要先做 OCR，或改为上传 markdown",
            400,
        )

    doc_title = title.strip() or _guess_title(text, filename)
    eff = effective_date.strip() or date.today().isoformat()
    admin = optional_user(request) or {}

    conn = get_conn()
    try:
        # 同名文件视为新版本：旧版本归档，保证"当时按哪一版判定"可追溯
        conn.execute(
            "UPDATE policy_docs SET status = 'archived', updated_at = datetime('now','localtime')"
            " WHERE filename = ? AND status = 'active'",
            (filename,),
        )
        cur = conn.execute(
            "INSERT INTO policy_docs (filename, title, version, effective_date, content, status, uploaded_by)"
            " VALUES (?,?,?,?,?,'active',?)",
            (filename, doc_title, version.strip() or "V1.0", eff, text, admin.get("display_name")),
        )
        conn.commit()
        doc_id = int(cur.lastrowid)
        n = rebuild_chunks(conn, doc_id)
    finally:
        conn.close()

    # 原始文件留档，便于人工核对（比如分段是否符合阅读习惯）
    try:
        target = settings.policies_dir / filename
        settings.policies_dir.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    except OSError:
        pass  # 留档失败不影响入库

    return RedirectResponse(url=f"/admin/policies?msg=已导入《{doc_title}》，切片 {n} 段", status_code=303)


@router.post("/policies/{doc_id}/delete")
def delete_policy(doc_id: int, request: Request) -> Response:
    if (resp := _admin_page(request)) is not None:
        return resp
    conn = get_conn()
    try:
        conn.execute("DELETE FROM policy_docs WHERE id = ?", (doc_id,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url="/admin/policies?msg=已删除该制度文档及其切片", status_code=303)


@router.post("/policies/{doc_id}/rechunk")
def rechunk_policy(doc_id: int, request: Request) -> Response:
    """切片参数（500 字）改了以后需要重建：切片入库而不是每次现算，才能审计。"""
    if (resp := _admin_page(request)) is not None:
        return resp
    conn = get_conn()
    try:
        n = rebuild_chunks(conn, doc_id)
    finally:
        conn.close()
    return RedirectResponse(url=f"/admin/policies?msg=已按当前参数重建切片，共 {n} 段", status_code=303)


@router.get("/policies/{doc_id}")
def policy_detail(doc_id: int, request: Request) -> Response:
    if (resp := _admin_page(request)) is not None:
        return resp
    conn = get_conn()
    try:
        doc = conn.execute("SELECT * FROM policy_docs WHERE id = ?", (doc_id,)).fetchone()
        if doc is None:
            raise ApiError("E_DOC_NOT_FOUND", "制度文档不存在", 404)
        chunks = [dict(r) for r in conn.execute(
            "SELECT * FROM policy_chunks WHERE doc_id = ? ORDER BY ord", (doc_id,)
        ).fetchall()]
    finally:
        conn.close()
    return render(request, _tpl(request), "admin_policy_detail.html",
                  {"doc": dict(doc), "chunks": chunks})


# ------------------------------------------------------------------ 提示词模板
@router.get("/prompts")
def prompts_page(request: Request, msg: str = "") -> Response:
    if (resp := _admin_page(request)) is not None:
        return resp
    conn = get_conn()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM prompt_templates ORDER BY key").fetchall()]
    finally:
        conn.close()
    return render(request, _tpl(request), "admin_prompts.html", {"prompts": rows, "msg": msg})


@router.post("/prompts/{key}")
def save_prompt(key: str, request: Request, content: str = Form("")) -> Response:
    """保存提示词：写数据库并自增版本号，下一轮对话立即生效（引擎每轮现读）。"""
    if (resp := _admin_page(request)) is not None:
        return resp
    body = content.strip()
    if len(body) < 10:
        raise ApiError("E_PROMPT_TOO_SHORT", "提示词太短，至少 10 个字", 400)

    admin = optional_user(request) or {}
    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM prompt_templates WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise ApiError("E_PROMPT_NOT_FOUND", "提示词模板不存在", 404)
        conn.execute(
            "UPDATE prompt_templates SET content = ?, version = version + 1,"
            " updated_by = ?, updated_at = datetime('now','localtime') WHERE key = ?",
            (body, admin.get("display_name"), key),
        )
        conn.commit()
        version = conn.execute("SELECT version FROM prompt_templates WHERE key = ?", (key,)).fetchone()[0]
    finally:
        conn.close()
    return RedirectResponse(url=f"/admin/prompts?msg=已保存 {key}（版本 v{version}），下一轮对话立即生效",
                            status_code=303)


# ------------------------------------------------------------------ 对话审计
@router.get("/audit")
def audit_page(request: Request, user_id: str = "", date_from: str = "",
               date_to: str = "", only_flagged: str = "") -> Response:
    if (resp := _admin_page(request)) is not None:
        return resp

    sql = (
        "SELECT m.*, s.user_id, u.display_name, u.department, s.title AS session_title,"
        " (SELECT COUNT(*) FROM audit_flags f WHERE f.message_id = m.id) AS n_flags"
        " FROM messages m JOIN sessions s ON s.id = m.session_id JOIN users u ON u.id = s.user_id"
        " WHERE m.role IN ('user','assistant')"
    )
    params: list[Any] = []
    if user_id.strip().isdigit():
        sql += " AND s.user_id = ?"
        params.append(int(user_id))
    if date_from.strip():
        sql += " AND date(m.created_at) >= date(?)"
        params.append(date_from.strip())
    if date_to.strip():
        sql += " AND date(m.created_at) <= date(?)"
        params.append(date_to.strip())
    if only_flagged.strip():
        sql += " AND EXISTS (SELECT 1 FROM audit_flags f WHERE f.message_id = m.id)"
    sql += " ORDER BY m.id DESC LIMIT 100"

    conn = get_conn()
    try:
        rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
        users = [dict(r) for r in conn.execute("SELECT id, display_name, department FROM users ORDER BY id").fetchall()]
        flags = [dict(r) for r in conn.execute(
            "SELECT f.*, m.content FROM audit_flags f JOIN messages m ON m.id = f.message_id"
            " ORDER BY f.id DESC LIMIT 50"
        ).fetchall()]
    finally:
        conn.close()

    for row in rows:
        hits = [w for w in SUSPICIOUS if w in (row["content"] or "")]
        row["suspicion"] = hits
        row["citations"] = json.loads(row["citations"]) if row["citations"] else []
        row["agent_meta"] = json.loads(row["agent_meta"]) if row["agent_meta"] else {}

    return render(request, _tpl(request), "admin_audit.html", {
        "rows": rows, "users": users, "flags": flags,
        "user_id": user_id, "date_from": date_from, "date_to": date_to,
        "only_flagged": only_flagged,
    })


@router.post("/audit/{message_id}/flag")
def flag_message(message_id: int, request: Request, reason: str = Form(...)) -> Response:
    """标记疑似违规：留痕（谁标的、为什么），而不是直接删消息。"""
    if (resp := _admin_page(request)) is not None:
        return resp
    admin = optional_user(request) or {}
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO audit_flags (message_id, reason, flagged_by) VALUES (?,?,?)",
            (message_id, reason.strip() or "疑似违规", admin.get("display_name") or admin.get("username")),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url="/admin/audit?only_flagged=1&msg=已标记，等待复核", status_code=303)


@router.post("/audit/flag/{flag_id}/resolve")
def resolve_flag(flag_id: int, request: Request) -> Response:
    if (resp := _admin_page(request)) is not None:
        return resp
    conn = get_conn()
    try:
        conn.execute("UPDATE audit_flags SET status = 'resolved' WHERE id = ?", (flag_id,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url="/admin/audit?only_flagged=1", status_code=303)


# ------------------------------------------------------------------ Agent 日志
@router.get("/logs")
def logs_page(request: Request, session_id: str = "") -> Response:
    """Agent 每轮决策的轨迹：工具名、参数、耗时、结果摘要（阶段 7 的日志表落地）。"""
    if (resp := _admin_page(request)) is not None:
        return resp
    sql = (
        "SELECT l.*, u.display_name FROM agent_logs l"
        " JOIN sessions s ON s.id = l.session_id JOIN users u ON u.id = s.user_id WHERE 1=1"
    )
    params: list[Any] = []
    if session_id.strip().isdigit():
        sql += " AND l.session_id = ?"
        params.append(int(session_id))
    sql += " ORDER BY l.id DESC LIMIT 200"

    conn = get_conn()
    try:
        rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    finally:
        conn.close()
    return render(request, _tpl(request), "admin_logs.html", {"rows": rows, "session_id": session_id})


# ------------------------------------------------------------------ 工具函数
def _extract_text(filename: str, raw: bytes) -> str:
    """按扩展名解析文本。PDF 用 pypdf；其余按 UTF-8（失败再试 GBK，兼容 Windows 导出的 txt）。"""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        try:
            from pypdf import PdfReader  # 延迟导入：没装 pypdf 也能跑 markdown 场景
        except ImportError as exc:
            raise ApiError("E_PDF_UNSUPPORTED", "缺少 pypdf，无法解析 PDF；请上传 markdown", 400) from exc
        reader = PdfReader(io.BytesIO(raw))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)

    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ApiError("E_DECODE_FAILED", "无法识别文件编码，请转成 UTF-8 后重试", 400)


def _guess_title(text: str, filename: str) -> str:
    """标题优先取第一个 markdown 一级标题，取不到就用文件名。"""
    for line in text.splitlines():
        if line.strip().startswith("# "):
            return line.strip()[2:].strip()[:60]
    return re.sub(r"\.[a-zA-Z0-9]+$", "", filename)[:60]
