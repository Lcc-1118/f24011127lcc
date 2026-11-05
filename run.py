"""一键启动入口：python run.py

它做四件事：
1. 初始化数据库（建表 + 种子账号 + 灌入 data/policies 下的制度，幂等）；
2. 自检运行环境（缺 fastapi 时给出明确的中文安装提示，而不是抛堆栈）；
3. 起 uvicorn；
4. 自动打开浏览器到 /login。

常用参数：
    python run.py --port 9000       换端口
    python run.py --no-browser      不自动开浏览器（服务器/容器里用）
    python run.py --reset           重置数据库（演示前清空历史数据）
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Windows 控制台默认 GBK，打印中文/符号会乱码或抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:  # pragma: no cover
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))


def _check_deps() -> bool:
    """依赖自检：给人看的提示，比 ModuleNotFoundError 有用。"""
    missing = []
    for mod, pkg in (("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("jinja2", "jinja2")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("缺少依赖：" + "、".join(missing))
        print("请执行： python -m pip install " + " ".join(missing))
        print("（只需要这几个包；检索与规则引擎是纯标准库实现，不需要 faiss / jieba）")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="报销助手智能体系统 · 本地启动")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--reset", action="store_true", help="删除数据库后重建（清空演示数据）")
    args = parser.parse_args()

    if not _check_deps():
        return 1

    from app.config import settings

    if args.reset and settings.db_path.exists():
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(settings.db_path) + suffix)
            if candidate.exists():
                candidate.unlink()
        print(f"已重置数据库：{settings.db_path}")

    from app.db import init_db

    conn = init_db()
    conn.close()

    url = f"http://{args.host}:{args.port}/login"
    print("=" * 62)
    print("  报销助手智能体系统已启动")
    print(f"  地址：{url}")
    print(f"  管理员：{settings.seed_admin_user} / {settings.seed_admin_pwd}")
    print(f"  员工　：{settings.seed_employee_user} / {settings.seed_employee_pwd}")
    print(f"  LLM  ：{settings.llm_model if settings.has_llm else '未配置 API Key → 离线规则引擎（功能完整可演示）'}")
    print("=" * 62)

    if not args.no_browser:
        # 服务未必立刻可连，延迟 1.2 秒再开浏览器，避免打开一个连接失败的页面
        threading.Thread(
            target=lambda: (time.sleep(1.2), webbrowser.open(url)),
            daemon=True,
        ).start()

    import uvicorn

    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info", reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
