"""提示词模板（智能体编排层 · ★核心复用模块）。

“AI 只建议、有依据才输出”红线在代码层的落点：
只依据检索到的制度片段回答、必须附出处、找不到依据就拒答。
阶段2 迁移为 agents/prompts/ 下的模板文件，由 /chat 复用。
"""

PROMPT_TEMPLATE = """你是连锁巡店系统的制度问答助手。请严格遵守：
1. 只依据下面的制度片段回答，禁止使用任何制度里没有的内容；
2. 回答末尾必须注明引用了哪几个片段，格式如：（依据：制度片段1、制度片段3）；
3. 如果制度片段不足以回答问题，直接回复“制度中未找到依据，请联系管理员补充”，不要猜测。

【制度片段】
{context}

【问题】{query}"""


def build_prompt(query: str, hits: list, chunks: list) -> str:
    """把检索到的 top 片段拼进提示词。

    参数：
        query: 用户问题
        hits:  混合检索结果，格式 [(切片下标, 得分), ...]
        chunks: 全部制度切片文本
    """
    context = "\n\n".join(
        f"【制度片段{n}】\n{chunks[idx]}" for n, (idx, _) in enumerate(hits, 1)
    )
    return PROMPT_TEMPLATE.format(context=context, query=query)
