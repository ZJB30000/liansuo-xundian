"""Agent 循环（阶段4）：模型自主决策调用工具，循环直到给出最终回答。

把"固定流程问答"升级为"自主决策智能体"：模型看到 TOOL_SCHEMAS 自行决定
调用什么工具；每轮把工具结果以 role=tool 回传，模型据此推进或自纠。

调用顺序约束的三层防线：
1. 系统提示词铁律（本模块 AGENT_SYSTEM_PROMPT）——软约束，引导模型走对流程；
2. 证据令牌硬校验（tools.py / db.consume_evidence_token）——跳过校验直接建单
   会被拒绝，错误码回传给模型后可自行补做校验；
3. 轮数熔断（MAX_TOOL_ROUNDS）——模型反复调工具不停时强制终止。

降级回退：免费模型不支持 tools 参数或返回空内容时，自动退回阶段2 的
固定流程问答（检索→模板→流式），服务不中断。
"""

import json

from openai import APITimeoutError

from my_tool.agents import llm_gateway          # 模块属性访问，测试可替换
from my_tool.agents.prompts import build_prompt
from my_tool.agents.tools import TOOL_SCHEMAS, ToolContext, run_tool
from my_tool.services.kb_service import hybrid_search

MAX_TOOL_ROUNDS = 8          # 熔断：单次问答最多 8 轮工具调用
AGENT_SYSTEM_PROMPT = """你是连锁巡店的整改智能助手，可以调用工具完成查制度、查巡检记录、归类建议、证据校验、创建整改单草稿。请严格遵守：
1. 铁律：未拿到 validate_evidence 返回的 evidence_token 之前，绝对禁止调用 create_rectification_draft。若收到 MISSING_VALIDATION 或 EVIDENCE_TOKEN_INVALID 错误，先补做证据校验再继续；
2. evidence_token 必须原样使用工具返回值，禁止自行编造或修改任何工具参数；
3. 每次只调用一个工具，等结果返回后再决定下一步；下结论前先查制度与巡检记录，回答需注明制度依据；
4. 创建整改单草稿前，应先调用 classify_issue 获取归类与严重度建议，并把结果填入 category / severity 参数；
5. 全程只建议、不裁决：不扣罚、不判合格、不用整改照片代替复查；整改单只建草稿，最终由人工审核定级；
6. submit_for_review 成功后必须告知用户"已转人工审核"；无法完成时明确说明卡在哪一步、需要用户做什么。"""


def _summarize(name: str, result: dict) -> str:
    """工具结果的中文一句话摘要（前端 tool 事件展示用）。"""
    if not result.get("ok"):
        return result["error"]["message"]
    r = result["result"]
    if name == "search_policy":
        return f"检索到 {len(r['items'])} 个制度片段"
    if name == "get_store_record":
        return f"查到 {len(r['records'])} 条巡检记录"
    if name == "classify_issue":
        return f"归类建议：{r['category']}（{r['severity']}，置信度 {r['confidence']}）"
    if name == "validate_evidence":
        return "证据校验通过，已签发一次性令牌" if r["valid"] else "证据不完整：" + "、".join(r["missing"])
    if name == "create_rectification_draft":
        return f"整改单草稿 #{r['draft_id']} 已创建（未派发）"
    if name == "submit_for_review":
        return f"草稿 #{r['draft_id']} 已转人工审核"
    return "完成"


def _fixed_flow(question: str, kb, history: list, rules: str, chat_cfg: dict):
    """降级回退：与阶段2 固定流程一致（检索→模板→流式），事件形态 (event, data)。"""
    hits = hybrid_search(question, kb.index, kb.bm25, kb.texts, kb.embed_cfg)
    yield "status", {"text": "正在检索巡店制度…"}
    prompt = build_prompt(question, hits, kb.chunks, history, rules=rules)
    yield "status", {"text": "正在生成回答…"}
    answer = ""
    try:
        for delta in llm_gateway.llm_chat_stream(prompt, chat_cfg):
            answer += delta
            yield "delta", {"text": delta}
    except APITimeoutError:
        yield "error", {"code": "LLM_TIMEOUT", "message": "大模型响应超时，请重试"}
        return
    except Exception:
        yield "error", {"code": "LLM_ERROR", "message": "大模型调用失败，请稍后重试"}
        return
    sources = [
        {"doc": kb.chunks[idx]["doc"], "snippet": kb.texts[idx][:60], "score": round(s, 4)}
        for idx, s in hits
    ]
    yield "answer", {"text": answer, "sources": sources}


def run_agent_loop(question: str, kb, chat_cfg: dict, ctx: ToolContext,
                   history: list = None, rules: str = None):
    """Agent 主循环，产出 (event, data) 事件流，由接口层包装成 SSE。

    事件：tool（每次工具调用的过程展示）/ delta（降级时的流式片段）/
    answer（最终完整回答与引用来源，接口层负责落库）/ status / error / fallback。
    """
    system = AGENT_SYSTEM_PROMPT
    if rules:
        system += "\n\n管理员补充要求：\n" + rules
    messages = [{"role": "system", "content": system}]
    for m in history or []:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": question})

    last_sources = []                       # 最近一次 search_policy 的片段，作为回答引用来源
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            msg = llm_gateway.llm_agent_step(messages, chat_cfg, TOOL_SCHEMAS)
        except APITimeoutError:
            yield "error", {"code": "LLM_TIMEOUT", "message": "大模型响应超时，请重试"}
            return
        except Exception:
            # 模型不支持 tools 参数 / 接口异常：退回固定流程，服务不中断
            yield "fallback", {"reason": "模型暂不支持工具调用，已切换到固定问答模式"}
            yield from _fixed_flow(question, kb, history, rules, chat_cfg)
            return

        calls = list(getattr(msg, "tool_calls", None) or [])
        if not calls:
            content = (getattr(msg, "content", "") or "").strip()
            if content:
                yield "answer", {"text": content, "sources": last_sources}
                return
            yield "fallback", {"reason": "模型未返回有效内容，已切换到固定问答模式"}
            yield from _fixed_flow(question, kb, history, rules, chat_cfg)
            return

        # 只执行第一个工具调用（系统提示词已约束"每次只调一个"，多传时忽略其余）
        tc = calls[0]
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = None
        if isinstance(args, dict):
            result = run_tool(tc.function.name, args, ctx)
            if tc.function.name == "search_policy" and result.get("ok"):
                last_sources = [
                    {"doc": it["doc"], "snippet": it["snippet"], "score": it["score"]}
                    for it in result["result"].get("items", [])
                ]
        else:
            result = {"ok": False,
                      "error": {"code": "INVALID_PARAM", "message": "工具参数不是合法 JSON 对象"}}
        yield "tool", {"name": tc.function.name, "ok": result["ok"],
                       "summary": _summarize(tc.function.name, result)}

        # 工具结果回传模型：assistant(带 tool_calls) + role=tool，错误码让模型自纠
        messages.append({
            "role": "assistant",
            "content": getattr(msg, "content", None),
            "tool_calls": [tc.model_dump() for tc in calls],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": getattr(tc, "id", "") or "call_0",
            "content": json.dumps(result, ensure_ascii=False),
        })

    yield "error", {"code": "TOOL_ROUND_LIMIT",
                    "message": "工具调用轮次已达上限，请把需求描述得更具体后重试"}
