"""登录 / 登出：JWT 写进 httpOnly Cookie，服务端渲染页面与 SSE 接口共用同一份凭证。

为什么用 Cookie 而不是 localStorage？
- SSE / 事件流用 fetch 时也要带凭证，Cookie 自动带，前端不用每个请求手动加头；
- httpOnly 让 XSS 拿不到 token；SameSite=Lax 挡住跨站表单提交。
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from app.config import settings
from app.db import get_conn
from app.security import create_access_token, verify_password
from app.web.deps import COOKIE_NAME, ApiError, optional_user, render

router = APIRouter()


@router.get("/login")
def login_page(request: Request, next: str = "/chat") -> Response:
    """登录页。已登录用户直接跳到目标页，避免重复登录。"""
    if optional_user(request) is not None:
        return RedirectResponse(url=_safe_next(next), status_code=303)
    return render(request, request.app.state.templates, "login.html", {
        "next": _safe_next(next),
        "seed_admin": settings.seed_admin_user,
        "seed_employee": settings.seed_employee_user,
    })


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/chat"),
) -> Response:
    """校验账号密码 → 签发 JWT → 写 Cookie。

    错误信息故意不区分"用户不存在"和"密码错误"，避免被用来枚举账号。
    """
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
    finally:
        conn.close()

    if row is None or not verify_password(password, row["password_hash"]):
        # 登录失败回显：不重置已填用户名，只加错误提示
        return render(request, request.app.state.templates, "login.html", {
            "next": _safe_next(next),
            "error": "账号或密码不正确",
            "username": username,
            "seed_admin": settings.seed_admin_user,
            "seed_employee": settings.seed_employee_user,
        }, status_code=401)

    token = create_access_token({
        "sub": row["id"],
        "username": row["username"],
        "role": row["role"],
    })

    target = _safe_next(next)
    if row["role"] == "admin" and target == "/chat":
        target = "/admin"          # 管理员登录默认进后台

    resp = RedirectResponse(url=target, status_code=303)
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.jwt_ttl_hours * 3600,
        path="/",
    )
    return resp


@router.post("/api/logout")
@router.get("/logout")
def logout() -> Response:
    """登出：清 Cookie。JWT 是自包含的，服务端不存会话，所以不需要"吊销"。"""
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@router.get("/api/me")
def me(request: Request) -> dict:
    """前端用来判断登录态与角色（决定是否显示"管理后台"入口）。"""
    user = optional_user(request)
    if user is None:
        raise ApiError("E_UNAUTHORIZED", "请先登录", 401)
    return {"ok": True, "user": user, "has_llm": settings.has_llm}


def _safe_next(raw: str | None) -> str:
    """防开放重定向：只允许站内相对路径。"""
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return "/chat"
    return raw
