"""知识库运行时（阶段3 重构）：从 SQLite 加载切片并构建内存索引。

与阶段2 的差异：切片来源从 data/*.md 改为 kb_chunks 表；
kb_version 变化（上传/删除）触发重建；新切片缺向量时批量补算并回写库。
"""

import json
import sys

from rank_bm25 import BM25Okapi

from my_tool.agents import llm_gateway
from my_tool.api import kb_store
from my_tool.services import kb_service


class KnowledgeBase:
    """进程内驻留的知识库：切片（带来源+版本号）、FAISS、BM25，构建一次反复检索。"""

    def __init__(self, chunks: list, texts: list, index, bm25, embed_cfg: dict, version: int):
        self.chunks = chunks        # [{"text","doc"}]：带来源文件名
        self.texts = texts          # [str]：纯文本，供检索
        self.index = index          # FAISS 向量索引
        self.bm25 = bm25            # BM25 关键词索引
        self.embed_cfg = embed_cfg  # 问题向量化配置
        self.version = version      # 构建时对应的 kb_version


def _build_kb() -> KnowledgeBase:
    """从库加载全部切片；缺向量的批量补算后回写，再建双路索引。

    向量接口不可用（免费额度不含 embedding、欠费 429、网络故障）时优雅降级：
    index 置 None，hybrid_search 自动退化为 BM25 关键词单路，服务不中断。
    """
    rows = kb_store.all_chunks()
    if not rows:
        raise FileNotFoundError("知识库为空，请管理员在后台上传制度文档")

    _, embed_cfg = llm_gateway.load_config()

    # 补算缺失向量：新上传/种子导入的切片此时才真正向量化。
    # 失败（429/余额不足/网络）不致命：跳过回写，走降级检索。
    missing = [r for r in rows if not r["embedding_json"]]
    if missing:
        try:
            vectors = llm_gateway.embed_texts([r["text"] for r in missing], embed_cfg)
        except Exception as e:
            print(f"[知识库] 向量化不可用，已退化为关键词检索：{e}", file=sys.stderr)
            vectors = None
        else:
            for row, vec in zip(missing, vectors):
                kb_store.save_embedding(row["id"], json.dumps([round(float(x), 6) for x in vec]))
                row["embedding_json"] = json.dumps([round(float(x), 6) for x in vec])

    chunks = [{"text": r["text"], "doc": r["doc"]} for r in rows]
    texts = [r["text"] for r in rows]

    # 任一切片缺向量就不能建 FAISS（索引下标会与 chunks 错位），整库退 BM25 单路
    embeddings = [r["embedding_json"] for r in rows]
    index = None
    if all(embeddings):
        import numpy as np
        import faiss
        mat = np.array([json.loads(e) for e in embeddings], dtype="float32")
        faiss.normalize_L2(mat)                  # 归一化后用内积近似余弦相似度
        index = faiss.IndexFlatIP(mat.shape[1])
        index.add(mat)

    bm25 = BM25Okapi([kb_service.tokenize(t) for t in texts])
    return KnowledgeBase(chunks, texts, index, bm25, embed_cfg, kb_store.get_kb_version())


def init_kb_state(app) -> None:
    """在应用上初始化知识库状态槽位与并发锁。"""
    app.state.kb = None
    app.state.kb_lock = None
    import threading
    app.state.kb_lock = threading.Lock()


def ensure_kb(app) -> None:
    """懒加载+按版本重建（带锁防并发）；失败时 kb 保持 None，
    调用方捕获异常返回 KB_NOT_READY，服务本体不崩。"""
    current = kb_store.get_kb_version()
    if app.state.kb is not None and app.state.kb.version == current:
        return
    with app.state.kb_lock:
        if app.state.kb is None or app.state.kb.version != current:
            app.state.kb = _build_kb()
