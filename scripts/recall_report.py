"""阶段 1 检索质量报告：recall@3 / recall@5 / MRR / 弃答准确率。

用途
----
给"制度问答"这条链路一个可复现的量化基线。它回答两个问题：

1. 员工问一个制度里**有答案**的问题，正确条文是否进入 Top3？
2. 员工问一个制度里**没有答案**的问题，系统是否敢说"制度中未找到"？

指标定义
--------
- recall@k：正确条文出现在前 k 条结果中的问题占比。一个问题的"正确条文"可以有多条
  （例如问"住宿标准"既该命中 2.2，也能接受 2.1 城市分级），这里 gold 用"章节号集合"表达。
- MRR：第一个正确条文的排名倒数的平均值，衡量"它排得够不够靠前"。
- 弃答准确率：域外 5 个问题里，返回空结果（=回答"制度中未找到"）的比例。

为什么还要对比 "gate=off"
-------------------------
相关性门禁是为了让陷阱题能弃答而加的，代价是可能误杀真问题。
脚本同时跑"门禁开"与"门禁关"两条，用数字证明这个代价有多大——
如果门禁关掉时 recall 只高一点点、但弃答准确率掉到 0，就说明门禁值得留。

运行
----
    python scripts/recall_report.py
    python scripts/recall_report.py --json docs/recall-report.json
"""

from __future__ import annotations

import argparse
import heapq
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import db  # noqa: E402
from app.rag.index import (  # noqa: E402
    PolicyIndex,
    expand_query,
    gate_tokens,
    get_index,
    is_out_of_scope,
)

# ---------------------------------------------------------------- 金标集
# (问题, 期望命中的章节号集合)；章节号与制度原文标题里的编号一致。
GOLD: list[tuple[str, set[str]]] = [
    ("出差上海3天住宿标准是多少", {"2.2", "2.1"}),
    ("住宿费每晚最多报多少钱", {"2.2"}),
    ("北京算一线城市吗", {"2.1", "2.2"}),
    ("伙食补助一天多少钱", {"2.4"}),
    ("当天往返的伙食补助怎么算", {"2.4"}),
    ("市内交通费每天限额多少", {"2.3"}),
    ("自己开车出差怎么算交通费", {"2.3"}),
    ("报销时效是多久", {"1.1", "6.5"}),
    ("上个月的发票还能报吗", {"1.1"}),
    ("发票抬头必须是什么", {"3.2"}),
    ("纳税人识别号填错了怎么办", {"3.2"}),
    ("哪些发票不能报销", {"3.3"}),
    ("境外发票需要什么材料", {"3.1", "3.2"}),
    ("发票连号需要提供明细吗", {"3.2"}),
    ("单笔报销2万元需要谁审批", {"4.1"}),
    ("审批要多久", {"4.2"}),
    ("可以申请加急付款吗", {"4.3"}),
    ("发票丢失怎么补报", {"6.1"}),
    ("出差生病了医疗费能报吗", {"6.3"}),
    ("实习生差旅标准怎么算", {"6.4"}),
    ("报销单被驳回后多久内要重新提交", {"6.5"}),
    ("业务招待费走哪个科目", {"5.1"}),
    ("出差审批需要提前几天申请", {"2.5"}),
    ("报销单审批链路是什么", {"4.1"}),
]

# 制度里确实没有答案的问题：系统必须弃答，而不是硬凑一段条文。
OUT_OF_SCOPE: list[str] = [
    "公司有健身房补贴吗",
    "员工生日礼金能报销吗",
    "出差可以报销宠物托运费用吗",
    "公司年会抽奖的奖品要交税吗",
    "出差期间可以顺便休假吗",
]


def section_of(chunk_heading: str) -> str:
    """从切片标题里取出章节号，例如 '... > 二、差旅费用标准 > 2.2 住宿标准（每晚上限，含税）' -> '2.2'。"""
    tail = chunk_heading.split(">")[-1].strip()
    return tail.split(" ")[0].strip()


def rank_of_sections(index: PolicyIndex, query: str, limit: int, use_gate: bool) -> list[str]:
    """返回按融合分排序的前 limit 个切片所属章节号；use_gate=False 时跳过相关性门禁。"""
    if use_gate and is_out_of_scope(query):
        return []
    expanded = expand_query(query)
    fused: dict[int, float] = {}
    for ranking in (index.bm25_ranking(expanded), index.vector_ranking(expanded)):
        for rank, (idx, _score) in enumerate(ranking, start=1):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (index.rrf_k + rank)
    best = heapq.nlargest(limit, fused.items(), key=lambda kv: kv[1]) if fused else []

    gate = set(gate_tokens(expanded)) or set()
    out: list[str] = []
    for idx, _score in best:
        chunk = index.chunks[idx]
        if use_gate and gate and not index._relevant(gate, set(chunk.tokens)):
            continue
        out.append(section_of(chunk.heading))
    return out


def evaluate(index: PolicyIndex, use_gate: bool) -> dict:
    r3 = r5 = 0
    rr_sum = 0.0
    misses: list[dict] = []
    for question, gold in GOLD:
        top5 = rank_of_sections(index, question, 5, use_gate)
        hit_pos = next((i + 1 for i, sec in enumerate(top5) if sec in gold), None)
        if hit_pos:
            r3 += 1 if hit_pos <= 3 else 0
            r5 += 1
            rr_sum += 1.0 / hit_pos
        else:
            misses.append({"question": question, "gold": sorted(gold), "top5": top5})
    abstained = 0
    oos_miss: list[dict] = []
    for question in OUT_OF_SCOPE:
        got = rank_of_sections(index, question, 5, use_gate)
        if not got:
            abstained += 1
        else:
            oos_miss.append({"question": question, "top5": got})
    n = len(GOLD)
    return {
        "gate": use_gate,
        "recall@3": round(r3 / n, 4),
        "recall@5": round(r5 / n, 4),
        "mrr": round(rr_sum / n, 4),
        "abstain_acc": round(abstained / len(OUT_OF_SCOPE), 4),
        "misses": misses,
        "oos_leaks": oos_miss,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="检索质量报告")
    parser.add_argument("--db", default="", help="数据库路径，默认用配置值")
    parser.add_argument("--json", default="", help="把结果写到指定 JSON 文件")
    args = parser.parse_args()

    conn = db.init_db(args.db or None)  # init_db 负责建表与种子，并返回连接
    index = get_index(conn)
    print(f"索引切片数：{len(index.chunks)}；金标问题：{len(GOLD)}；域外问题：{len(OUT_OF_SCOPE)}\n")

    results = [evaluate(index, True), evaluate(index, False)]
    for res in results:
        tag = "门禁开（线上实际）" if res["gate"] else "门禁关（仅对比）"
        print(f"== {tag} ==")
        print(f"   recall@3 = {res['recall@3']:.1%}")
        print(f"   recall@5 = {res['recall@5']:.1%}")
        print(f"   MRR      = {res['mrr']:.3f}")
        print(f"   弃答准确率 = {res['abstain_acc']:.1%}（{len(OUT_OF_SCOPE)} 条域外问题）")
        for m in res["misses"]:
            print(f"   漏召回: {m['question']}  期望 {m['gold']}  实际 Top5 {m['top5']}")
        for m in res["oos_leaks"]:
            print(f"   域外误命中: {m['question']}  Top5 {m['top5']}")
        print()

    if args.json:
        payload = {"chunks": len(index.chunks), "gold": len(GOLD), "runs": results}
        with io.open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
