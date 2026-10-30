"""阶段1 裸问答原型：巡店制度问答 CLI（纯命令行，无 Web）。

核心逻辑全部在 src/my_tool/ 下（对应老师框架的 src 源码目录）：
    src/my_tool/agents/llm_gateway.py   LLM/向量调用，供应商可配置
    src/my_tool/agents/prompts.py       带出处、可拒答的提示词
    src/my_tool/services/kb_service.py  切片 + FAISS/BM25 混合检索
本文件只是命令行编排，阶段2 由 FastAPI /chat 接口替代。

运行方式：
    export LLM_API_KEY="你的APIKey"
    python examples/rag_prototype.py data/巡店制度示例.md
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from rank_bm25 import BM25Okapi  # noqa: E402

from my_tool.agents.llm_gateway import load_config, llm_chat  # noqa: E402
from my_tool.agents.prompts import build_prompt  # noqa: E402
from my_tool.services.kb_service import (  # noqa: E402
    build_vector_index,
    chunk_markdown,
    hybrid_search,
    tokenize,
)


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
    index, _ = build_vector_index(chunks, embed_cfg)
    bm25 = BM25Okapi([tokenize(c) for c in chunks])

    print("[3/3] 就绪。输入巡店制度问题，回车提问，输入 q 退出。")
    while True:
        try:
            query = input("\n问题> ").strip()
        except EOFError:                       # 管道输入结束（如 echo xx | python ...）时优雅退出
            break
        if not query:
            continue
        if query.lower() == "q":
            break
        hits = hybrid_search(query, index, bm25, chunks, embed_cfg)
        print(f"（检索到 {len(hits)} 个片段，RRF 得分：{[round(s, 4) for _, s in hits]}）")
        print("-" * 50)
        print(llm_chat(build_prompt(query, hits, chunks), chat_cfg))
        print("-" * 50)


if __name__ == "__main__":
    main()
