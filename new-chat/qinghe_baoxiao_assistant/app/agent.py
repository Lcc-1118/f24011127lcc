"""
青禾报销助手 - 智能体核心模块
负责：意图识别、路由分发、工具调用、回复生成
"""
import re
from dataclasses import dataclass
from typing import Optional

from .policy_kb import PolicyKnowledgeBase
from .document_db import DocumentDB


@dataclass
class Session:
    """用户会话"""
    user_id: str = ""
    user_name: str = ""
    role: str = "employee"  # employee / admin / finance
    authorized: bool = False  # 是否已授权查询本人单据


class ReimbursementAgent:
    """青禾报销助手智能体"""

    def __init__(self, policy_kb: PolicyKnowledgeBase, doc_db: DocumentDB, config: dict):
        self.kb = policy_kb
        self.db = doc_db
        self.config = config
        self.session = Session()

    # ---- 身份与授权 ----
    def login(self, user_id: str) -> str:
        """模拟登录"""
        user = self.db.get_user(user_id)
        if not user:
            return f"未找到用户 {user_id}，请联系管理员初始化账号。"
        self.session.user_id = user["user_id"]
        self.session.user_name = user["name"]
        self.session.role = user["role"]
        self.session.authorized = False
        role_label = {"employee": "员工", "admin": "行政", "finance": "财务"}.get(user["role"], user["role"])
        return f"登录成功：{user['name']}（{role_label}）。输入你想了解的报销问题即可。"

    def authorize_self_query(self) -> str:
        """授权查询本人单据"""
        if self.session.role != "employee":
            return "当前角色无需授权，可直接查看。"
        self.session.authorized = True
        return "已授权：你可以查询本人的报销单据状态了。试试问「我的报销到哪一步了？」"

    # ---- 意图识别 ----
    def _detect_intent(self, query: str) -> str:
        """
        识别用户意图：
        - policy_query: 制度咨询（查报销需要什么材料、住宿标准等）
        - document_query: 单据状态查询（我的报销到哪了、单据R-0001状态）
        - admin_action: 管理操作（乔姐发布/撤回制度）
        - finance_action: 财务操作（周敏更新单据状态）
        - greeting: 打招呼
        """
        q = query.strip()

        if re.search(r"你好|hi|hello|在吗|您好", q, re.I):
            return "greeting"

        # 单据状态查询意图（含单据号 R-xxxx）
        if re.search(r"报销.*(到哪|进度|状态|审核|批了|打款)|我的.*单据|单据.*状态|R-\d|查.*报销", q):
            return "document_query"

        # 财务/管理员查看单据列表
        if self.session.role in ("finance", "admin") and re.search(r"查看.*单据|所有单据|单据.*列表", q):
            return "document_query"

        # 财务操作意图
        if self.session.role == "finance" and re.search(r"更新|审核通过|退回|打款|状态改为", q):
            return "finance_action"

        # 管理操作意图
        if self.session.role == "admin" and re.search(r"发布|撤回|失效|上线制度", q):
            return "admin_action"

        # 默认：制度咨询
        return "policy_query"

    # ---- 回复生成 ----
    def chat(self, query: str) -> str:
        """主对话入口"""
        if not self.session.user_id:
            return "请先输入你的员工编号登录（例如：emp_001 / admin_qiaojie / finance_zhoumin）。"

        intent = self._detect_intent(query)

        if intent == "greeting":
            return f"你好{self.session.user_name}！我是青禾报销助手。可以问我：\n1. 报销需要什么材料？\n2. 出差住宿标准是多少？\n3. 我的报销到哪一步了？（需先授权）"

        if intent == "policy_query":
            return self._handle_policy_query(query)

        if intent == "document_query":
            return self._handle_document_query(query)

        if intent == "admin_action":
            return self._handle_admin_action(query)

        if intent == "finance_action":
            return self._handle_finance_action(query)

        return "抱歉，我暂时理解不了这个问题。可以试试问报销制度或单据进度。"

    # ---- 制度查询（小林自助查有效制度）----
    def _handle_policy_query(self, query: str) -> str:
        results = self.kb.search(query, only_effective=True, top_k=3)
        if not results:
            return ("没有找到相关的有效制度条款。\n"
                    "建议：1) 换个关键词试试（如「住宿」「发票」「出差」）；"
                    "2) 如制度未覆盖，可联系乔姐（行政专员）确认是否有最新规定。")

        lines = ["根据现行有效制度，相关规定如下：\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"【{i}】{r['policy_title']}（{r['policy_id']} {r['version']}）")
            lines.append(f"    章节：{r['section']}")
            lines.append(f"    {r['content_snippet']}")
            lines.append("")
        lines.append("以上内容来自乔姐维护的现行有效制度。如需完整制度原文，可让乔姐提供。")
        return "\n".join(lines)

    # ---- 单据状态查询（经授权查询本人单据）----
    def _handle_document_query(self, query: str) -> str:
        # 员工查自己的单据
        if self.session.role == "employee":
            if not self.session.authorized:
                return ("查询本人单据需要先授权。请输入「授权查询我的单据」，"
                        "我会确认你只能查看本人记录。")
            docs = self.db.get_documents_by_employee(self.session.user_id)
            if not docs:
                return "你目前没有提交过报销单据。如有疑问可联系周敏（财务专员）。"
            lines = [f"你好{self.session.user_name}，以下是你的报销单据：\n"]
            for d in docs:
                status_label = self.config["document_status"].get(d["status"], d["status"])
                lines.append(f"  单据号：{d['doc_id']}")
                lines.append(f"  类型：{d['expense_type']} | 金额：¥{d['amount']:.2f}")
                lines.append(f"  当前状态：【{status_label}】")
                if d.get("review_note"):
                    lines.append(f"  备注：{d['review_note']}")
                lines.append("")
            lines.append("注：以上业务事实由周敏核对确认。如需进一步说明，可联系周敏。")
            return "\n".join(lines)

        # 财务/管理员查指定单据
        doc_id_match = re.search(r"R-\d+", query)
        if doc_id_match:
            doc_id = doc_id_match.group()
            doc = self.db.get_document(doc_id)
            if not doc:
                return f"未找到单据 {doc_id}。"
            status_label = self.config["document_status"].get(doc["status"], doc["status"])
            return (f"单据 {doc['doc_id']}：\n"
                    f"  提交人：{doc['employee_name']}（{doc['employee_id']}）\n"
                    f"  类型：{doc['expense_type']} | 金额：¥{doc['amount']:.2f}\n"
                    f"  状态：【{status_label}】\n"
                    f"  提交时间：{doc['submit_date']}\n"
                    f"  备注：{doc.get('review_note', '无')}")
        elif re.search(r"查看.*单据|所有单据|单据.*列表", query):
            # 财务/管理员查看全部单据列表
            docs = self.db.get_all_documents()
            if not docs:
                return "当前没有任何报销单据。"
            lines = ["当前所有报销单据：\n"]
            lines.append(f"  {'单据号':<10} {'提交人':<6} {'类型':<10} {'金额':>10}  状态")
            lines.append(f"  {'-'*10} {'-'*6} {'-'*10} {'-'*10}  {'-'*8}")
            for d in docs:
                status_label = self.config["document_status"].get(d["status"], d["status"])
                lines.append(f"  {d['doc_id']:<10} {d['employee_name']:<6} {d['expense_type']:<10} ¥{d['amount']:>9.2f}  {status_label}")
            lines.append("\n输入「R-xxxx」可查看指定单据详情。")
            return "\n".join(lines)
        else:
            return "请提供单据号（格式如 R-0001），或输入「查看所有单据」。"

    # ---- 乔姐管理操作 ----
    def _handle_admin_action(self, query: str) -> str:
        if self.session.role != "admin":
            return "仅制度维护人（乔姐）可执行此操作。"
        if "撤回" in query or "失效" in query:
            return "撤回制度功能：请在 data/policies/ 目录下将对应 .md 文件中的「状态」改为「已撤回」，然后对我说「刷新制度」。"
        if "发布" in query:
            return "发布新制度：请将制度 .md 文件放入 data/policies/ 目录（头部按现有格式写好元数据），然后对我说「刷新制度」。"
        if "刷新" in query:
            self.kb.reload()
            return f"制度库已刷新，当前共加载 {len(self.kb.policies)} 份制度文档。"
        return "乔姐你好。可用操作：发布制度、撤回制度、刷新制度库。"

    # ---- 周敏财务操作 ----
    def _handle_finance_action(self, query: str) -> str:
        if self.session.role != "finance":
            return "仅财务专员（周敏）可执行此操作。"
        doc_id_match = re.search(r"R-\d+", query)
        if not doc_id_match:
            docs = self.db.get_all_documents()
            lines = ["当前所有报销单据：\n"]
            for d in docs:
                status_label = self.config["document_status"].get(d["status"], d["status"])
                lines.append(f"  {d['doc_id']} | {d['employee_name']} | {d['expense_type']} | ¥{d['amount']:.2f} | {status_label}")
            return "\n".join(lines)
        return (f"单据 {doc_id_match.group()} 已定位。请告诉我新状态，例如：\n"
                f"「将 R-0001 状态改为 材料审核中」或「将 R-0001 退回，备注：发票不合规」。")
