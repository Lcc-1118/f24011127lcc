"""
单据数据库模块
负责：报销单据的存储、状态管理
角色对应：周敏（财务专员）核对业务事实后更新单据状态
"""
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional


class DocumentDB:
    """报销单据数据库（SQLite）"""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """建表"""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                employee_name TEXT NOT NULL,
                employee_id TEXT NOT NULL,
                expense_type TEXT NOT NULL,
                amount REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                submit_date TEXT,
                review_note TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                dept TEXT DEFAULT ''
            )
        """)
        conn.commit()
        conn.close()

    # ---- 用户 ----
    def add_user(self, user_id: str, name: str, role: str, dept: str = ""):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO users (user_id, name, role, dept) VALUES (?, ?, ?, ?)",
            (user_id, name, role, dept),
        )
        conn.commit()
        conn.close()

    def get_user(self, user_id: str) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    # ---- 单据 ----
    def add_document(self, doc_id: str, employee_name: str, employee_id: str,
                     expense_type: str, amount: float, status: str = "draft"):
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO documents
               (doc_id, employee_name, employee_id, expense_type, amount, status, submit_date)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, employee_name, employee_id, expense_type, amount, status,
             datetime.now().strftime("%Y-%m-%d %H:%M")),
        )
        conn.commit()
        conn.close()

    def get_document(self, doc_id: str) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_documents_by_employee(self, employee_id: str) -> list[dict]:
        """查询某员工的所有单据（用于"本人单据状态查询"）"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM documents WHERE employee_id = ? ORDER BY created_at DESC",
            (employee_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_all_documents(self) -> list[dict]:
        """查看所有单据（周敏/财务使用）"""
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_document_status(self, doc_id: str, new_status: str, note: str = ""):
        """更新单据状态（周敏核对后操作）"""
        conn = self._get_conn()
        conn.execute(
            """UPDATE documents
               SET status = ?, review_note = ?, updated_at = datetime('now', 'localtime')
               WHERE doc_id = ?""",
            (new_status, note, doc_id),
        )
        conn.commit()
        conn.close()

    def count_documents(self) -> int:
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) as cnt FROM documents").fetchone()
        conn.close()
        return row["cnt"]
