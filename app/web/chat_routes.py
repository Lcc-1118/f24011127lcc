"""员工端：会话管理 + SSE 流式问答 + 草稿确认。

接口清单（阶段 4 的"先给接口设计"落成的实现）：
  GET    /chat                      聊天页（服务端渲染外壳，内容是 JS 拉取）
  GET    /api/sessions              我的会话列表
  POST   /api/sessions              新建会话
  DELETE /api/sessions/{id}         删除会话（级联删消息）
  GET    /api/sessions/{id}/messages 历史消息（含引用与 agent 轨迹）
  POST   /api/chat                  SSE 流式问答：{session_id, question}
  POST   /api/chat/confirm          草稿确认：{session_id, accepted, reason}
  GET    /my/drafts                 我的报销草稿页
"""
from __future__ import annotations

import json
from typing import Any, Iterator

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from app.agent.engine import AgentEngine, find_pending
from app.db import get_conn
from app.web.deps import ApiError, optional_user, render, require_user, sse_pack

router = APIRouter()


# ------------------------------------------------------------------ 页面
@router.get("/chat")
def chat_page(request: Request) -> Response:
    user = optional_user(request)
    if user is None:
        return RedirectResponse(url="/login?next=/chat", status_code=303)
    return render(request, request.app.state.templates, "chat.html", {"sessions": _sessions_of(user["id"])})


@router.get("/my/drafts")
def drafts_page(request: Request) -> Response:
    user = optional_user(request)
    if user is None:
        return RedirectResponse(url="/login?next=/my/drafts", status_code=303)

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM drafts WHERE user_id = ? ORDER BY id DESC LIMIT 20", (user["id"],)
        ).fetchall()
        drafts = []
        for row in rows:
            items = conn.execute(
                "SELECT * FROM draft_items WHERE draft_id = ? ORDER BY id", (row["id"],)
            ).fetchall()
            drafts.append({"draft": dict(row), "items": [dict(i) for i in items]})
    finally:
        conn.close()
    return render(request, request.app.state.templates, "drafts.html", {"drafts": drafts})


# ------------------------------------------------------------------ 会话 API
def _sessions_of(user_id: int) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS n_msgs"
            " FROM sessions s WHERE s.user_id = ? ORDER BY s.updated_at DESC, s.id DESC LIMIT 30",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _own_session(conn: Any, user: dict[str, Any], session_id: int) -> Any:
    """越权防护：会话必须属于当前用户，否则按"不存在"处理（不泄露他人会话存在性）。"""
    row = conn.execute(
        "SELECT * FROM sessions WHERE id = ? AND user_id = ?", (session_id, user["id"])
    ).fetchone()
    if row is None:
        raise ApiError("E_SESSION_NOT_FOUND", "会话不存在或无权访问", 404)
    return row


@router.get("/api/sessions")
def list_sessions(request: Request) -> dict:
    """我的会话列表（按最近活动排序）。"""
    user = require_user(request)
    return {"ok": True, "sessions": _sessions_of(user["id"])}


@router.post("/api/sessions")
def create_session(request: Request) -> dict:
    user = require_user(request)
    conn = get_conn()
    try:
        cur = conn.execute("INSERT INTO sessions (user_id, title) VALUES (?, '新会话')", (user["id"],))
        conn.commit()
        sid = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    finally:
        conn.close()
    return {"ok": True, "session": dict(row)}


@router.get("/api/sessions/{session_id}/messages")
def list_messages(session_id: int, request: Request) -> dict:
    user = require_user(request)
    conn = get_conn()
    try:
        _own_session(conn, user, session_id)
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? AND role IN ('user','assistant')"
            " ORDER BY id LIMIT 200",
            (session_id,),
        ).fetchall()
        messages = []
        for row in rows:
            messages.append({
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "citations": json.loads(row["citations"]) if row["citations"] else [],
                "meta": json.loads(row["agent_meta"]) if row["agent_meta"] else {},
                "created_at": row["created_at"],
            })
        pending = find_pending(conn, session_id)
    finally:
        conn.close()
    return {"ok": True, "messages": messages, "pending": pending}


@router.delete("/api/sessions/{session_id}")
def delete_session(session_id: int, request: Request) -> dict:
    user = require_user(request)
    conn = get_conn()
    try:
        _own_session(conn, user, session_id)
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ------------------------------------------------------------------ SSE 问答
@router.post("/api/chat")
def chat(request: Request, body: dict) -> StreamingResponse:
    """SSE 流式问答。

    为什么用 POST + fetch 而不是 EventSource？
    问题文本可能很长，EventSource 只能 GET 且要拼 query string；
    前端用 fetch + ReadableStream 手动解析 `data: {...}\\n\\n` 帧，能力一样。
    """
    user = require_user(request)
    session_id = body.get("session_id")
    question = (body.get("question") or "").strip()

    if not session_id:
        raise ApiError("E_BAD_REQUEST", "缺少 session_id", 400)
    if not question:
        raise ApiError("E_EMPTY_QUESTION", "请输入问题后再发送", 400)
    if len(question) > 2000:
        raise ApiError("E_QUESTION_TOO_LONG", "问题过长，请控制在 2000 字以内", 400)

    conn = get_conn()
    try:
        _own_session(conn, user, int(session_id))
    finally:
        conn.close()

    def event_stream() -> Iterator[str]:
        # 连接在生成器内部打开/关闭：流式响应期间依赖注入的清理时机不可控，
        # 自己管理生命周期最稳（也方便每个事件之间提交事务）。
        stream_conn = get_conn()
        try:
            engine = AgentEngine(stream_conn, user, int(session_id))
            for event in engine.run(question):
                yield sse_pack(event)
            yield sse_pack({"type": "end"})
        except ApiError as exc:
            yield sse_pack({"type": "error", "code": exc.detail.get("code"), "message": exc.detail.get("message")})
        except Exception as exc:  # noqa: BLE001 - 任何未捕获异常都要变成前端可读的提示
            yield sse_pack({"type": "error", "code": "E_INTERNAL", "message": f"服务异常：{exc}"})
        finally:
            stream_conn.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # 让 nginx 别缓冲，否则"打字机"会一次性出现
        },
    )


@router.post("/api/chat/confirm")
def confirm_draft(request: Request, body: dict) -> dict:
    """员工确认/取消草稿。真正的建单动作只在这里发生（人审闸门）。"""
    user = require_user(request)
    session_id = body.get("session_id")
    accepted = bool(body.get("accepted"))
    reason = (body.get("reason") or "").strip()

    if not session_id:
        raise ApiError("E_BAD_REQUEST", "缺少 session_id", 400)

    conn = get_conn()
    try:
        _own_session(conn, user, int(session_id))
        engine = AgentEngine(conn, user, int(session_id))
        result = engine.confirm(accepted, reason)
    finally:
        conn.close()

    if not result.get("ok"):
        raise ApiError("E_CONFIRM_FAILED", result.get("error") or "确认失败", 400)
    return result
