"""提示词组装：系统提示词 + 历史轮次 + 引用条文的拼装规则。

提示词模板存在数据库里（prompt_templates 表），管理员改完立即生效，
这样"调提示词"不需要改代码、不需要发版 —— 这正是阶段 5 的需求。
"""
from __future__ import annotations

import json
from typing import Any

from ..config import settings
from .tools import TOOLS

CITATION_HEADER = "\n\n---\n**引用来源：**"


def tool_guide() -> str:
    """把工具清单渲染进系统提示词，让模型清楚自己有哪些能力。"""
    lines = []
    for tool in TOOLS.values():
        effect = "（有副作用，需用户确认）" if tool.side_effect else ""
        lines.append(f"- {tool.name}{effect}：{tool.description}")
    return "\n".join(lines)


def build_agent_system_prompt(base: str, user: dict[str, Any]) -> str:
    """系统提示词 = 管理员配置的模板 + 运行期身份与硬约束。"""
    identity = (
        f"\n\n当前员工：{user.get('display_name', '')}（工号 {user.get('employee_no') or '-'}，"
        f"职级 {user.get('job_level', '')}，部门 {user.get('department', '')}）。"
        f"公司全称：{settings.company_name}；税号：{settings.company_tax_id}。"
    )
    return f"{base}{identity}\n\n可用工具：\n{tool_guide()}"


def build_llm_history(rows: list[Any]) -> list[dict[str, str]]:
    """把最近 N 轮消息压成 chat 格式。tool 消息不带入下一轮，避免上下文膨胀。"""
    messages: list[dict[str, str]] = []
    for row in rows:
        role = row["role"]
        if role not in ("user", "assistant"):
            continue
        messages.append({"role": role, "content": row["content"]})
    return messages


def format_tool_result(name: str, result: dict[str, Any]) -> str:
    """工具结果回填给模型时的文本形式：控制体积，保留判定所需字段。"""
    payload = json.dumps(result, ensure_ascii=False, default=str)
    if len(payload) > 1800:
        payload = payload[:1800] + "…(truncated)"
    return f"[tool:{name}] {payload}"


def rag_answer_messages(system: str, question: str, hits: list[dict[str, Any]]) -> list[dict[str, str]]:
    """纯问答（无工具）场景的提示词：严格约束"只依据片段回答"。"""
    blocks = []
    for i, hit in enumerate(hits, start=1):
        blocks.append(
            f"[片段{i}] 文档《{hit['doc_title']}》 {hit['heading']}\n{hit['text']}"
        )
    context = "\n\n".join(blocks) or "（未检索到任何制度条文）"
    user_content = (
        f"以下是从公司制度库检索到的条文：\n\n{context}\n\n"
        f"员工问题：{question}\n\n"
        "回答要求：\n"
        "1. 只使用上面条文中的信息，条文没有就回答「制度中未找到相关规定」；\n"
        "2. 涉及金额必须逐字引用原文数字；\n"
        "3. 末尾用「引用：片段N（标题）」标注依据。"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user_content}]


def render_citations(citations: list[dict[str, Any]], limit: int = 3) -> str:
    if not citations:
        return ""
    lines = [CITATION_HEADER]
    for i, cite in enumerate(citations[:limit], start=1):
        snippet = cite["text"].replace("\n", " ")[:120]
        lines.append(f"{i}. 《{cite.get('doc_title') or cite['doc']}》 {cite['heading']} —— {snippet}…")
    return "\n".join(lines)
