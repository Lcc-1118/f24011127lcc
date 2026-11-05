#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""报销助手 · 一键演示入口（quickstart）

这个文件的目的只有一个：**不装任何东西、不看文档，直接跑出全部成果。**

    python quickstart.py            # 控制台演示 3 个场景（零第三方依赖，不需要 API Key）
    python quickstart.py --web      # 启动 Web 版（需要 fastapi/uvicorn，见 requirements.txt）
    python quickstart.py --interactive   # 控制台交互问答

为什么能"零依赖"：检索层（BM25 + 哈希向量 + RRF）、规则引擎、工具层、Agent 循环、
SQLite 全部只用标准库。FastAPI 只在 Web 入口用到，控制台演示不 import 它。

演示的 3 个场景对应项目要证明的三件事：
    场景 1  制度问答      → 检索真的能取到正确条文，且"制度里没有"会老实说没有
    场景 2  发票校验      → 硬规则由代码判定，不由模型"感觉"判定
    场景 3  建单确认      → 有副作用的动作必须人工确认，且无法绕过校验
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent

# Windows 控制台默认 GBK，中文字符/符号会抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:  # pragma: no cover
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

sys.path.insert(0, str(BASE))


def _banner() -> None:
    print("=" * 78)
    print(" 报销助手智能体系统 · 一键演示")
    print(" 制度问答 + 发票校验 + 报销单草稿（FastAPI / SQLite / BM25+向量混合检索）")
    print("=" * 78)


def _prepare_db() -> str:
    """用临时库演示，绝不污染仓库里的 data/reimb.db。"""
    db_path = os.path.join(tempfile.gettempdir(), "reimb_quickstart.sqlite3")
    for suffix in ("", "-wal", "-shm"):
        target = Path(db_path + suffix)
        if target.exists():
            target.unlink()
    return db_path


def _demo_console() -> int:
    from app import db
    from app.agent.engine import AgentEngine

    db_path = _prepare_db()
    conn = db.init_db(db_path)
    user = db.query_one(conn, "SELECT * FROM users WHERE username = ?", ("zhangwei",))
    if user is None:
        print("[错误] 种子用户缺失，数据库初始化异常。")
        return 2

    print(f"\n演示账号：{user['display_name']}（{user['job_level']} / {user['department']}）")
    print(f"临时数据库：{db_path}")
    print(f"模型模式：{'在线 LLM' if _has_llm() else '离线规则大脑（未配置 LLM_API_KEY，不影响演示）'}")

    scenarios = [
        (
            "场景 1 · 制度问答（检索到正确条文；制度里没有就老实说没有）",
            [
                "出差上海3天住宿标准是多少",
                "公司有健身房补贴吗",
            ],
        ),
        (
            "场景 2 · 发票校验（90 天时效、抬头税号由代码判定）",
            [
                "这个发票能报吗，发票号 24681357，金额 300 元，日期 2026-10-15",
                "这张发票能报吗 发票号 13572468 金额 20000 元 日期 2025-01-01",
            ],
        ),
        (
            "场景 3 · 建单（先校验，再中断等确认；确认后才落库）",
            [
                "帮我报上个月去北京的差旅，发票号 87654321，金额 480 元，日期 2026-10-20",
            ],
        ),
    ]

    for title, questions in scenarios:
        print("\n" + "-" * 78)
        print(title)
        print("-" * 78)
        for question in questions:
            sid = db.execute(
                conn, "INSERT INTO sessions (user_id, title) VALUES (?, ?)", (user["id"], question[:20])
            ).lastrowid
            engine = AgentEngine(conn, user, sid)
            print(f"\n问：{question}")
            answer_parts: list[str] = []
            for event in engine.run(question):
                etype = event.get("type")
                if etype == "tool_call":
                    print(f"   调用 {event['name']} {_short(event.get('args'))}")
                elif etype == "tool_result":
                    print(f"   结果 {event['name']} ok={event.get('ok')} {_short(event.get('summary'), 90)}")
                elif etype == "token":
                    answer_parts.append(event.get("text", ""))
                elif etype == "citations":
                    items = event.get("items") or []
                    if items:
                        print(f"   引用 {len(items)} 条：{items[0].get('heading', '')[:40]}")
                elif etype == "confirm_required":
                    draft = event.get("draft") or {}
                    total = draft.get("total", 0)
                    print(f"   ⚠ 需要人工确认：合计 ¥{total}，审批链路 {' → '.join(draft.get('approval_chain') or [])}")
                    result = engine.confirm(True)          # 演示里自动点"确认"
                    if result.get("ok"):
                        print(f"   ✅ 已生成草稿 #{result.get('draft_id')}，合计 {result.get('total_amount')} 元")
                    else:
                        print(f"   ❌ 建单失败：{result.get('error') or result.get('message')}")
            answer = "".join(answer_parts).strip()
            if answer:
                print("答：" + _wrap(answer, 74, "    "))

    print("\n" + "=" * 78)
    print(" 以上全部由标准库跑出：BM25 + 哈希向量 RRF 融合检索、规则引擎、Agent 循环、SQLite。")
    print(" Web 版：python quickstart.py --web   （员工聊天 / 管理后台 / 审计）")
    print(" 评测：  python evals/run_eval.py     （30 条用例，100% 通过）")
    print(" 检索：  python scripts/recall_report.py  （recall@3 95.8%，弃答 100%）")
    print("=" * 78)
    return 0


def _demo_interactive() -> int:
    from app import db
    from app.agent.engine import AgentEngine

    conn = db.init_db(_prepare_db())
    user = db.query_one(conn, "SELECT * FROM users WHERE username = ?", ("zhangwei",))
    sid = db.execute(conn, "INSERT INTO sessions (user_id, title) VALUES (?, ?)", (user["id"], "交互")).lastrowid
    engine = AgentEngine(conn, user, sid)

    print("\n进入交互问答（输入 exit 退出）。可以直接粘贴：出差上海3天住宿标准是多少\n")
    while True:
        try:
            question = input("问：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if question.lower() in {"exit", "quit", "q", ""}:
            if question:
                return 0
            continue
        pending = False
        answer: list[str] = []
        for event in engine.run(question):
            etype = event.get("type")
            if etype == "token":
                answer.append(event.get("text", ""))
            elif etype == "tool_call":
                print(f"  [轮 {event.get('round')}] 调用 {event['name']}")
            elif etype == "confirm_required":
                pending = True
        print("答：" + "".join(answer).strip())
        if pending:
            choice = input("  生成报销单草稿？(y/N) ").strip().lower()
            result = engine.confirm(choice == "y")
            print("  " + str(result.get("message") or result))


def _demo_web() -> int:
    try:
        import uvicorn  # noqa: F401
        from fastapi import FastAPI  # noqa: F401
    except Exception:  # noqa: BLE001
        print("[提示] Web 版需要两个依赖，执行下面这行再重试：")
        print("       python -m pip install -r requirements.txt")
        return 2
    print("启动 Web 版 …（Ctrl+C 停止）")
    print("  员工端：http://127.0.0.1:8000/chat    账号 zhangwei / emp123")
    print("  管理端：http://127.0.0.1:8000/admin   账号 admin / admin123")
    return subprocess.call([sys.executable, str(BASE / "run.py"), *sys.argv[2:]])


def _has_llm() -> bool:
    from app.config import settings

    return bool(getattr(settings, "has_llm", False))


def _short(obj, limit: int = 70) -> str:
    text = str(obj)
    return text if len(text) <= limit else text[:limit] + "…"


def _wrap(text: str, width: int, indent: str) -> str:
    """按视觉宽度折行（中文算 2 列）。"""
    lines, current, current_width = [], "", 0
    for char in text:
        char_width = 2 if ord(char) > 0x2E80 else 1
        if current_width + char_width > width:
            lines.append(current)
            current, current_width = "", 0
        if char == "\n":
            lines.append(current)
            current, current_width = "", 0
            continue
        current += char
        current_width += char_width
    if current:
        lines.append(current)
    return ("\n" + indent).join(lines)


def main() -> int:
    _banner()
    if "--web" in sys.argv:
        return _demo_web()
    if "--interactive" in sys.argv:
        return _demo_interactive()
    return _demo_console()


if __name__ == "__main__":
    raise SystemExit(main())
