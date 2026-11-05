#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报销助手 · 阶段 1 最小原型（单文件，无 Web）
================================================================================

目标
----
用一条命令验证整条链路是否成立：
    制度 Markdown → 切片 → 向量化 + BM25 → RRF 混合检索 top3 → 拼提示词 → 调 LLM → 带引用的回答

刻意不做的部分（因为第一周不该做）
----------------------------------
不做数据库、不做 Web、不做登录、不做审批流、不做 OCR。
这些都不影响"检索能否支撑制度问答"这个核心假设。

运行方式
--------
    pip install -r requirements.txt

    # 1) 有 API Key（OpenAI 兼容接口，DeepSeek/Moonshot/通义/vLLM 都一样）
    export DEEPSEEK_API_KEY=sk-xxxx          # Windows PowerShell: $env:DEEPSEEK_API_KEY="sk-xxxx"
    python reimbursement_assistant.py                      # 进入交互问答
    python reimbursement_assistant.py --ask "北京出差住宿标准是多少"
    python reimbursement_assistant.py --ask "..." --show-prompt   # 看真正发给模型的提示词

    # 2) 没有 API Key，只想验证"检索链路"是否通（用哈希向量兜底，不调任何网络）
    python reimbursement_assistant.py --offline --ask "住宿标准"

标注说明
--------
代码里凡是带【复用·阶段2】的模块，都是后续 Web 阶段直接搬进 app/rag/、app/llm/ 的部分，
建议保持函数签名不变，只把"读本地文件"换成"读数据库"。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np

# Windows 控制台默认是 GBK，直接打印中文/符号会抛 UnicodeEncodeError。
# 这里把标准输出强制成 UTF-8（失败则忽略，不影响逻辑）。
for _stream in (sys.stdout, sys.stderr):
    try:  # pragma: no cover - 只在终端环境生效
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

# ---------------------------------------------------------------------------
# 可选依赖：装了更好，没装也能跑
#   faiss  : 向量检索（没装则退化为 numpy 精确内积，结果一致，只是慢）
#   jieba  : 中文分词（没装则退化为"中文字符 bigram + 英文整词"，BM25 效果会下降）
# ---------------------------------------------------------------------------
try:  # pragma: no cover
    import faiss  # type: ignore
except Exception:  # noqa: BLE001 - 任何导入失败都视为"不可用"
    faiss = None

try:  # pragma: no cover
    import jieba  # type: ignore
except Exception:  # noqa: BLE001
    jieba = None


# ===========================================================================
# 0. 配置层【复用·阶段2 → app/core/config.py】
# ===========================================================================
@dataclass
class Config:
    """全部可配置项集中在这里，后续换供应商 = 改环境变量，不改代码。"""

    # ---- LLM（对话模型）----
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.2      # 制度问答压低温度：要的是复述，不是创作
    llm_max_tokens: int = 900
    llm_timeout: int = 60

    # ---- Embedding（向量化模型）----
    # 留空则复用 llm_base_url（大多数 OpenAI 兼容网关同时提供 /embeddings）
    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_model: str = "text-embedding-3-small"

    # ---- 检索参数 ----
    chunk_size: int = 500             # 题目要求 500 字
    chunk_overlap: int = 50           # 相邻切片重叠，防止一条规则被切断
    top_k: int = 3
    rrf_k: int = 60                   # RRF 平滑常数，业界默认 60，无需调
    heading_weight: int = 2           # 标题在 BM25 索引里重复的次数：标题是整段的语义摘要
    bm25_weight: float = 1.0
    vector_weight: float = 1.0

    # ---- 运行模式 ----
    offline: bool = False             # True = 不调网络，用哈希向量 + 回显 LLM 验证链路

    @classmethod
    def from_env(cls, offline: bool = False) -> "Config":
        """从环境变量装配配置；.env 文件也会被读取（见 load_dotenv_light）。"""
        cfg = cls()
        cfg.llm_base_url = os.getenv("LLM_BASE_URL", cfg.llm_base_url).rstrip("/")
        cfg.llm_api_key = os.getenv("LLM_API_KEY", "") or os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
        cfg.llm_model = os.getenv("LLM_MODEL", cfg.llm_model)
        cfg.llm_temperature = float(os.getenv("LLM_TEMPERATURE", cfg.llm_temperature))

        cfg.embed_base_url = (os.getenv("EMBED_BASE_URL", "") or cfg.llm_base_url).rstrip("/")
        cfg.embed_api_key = os.getenv("EMBED_API_KEY", "") or cfg.llm_api_key
        cfg.embed_model = os.getenv("EMBED_MODEL", cfg.embed_model)

        cfg.chunk_size = int(os.getenv("CHUNK_SIZE", cfg.chunk_size))
        cfg.chunk_overlap = int(os.getenv("CHUNK_OVERLAP", cfg.chunk_overlap))
        cfg.top_k = int(os.getenv("TOP_K", cfg.top_k))
        cfg.offline = offline or os.getenv("OFFLINE", "0") == "1"
        return cfg


def load_dotenv_light(path: Path) -> None:
    """极简 .env 读取（避免为一个功能引入 python-dotenv 依赖）。已存在的环境变量优先。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# ===========================================================================
# 1. 切片【复用·阶段2 → app/rag/chunker.py】
# ===========================================================================
@dataclass
class Chunk:
    """一个可检索的最小语义单元。heading 单独存，便于回答里显示出处。"""

    index: int
    heading: str      # 例："差旅管理 > 住宿标准 > 一线城市"
    text: str         # 切片正文（含 Markdown 原文）
    char_count: int = 0

    def __post_init__(self) -> None:
        if not self.char_count:
            self.char_count = len(self.text)

    def to_dict(self) -> dict:
        return {"index": self.index, "heading": self.heading, "text": self.text, "char_count": self.char_count}


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def split_markdown(text: str, chunk_size: int = 500, overlap: int = 50) -> list[Chunk]:
    """标题感知 + 滑动窗口切片。

    为什么不是粗暴地按 500 字硬切？
      1. 硬切会把"一线城市 800 元/晚"和它的适用条件切到两个切片里，检索到也没有意义；
      2. 按标题先分段，每段尽量保持完整规则；只有超长段才滑切；
      3. 每个切片都带上标题路径（heading），回答时可以显示"依据《差旅管理》住宿标准"。

    参数
    ----
    text       : Markdown 原文
    chunk_size : 单切片最大字符数（题目要求 500）
    overlap    : 滑切时的重叠字符数，避免边界处语义断裂
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须为正数")
    # 重叠不能大于等于切片长度，否则滑窗不会前进 → 死循环
    overlap = max(0, min(overlap, chunk_size // 2))

    sections: list[tuple[list[str], str]] = []   # (标题路径, 正文)
    path: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            sections.append((list(path), body))
        buf.clear()

    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            # 保留层级：二级标题出现时，三级及更深路径被截断
            path = path[: level - 1] + [title]
            buf.append(line)          # 标题行保留在正文首行，切片自带上下文
        else:
            buf.append(line)
    flush()

    chunks: list[Chunk] = []
    for heading_path, body in sections:
        heading = " > ".join(heading_path)
        if len(body) <= chunk_size:
            chunks.append(Chunk(index=len(chunks), heading=heading, text=body))
            continue
        start = 0
        while start < len(body):
            end = min(start + chunk_size, len(body))
            piece = body[start:end].strip()
            if piece:
                chunks.append(Chunk(index=len(chunks), heading=heading, text=piece))
            if end >= len(body):
                break
            start = end - overlap          # 回退 overlap 个字符，形成重叠窗口
    return chunks


# ===========================================================================
# 2. 分词与 BM25【复用·阶段2 → app/rag/bm25.py】
# ===========================================================================
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9._%+\-]*")

# 停用词：只放"没有业务含义"的词，绝不能放 标准/金额/审批 这类领域词。
# 为什么要在这里拦：问句里的"怎么办/多少"会命中其他章节的标题
# （"6.1 发票丢失怎么办"），把真正该排第一的"3.2 发票合规要求"挤下去。
# 同时覆盖 jieba 分词（"怎么办"）与 bigram 兜底（"怎么""么办"）两种切法。
_NOISE = {
    "怎么办", "怎么", "么办", "如何", "什么", "哪些", "哪个", "多少",
    "能不能", "能不", "不能", "可否", "可以", "需要", "是否", "请问",
    "一下", "应该", "我们", "我的", "这个", "那个",
    "的", "了", "吗", "呢", "吧", "是", "有", "在", "和", "与", "及",
    "我", "你", "他", "它", "这", "那", "其", "之", "也", "就", "都",
    "还", "又", "把", "被", "给", "对", "为", "以", "于", "着", "过",
    "情况", "处理", "相关", "一般",
}


def tokenize(text: str) -> list[str]:
    """中文分词 + 去停用词。

    有 jieba 用 jieba（效果好）；没有则退化为：
      - 英文/数字：整词
      - 中文：相邻二字 bigram（"住宿标准" → 住宿/宿标/标准）
    bigram 是中文检索的经典低成本方案，召回尚可，精确度不如分词。
    """
    text = text.lower()
    if jieba is not None:
        return [t for t in jieba.lcut(text) if t.strip() and not t.isspace() and t not in _NOISE]

    tokens: list[str] = []
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    tokens.extend(_WORD_RE.findall(text))
    return [t for t in tokens if t not in _NOISE]


class BM25:
    """标准 BM25Okapi（k1=1.5, b=0.75），纯 Python 实现，不依赖 rank_bm25。

    BM25 负责"精确词命中"（如"800 元""经济舱""增值税专用发票"），
    向量负责"换个说法也能找到"（如"住宿能报多少" vs "住宿标准"）。
    两者互补，这才是混合检索的意义。
    """

    def __init__(self, docs_tokens: Sequence[Sequence[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.docs = [list(d) for d in docs_tokens]
        self.k1, self.b = k1, b
        self.n_docs = len(self.docs)
        self.avgdl = (sum(len(d) for d in self.docs) / self.n_docs) if self.n_docs else 0.0
        self.tf = [Counter(d) for d in self.docs]

        df: Counter[str] = Counter()
        for tokens in self.docs:
            df.update(set(tokens))
        # 加 1 的平滑 IDF，避免高频词取到负值
        self.idf = {t: math.log(1 + (self.n_docs - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def scores(self, query_tokens: Sequence[str]) -> np.ndarray:
        out = np.zeros(self.n_docs, dtype="float32")
        for q in query_tokens:
            idf = self.idf.get(q)
            if idf is None:            # 查询词不在任何切片里 → 对排序无贡献
                continue
            for i, tf in enumerate(self.tf):
                f = tf.get(q, 0)
                if not f:
                    continue
                dl = len(self.docs[i]) or 1
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                out[i] += idf * (f * (self.k1 + 1) / denom)
        return out


# ===========================================================================
# 3. 向量化【复用·阶段2 → app/llm/embedding.py】
# ===========================================================================
class EmbeddingClient(Protocol):
    name: str
    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


def _normalize(mat: np.ndarray) -> np.ndarray:
    """L2 归一化：归一化后内积 == 余弦相似度，FAISS 用 IndexFlatIP 即可。"""
    mat = np.asarray(mat, dtype="float32")
    if mat.ndim == 1:
        mat = mat.reshape(1, -1)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def _post_json(url: str, payload: dict, api_key: str, timeout: int = 60) -> dict:
    """最小 HTTP POST（stdlib，避免引入 requests）。

    生产环境建议换 httpx.AsyncClient：能复用连接池、支持异步取消，
    这也是阶段2 把本函数搬进 app/llm/openai_compat.py 时要改的第一处。
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:                       # 4xx/5xx
        detail = exc.read().decode("utf-8", "replace")[:600]
        raise RuntimeError(f"接口返回 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:                        # 网络不通
        raise RuntimeError(f"无法连接 {url}：{exc.reason}") from exc


class OpenAIEmbedding:
    """OpenAI 兼容的 /embeddings 实现（DeepSeek 无 embedding，故默认走 text-embedding-3-small）。"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 60, batch_size: int = 32) -> None:
        self.url = f"{base_url.rstrip('/')}/embeddings"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.batch_size = batch_size

    @property
    def name(self) -> str:
        # 名字进缓存文件名：换模型自动重建索引，避免新旧向量混用
        return "remote_" + re.sub(r"[^a-zA-Z0-9_-]", "_", self.model)

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = list(texts[i : i + self.batch_size])
            resp = _post_json(self.url, {"model": self.model, "input": batch}, self.api_key, self.timeout)
            # 按 index 排序，保证向量与文本顺序严格对齐（错位是最隐蔽的 bug）
            data = sorted(resp["data"], key=lambda d: d.get("index", 0))
            vectors.extend(d["embedding"] for d in data)
            time.sleep(0.05)          # 轻微退避，避免触发网关限流
        return _normalize(np.asarray(vectors, dtype="float32"))


class HashEmbedding:
    """离线兜底向量：把 token 哈希到固定维度并累加（带符号）。

    它**不理解语义**，只能反映词面重合，但足够在无 API Key 的环境里验证
    "切片 → 检索 → 拼提示词"链路是否跑通。不要把它的效果当成 RAG 的真实上限。
    """

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    @property
    def name(self) -> str:
        return f"hash{self.dim}"

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype="float32")
        for row, text in enumerate(texts):
            for token in tokenize(text):
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                idx = int.from_bytes(digest[:4], "little") % self.dim
                sign = 1.0 if digest[4] & 1 else -1.0
                out[row, idx] += sign * (1.0 + math.log1p(len(token)))
        return _normalize(out)


# ===========================================================================
# 4. 向量索引【复用·阶段2 → app/rag/vector_store.py】
# ===========================================================================
class VectorIndex:
    """FAISS 封装；faiss 不可用时自动退化为 numpy 精确检索，接口不变。

    阶段2 换 pgvector 时，只要保持 search() 的签名，上层检索器完全不用改。
    """

    def __init__(self, vectors: np.ndarray) -> None:
        self.vectors = _normalize(vectors)
        self._faiss_index = None
        self.backend = "numpy"
        if faiss is not None and len(self.vectors):
            index = faiss.IndexFlatIP(self.vectors.shape[1])   # 内积 + 已归一化 = 余弦
            index.add(self.vectors)
            self._faiss_index = index
            self.backend = "faiss"

    def __len__(self) -> int:
        return len(self.vectors)

    def search(self, query_vec: np.ndarray, k: int) -> list[tuple[int, float]]:
        if not len(self.vectors):
            return []
        k = min(k, len(self.vectors))
        q = _normalize(query_vec)                                  # (1, dim)
        if self._faiss_index is not None:
            scores, idx = self._faiss_index.search(q, k)
            return [(int(i), float(s)) for i, s in zip(idx[0], scores[0]) if i >= 0]
        sims = self.vectors @ q[0]
        order = np.argsort(-sims)[:k]
        return [(int(i), float(sims[i])) for i in order]


# ===========================================================================
# 5. 混合检索【复用·阶段2 → app/rag/retriever.py】
# ===========================================================================
def rrf_fuse(ranked_lists: Sequence[Sequence[int]], weights: Sequence[float], k: int = 60) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion：只用名次融合，不用原始分数。

    为什么不用"分数加权求和"？BM25 分数无上界（可能 0~30），余弦相似度在 0~1，
    直接相加会让 BM25 单方面主导，且换文档集就要重新调权重。
    RRF 只看排名，量纲无关，是混合检索最稳的默认选择。
    """
    fused: dict[int, float] = {}
    for weight, ranked in zip(weights, ranked_lists):
        for rank, doc_idx in enumerate(ranked, start=1):
            fused[doc_idx] = fused.get(doc_idx, 0.0) + weight / (k + rank)
    return sorted(fused.items(), key=lambda kv: -kv[1])


@dataclass
class Hit:
    chunk: Chunk
    rrf_score: float
    bm25_score: float
    vector_score: float


class HybridRetriever:
    """BM25 + 向量 双路召回 → RRF 融合 → top_k。"""

    def __init__(self, chunks: Sequence[Chunk], embedder: EmbeddingClient, vectors: np.ndarray | None = None,
                 heading_weight: int = 2) -> None:
        self.chunks = list(chunks)
        self.embedder = embedder
        self.heading_weight = max(1, heading_weight)
        # 标题加权：把标题 token 重复若干次，等于"标题命中"比"正文命中"更值钱。
        # 不这么做时，"6.4 实习生有差旅标准吗"会因为正文里出现"住宿/标准"而排在
        # "2.2 住宿标准"前面 —— 这是制度问答里最常见的错误召回。
        self.tokens = [
            tokenize(c.heading) * self.heading_weight + tokenize(c.text) for c in self.chunks
        ]
        self.bm25 = BM25(self.tokens)
        self.vectors = vectors if vectors is not None else embedder.embed([c.text for c in self.chunks])
        self.vindex = VectorIndex(self.vectors)

    @classmethod
    def from_cached(cls, chunks: Sequence[Chunk], vectors: np.ndarray, embedder: EmbeddingClient,
                    heading_weight: int = 2) -> "HybridRetriever":
        return cls(chunks, embedder, vectors, heading_weight)

    def search(self, question: str, top_k: int = 3, rrf_k: int = 60,
               bm25_weight: float = 1.0, vector_weight: float = 1.0) -> list[Hit]:
        if not self.chunks:
            return []
        bm25_scores = self.bm25.scores(tokenize(question))
        bm25_ranked = np.argsort(-bm25_scores).tolist()

        query_vec = self.embedder.embed([question])[0]
        vec_pairs = self.vindex.search(query_vec, len(self.chunks))
        vec_ranked = [i for i, _ in vec_pairs]
        vec_map = dict(vec_pairs)

        fused = rrf_fuse([bm25_ranked, vec_ranked], weights=[bm25_weight, vector_weight], k=rrf_k)
        hits: list[Hit] = []
        for doc_idx, score in fused[:top_k]:
            hits.append(Hit(
                chunk=self.chunks[doc_idx],
                rrf_score=score,
                bm25_score=float(bm25_scores[doc_idx]),
                vector_score=float(vec_map.get(doc_idx, 0.0)),
            ))
        return hits


# ===========================================================================
# 6. 提示词【复用·阶段2 → app/rag/prompts.py，注意带上版本号】
# ===========================================================================
PROMPT_VERSION = "v1.0"

SYSTEM_PROMPT = """你是企业财务报销助手，只依据提供的【制度片段】回答员工的问题。

硬性规则：
1. 只使用【制度片段】中的信息。片段里没有的，直接回答"现有制度中未找到相关规定，建议咨询财务部"，不要推测。
2. 每个结论后必须标注来源编号，如 [1]、[2]；标注必须对应真实使用过的片段。
3. 涉及金额、比例、时限、天数时，必须逐字引用原文数字，不得换算或估算。
4. 制度之间存在冲突时，指出冲突并并列列出两个条款，不要自行裁决。
5. 回答用简体中文，先给结论（一句话），再给依据（分条），最后给"需要准备什么"（如适用）。
6. 不要输出与问题无关的寒暄。"""


def build_messages(question: str, hits: Sequence[Hit]) -> list[dict]:
    """把检索结果拼成"带编号的上下文"，编号是引用与审计的锚点。"""
    blocks = []
    for rank, hit in enumerate(hits, start=1):
        blocks.append(
            f"[{rank}] 出处：{hit.chunk.heading or '（无标题）'}\n{hit.chunk.text}"
        )
    context = "\n\n---\n\n".join(blocks) if blocks else "（无检索结果）"
    user = (
        f"【制度片段】\n{context}\n\n"
        f"【员工问题】\n{question}\n\n"
        f"请按规则作答，并在结尾用一行列出你引用过的编号，格式：引用：[1][2]"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


# ===========================================================================
# 7. LLM 调用【复用·阶段2 → app/llm/openai_compat.py】
# ===========================================================================
class LLMClient(Protocol):
    def chat(self, messages: Sequence[dict], temperature: float = 0.2, max_tokens: int = 900) -> str: ...


class OpenAICompatibleLLM:
    """OpenAI Chat Completions 协议。DeepSeek / Moonshot / 通义 / vLLM / Ollama 都兼容，
    换供应商只需改 LLM_BASE_URL + LLM_MODEL，不动业务代码 —— 这就是"可替换"的落地方式。
    """

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 60) -> None:
        self.url = f"{base_url.rstrip('/')}/chat/completions"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def chat(self, messages: Sequence[dict], temperature: float = 0.2, max_tokens: int = 900) -> str:
        payload = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        resp = _post_json(self.url, payload, self.api_key, self.timeout)
        try:
            return resp["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"返回结构异常：{json.dumps(resp, ensure_ascii=False)[:400]}") from exc


class EchoLLM:
    """离线占位：把拼接好的提示词"回显"出来，用于在没有 Key 时验证链路，
    也方便你在 CI 里跑不花钱的端到端测试。"""

    def __init__(self, preview_chars: int = 1600) -> None:
        self.preview_chars = preview_chars

    def chat(self, messages: Sequence[dict], temperature: float = 0.2, max_tokens: int = 900) -> str:
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return (
            "【离线模式·未调用真实模型】以下是系统将发送的上下文（截断展示）：\n\n"
            + user[: self.preview_chars]
            + "\n\n…… 配置 LLM_API_KEY 后即可获得真实回答。"
        )


# ===========================================================================
# 8. 索引缓存：内容 hash 变了才重建【复用·阶段2 → policy_service.ensure_indexed】
# ===========================================================================
def build_or_load_index(policy_path: Path, cfg: Config, embedder: EmbeddingClient, index_dir: Path,
                        rebuild: bool = False) -> tuple[list[Chunk], HybridRetriever]:
    if not policy_path.exists():
        raise FileNotFoundError(f"制度文件不存在：{policy_path}")

    text = policy_path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    index_dir.mkdir(parents=True, exist_ok=True)
    # 缓存键包含 embedding 模型名：换模型必须重建，否则向量空间不一致会静默错乱
    key = f"{policy_path.stem}.{embedder.name}.{digest}"
    meta_path = index_dir / f"{key}.json"
    vec_path = index_dir / f"{key}.npy"

    if not rebuild and meta_path.exists() and vec_path.exists():
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        chunks = [Chunk(**c) for c in payload["chunks"]]
        vectors = np.load(vec_path)
        print(f"[索引] 命中缓存：{len(chunks)} 个切片，{vectors.shape[1]} 维（{meta_path.name}）")
        return chunks, HybridRetriever.from_cached(chunks, vectors, embedder, cfg.heading_weight)

    chunks = split_markdown(text, cfg.chunk_size, cfg.chunk_overlap)
    if not chunks:
        raise ValueError(f"制度文件没有可用内容：{policy_path}")
    print(f"[索引] 切出 {len(chunks)} 个切片（size={cfg.chunk_size}, overlap={cfg.chunk_overlap}），开始向量化…")
    t0 = time.time()
    vectors = embedder.embed([c.text for c in chunks])
    print(f"[索引] 向量化完成，耗时 {time.time() - t0:.1f}s，维度 {vectors.shape[1]}")

    meta_path.write_text(
        json.dumps({"policy": str(policy_path), "chunks": [c.to_dict() for c in chunks]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    np.save(vec_path, vectors)
    print(f"[索引] 已缓存到 {index_dir}")
    return chunks, HybridRetriever(chunks, embedder, vectors)


# ===========================================================================
# 9. 问答主流程：检索 → 提示词 → LLM
# ===========================================================================
def answer(question: str, retriever: HybridRetriever, llm: LLMClient, cfg: Config,
           show_prompt: bool = False) -> str:
    t0 = time.time()
    hits = retriever.search(question, top_k=cfg.top_k, rrf_k=cfg.rrf_k,
                            bm25_weight=cfg.bm25_weight, vector_weight=cfg.vector_weight)
    elapsed = (time.time() - t0) * 1000

    if not hits:
        return "制度库为空，无法回答。请管理员先上传制度文档。"

    print("\n" + "─" * 72)
    print(f"检索命中 {len(hits)} 条（融合耗时 {elapsed:.0f} ms）")
    for rank, hit in enumerate(hits, start=1):
        preview = hit.chunk.text.replace("\n", " ")[:70]
        print(f"  [{rank}] rrf={hit.rrf_score:.4f} bm25={hit.bm25_score:.2f} cos={hit.vector_score:.3f}")
        print(f"      {hit.chunk.heading or '（无标题）'}")
        print(f"      {preview}…")
    print("─" * 72)

    messages = build_messages(question, hits)
    if show_prompt:
        print("\n【发给模型的完整提示词】")
        for msg in messages:
            print(f"\n--- {msg['role']} ---\n{msg['content']}")
        print()

    return llm.chat(messages, temperature=cfg.llm_temperature, max_tokens=cfg.llm_max_tokens)


# ===========================================================================
# 10. CLI
# ===========================================================================
def build_arg_parser() -> argparse.ArgumentParser:
    base = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description="报销助手 · 阶段1 最小原型（BM25 + 向量混合检索）")
    # 制度原文放在仓库根目录的 data/policies 下，与 Web 版共用同一份文档，避免两处各改一遍。
    p.add_argument("--policy", type=Path, default=base.parent / "data" / "policies" / "company_reimbursement.md",
                   help="制度 Markdown 文件路径")
    p.add_argument("--index-dir", type=Path, default=base / "data" / "index", help="索引缓存目录")
    p.add_argument("--ask", type=str, default=None, help="单次提问后退出；不传则进入交互模式")
    p.add_argument("--top-k", type=int, default=None, help="检索返回条数（默认 3）")
    p.add_argument("--rebuild", action="store_true", help="忽略缓存，强制重建索引")
    p.add_argument("--show-prompt", action="store_true", help="打印发给模型的完整提示词")
    p.add_argument("--offline", action="store_true", help="不调网络：哈希向量 + 回显 LLM，仅验证链路")
    return p


def make_embedder(cfg: Config) -> EmbeddingClient:
    if cfg.offline:
        print("[模式] 离线：使用哈希向量（仅验证链路，不代表真实语义检索效果）")
        # 哈希向量本质上也是"词面匹配"，与 BM25 信号高度重复，且精度更差；
        # 等权融合反而会把 BM25 的正确排序拉偏，所以这里压低向量权重。
        cfg.vector_weight = 0.35
        print(f"[融合] 权重已下调：bm25={cfg.bm25_weight} vector={cfg.vector_weight}（离线兜底向量仅作弱信号）")
        return HashEmbedding()
    if not cfg.embed_api_key:
        print("[警告] 未配置 EMBED_API_KEY / LLM_API_KEY，自动降级为离线哈希向量")
        cfg.offline = True
        cfg.vector_weight = 0.35          # 同上：兜底向量只作弱信号
        return HashEmbedding()
    print(f"[模式] 在线 Embedding：{cfg.embed_model} @ {cfg.embed_base_url}")
    return OpenAIEmbedding(cfg.embed_base_url, cfg.embed_api_key, cfg.embed_model, cfg.llm_timeout)


def make_llm(cfg: Config) -> LLMClient:
    if cfg.offline or not cfg.llm_api_key:
        return EchoLLM()
    print(f"[模式] 在线 LLM：{cfg.llm_model} @ {cfg.llm_base_url}")
    return OpenAICompatibleLLM(cfg.llm_base_url, cfg.llm_api_key, cfg.llm_model, cfg.llm_timeout)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    # .env 查找顺序：项目根 → prototype/，先找到的先加载（已存在的环境变量不被覆盖）
    here = Path(__file__).resolve().parent
    for env_path in (here.parent / ".env", here / ".env"):
        load_dotenv_light(env_path)

    cfg = Config.from_env(offline=args.offline)
    if args.top_k:
        cfg.top_k = args.top_k

    print("=" * 72)
    print("报销助手 · 阶段1 原型")
    print(f"  分词后端 : {'jieba' if jieba is not None else '中文 bigram 兜底（建议 pip install jieba）'}")
    print(f"  向量后端 : {'faiss' if faiss is not None else 'numpy 精确检索（建议 pip install faiss-cpu）'}")
    print(f"  制度文件 : {args.policy}")
    print("=" * 72)

    embedder = make_embedder(cfg)
    llm = make_llm(cfg)

    try:
        chunks, retriever = build_or_load_index(args.policy, cfg, embedder, args.index_dir, rebuild=args.rebuild)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    if args.ask:
        print(f"\n问：{args.ask}")
        print("\n答：", answer(args.ask, retriever, llm, cfg, args.show_prompt))
        return 0

    print("\n进入交互问答。输入 exit 退出，输入 :prompt 切换「是否打印完整提示词」。")
    show_prompt = args.show_prompt
    while True:
        try:
            question = input("\n问：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if question.lower() in {"exit", "quit", "q"}:
            break
        if not question:
            continue
        if question == ":prompt":
            show_prompt = not show_prompt
            print(f"完整提示词打印：{'开' if show_prompt else '关'}")
            continue
        try:
            print("\n答：", answer(question, retriever, llm, cfg, show_prompt=show_prompt))
        except RuntimeError as exc:          # 网络/鉴权类错误：不让进程挂掉
            print(f"[调用失败] {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
