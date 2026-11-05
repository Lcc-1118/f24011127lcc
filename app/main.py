"""FastAPI 应用装配：入口层。

启动顺序：
1. init_db()：建表 + 种子数据（用户、提示词模板、制度文档与切片）——幂等，重复启动不会重复灌数据；
2. 挂载静态资源与模板引擎；
3. 注册路由：登录、员工端、管理后台；
4. 统一异常处理：所有业务异常都变成 {ok, code, message}，前端不用猜。

本地运行：python run.py            （自动开浏览器）
生产运行：uvicorn app.main:app --host 0.0.0.0 --port 8000
        注：SQLite + 内存向量索引适合单 worker；要多 worker 就把数据库换成 PostgreSQL，
        把向量库换成 pgvector / Milvus，接口层不用改。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.db import init_db
from app.web import admin_routes, auth_routes, chat_routes
from app.web.deps import ApiError, error_response, optional_user

logger = logging.getLogger("reimb")

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动时初始化数据库，退出时无需额外清理（SQLite 无连接池）。"""
    conn = init_db()
    conn.close()
    logger.info("数据库就绪：%s", settings.db_path)
    logger.info("LLM 模式：%s", settings.llm_model if settings.has_llm else "离线规则引擎")
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="报销助手智能体系统",
        description="制度问答 + 报销单草稿生成 + 管理后台（FastAPI + SSE + RAG + Agent）",
        version="1.0.0",
        lifespan=lifespan,
    )

    # ---- 模板与静态资源
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.templates = templates
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ---- 路由
    app.include_router(auth_routes.router)
    app.include_router(chat_routes.router)
    app.include_router(admin_routes.router)

    # ---- 异常处理
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        detail: Any = exc.detail
        if not isinstance(detail, dict):
            detail = {"code": "E_ERROR", "message": str(detail)}
        return error_response(detail.get("code", "E_ERROR"), detail.get("message", "请求失败"), exc.status_code)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> Response:
        logger.exception("未捕获异常: %s", exc)
        # 用户看到的永远是"发生了什么 + 下一步做什么"，不暴露堆栈
        return error_response("E_INTERNAL", "服务内部错误，请稍后重试；若持续失败请联系管理员", 500)

    # ---- 首页与健康检查
    @app.get("/")
    def index(request: Request) -> Response:
        user = optional_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        return RedirectResponse(url="/admin" if user["role"] == "admin" else "/chat", status_code=303)

    @app.get("/healthz")
    def healthz() -> dict:
        """给容器探针用，顺带暴露"当前是离线模式还是接了模型"。"""
        return {
            "ok": True,
            "llm": "online" if settings.has_llm else "offline",
            "model": settings.llm_model if settings.has_llm else "heuristic",
            "db": str(settings.db_path),
        }

    return app


app = create_app()
