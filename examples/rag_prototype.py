"""连锁巡店与运营改进系统 · 阶段1 裸问答原型（纯命令行，无 Web）

对应《架构选型》第一周最小验证目标：验证「制度文档 → 切片向量化 → 混合检索 → LLM 带引用回答」这条 RAG 链路。

运行方式：
    pip install -r requirements.txt          # 含本脚本新增依赖
    export LLM_API_KEY="你的APIKey"           # 供应商见下方 PROVIDERS
    python examples/rag_prototype.py data/巡店制度示例.md

红线约束（与需求清单一致）：AI 只依据检索到的制度原文回答并附出处；
检索不到依据就明确拒答，不许编造；本脚本只做问答，不写任何业务状态。

后续阶段复用关系（★ 标注处为核心复用模块）：
    embed_texts / llm_chat        → 阶段2 agents/llm_gateway.py（供应商可替换网关）
    chunk_markdown                → 阶段2 services/kb_service.py（入库管道）
    build_vector_index/vector_search/bm25_search/hybrid_search → services/kb_service.py（检索）
    build_prompt                  → 阶段2 agents/prompts/（提示词模板）
    main()                        → 一次性 CLI，阶段2 由 FastAPI /chat 接口替代
"""

import os
import sys

import numpy as np
import faiss                      # 向量索引；课程项目用 faiss-cpu 即可
import jieba                      # 中文分词，BM25 检索需要
from rank_bm25 import BM25Okapi   # 轻量 BM25 实现
from openai import OpenAI         # 所有主流供应商都兼容 OpenAI 接口，换供应商只改配置

# ============================================================
# 0. 供应商配置：换供应商只改这一张表（或用环境变量覆盖），代码不动
#    ★核心复用模块：阶段2 迁入 config.py + agents/llm_gateway.py
# ============================================================
PROVIDERS = {
    # 硅基流动：同时提供对话与向量模型，默认选项
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "chat_model": "deepseek-ai/DeepSeek-V3",
        "embed_model": "BAAI/bge-m3",
    },
    # DeepSeek：只有对话模型，向量需另配 EMBED_BASE_URL
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "chat_model": "deepseek-chat",
        "embed_model": "",  # 留空 = 用 EMBED_* 环境变量另配
    },
    # 智谱 GLM
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "chat_model": "glm-4-flash",
        "embed_model": "embedding-3",
    },
}


def load_config():
    """从环境变量读取配置，返回对话与向量两套客户端参数。"""
    name = os.environ.get("LLM_PROVIDER", "siliconflow")   # 供应商名，默认硅基流动
    p = PROVIDERS[name]
    chat_cfg = {
        "base_url": os.environ.get("BASE_URL", p["base_url"]),
        "model": os.environ.get("CHAT_MODEL", p["chat_model"]),
        "api_key": os.environ["LLM_API_KEY"],              # 没设置就直接报错，尽早发现
    }
    embed_cfg = {
        "base_url": os.environ.get("EMBED_BASE_URL", chat_cfg["base_url"]),
        "model": os.environ.get("EMBED_MODEL", p["embed_model"]),
        "api_key": os.environ.get("EMBED_API_KEY", chat_cfg["api_key"]),
    }
    if not embed_cfg["model"]:
        raise SystemExit("该供应商未配置向量模型，请设置 EMBED_BASE_URL 和 EMBED_MODEL")
    return chat_cfg, embed_cfg


# ============================================================
# 1. LLM 与向量调用封装：全系统只允许这两个函数直接碰外部 API
#    ★核心复用模块：阶段2 原样迁入 agents/llm_gateway.py，
#    FastAPI 的所有 AI 功能都走它们，保证供应商可替换
# ============================================================
def llm_chat(prompt: str, chat_cfg: dict) -> str:
    """调用对话模型，返回回答文本。temperature=0 让回答稳定、便于验收。"""
    client = OpenAI(base_url=chat_cfg["base_url"], api_key=chat_cfg["api_key"])
    resp = client.chat.completions.create(
        model=chat_cfg["model"],
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    return resp.choices[0].message.content


def embed_texts(texts: list, embed_cfg: dict) -> np.ndarray:
    """把一批文本转向量，返回 numpy 矩阵（每行一个向量，已做 L2 归一化）。"""
    client = OpenAI(base_url=embed_cfg["base_url"], api_key=embed_cfg["api_key"])
    resp = client.embeddings.create(model=embed_cfg["model"], input=texts)
    vecs = np.array([d.embedding for d in resp.data], dtype="float32")
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.maximum(norms, 1e-10)   # 归一化后用内积即等价余弦相似度


# ============================================================
# 2. 文档切片：先按标题分节，再合并/切分到约 500 字（带 50 字重叠）
#    ★核心复用模块：阶段2 迁入 services/kb_service.py，
#    管理员上传制度文档后走同一段逻辑入库
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
# 3. 双路检索：FAISS 向量检索（语义）+ BM25（关键词），RRF 融合
#    ★核心复用模块：阶段2 迁入 services/kb_service.py，
#    /chat 接口与置信度判断都依赖这一段
# ============================================================
def build_vector_index(chunks: list, embed_cfg: dict):
    """对全部切片做向量化，建 FAISS 内积索引，返回 (索引, 切片向量)。"""
    matrix = embed_texts(chunks, embed_cfg)
    index = faiss.IndexFlatIP(matrix.shape[1])         # 内积索引：配合归一化向量即余弦相似度
    index.add(matrix)
    return index, matrix


def vector_search(query: str, index, chunk_vecs, chunks: list, embed_cfg: dict, k: int) -> list:
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


def hybrid_search(query: str, index, chunk_vecs, bm25, chunks: list, embed_cfg: dict, k: int = 3) -> list:
    """混合检索：两路各取前 5，再用 RRF（倒数排名融合）合并成 top3。
    RRF 公式：得分 = Σ 1/(60+名次)。只用名次不用原始分，
    避免向量余弦分和 BM25 分数值量级不同没法直接相加的问题。"""
    vec_hits = vector_search(query, index, chunk_vecs, chunks, embed_cfg, k=5)
    kw_hits = bm25_search(query, bm25, chunks, k=5)
    rrf = {}
    for rank, (idx, _) in enumerate(vec_hits):         # 向量路按名次计分
        rrf[idx] = rrf.get(idx, 0) + 1 / (60 + rank + 1)
    for rank, (idx, _) in enumerate(kw_hits):          # 关键词路按名次计分
        rrf[idx] = rrf.get(idx, 0) + 1 / (60 + rank + 1)
    top = sorted(rrf.items(), key=lambda x: -x[1])[:k]
    return [(idx, score) for idx, score in top]


# ============================================================
# 4. 提示词拼装：把检索片段作为唯一依据塞进提示词，并写死拒答规则
#    ★核心复用模块：阶段2 迁入 agents/prompts/rag_answer.md，
#    是“AI 只建议、有依据才输出”红线在代码层的落点
# ============================================================
def build_prompt(query: str, hits: list, chunks: list) -> str:
    ctx = "\n\n".join(
        f"【制度片段{n}】\n{chunks[idx]}" for n, (idx, _) in enumerate(hits, 1)
    )
    return f"""你是连锁巡店系统的制度问答助手。请严格遵守：
1. 只依据下面的制度片段回答，禁止使用任何制度里没有的内容；
2. 回答末尾必须注明引用了哪几个片段，格式如：（依据：制度片段1、制度片段3）；
3. 如果制度片段不足以回答问题，直接回复“制度中未找到依据，请联系管理员补充”，不要猜测。

【制度片段】
{ctx}

【问题】{query}"""


# ============================================================
# 5. 命令行入口：读文件 → 建索引 → 循环问答
#    一次性代码，阶段2 由 FastAPI /chat 接口替代，不迁移
# ============================================================
def main():
    if len(sys.argv) < 2:                              # 用法：python rag_prototype.py 制度文件.md
        raise SystemExit("用法: python examples/rag_prototype.py <制度markdown文件>")
    md_path = sys.argv[1]

    chat_cfg, embed_cfg = load_config()

    print(f"[1/3] 读取制度文档 {md_path} ...")
    with open(md_path, encoding="utf-8") as f:
        chunks = chunk_markdown(f.read())
    print(f"      切片完成：{len(chunks)} 片")

    print("[2/3] 向量化并建索引（FAISS + BM25）...")
    index, chunk_vecs = build_vector_index(chunks, embed_cfg)
    bm25 = BM25Okapi([tokenize(c) for c in chunks])

    print("[3/3] 就绪。输入巡店制度问题，回车提问，输入 q 退出。")
    while True:
        query = input("\n问题> ").strip()
        if not query:
            continue
        if query.lower() == "q":
            break
        hits = hybrid_search(query, index, chunk_vecs, bm25, chunks, embed_cfg)
        print(f"（检索到 {len(hits)} 个片段，RRF 得分：{[round(s, 4) for _, s in hits]}）")
        print("-" * 50)
        print(llm_chat(build_prompt(query, hits, chunks), chat_cfg))
        print("-" * 50)


if __name__ == "__main__":
    main()
