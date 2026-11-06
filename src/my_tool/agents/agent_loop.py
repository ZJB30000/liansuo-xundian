"""Agent Harness（阶段5 / 提示词7）：模型决策 → 执行工具 → 结果回填 → 再决策。

Harness = 把"模型接入 + 工具注册表 + 执行循环 + 上下文 + 策略"打包的执行骨架：
- 模型接入：llm_gateway（glm-4-flash，OpenAI 兼容 function calling）；
- 工具注册表：tools.py 的 TOOL_SCHEMAS / run_tool（含证据令牌硬校验）；
- 执行循环：本模块 _harness_loop，最多 MAX_TOOL_ROUNDS 轮；
- 策略层（提示词7 新增）：
  1. 轮数熔断：单次会话最多 5 轮工具决策，超限强制终止转人工；
  2. 轮次日志：每执行一次工具落一行 agent_logs（工具名/参数/耗时/结果摘要），
     供阶段5 做归类一致率与节点耗时分析；
  3. HITL 强制中断（Human-in-the-loop）：模型要调 create_rectification_draft 时
     不直接执行——先挂起（agent_sessions 表 + 一次性确认令牌），把草稿内容
     以 confirm_request 事件推给员工；员工确认后由 /api/chat/confirm 恢复执行。
     确认相当于一次人工监督节点，恢复后轮数预算重新计（日志轮号仍连续）。

调用顺序约束的三层防线（阶段4 沿用）：
1. 系统提示词铁律（AGENT_SYSTEM_PROMPT）——软约束，引导模型走对流程；
2. 证据令牌硬校验（tools.py / db.consume_evidence_token）——跳过校验直接建单
   会被拒绝，错误码回传给模型后可自行补做校验；
3. 本模块轮数熔断——模型反复调工具不停时强制终止。

降级回退：免费模型不支持 tools 参数或返回空内容时，自动退回阶段2 的
固定流程问答（检索→模板→流式），服务不中断。
"""

import json
import time

from openai import APITimeoutError

from my_tool.agents import llm_gateway          # 模块属性访问，测试可替换
from my_tool.agents.prompts import build_prompt
from my_tool.agents.tools import TOOL_SCHEMAS, ToolContext, run_tool
from my_tool.api import db
from my_tool.services.kb_service import hybrid_search

MAX_TOOL_ROUNDS = 5          # 熔断：单次会话最多 5 轮工具决策（提示词7 硬性要求）
AGENT_SYSTEM_PROMPT = """你是连锁巡店的整改智能助手，可以调用工具完成查制度、查巡检记录、归类建议、证据校验、创建整改单草稿。请严格遵守：
1. 铁律：未拿到 validate_evidence 返回的 evidence_token 之前，绝对禁止调用 create_rectification_draft。若收到 MISSING_VALIDATION 或 EVIDENCE_TOKEN_INVALID 错误，先补做证据校验再继续；
2. evidence_token 必须原样使用工具返回值，禁止自行编造或修改任何工具参数；
3. 每次只调用一个工具，等结果返回后再决定下一步；下结论前先查制度与巡检记录，回答需注明制度依据；
4. 创建整改单草稿前，应先调用 classify_issue 获取归类与严重度建议，并把结果填入 category / severity 参数；系统会在你提出建单时请用户确认，请等待确认结果；
5. 全程只建议、不裁决：不扣罚、不判合格、不用整改照片代替复查；整改单只建草稿，最终由人工审核定级；
6. submit_for_review 成功后必须告知用户"已转人工审核"；无法完成时明确说明卡在哪一步、需要用户做什么。"""


def _summarize(name: str, result: dict) -> str:
    """工具结果的中文一句话摘要（前端 tool 事件与 agent_logs 通用）。"""
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


def _draft_preview(args: dict) -> dict:
    """把建单参数整理成给员工看的草稿预览（确认卡片内容）。"""
    rec = None
    record_id = str(args.get("record_id") or "").strip()
    if record_id:
        rows = db.find_records(business_id=record_id)
        rec = rows[0] if rows else None
    return {
        "record_id": record_id,
        "store_id": rec["store_id"] if rec else "",
        "description": rec["description"] if rec else "",
        "category": str(args.get("category") or "待归类"),
        "severity": str(args.get("severity") or "待定级"),
        "advice": str(args.get("advice") or ""),
    }


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


def _execute_and_log(name: str, args, ctx: ToolContext, session_id: str, round_no: int):
    """执行一个工具并落轮次日志，返回 (result, 耗时毫秒)。

    时间用 perf_counter 计（不受系统对时影响）；参数非法时同样落一行失败日志，
    保证阶段5 看到的决策轨迹是完整的。
    """
    t0 = time.perf_counter()
    if isinstance(args, dict):
        result = run_tool(name, args, ctx)
    else:
        result = {"ok": False,
                  "error": {"code": "INVALID_PARAM", "message": "工具参数不是合法 JSON 对象"}}
    cost_ms = int((time.perf_counter() - t0) * 1000)
    if session_id:
        db.log_agent_round(session_id, round_no, name, args if isinstance(args, dict) else {},
                           cost_ms, _summarize(name, result), ok=result["ok"])
    return result, cost_ms


def _harness_loop(messages: list, kb, chat_cfg: dict, ctx: ToolContext,
                  session_id: str, round_offset: int = 0, question: str = "",
                  history: list = None, rules: str = None):
    """Harness 核心循环（★提示词7）：决策 → 执行工具 → 结果回填 → 再决策。

    round_offset：日志轮号偏移。HITL 确认后恢复执行时传入挂起时的已完成轮数，
    让 agent_logs 的轮号跨确认周期连续；而轮数预算重新从 1 计——
    人工确认本身是一次监督介入，监督后循环重新计时（防失控的初衷不变）。

    事件：tool（工具过程）/ confirm_request（建单确认卡片）/ answer（最终回答）
    / status / error / fallback。
    """
    last_sources = []                       # 最近一次 search_policy 的片段，作为回答引用来源
    for i in range(1, MAX_TOOL_ROUNDS + 1):
        round_no = round_offset + i
        try:
            msg = llm_gateway.llm_agent_step(messages, chat_cfg, TOOL_SCHEMAS)
        except APITimeoutError:
            yield "error", {"code": "LLM_TIMEOUT", "message": "大模型响应超时，请重试"}
            return
        except Exception:
            # 模型不支持 tools 参数 / 接口异常：退回固定流程，服务不中断
            if question:
                yield "fallback", {"reason": "模型暂不支持工具调用，已切换到固定问答模式"}
                yield from _fixed_flow(question, kb, history, rules, chat_cfg)
            else:
                yield "error", {"code": "LLM_ERROR", "message": "大模型调用失败，请稍后重试"}
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
        name = tc.function.name
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = None

        # ---- HITL 强制中断（提示词7 第 3 条）：建单前必须员工确认 ----
        # 不做任何预检直接挂起：确认环节本身就是流程红线，参数缺失也拦在确认之后，
        # 由工具的硬校验把错误码回传给模型自纠。
        if name == "create_rectification_draft":
            messages.append({
                "role": "assistant",
                "content": getattr(msg, "content", None),
                "tool_calls": [c.model_dump() for c in calls],
            })
            if session_id:
                token = db.save_agent_pending(
                    session_id, round_no, messages, name,
                    args if isinstance(args, dict) else {},
                    _draft_preview(args) if isinstance(args, dict) else {}, ctx.user_id)
                yield "confirm_request", {
                    "token": token,
                    "preview": _draft_preview(args) if isinstance(args, dict) else {},
                    "message": "创建整改单草稿前需要您确认，确认后才会真正建单",
                }
            return

        # ---- 普通工具：执行 + 落日志 + 过程事件 ----
        result, _ = _execute_and_log(name, args, ctx, session_id, round_no)
        if name == "search_policy" and result.get("ok"):
            last_sources = [
                {"doc": it["doc"], "snippet": it["snippet"], "score": it["score"]}
                for it in result["result"].get("items", [])
            ]
        yield "tool", {"name": name, "ok": result["ok"],
                       "summary": _summarize(name, result)}

        # 工具结果回传模型：assistant(带 tool_calls) + role=tool，错误码让模型自纠
        messages.append({
            "role": "assistant",
            "content": getattr(msg, "content", None),
            "tool_calls": [c.model_dump() for c in calls],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": getattr(tc, "id", "") or "call_0",
            "content": json.dumps(result, ensure_ascii=False),
        })

    yield "error", {"code": "TOOL_ROUND_LIMIT",
                    "message": "工具调用轮次已达上限，请把需求描述得更具体后重试"}


def run_agent_loop(question: str, kb, chat_cfg: dict, ctx: ToolContext,
                   history: list = None, rules: str = None, session_id: str = None):
    """Agent 主入口（首问）：构建上下文后进入 Harness 循环。

    session_id 用于轮次日志归属（提示词7 第 2 条）；不传则只执行不落日志。
    """
    system = AGENT_SYSTEM_PROMPT
    if rules:
        system += "\n\n管理员补充要求：\n" + rules
    messages = [{"role": "system", "content": system}]
    for m in history or []:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": question})
    yield from _harness_loop(messages, kb, chat_cfg, ctx, session_id,
                             question=question, history=history, rules=rules)


def resume_agent_loop(pending: dict, approve: bool, chat_cfg: dict, kb, user_id: int):
    """HITL 恢复入口（提示词7 第 3 条后半段）：员工确认后继续执行。

    pending：db.get_agent_pending 取出的挂起会话行；approve：员工是否同意建单。
    - 同意：执行挂起的工具（正常落日志），结果回填模型继续循环；
    - 拒绝：不执行，把 USER_REJECTED 错误回填给模型——模型会向用户致歉收尾，
      绝不会建单；日志同样落一行（ok=0），审计能看到这次被拦截的调用。
    """
    messages = json.loads(pending["messages_json"])
    name = pending["tool_name"]
    args = json.loads(pending["tool_args_json"])
    session_id = pending["session_id"]
    round_no = pending["round"]
    ctx = ToolContext(kb, user_id)
    # 取挂起时 assistant.tool_calls[0] 的真实 id（真机是 call_xxx 随机串），
    # role=tool 消息的 tool_call_id 必须与之配对，部分接口会严格校验
    tc_id = "call_0"
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            tc_id = m["tool_calls"][0].get("id") or "call_0"
            break

    if approve:
        db.finish_agent_session(pending["confirm_token"], "confirmed")
        result, _ = _execute_and_log(name, args, ctx, session_id, round_no)
    else:
        db.finish_agent_session(pending["confirm_token"], "rejected")
        result = {"ok": False,
                  "error": {"code": "USER_REJECTED",
                            "message": "用户拒绝创建整改单草稿，请不要再次尝试建单，向用户致歉并说明可重新发起"}}
        if session_id:
            db.log_agent_round(session_id, round_no, name, args, 0,
                               "用户拒绝创建整改单草稿（HITL 拦截）", ok=False)

    yield "tool", {"name": name, "ok": result["ok"], "summary": _summarize(name, result)}
    messages.append({
        "role": "tool",
        "tool_call_id": tc_id,              # 与挂起时 assistant.tool_calls 的 id 对齐
        "content": json.dumps(result, ensure_ascii=False),
    })
    # 人工确认是监督节点：轮数预算重新计，日志轮号继续累加
    yield from _harness_loop(messages, kb, chat_cfg, ctx, session_id,
                             round_offset=round_no)
