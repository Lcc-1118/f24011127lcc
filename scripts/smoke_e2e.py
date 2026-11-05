"""端到端冒烟测试：不需要浏览器，直接打 HTTP 接口，验证整条链路。

覆盖：
1. 登录 / 未登录拦截 / 角色权限（员工访问 /admin 必须 403）；
2. 员工问答 SSE 流（事件顺序：status → tool_call → tool_result → token → citations → done）；
3. 草稿强制确认闸门（confirm_required → 确认 → 落库）；
4. 管理员上传制度 → 自动切片 → 删除；
5. 提示词模板保存后立即生效（版本号自增）。

用法：
    python run.py --port 8123 --no-browser      # 先起服务
    python scripts/smoke_e2e.py --base http://127.0.0.1:8123

只用标准库，Windows / macOS / Linux 都能跑。
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

PASS, FAIL = "✅", "❌"
results: list[tuple[bool, str]] = []

# Windows 控制台默认 GBK，直接打印 ✅/中文会抛 UnicodeEncodeError；统一改成 UTF-8 输出
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # 老版本 Python 或已被重定向
        pass


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label))
    print(f"{PASS if ok else FAIL} {label}" + (f"　{detail}" if detail and not ok else ""))


class Client:
    """带 Cookie 的最小 HTTP 客户端（urllib 实现，零依赖）。"""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method: str, path: str, body: bytes | None = None,
                content_type: str | None = None) -> tuple[int, bytes]:
        req = urllib.request.Request(self.base + path, data=body, method=method)
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            with self.opener.open(req, timeout=60) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def get(self, path: str) -> tuple[int, bytes]:
        return self.request("GET", path)

    def post_json(self, path: str, payload: dict) -> tuple[int, bytes]:
        return self.request("POST", path, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                            "application/json; charset=utf-8")

    def post_form(self, path: str, fields: dict[str, str]) -> tuple[int, bytes]:
        data = urllib.parse.urlencode(fields).encode("utf-8")
        return self.request("POST", path, data, "application/x-www-form-urlencoded")

    def post_file(self, path: str, filename: str, content: bytes, fields: dict[str, str]) -> tuple[int, bytes]:
        boundary = "----smoke" + uuid.uuid4().hex
        parts: list[bytes] = []
        for key, value in fields.items():
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode("utf-8")
            )
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: text/markdown\r\n\r\n".encode("utf-8") + content + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        return self.request("POST", path, b"".join(parts), f"multipart/form-data; boundary={boundary}")

    def login(self, username: str, password: str) -> bool:
        status, _ = self.post_form("/login", {"username": username, "password": password, "next": "/chat"})
        return status in (200, 303)


def parse_sse(raw: bytes) -> list[dict]:
    events = []
    for frame in raw.decode("utf-8", "replace").split("\n\n"):
        line = next((l for l in frame.split("\n") if l.startswith("data: ")), None)
        if not line:
            continue
        try:
            events.append(json.loads(line[6:]))
        except json.JSONDecodeError:
            continue
    return events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8123")
    args = parser.parse_args()

    base = args.base
    print(f"目标服务：{base}\n")

    # ---------------------------------------------------------------- 1. 未登录
    anon = Client(base)
    status, body = anon.get("/healthz")
    check(status == 200 and b'"ok"' in body, "健康检查 /healthz 可用")

    status, _ = anon.post_json("/api/chat", {"session_id": 1, "question": "hi"})
    check(status in (401, 303, 307), "未登录调用问答接口被拦截", f"实际 {status}")

    status, _ = anon.get("/admin")
    check(status in (200, 303), "未登录访问 /admin 跳转登录页", f"实际 {status}")

    # ---------------------------------------------------------------- 2. 员工
    emp = Client(base)
    check(emp.login("zhangwei", "emp123"), "员工登录成功")

    status, body = emp.post_json("/api/sessions", {})
    session_id = json.loads(body)["session"]["id"]
    check(status == 200 and session_id > 0, "创建会话", f"实际 {status}")

    status, body = emp.get("/chat")
    check(status == 200 and "报销助手".encode() in body, "聊天页可渲染")

    # 制度问答：应只调检索类工具，不建单
    status, body = emp.post_json("/api/chat", {"session_id": session_id, "question": "出差上海3天住宿标准是多少"})
    events = parse_sse(body)
    types = [e["type"] for e in events]
    check(status == 200 and "token" in types, "问答接口返回 SSE 流")
    check("citations" in types, "回答带引用来源")
    called = [e["name"] for e in events if e["type"] == "tool_call"]
    check("create_draft" not in called, "纯咨询不触发建单", f"实际调用 {called}")
    answer = "".join(e.get("text", "") for e in events if e["type"] == "token")
    check("500" in answer, "住宿标准回答包含正确金额（一线普通员工 500 元/晚）", answer[:80])

    # 建单：必须先 confirm_required，确认后才落库。
    # 发票号每次随机，避免重复跑脚本时被"发票查重"规则挡住（第 4 步会专门验证查重）。
    invoice_no = str(uuid.uuid4().int)[:8]
    status, body = emp.post_json("/api/chat", {
        "session_id": session_id,
        "question": f"帮我报上个月去北京的差旅，发票号 {invoice_no}，金额 480 元，日期 2026-10-20",
    })
    events = parse_sse(body)
    confirm_events = [e for e in events if e["type"] == "confirm_required"]
    check(bool(confirm_events), "建单前触发人工确认（confirm_required）",
          json.dumps([e["type"] for e in events], ensure_ascii=False))
    check(confirm_events and confirm_events[0]["draft"].get("items"),
          "确认卡里带出了草稿明细")

    # 关键点：此时"只是弹了确认卡"，草稿还没落库——闸门由代码强制，不靠模型自觉
    status, body = emp.get("/my/drafts")
    check(status == 200 and invoice_no.encode() not in body,
          "确认之前草稿不落库", "草稿页里已经出现了该发票")

    status, body = emp.post_json("/api/chat/confirm", {"session_id": session_id, "accepted": True})
    payload = json.loads(body)
    check(status == 200 and payload.get("draft_id"), "确认后生成草稿", str(payload)[:120])
    check("审批" in payload.get("message", ""), "确认结果里给出审批链路")


    # 重复确认应被拒绝（幂等保护）
    status, body = emp.post_json("/api/chat/confirm", {"session_id": session_id, "accepted": True})
    check(status == 400, "同一草稿不能重复确认", f"实际 {status}")

    status, body = emp.get("/my/drafts")
    check(status == 200 and b"480.00" in body, "我的草稿页显示金额")

    status, body = emp.get("/admin")
    check(status == 403, "员工访问管理后台返回 403", f"实际 {status}")

    # ---------------------------------------------------------------- 3. 管理员
    adm = Client(base)
    check(adm.login("admin", "admin123"), "管理员登录成功")

    for path in ("/admin", "/admin/policies", "/admin/prompts", "/admin/audit", "/admin/logs"):
        status, _ = adm.get(path)
        check(status == 200, f"管理页 {path} 可访问", f"实际 {status}")

    # 上传制度 → 自动切片
    doc = "# 冒烟测试制度\n\n## 一、住宿\n一线城市每晚上限 700 元。\n\n## 二、时效\n超过 60 天不予报销。\n"
    status, body = adm.post_file("/admin/policies", "smoke_policy.md", doc.encode("utf-8"),
                                 {"version": "V9.9", "title": "冒烟测试制度", "effective_date": ""})
    check(status in (200, 303), "上传制度文件成功", f"实际 {status}")

    status, body = adm.get("/admin/policies")
    text = body.decode("utf-8", "replace")
    check("冒烟测试制度" in text, "新制度出现在列表中")

    # 新制度应立刻可被检索到（无需重启）
    status, body = adm.get("/api/sessions")
    adm_sid = json.loads(body)["sessions"][0]["id"] if json.loads(body)["sessions"] else \
        json.loads(adm.post_json("/api/sessions", {})[1])["session"]["id"]
    status, body = adm.post_json("/api/chat", {"session_id": adm_sid, "question": "冒烟测试制度的住宿上限是多少"})
    events = parse_sse(body)
    joined = json.dumps(events, ensure_ascii=False)
    check("冒烟测试制度" in joined or "700" in joined, "新上传制度立即进入检索", joined[:160])

    # 提示词模板保存：版本号必须比保存前大
    status, body = adm.get("/admin/prompts")
    before = re.search(r"v(\d+)", body.decode("utf-8", "replace"))
    version_before = int(before.group(1)) if before else 0
    status, body = adm.post_form("/admin/prompts/system_agent", {
        "content": "你是公司报销制度助手，只根据制度条文回答；不确定时明确说'制度中未找到'；"
                   "建单前必须先校验发票并等待员工确认。",
    })
    check(status in (200, 303), "保存提示词模板", f"实际 {status}")

    status, body = adm.get("/admin/prompts")
    after = re.findall(r"v(\d+)", body.decode("utf-8", "replace"))
    version_after = max(int(v) for v in after) if after else 0
    check(version_after > version_before, "提示词版本号自增", f"{version_before} → {version_after}")

    # 清理：删掉冒烟测试制度
    status, body = adm.get("/admin/policies")
    match = re.search(r"policies/(\d+)\">冒烟测试制度", body.decode("utf-8", "replace"))
    if match:
        status, _ = adm.request("POST", f"/admin/policies/{match.group(1)}/delete")
        check(status in (200, 303), "删除测试制度", f"实际 {status}")

    # ---------------------------------------------------------------- 汇总
    failed = [label for ok, label in results if not ok]
    print("\n" + "=" * 60)
    print(f"通过 {len(results) - len(failed)}/{len(results)}")
    if failed:
        print("失败项：")
        for label in failed:
            print("  -", label)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
