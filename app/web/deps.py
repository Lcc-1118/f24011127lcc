"""Web 层公共依赖：数据库连接、JWT 鉴权、角色校验、统一错误响应。

为什么要单独一层？
- 鉴权是"横切关注点"，如果散落在每个路由里，迟早会漏一个接口；
- 这里把"取用户"和"要权限"做成依赖函数，路由只要声明依赖就自动生效；
- 页面类请求失败要 302 去登录页，接口类请求失败要返回 JSON 错误码，
  两种语义分开（require_user / _login_redirect），避免前端拿到一坨 HTML。
"""
from __future__ import annotations

import json
from typing import Any, Iterator

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.config import settings
from app.db import get_conn
from app.security import TokenError, decode_access_token

# JWT 存放位置：httpOnly Cookie（防 XSS 读取）+ 兼容 Authorization: Bearer（方便 curl / 自动化测试）
COOKIE_NAME = "reimb_token"


# ------------------------------------------------------------------ 数据库
def db_conn() -> Iterator[Any]:
    """每个请求一个 SQLite 连接，请求结束即关闭。

    不要在模块级复用连接：SQLite 连接跨线程会炸，而且长连接会拖住 WAL。
    """
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


# ------------------------------------------------------------------ 错误
def error_response(code: str, message: str, status_code: int = 400) -> JSONResponse:
    """统一错误结构：{ok:false, code, message}。前端只看 code 决定提示，message 给人看。"""
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "code": code, "message": message},
    )


class ApiError(HTTPException):
    """业务异常：抛出后由 main.py 的异常处理器统一转成 {ok, code, message}。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message})


# ------------------------------------------------------------------ 取 token
def _token_from(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(COOKIE_NAME)


def optional_user(request: Request) -> dict[str, Any] | None:
    """解析当前用户；未登录 / token 过期都返回 None（不抛异常）。"""
    token = _token_from(request)
    if not token:
        return None
    try:
        payload = decode_access_token(token)
    except TokenError:
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, username, display_name, role, job_level, department, employee_no"
            " FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _login_redirect(request: Request) -> Response:
    """页面请求未登录：直接 302 到登录页，并带回跳地址。"""
    nxt = request.url.path
    return RedirectResponse(url=f"/login?next={nxt}", status_code=303)


def require_user(request: Request) -> dict[str, Any]:
    """接口用：要求已登录，否则 401 + 中文提示。"""
    user = optional_user(request)
    if user is None:
        raise ApiError("E_UNAUTHORIZED", "请先登录，或登录状态已过期", 401)
    return user


def require_admin(request: Request) -> dict[str, Any]:
    """接口用：要求管理员角色。员工调管理接口必须 403，而不是 404 藏起来。"""
    user = require_user(request)
    if user["role"] != "admin":
        raise ApiError("E_FORBIDDEN", "需要管理员权限才能执行此操作", 403)
    return user


# ------------------------------------------------------------------ 页面渲染
def render(request: Request, templates: Any, name: str, ctx: dict[str, Any] | None = None,
           status_code: int = 200) -> Response:
    """渲染模板，自动注入当前用户与全局配置（所有页面都要用）。"""
    context: dict[str, Any] = {
        "user": optional_user(request),
        "company_name": settings.company_name,
        "has_llm": settings.has_llm,
    }
    context.update(ctx or {})
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def sse_pack(payload: dict[str, Any]) -> str:
    """SSE 帧格式：`data: {json}\\n\\n`。浏览器 EventSource 与 fetch 流式解析都能吃。"""
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
