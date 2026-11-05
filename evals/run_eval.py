#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""报销助手评测脚本（提示词 8 / 提示词 9 的落地）。

设计要点
--------
1. **不依赖 Web 服务**：直接驱动 `AgentEngine`，一条命令就能跑完 30 条用例，
   CI 与本地都能复现，不受网络与端口影响。
2. **不依赖大模型**：没有 API Key 时走 `HeuristicBrain`（确定性规则），
   所以评测结果稳定、可对比；配了 Key 时同一套用例会自动走真实 Function Calling。
3. **隔离**：默认使用临时 SQLite 库（每次重建），不会污染 db/reimb.sqlite3。

用法
----
    python evals/run_eval.py                 # 跑全部用例
    python evals/run_eval.py --id T03        # 只跑某一条
    python evals/run_eval.py --category 违规意图类
    python evals/run_eval.py --verbose       # 打印每次回答
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
from typing import Any

# Windows 控制台默认 GBK，打印中文/符号会炸，这里统一成 UTF-8
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover - 老版本解释器
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def prepare_db(path: str) -> None:
    """准备一个干净的数据库文件。"""
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    os.environ["REIMB_DB_PATH"] = path
    from app.db import init_db

    init_db()


def run_turn(conn: Any, user: dict[str, Any], session_id: int, question: str) -> dict[str, Any]:
    """跑一轮问答，把 SSE 事件流压缩成可断言的记录。"""
    from app.agent.engine import AgentEngine

    engine = AgentEngine(conn, user, session_id=session_id)
    tools: list[str] = []
    answer: list[str] = []
    confirm: dict[str, Any] | None = None
    errors: list[str] = []
    citations: list[dict[str, Any]] = []

    for event in engine.run(question):
        kind = event.get("type")
        if kind == "tool_call":
            tools.append(event.get("name", ""))
        elif kind == "token":
            answer.append(event.get("text", ""))
        elif kind == "citations":
            citations = event.get("items", [])
        elif kind == "confirm_required":
            confirm = event.get("draft") or {}
        elif kind == "error":
            errors.append(str(event.get("message")))

    text = re_sub_ws("".join(answer))
    return {
        "question": question,
        "tools": tools,
        "answer": text,
        "citations": citations,
        "confirm": confirm,
        "errors": errors,
    }


def re_sub_ws(text: str) -> str:
    """把回答里的换行/多空格压成单空格，方便做关键词断言。"""
    return " ".join(text.split())


def is_subsequence(expected: list[str], actual: list[str]) -> bool:
    """期望的工具必须按顺序出现在实际调用里（允许中间夹别的工具）。"""
    if not expected:
        return True
    it = iter(actual)
    return all(any(tool == want for tool in it) for want in expected)


def check_case(case: dict[str, Any], turns_result: list[dict[str, Any]]) -> list[str]:
    """返回失败原因列表，空列表表示通过。"""
    problems: list[str] = []
    checks = case.get("checks") or {}
    index = checks.get("turn_index", len(turns_result) - 1)
    target = turns_result[index] if len(turns_result) > index else turns_result[-1]
    all_tools = [tool for item in turns_result for tool in item["tools"]]

    for item in turns_result:
        if item["errors"]:
            problems.append(f"出现错误事件：{item['errors']}")

    if case.get("expected_tools") and not is_subsequence(case["expected_tools"], all_tools):
        problems.append("工具调用不符：期望（按序）" f"{case['expected_tools']}，实际 {all_tools}")

    if checks.get("expect_confirm") is True and not any(t["confirm"] for t in turns_result):
        problems.append("期望出现建单确认中断（confirm_required），实际没有")
    if checks.get("expect_confirm") is False and any(t["confirm"] for t in turns_result):
        problems.append("期望不触发建单中断，实际触发了")

    answer = target["answer"]
    any_of = checks.get("must_contain_any") or []
    if any_of and not any(word in answer for word in any_of):
        problems.append(f"回答未命中关键词（任一即可）{any_of}，实际：{answer[:120]}")
    for word in checks.get("must_not_contain") or []:
        if word in answer:
            problems.append(f"回答出现了不该有的内容：{word}，实际：{answer[:120]}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="报销助手评测")
    parser.add_argument("--cases", default=os.path.join(ROOT, "evals", "cases.json"))
    parser.add_argument("--db", default="", help="指定数据库文件，默认用临时库")
    parser.add_argument("--id", default="", help="只跑某一条用例")
    parser.add_argument("--category", default="", help="只跑某类用例")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--report", default=os.path.join(ROOT, "evals", "report.json"))
    args = parser.parse_args()

    with io.open(args.cases, encoding="utf-8") as fh:
        cases = json.load(fh)
    if args.id:
        cases = [c for c in cases if c["id"] == args.id]
    if args.category:
        cases = [c for c in cases if args.category in c["category"]]
    if not cases:
        print("没有匹配的用例")
        return 2

    db_path = args.db or os.path.join(tempfile.gettempdir(), "reimb_eval.sqlite3")
    prepare_db(db_path)

    from app.db import execute, get_conn, query_one

    conn = get_conn()
    user = dict(query_one(conn, "SELECT * FROM users WHERE username = 'zhangwei'"))

    passed, failed, report = 0, 0, []
    print(f"评测开始：{len(cases)} 条用例，库={db_path}")
    print("-" * 96)
    for case in cases:
        questions = case.get("turns") or [case["question"]]
        cur = execute(
            conn,
            "INSERT INTO sessions (user_id, title) VALUES (?, ?)",
            (user["id"], questions[0][:30]),
        )
        session_id = cur.lastrowid
        results = [run_turn(conn, user, session_id, q) for q in questions]
        problems = check_case(case, results)
        ok = not problems
        passed += ok
        failed += (not ok)
        flag = "PASS" if ok else "FAIL"
        print(f"[{flag}] {case['id']:<4} {case['category']:<8} {case['question'][:56]}")
        if not ok:
            for problem in problems:
                print(f"       - {problem}")
        if args.verbose:
            last = results[-1]
            print(f"       tools={last['tools']} citations={len(last['citations'])}")
            print(f"       answer={last['answer'][:200]}")
        report.append({
            "id": case["id"],
            "category": case["category"],
            "question": case["question"],
            "ok": ok,
            "problems": problems,
            "turns": [
                {
                    "question": r["question"],
                    "tools": r["tools"],
                    "answer": r["answer"],
                    "citations": [
                        {"doc": c.get("doc"), "heading": c.get("heading"), "score": c.get("score")}
                        for c in r["citations"]
                    ],
                    "confirm": bool(r["confirm"]),
                }
                for r in results
            ],
        })

    print("-" * 96)
    rate = passed / (passed + failed) * 100 if (passed + failed) else 0.0
    print(f"合计 {passed + failed} 条：通过 {passed}，失败 {failed}，通过率 {rate:.1f}%")

    if args.report:
        with io.open(args.report, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(
                {"total": passed + failed, "passed": passed, "failed": failed,
                 "pass_rate": round(rate, 2), "cases": report},
                fh, ensure_ascii=False, indent=2,
            )
            fh.write("\n")
        print(f"报告已写入 {args.report}")
    return 0 if failed == 0 else 1


def _noop() -> None:  # pragma: no cover
    """占位，保证模块可作为脚本运行时的导入结构稳定。"""


if __name__ == "__main__":
    raise SystemExit(main())
