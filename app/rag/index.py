"""混合检索核心：标题感知切片 + BM25 + 向量余弦 + RRF 融合。

刻意做成"零第三方依赖"：只用标准库 + 可选 numpy。
设计取舍（与 docs/04-阶段1-检索报告.md 一致）：
- 中文没有空格，用 **bigram** 分词，避免为一个分词器引入 jieba；
- 没有 embedding API 时，用 **特征哈希** 造稠密向量（hashing trick），
  它是真实语义向量的"下限替身"，接上真 embedding 后接口不变；
- 融合用 **RRF**（Reciprocal Rank Fusion），因为它不需要把 BM25 分数和
  余弦分数归一化到同一量纲，调参成本最低。
"""
from __future__ import annotations

import heapq
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

# ---------------------------------------------------------------- 分词

_CJK = r"\u4e00-\u9fff"
_ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_+\-\.]{0,}")
_CJK_RUN = re.compile(f"[{_CJK}]+")
_NUM = re.compile(r"\d+(?:\.\d+)?")

# 中文停用词：只保留极少数高频虚词，避免过度剪枝把"不得/不予"这类关键否定词删掉
_STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "及", "或", "有", "为", "对", "从", "到",
    "这", "那", "其", "之", "并", "等", "被", "把", "向", "按", "以", "于", "中",
    "我", "你", "他", "们", "个", "上", "下", "里", "就", "都", "也", "还", "要", "会",
}


def tokenize(text: str) -> list[str]:
    """把中英混排文本切成检索词：英文按词、数字整体、中文按 bigram + 单字兜底。"""
    text = text.lower()
    tokens: list[str] = []

    for word in _ASCII_WORD.findall(text):
        if len(word) >= 2:
            tokens.append(word)
    tokens.extend(_NUM.findall(text))

    for run in _CJK_RUN.findall(text):
        chars = [c for c in run if c not in _STOPWORDS]
        if len(chars) == 1:
            tokens.append(chars[0])
            continue
        # bigram 是中文检索性价比最高的切分：既保留词边界信息，又不需要词典
        for i in range(len(chars) - 1):
            tokens.append(chars[i] + chars[i + 1])
    return tokens


# ---------------------------------------------------------------- 切片

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def chunk_document(
    content: str,
    doc_title: str = "",
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[dict[str, str]]:
    """标题感知切片。

    1. 先按 Markdown 标题把文档切成"节"，记录标题路径（如 `二、差旅费用标准 > 2.2 住宿标准`）；
    2. 每节超过 chunk_size 时按滑动窗口再切，窗口之间保留 overlap 字符重叠，
       避免一句话被切断后 BM25 两边都匹配不上；
    3. 每个切片都带上标题路径，检索命中后引用能给到条级定位。
    """
    from ..config import settings

    chunk_size = chunk_size or settings.chunk_size
    overlap = overlap if overlap is not None else settings.chunk_overlap

    sections: list[tuple[str, list[str]]] = []
    heading_stack: list[tuple[int, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer and any(line.strip() for line in buffer):
            path = " > ".join(h for _, h in heading_stack) or doc_title
            sections.append((path, buffer.copy()))
        buffer.clear()

    for line in content.splitlines():
        match = _HEADING.match(line)
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            continue
        buffer.append(line)
    flush()

    chunks: list[dict[str, str]] = []
    for heading_path, lines in sections:
        text = "\n".join(lines).strip()
        if not text:
            continue
        if len(text) <= chunk_size:
            chunks.append({"heading": heading_path, "text": text})
            continue
        step = max(1, chunk_size - overlap)
        for start in range(0, len(text), step):
            piece = text[start : start + chunk_size].strip()
            if piece:
                chunks.append({"heading": heading_path, "text": piece})
            if start + chunk_size >= len(text):
                break
    return chunks


# ---------------------------------------------------------------- BM25

class BM25:
    """标准 Okapi BM25。中文长文档场景 k1=1.5 / b=0.75 是稳健默认值。"""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.docs = docs
        self.k1 = k1
        self.b = b
        self.n = len(docs)
        self.doc_len = [len(d) for d in docs]
        self.avg_len = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.tf: list[Counter[str]] = [Counter(d) for d in docs]
        df: Counter[str] = Counter()
        for tf in self.tf:
            df.update(tf.keys())
        # 带 +0.5 平滑的 IDF，避免语料极小时出现负分
        self.idf = {
            term: math.log(1 + (self.n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def scores(self, query_tokens: Iterable[str]) -> list[float]:
        q = list(query_tokens)
        result = [0.0] * self.n
        for i in range(self.n):
            tf = self.tf[i]
            dl = self.doc_len[i] or 1
            score = 0.0
            for term in q:
                freq = tf.get(term)
                if not freq:
                    continue
                idf = self.idf.get(term, 0.0)
                denom = freq + self.k1 * (1 - self.b + self.b * dl / (self.avg_len or 1))
                score += idf * freq * (self.k1 + 1) / denom
            result[i] = score
        return result


# ---------------------------------------------------------------- 向量（特征哈希）

class HashEmbedder:
    """特征哈希向量：把 token 哈希到固定维度并累加 tf 权重，再做 L2 归一化。

    它没有真实语义能力（同义词不近邻），但能提供"字面相似"以外的词频加权匹配，
    与 BM25 融合后对中文制度问答的召回提升明显；接真 embedding 时替换 embed() 即可。
    """

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def embed(self, tokens: list[str]) -> list[float]:
        vec = [0.0] * self.dim
        for token, count in Counter(tokens).items():
            # 稳定哈希：不使用 Python 内置 hash（受 PYTHONHASHSEED 影响，跨进程不一致）
            h = 2166136261
            for ch in token:
                h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
            idx = h % self.dim
            vec[idx] += 1.0 + math.log(count)
        norm = math.sqrt(sum(v * v for v in vec))
        if norm:
            vec = [v / norm for v in vec]
        return vec


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# ---------------------------------------------------------------- 索引与检索

@dataclass
class Chunk:
    doc_id: int
    doc_title: str
    filename: str
    version: str
    heading: str
    text: str
    tokens: list[str] = field(default_factory=list, repr=False)
    vector: list[float] = field(default_factory=list, repr=False)

    def citation(self, score: float) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "doc": self.filename,
            "doc_title": self.doc_title,
            "version": self.version,
            "heading": self.heading,
            "text": self.text,
            "score": round(score, 6),
        }


# 疑问/语气类字符：判断"这段条文能不能回答这个问题"时要把它们构成的 bigram 剔除，
# 否则"能报销吗"这种问句本身就会命中条文里的"能报销"，把不相关的段落判成相关。
_QUERY_NOISE_CHARS = set("吗呢吧啊什怎多如何能否可以须要会能该把被很太都也还就请问一下")

# 段落级通用词：到处都出现，命中它们不能说明"这段条文相关"
_GENERIC_TOKENS = {
    "报销", "费用", "公司", "员工", "制度", "发票", "标准", "规定", "流程", "情况",
    "需要", "提供", "申请", "进行", "以及", "其他", "相关", "金额",
}


# 制度核心术语：只要查询里出现、且条文里也出现，就足以认定"这段条文在讲这件事"。
# 作用：解决"报销时效是多久"这类提问与条文用词不一致（条文写"90 个自然日内"）的漏召回。
_CORE_TERMS = {
    "时效", "抬头", "税号", "专票", "普票", "汇率", "境外", "丢失", "遗失", "查重",
    "连号", "明细", "加急", "超标", "上浮", "住宿", "伙食", "补助", "交通", "自驾",
    "里程", "餐饮", "预审", "驳回", "实习生", "生病", "医疗", "科目", "审批", "差旅",
}

# 查询改写（同义词扩展）：用户的口语说法 → 制度里的书面说法。
# 只在命中关键词时追加，不改原句，因此不会把"制度之外的问题"改造成"制度内的问题"。
_QUERY_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("时效", "多久", "多长时间", "期限", "过期"), "费用发生日 90 个自然日内提交 报销时效"),
    (("抬头", "税号"), "发票抬头 公司全称 纳税人识别号"),
    (("交通", "打车", "地铁", "出租", "公交"), "交通费 市内交通 自驾 里程"),
    (("境外", "海外", "国外"), "境外票据 翻译件 汇率"),
    (("专票", "专用发票"), "增值税专用发票"),
    (("普票", "普通发票", "电子发票"), "增值税普通发票"),
    (("丢失", "丢了", "遗失"), "发票丢失 证明单 复印件"),
    (("实习生", "实习"), "实习生 80%"),
    (("超标", "超出", "超额"), "超标 上浮 50%"),
    (("查重", "重复", "已经报过"), "重复报销 查重 发票号"),
    (("上个月", "上月", "过期", "超期", "逾期", "还能报"), "费用发生日 90 个自然日内提交 报销时效 逾期不予受理"),
    (("不能报销", "不予报销", "不得报销", "能报哪些"), "不得报销的票据 不予报销 报销原则"),
]

# 制度明确没有覆盖的业务领域词。命中即直接弃答，不进入检索。
# 理由：这类词在制度里一次都没出现，任何"最相似段落"都是硬凑；
# 与其让模型拿着 6.3（生病医疗）去回答"出差能不能顺便休假"，不如直接说"未找到"。
# 维护成本很低（只在新增业务线时补词），比调分数阈值可靠得多。
_OOS_TERMS = {
    "健身", "健身房", "礼金", "生日礼金", "宠物", "托运", "年会", "抽奖", "奖品",
    "休假", "年假", "旅游", "景点", "体检", "团建", "按摩", "娱乐活动", "停车费",
}


def expand_query(query: str) -> str:
    """同义词扩展：把口语提问补上制度里的书面用词，提升召回（不影响域外问题的判定）。"""
    extra: list[str] = []
    for keys, hint in _QUERY_HINTS:
        if any(k in query for k in keys):
            extra.append(hint)
    if not extra:
        return query
    return query + " " + " ".join(extra)


def is_out_of_scope(query: str) -> bool:
    """问题是否落在制度完全没覆盖的业务领域（健身房、礼金、宠物托运……）。"""
    return any(term in query for term in _OOS_TERMS)


def gate_tokens(query: str) -> list[str]:
    """用于"相关性门禁"的查询词：去掉疑问词 bigram 与通用词，只留业务实体词。"""
    out: list[str] = []
    for tok in tokenize(query):
        if tok in _GENERIC_TOKENS:
            continue
        if any(ch in _QUERY_NOISE_CHARS for ch in tok):
            continue
        out.append(tok)
    return out


class PolicyIndex:
    """把切片集合建成可检索索引，一次构建、多次查询。"""

    def __init__(self, chunks: list[Chunk], top_k: int = 3, rrf_k: int = 60) -> None:
        self.chunks = chunks
        self.top_k = top_k
        self.rrf_k = rrf_k
        self.embedder = HashEmbedder()
        for chunk in self.chunks:
            chunk.tokens = tokenize(chunk.heading + "\n" + chunk.text)
            chunk.vector = self.embedder.embed(chunk.tokens)
        self.bm25 = BM25([c.tokens for c in self.chunks]) if self.chunks else None

    # -- 单路召回
    def bm25_ranking(self, query: str) -> list[tuple[int, float]]:
        if not self.bm25:
            return []
        scores = self.bm25.scores(tokenize(query))
        ranked = sorted(enumerate(scores), key=lambda kv: kv[1], reverse=True)
        return [(i, s) for i, s in ranked if s > 0]

    def vector_ranking(self, query: str) -> list[tuple[int, float]]:
        if not self.chunks:
            return []
        qv = self.embedder.embed(tokenize(query))
        scores = [(i, cosine(qv, c.vector)) for i, c in enumerate(self.chunks)]
        ranked = sorted(scores, key=lambda kv: kv[1], reverse=True)
        return [(i, s) for i, s in ranked if s > 0.01]

    # -- RRF 融合
    def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        k = top_k or self.top_k
        # 域外问题直接弃答：制度里根本没这个词，再相似也是硬凑。
        if is_out_of_scope(query):
            return []
        expanded = expand_query(query)
        bm25_rank = self.bm25_ranking(expanded)
        vec_rank = self.vector_ranking(expanded)

        fused: dict[int, float] = {}
        for ranking in (bm25_rank, vec_rank):
            for rank, (idx, _score) in enumerate(ranking, start=1):
                fused[idx] = fused.get(idx, 0.0) + 1.0 / (self.rrf_k + rank)

        best = heapq.nlargest(k, fused.items(), key=lambda kv: kv[1]) if fused else []

        # ---- 相关性门禁（对应制度里"不确定就说制度中未找到"的要求）----
        # 只靠分数阈值不可靠：BM25 分数的量纲随查询长度浮动，
        # 所以再看"查询里的业务实体词有多少真的出现在这段条文里"（词覆盖率）。
        # 覆盖率过低 => 这段条文只是碰巧含了几个通用词，不能当依据。
        gate = gate_tokens(expanded)
        if not gate:
            gate = tokenize(expanded)
        gate_set = set(gate)
        hits: list[dict[str, Any]] = []
        for idx, score in best:
            chunk = self.chunks[idx]
            if not self._relevant(gate_set, set(chunk.tokens)):
                continue
            hits.append(chunk.citation(score))
        return hits

    @staticmethod
    def _relevant(query_tokens: set[str], chunk_tokens: set[str], min_ratio: float = 0.34) -> bool:
        """判断这段条文是否真的能回答这个问题。

        三条通道（任一成立即相关）：
        1. 命中 >= 2 个业务词，且覆盖率 >= 1/3；
        2. 命中的词里有"制度核心术语"（时效/抬头/住宿/伙食……）；
        3. 单个业务词命中且覆盖率 >= 1/2。
        都不成立 => 该段落与被问到的事情无关，宁可答"制度中未找到"。
        """
        if not query_tokens or not chunk_tokens:
            return False
        matched = query_tokens & chunk_tokens
        if not matched:
            return False
        ratio = len(matched) / len(query_tokens)
        if len(matched) >= 2 and ratio >= min_ratio:
            return True
        if matched & _CORE_TERMS:
            return True
        return len(matched) >= 1 and ratio >= 0.5


# ---------------------------------------------------------------- 索引缓存

_CACHE: dict[tuple, PolicyIndex] = {}


def get_index(conn: Any, top_k: int | None = None, rrf_k: int | None = None) -> PolicyIndex:
    """按 DB 内容签名缓存索引；文档增删改后签名变化，自动重建。

    签名用 (doc_id, updated_at) 列表，避免每次请求都重算 BM25 与向量。
    """
    from ..config import settings

    docs = conn.execute(
        "SELECT id, filename, title, version, updated_at FROM policy_docs WHERE status = 'active' ORDER BY id"
    ).fetchall()
    signature = tuple((d["id"], d["updated_at"], d["version"]) for d in docs)
    cache_key = (signature, top_k, rrf_k)

    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    chunks: list[Chunk] = []
    for doc in docs:
        rows = conn.execute(
            "SELECT heading, text FROM policy_chunks WHERE doc_id = ? ORDER BY ord", (doc["id"],)
        ).fetchall()
        for row in rows:
            chunks.append(
                Chunk(
                    doc_id=doc["id"],
                    doc_title=doc["title"],
                    filename=doc["filename"],
                    version=doc["version"],
                    heading=row["heading"],
                    text=row["text"],
                )
            )

    index = PolicyIndex(
        chunks,
        top_k=top_k or settings.top_k,
        rrf_k=rrf_k or settings.rrf_k,
    )
    # 只保留最新签名对应的索引，防止缓存无限增长
    _CACHE.clear()
    _CACHE[cache_key] = index
    return index
