"""SQLite 数据访问层：建表、初始化数据、轻量查询助手。

为什么用 SQLite：MVP 阶段零运维、单文件、可直接随仓库分发。
表结构刻意与 PostgreSQL 兼容（自增主键用 INTEGER/序列、JSON 用 TEXT 存），
后续迁移只需替换连接层与 DDL 方言。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .config import settings
from .security import hash_password

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 1. 用户：员工 / 管理员两种角色，职级决定差旅标准
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('employee', 'admin')),
    job_level     TEXT NOT NULL DEFAULT 'P5',      -- 职级：M3/M1/P5/P3...
    department    TEXT NOT NULL DEFAULT '研发部',
    employee_no   TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 2. 会话
CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT NOT NULL DEFAULT '新会话',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, updated_at DESC);

-- 3. 消息：每条 AI 回答都留引用来源与 agent 轨迹，支撑审计
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role          TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content       TEXT NOT NULL,
    citations     TEXT,          -- JSON: [{doc, heading, text, score}]
    agent_meta    TEXT,          -- JSON: {rounds, tools, model, latency_ms}
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

-- 4. 制度文档（含版本与生效期，支撑"当时按哪版判的"）
CREATE TABLE IF NOT EXISTS policy_docs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    filename       TEXT NOT NULL,
    title          TEXT NOT NULL,
    version        TEXT NOT NULL DEFAULT 'V1.0',
    effective_date TEXT NOT NULL DEFAULT (date('now')),
    content        TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    uploaded_by    TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 5. 制度切片：切片结果落库，重建索引可追溯
CREATE TABLE IF NOT EXISTS policy_chunks (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id   INTEGER NOT NULL REFERENCES policy_docs(id) ON DELETE CASCADE,
    ord      INTEGER NOT NULL,
    heading  TEXT NOT NULL,
    text     TEXT NOT NULL,
    n_chars  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON policy_chunks(doc_id, ord);

-- 6. 报销单草稿
CREATE TABLE IF NOT EXISTS drafts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id   INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    status       TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'submitted', 'rejected')),
    trip_city    TEXT,
    trip_start   TEXT,
    trip_end     TEXT,
    total_amount REAL NOT NULL DEFAULT 0,
    note         TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 7. 草稿明细
CREATE TABLE IF NOT EXISTS draft_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id     INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    category     TEXT NOT NULL,          -- 住宿费/交通费/伙食补助/其他
    amount       REAL NOT NULL,
    invoice_no   TEXT,
    invoice_date TEXT,
    merchant     TEXT,
    note         TEXT
);

-- 8. Agent 决策日志：每轮的工具名、参数、耗时、结果摘要（阶段 5 审计与分析用）
CREATE TABLE IF NOT EXISTS agent_logs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    message_id     INTEGER,
    round_no       INTEGER NOT NULL,
    tool_name      TEXT NOT NULL,
    args_json      TEXT,
    result_summary TEXT,
    duration_ms    INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_agent_logs_session ON agent_logs(session_id, id);

-- 9. 提示词模板：管理员可改，改完立即生效（不写死在代码里）
CREATE TABLE IF NOT EXISTS prompt_templates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    content    TEXT NOT NULL,
    version    INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 10. 对话审计：管理员标记疑似违规问答
CREATE TABLE IF NOT EXISTS audit_flags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    reason     TEXT NOT NULL,
    flagged_by TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


def get_conn(db_path: Path | str | None = None) -> sqlite3.Connection:
    """每次调用建一个新连接：SQLite 写锁短、连接便宜，避免跨线程共享连接。"""
    path = Path(db_path or settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def query_all(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, tuple(params)).fetchall()


def query_one(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return conn.execute(sql, tuple(params)).fetchone()


def execute(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur


# ---------------------------------------------------------------- 初始化

DEFAULT_PROMPTS = {
    "system_qa": (
        "你是公司报销制度助手，只根据提供的制度条文回答，不确定时明确说'制度中未找到'，\n"
        "回答末尾附引用来源，不得编造报销标准。\n"
        "回答要简洁、给数字、给依据条款；金额一律按制度原文，不要四舍五入或换算。"
    ),
    "system_agent": (
        "你是企业报销助手 Agent。你可以调用工具完成：查制度、算差旅标准、校验发票、生成报销单草稿。\n"
        "硬性规则：\n"
        "1) 任何金额/标准类结论必须先 search_policy 或 query_expense_standard 取到依据，不得凭记忆回答；\n"
        "2) 建单前必须先 validate_invoice 且通过；未通过时禁止调用 create_draft，改为告知问题清单；\n"
        "3) 缺少发票号/日期/金额时，先向员工索要，不要编造占位值；\n"
        "4) 不要跳过校验直接建单，即使员工要求'先建了再说'；\n"
        "5) 制度里没有的内容，明确说'制度中未找到'，不要推断。"
    ),
}


def _seed_users(conn: sqlite3.Connection) -> None:
    rows = [
        (settings.seed_employee_user, hash_password(settings.seed_employee_pwd), "张伟", "employee", "P5", "研发部", "E1001"),
        (settings.seed_admin_user, hash_password(settings.seed_admin_pwd), "李静（财务）", "admin", "M1", "财务部", "E0001"),
    ]
    for row in rows:
        conn.execute(
            "INSERT INTO users (username, password_hash, display_name, role, job_level, department, employee_no)"
            " VALUES (?,?,?,?,?,?,?) ON CONFLICT(username) DO NOTHING",
            row,
        )
    conn.commit()


def _seed_prompts(conn: sqlite3.Connection) -> None:
    for key, name in (("system_qa", "问答系统提示词"), ("system_agent", "Agent 系统提示词")):
        conn.execute(
            "INSERT INTO prompt_templates (key, name, content) VALUES (?,?,?)"
            " ON CONFLICT(key) DO NOTHING",
            (key, name, DEFAULT_PROMPTS[key]),
        )
    conn.commit()


def _seed_policies(conn: sqlite3.Connection) -> None:
    """首次启动把 data/policies/*.md 灌入知识库；已有数据则跳过，避免覆盖管理员改动。"""
    existing = conn.execute("SELECT COUNT(*) AS c FROM policy_docs").fetchone()["c"]
    if existing:
        return
    from .rag.index import chunk_document  # 延迟导入，避免循环依赖

    for path in sorted(settings.policies_dir.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        title = path.stem
        for line in content.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        cur = conn.execute(
            "INSERT INTO policy_docs (filename, title, version, effective_date, content, status, uploaded_by)"
            " VALUES (?,?,?,?,?, 'active', 'system')",
            (path.name, title, "V3.2", "2025-01-01", content),
        )
        doc_id = cur.lastrowid
        chunks = chunk_document(content, doc_title=title)
        conn.executemany(
            "INSERT INTO policy_chunks (doc_id, ord, heading, text, n_chars) VALUES (?,?,?,?,?)",
            [(doc_id, i, c["heading"], c["text"], len(c["text"])) for i, c in enumerate(chunks)],
        )
    conn.commit()


def rebuild_chunks(conn: sqlite3.Connection, doc_id: int) -> int:
    """文档更新/上传后重建切片，返回切片数。"""
    from .rag.index import chunk_document

    row = conn.execute("SELECT title, content FROM policy_docs WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        return 0
    conn.execute("DELETE FROM policy_chunks WHERE doc_id = ?", (doc_id,))
    chunks = chunk_document(row["content"], doc_title=row["title"])
    conn.executemany(
        "INSERT INTO policy_chunks (doc_id, ord, heading, text, n_chars) VALUES (?,?,?,?,?)",
        [(doc_id, i, c["heading"], c["text"], len(c["text"])) for i, c in enumerate(chunks)],
    )
    conn.commit()
    return len(chunks)


def init_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = get_conn(db_path)
    conn.executescript(SCHEMA)
    _seed_users(conn)
    _seed_prompts(conn)
    _seed_policies(conn)
    return conn


def get_prompt(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT content FROM prompt_templates WHERE key = ?", (key,)).fetchone()
    if row:
        return row["content"]
    return DEFAULT_PROMPTS.get(key, "")


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
