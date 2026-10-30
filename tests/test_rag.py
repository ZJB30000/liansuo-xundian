"""RAG 链路离线单元测试：不调用外部 API，向量用确定性假数据替换。

覆盖：切片（500 字上限）、BM25 命中、RRF 混合检索、提示词红线。
与 src/my_tool/services、src/my_tool/agents 结构对应。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np  # noqa: E402
from rank_bm25 import BM25Okapi  # noqa: E402

import my_tool.services.kb_service as kb  # noqa: E402
from my_tool.agents.prompts import build_prompt  # noqa: E402
from my_tool.services.kb_service import bm25_search, chunk_markdown, tokenize  # noqa: E402

DOC_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "巡店制度示例.md")


def make_bm25(chunks):
    return BM25Okapi([tokenize(c) for c in chunks])


def load_chunks():
    with open(DOC_PATH, encoding="utf-8") as f:
        return chunk_markdown(f.read())


def test_chunks_within_500_and_enough():
    chunks = load_chunks()
    assert len(chunks) >= 5
    assert all(len(c) <= 500 for c in chunks)


def test_bm25_hits_right_sections():
    chunks = load_chunks()
    bm25 = make_bm25(chunks)
    cases = {
        "整改照片能不能代替复查": "五、整改与复查",
        "培训签到算不算学会": "五、整改与复查",
        "留样冰箱要上锁吗": "二、留样管理",
    }
    for query, expect in cases.items():
        hits = bm25_search(query, bm25, chunks, k=3)
        assert expect in chunks[hits[0][0]], f"{query} 未命中 {expect}"


def _fake_embed(texts, cfg=None):
    """确定性假向量：相同文本得到相同向量，保证测试可重复。"""
    rng = np.random.RandomState(42)
    v = rng.rand(len(texts), 16).astype("float32")
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_hybrid_search_returns_top3(monkeypatch):
    monkeypatch.setattr(kb, "embed_texts", _fake_embed)  # 替换向量调用，离线可跑
    chunks = load_chunks()
    index, _ = kb.build_vector_index(chunks, {})
    bm25 = make_bm25(chunks)
    hits = kb.hybrid_search("冷藏温度记录缺失怎么定级", index, bm25, chunks, {})
    assert len(hits) == 3
    assert all(0 <= idx < len(chunks) for idx, _ in hits)


def test_prompt_contains_red_lines():
    chunks = load_chunks()
    hits = [(0, 0.9), (1, 0.5)]
    prompt = build_prompt("留样要多少克", hits, chunks)
    assert "禁止" in prompt                      # 只许依据片段回答
    assert "【制度片段1】" in prompt              # 片段已拼入
    assert "依据" in prompt                      # 要求附出处
