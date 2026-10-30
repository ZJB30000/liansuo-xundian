"""知识库服务（服务层 · ★核心复用模块）。

制度文档切片 + FAISS 向量检索 + BM25 关键词检索 + RRF 混合融合。
对应《架构选型》服务层的知识库服务；阶段2 管理员上传制度文档后走同一段逻辑入库，
/chat 接口与置信度判断都依赖本模块。
"""

import os
import sys

import numpy as np
import faiss                      # 向量索引；课程项目用 faiss-cpu 即可
import jieba                      # 中文分词，BM25 检索需要
from rank_bm25 import BM25Okapi   # 轻量 BM25 实现

from my_tool.agents.llm_gateway import embed_texts


# ============================================================
# 文档装载：读取制度文件 → 切片 → 标注来源文件名（★阶段2 复用）
# ============================================================
def load_kb_documents(paths: list) -> list:
    """读取多个制度 markdown 文件，切片并标注来源。

    返回 [{"text": 切片文本, "doc": 文件名}, ...]；来源文件名用于
    /chat 接口的 sources 事件（前端展示“文件名+片段”）。
    """
    out = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for piece in chunk_markdown(f.read()):
                out.append({"text": piece, "doc": os.path.basename(p)})
    return out


# ============================================================
# 文档切片：先按标题分节，再合并/切分到约 500 字（带 50 字重叠）
# ============================================================
def chunk_markdown(text: str, size: int = 500, overlap: int = 50) -> list:
    """返回切片列表。按标题切节能保证一段切片只讲一类标准，检索更准。"""
    sections, cur = [], []
    for line in text.splitlines():                     # 按标题行分节
        if line.startswith("#") and cur:
            sections.append("\n".join(cur))
            cur = [line]
        else:
            cur.append(line)
    if cur:
        sections.append("\n".join(cur))

    chunks = []
    for sec in sections:
        sec = sec.strip()
        if not sec:
            continue
        if len(sec) <= size:                           # 一节不足 500 字：整节一片
            chunks.append(sec)
            continue
        step = size - overlap                          # 长节按 500 字滑窗切，留重叠防句子被拦腰截断
        for i in range(0, len(sec), step):
            piece = sec[i:i + size].strip()
            if len(piece) >= 30:                       # 丢弃太短的尾巴，避免噪声切片
                chunks.append(piece)
    return chunks


# ============================================================
# 双路检索：FAISS 向量（语义）+ BM25（关键词），RRF 融合
# ============================================================
def build_vector_index(chunks: list, embed_cfg: dict):
    """对全部切片向量化，建 FAISS 内积索引，返回 (索引, 切片向量矩阵)。

    优雅降级：向量接口不可用（如免费额度不含 embedding、欠费 429）时不抛异常，
    返回 (None, None)，hybrid_search 自动退化为 BM25 关键词检索，服务不中断。
    """
    try:
        matrix = embed_texts(chunks, embed_cfg)
    except Exception as e:                             # 429 / 余额不足 / 网络失败都走这里
        print(f"[知识库] 向量化不可用，已退化为关键词检索：{e}", file=sys.stderr)
        return None, None
    index = faiss.IndexFlatIP(matrix.shape[1])         # 内积索引：配合归一化向量即余弦相似度
    index.add(matrix)
    return index, matrix


def vector_search(query: str, index, chunks: list, embed_cfg: dict, k: int) -> list:
    """向量检索：问题转向量 → 索引里找最近邻，返回 [(下标, 得分)]。"""
    qv = embed_texts([query], embed_cfg)
    scores, ids = index.search(qv, min(k, len(chunks)))
    return list(zip(ids[0].tolist(), scores[0].tolist()))


def tokenize(text: str) -> list:
    """中文分词：BM25 以词为单位匹配，jieba 够课程项目使用。"""
    return [w for w in jieba.lcut(text) if w.strip()]


def bm25_search(query: str, bm25: BM25Okapi, chunks: list, k: int) -> list:
    """关键词检索：擅长命中“留样 125 克”这类必须精确匹配的词。"""
    scores = bm25.get_scores(tokenize(query))
    order = np.argsort(scores)[::-1][:k]               # 得分从高到低取前 k
    return [(int(i), float(scores[i])) for i in order if scores[i] > 0]


def hybrid_search(query: str, index, bm25, chunks: list, embed_cfg: dict, k: int = 3) -> list:
    """混合检索：两路各取前 5，再用 RRF（倒数排名融合）合并成 top3。

    RRF 公式：得分 = Σ 1/(60+名次)。只用名次不用原始分，
    避免向量余弦分和 BM25 分数值量级不同没法直接相加的问题。
    降级规则：index 为 None 或问题向量调用失败时，只走 BM25 关键词检索。
    """
    vec_hits = []
    if index is not None:
        try:
            vec_hits = vector_search(query, index, chunks, embed_cfg, k=5)
        except Exception:
            vec_hits = []                              # 问题向量化失败不致命，退单路
    kw_hits = bm25_search(query, bm25, chunks, k=5)
    if not vec_hits:                                   # 无向量路：直接用关键词名次
        return kw_hits[:k]
    rrf = {}
    for rank, (idx, _) in enumerate(vec_hits):         # 向量路按名次计分
        rrf[idx] = rrf.get(idx, 0) + 1 / (60 + rank + 1)
    for rank, (idx, _) in enumerate(kw_hits):          # 关键词路按名次计分
        rrf[idx] = rrf.get(idx, 0) + 1 / (60 + rank + 1)
    top = sorted(rrf.items(), key=lambda x: -x[1])[:k]
    return [(idx, score) for idx, score in top]
