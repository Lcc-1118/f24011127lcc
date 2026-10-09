"""
制度知识库模块
负责：加载制度文档、解析制度元数据、按关键词检索有效制度
角色对应：乔姐（行政专员）维护的制度内容
"""
import os
import re
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PolicyDoc:
    """制度文档"""
    filename: str
    title: str = ""
    policy_id: str = ""
    version: str = ""
    status: str = "有效"  # 有效 / 已撤回
    publish_date: str = ""
    maintainer: str = ""
    content: str = ""
    sections: list = field(default_factory=list)  # 解析后的章节


class PolicyKnowledgeBase:
    """制度知识库"""

    def __init__(self, policy_dir: str):
        self.policy_dir = Path(policy_dir)
        self.policies: dict[str, PolicyDoc] = {}
        self._load_all()

    def _parse_frontmatter(self, text: str) -> dict:
        """解析制度文件头部的元数据（简单解析，不依赖yaml frontmatter格式）"""
        meta = {}
        # 提取 **字段**：值 格式的元数据
        patterns = {
            "title": r"#\s+(.+)",
            "policy_id": r"\*\*制度编号\*\*[：:]\s*(.+)",
            "version": r"\*\*版本\*\*[：:]\s*(.+)",
            "status": r"\*\*状态\*\*[：:]\s*(.+)",
            "publish_date": r"\*\*发布日期\*\*[：:]\s*(.+)",
            "maintainer": r"\*\*维护人\*\*[：:]\s*(.+)",
        }
        for key, pattern in patterns.items():
            m = re.search(pattern, text)
            if m:
                meta[key] = m.group(1).strip()
        return meta

    def _parse_sections(self, content: str) -> list:
        """将制度内容按章节拆分，便于检索定位"""
        sections = []
        # 按 ## 标题拆分
        parts = re.split(r"\n(##\s+.+)", content)
        current_title = "总则"
        current_body = ""
        for part in parts:
            if part.startswith("## "):
                if current_body.strip():
                    sections.append({"title": current_title, "content": current_body.strip()})
                current_title = part.replace("## ", "").strip()
                current_body = ""
            else:
                current_body += part
        if current_body.strip():
            sections.append({"title": current_title, "content": current_body.strip()})
        return sections

    def _load_all(self):
        """加载所有制度文件"""
        self.policies.clear()
        if not self.policy_dir.exists():
            return
        for f in self.policy_dir.glob("*.md"):
            text = f.read_text(encoding="utf-8")
            meta = self._parse_frontmatter(text)
            doc = PolicyDoc(
                filename=f.name,
                title=meta.get("title", f.stem),
                policy_id=meta.get("policy_id", ""),
                version=meta.get("version", ""),
                status=meta.get("status", "有效"),
                publish_date=meta.get("publish_date", ""),
                maintainer=meta.get("maintainer", ""),
                content=text,
                sections=self._parse_sections(text),
            )
            self.policies[f.name] = doc

    def reload(self):
        """重新加载（乔姐更新制度后调用）"""
        self._load_all()

    def list_effective_policies(self) -> list[PolicyDoc]:
        """列出所有有效制度"""
        return [p for p in self.policies.values() if p.status == "有效"]

    def list_all_policies(self) -> list[PolicyDoc]:
        """列出所有制度（含已撤回，供乔姐查看）"""
        return list(self.policies.values())

    def _extract_keywords(self, query: str) -> list[str]:
        """
        中文关键词提取：去除常见停用词后，用2~4字滑窗生成候选关键词
        """
        stopwords = {"什么", "怎么", "如何", "多少", "需要", "可以", "请问", "一下",
                     "的话", "我们", "你们", "这个", "那个", "哪些", "是不是", "能不能"}
        # 先按标点和空格粗分
        rough = [w for w in re.split(r"[\s,，。、？?！!；;：:]+", query) if len(w) >= 2]
        keywords = set()
        for seg in rough:
            # 去掉停用词
            for sw in stopwords:
                seg = seg.replace(sw, "")
            if len(seg) < 2:
                continue
            # 生成2字和3字滑窗
            for n in (2, 3, 4):
                for i in range(len(seg) - n + 1):
                    keywords.add(seg[i:i + n])
        return list(keywords)

    def search(self, query: str, only_effective: bool = True, top_k: int = 3) -> list[dict]:
        """
        关键词检索制度（简化版RAG：基于关键词匹配 + 章节定位）
        返回匹配的章节片段，附带制度元数据
        """
        results = []
        keywords = self._extract_keywords(query)

        candidates = self.list_effective_policies() if only_effective else list(self.policies.values())

        for doc in candidates:
            for section in doc.sections:
                score = 0
                for kw in keywords:
                    if kw in section["content"]:
                        score += 1
                    if kw in section["title"]:
                        score += 2
                # 制度标题匹配加权
                for kw in keywords:
                    if kw in doc.title:
                        score += 3
                if score > 0:
                    results.append({
                        "policy_title": doc.title,
                        "policy_id": doc.policy_id,
                        "version": doc.version,
                        "section": section["title"],
                        "content_snippet": section["content"][:300],
                        "score": score,
                    })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def get_policy_detail(self, policy_id_or_title: str) -> Optional[PolicyDoc]:
        """按编号或标题获取完整制度"""
        for doc in self.policies.values():
            if policy_id_or_title in doc.policy_id or policy_id_or_title in doc.title:
                return doc
        return None
