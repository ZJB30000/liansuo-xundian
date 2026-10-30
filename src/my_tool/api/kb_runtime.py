"""知识库运行时：构建并驻留进程内的制度索引（health 与 chat 共用）。"""

import os
import threading
from pathlib import Path

from rank_bm25 import BM25Okapi

from my_tool.agents import llm_gateway
from my_tool.services import kb_service

# 仓库根目录：src/my_tool/api/kb_runtime.py → 上溯 3 级
_REPO_ROOT = Path(__file__).resolve().parents[3]


class KnowledgeBase:
    """进程内驻留的知识库：切片（带来源）、向量索引、BM25 索引，构建一次反复检索。"""

    def __init__(self, chunks: list, texts: list, index, bm25, embed_cfg: dict):
        self.chunks = chunks        # [{"text","doc"}]：带来源文件名
        self.texts = texts          # [str]：纯文本，供检索
        self.index = index          # FAISS 向量索引
        self.bm25 = bm25            # BM25 关键词索引
        self.embed_cfg = embed_cfg  # 问题向量化配置


def build_kb() -> KnowledgeBase:
    """从 data/ 目录读取全部制度 md，切片并建双路索引。"""
    kb_dir = Path(os.environ.get("KB_DIR", str(_REPO_ROOT / "data")))
    md_files = sorted(kb_dir.glob("*.md"))
    if not md_files:
        raise FileNotFoundError(f"{kb_dir} 下没有制度 markdown 文件")

    chunks = kb_service.load_kb_documents([str(p) for p in md_files])
    texts = [c["text"] for c in chunks]
    _, embed_cfg = llm_gateway.load_config()
    index, _ = kb_service.build_vector_index(texts, embed_cfg)
    bm25 = BM25Okapi([kb_service.tokenize(t) for t in texts])
    return KnowledgeBase(chunks, texts, index, bm25, embed_cfg)


def init_kb_state(app) -> None:
    """在应用上初始化知识库状态槽位与并发锁。"""
    app.state.kb = None
    app.state.kb_lock = threading.Lock()


def ensure_kb(app) -> None:
    """懒加载知识库（带锁防并发重复构建）；失败时 kb 保持 None，
    调用方捕获异常返回 KB_NOT_READY，服务本体不崩。"""
    if app.state.kb is not None:
        return
    with app.state.kb_lock:
        if app.state.kb is None:
            app.state.kb = build_kb()
