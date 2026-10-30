"""提示词模板（智能体编排层 · ★核心复用模块）。

“AI 只建议、有依据才输出”红线在代码层的落点：
只依据检索到的制度片段回答、必须附出处、找不到依据就拒答。
阶段2 在阶段1 基础上扩展：支持多轮历史上下文、切片带来源文件名。
"""

PROMPT_RULES = """你是连锁巡店助手的制度问答助手。请严格遵守：
1. 只依据下面的制度片段和对话历史回答，禁止使用任何制度里没有的内容；
2. 回答末尾必须注明引用了哪几个片段，格式如：（依据：制度片段1、制度片段3）；
3. 如果制度片段不足以回答问题，直接回复“制度中未找到依据，请联系管理员补充”，不要猜测。"""

# 输出格式约束：无论管理员把模板改成什么，这两条红线不放开（阶段3 起拼在模板后）
RULE_SUFFIX = """\n输出约束（不可违背）：
- 回答末尾必须注明引用片段，格式：（依据：制度片段N）；
- 制度片段不足以回答时，回复：制度中未找到依据。"""


def _chunk_text(chunk) -> str:
    """兼容两种切片形态：阶段1的纯文本，或阶段2带来源的 {"text","doc"} 字典。"""
    if isinstance(chunk, dict):
        return chunk.get("text", "")
    return chunk


def _chunk_label(n: int, chunk) -> str:
    """切片在提示词里的标题；有来源文件名的切片标注《文件名》，回答出处更可信。"""
    if isinstance(chunk, dict) and chunk.get("doc"):
        return f"【制度片段{n} · 《{chunk['doc']}》】"
    return f"【制度片段{n}】"


def _history_block(history) -> str:
    """把最近几轮对话拼成提示词里的历史段；history 为空则返回空串。

    history 格式：[{"role": "user"/"assistant", "content": "..."}, ...]，按时间正序。
    """
    if not history:
        return ""
    lines = [f"{'用户' if m['role'] == 'user' else '助手'}：{m['content']}" for m in history]
    return "【历史对话】\n" + "\n".join(lines) + "\n\n"


def build_prompt(query: str, hits: list, chunks: list, history: list = None,
                 rules: str = None) -> str:
    """组装最终提示词：规则 → 历史对话（可选）→ 检索片段 → 问题。

    参数：
        query:   用户本次问题
        hits:    混合检索结果 [(切片下标, 得分), ...]
        chunks:  全部切片（纯文本或 {"text","doc"} 字典）
        history: 最近几轮对话（默认 None，阶段1 调用方式不受影响）
        rules:   系统提示词规则文本；缺省用代码内置版（阶段3 起服务端
                 会传入数据库里管理员维护的模板）
    """
    context = "\n\n".join(
        f"{_chunk_label(n, chunks[idx])}\n{_chunk_text(chunks[idx])}"
        for n, (idx, _) in enumerate(hits, 1)
    )
    return (
        f"{rules or PROMPT_RULES}\n\n"
        f"{_history_block(history)}"
        f"【制度片段】\n{context}\n\n"
        f"【问题】{query}"
    )
