# -*- coding: utf-8 -*-
"""延迟基线：问答首字/总耗时、发票预检耗时，输出 latency.json（供测试方案与报告引用）。"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import statistics
import time
import urllib.request
from datetime import date
from pathlib import Path

Q = [
    "出差上海3天住宿标准是多少",
    "出差成都住宿标准是多少",
    "伙食补助一天多少钱",
    "报销时效是多久",
    "发票抬头必须是什么",
    "单笔报销2万元需要谁审批",
    "实习生差旅标准怎么算",
    "市内交通一天能报多少",
    "高铁能坐一等座吗",
    "住宿超标了怎么办",
]


def pct(xs, p):
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8124")
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.open(urllib.request.Request(base + "/login",
                                   data=b"username=zhangwei&password=emp123&next=/chat",
                                   headers={"Content-Type": "application/x-www-form-urlencoded"}))

    def new_session():
        r = op.open(urllib.request.Request(base + "/api/sessions", data=b"", method="POST"))
        return json.loads(r.read())["session"]["id"]

    def ask(sid, q):
        body = json.dumps({"session_id": sid, "question": q}, ensure_ascii=False).encode()
        req = urllib.request.Request(base + "/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        first = None
        answer = ""
        with op.open(req, timeout=120) as resp:
            buf = b""
            while True:
                chunk = resp.read(64)
                if not chunk:
                    break
                if first is None:
                    first = (time.perf_counter() - t0) * 1000
                buf += chunk
                while b"\n\n" in buf:
                    frame, buf = buf.split(b"\n\n", 1)
                    line = frame.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:])
                    except Exception:
                        continue
                    if ev.get("type") == "token":
                        answer += ev.get("text", "")
        return first or 0, (time.perf_counter() - t0) * 1000, answer

    firsts, totals = [], []
    sid = new_session()
    for _ in range(args.rounds):
        for q in Q:
            f, t, a = ask(sid, q)
            firsts.append(f)
            totals.append(t)

    inv_ms, inv_pass = [], 0
    for i in range(20):
        sid = new_session()
        q = f"这个发票能报吗，发票号 {88000000 + i}，金额 {100 + i * 37} 元，日期 {date.today().isoformat()}"
        t0 = time.perf_counter()
        ask(sid, q)
        inv_ms.append((time.perf_counter() - t0) * 1000)

    data = {
        "base": base,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "chat_n": len(totals),
        "first_token_ms": {"p50": round(pct(firsts, 50), 1), "p95": round(pct(firsts, 95), 1),
                           "min": round(min(firsts), 1), "max": round(max(firsts), 1),
                           "mean": round(statistics.mean(firsts), 1)},
        "chat_total_ms": {"p50": round(pct(totals, 50), 1), "p95": round(pct(totals, 95), 1),
                          "min": round(min(totals), 1), "max": round(max(totals), 1),
                          "mean": round(statistics.mean(totals), 1)},
        "invoice_ms": {"p50": round(pct(inv_ms, 50), 1), "p95": round(pct(inv_ms, 95), 1),
                       "min": round(min(inv_ms), 1), "max": round(max(inv_ms), 1)},
    }
    out = Path(__file__).resolve().parent.parent / "_runtime" / "latency.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
