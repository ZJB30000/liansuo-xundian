"""POST /api/chat 与 /api/chat/confirm：SSE 流式问答接口。

阶段3：JWT 鉴权 + 违规标记 + 库内模板；阶段4：agent_mode 智能体循环；
阶段5（提示词7）：/api/chat/confirm 确认恢复接口——建单前 Harness 挂起，
员工确认后从这里恢复执行（HITL：Human-in-the-loop）。
系统提示词从数据库读取（管理员可改，立即生效）；输出格式红线拼在模板后。
"""

import json
from collections.abc import Generator
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from openai import APITimeoutError

from my_tool.agents import llm_gateway
from my_tool.agents.agent_loop import resume_agent_loop, run_agent_loop
from my_tool.agents.prompts import RULE_SUFFIX, build_prompt
from my_tool.agents.tools import ToolContext
from my_tool.api import db, kb_store
from my_tool.api.errors import ApiError
from my_tool.api.kb_runtime import ensure_kb
from my_tool.api.moderation import scan
from my_tool.api.security import get_current_user
from my_tool.services.kb_service import hybrid_search

router = APIRouter(dependencies=[Depends(get_current_user)])   # 整组接口需登录

MAX_QUESTION = 500     # 问题长度上限（字）
HISTORY_ROUNDS = 6     # 携带最近 6 轮历史（12 条消息）
SNIPPET_LEN = 60       # 来源片段预览长度（字）


def _sse(event: str, data: dict) -> str:
    """按 SSE 协议格式化一帧事件，中文不转义。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _validate_question(raw) -> str:
    """问题校验：必须是字符串、非空、不超长，失败即抛中文错误。"""
    if not isinstance(raw, str):
        raise ApiError(400, "INVALID_PARAM", "question 必须是字符串")
    question = raw.strip()
    if not question:
        raise ApiError(400, "EMPTY_QUESTION", "问题不能为空")
    if len(question) > MAX_QUESTION:
        raise ApiError(400, "QUESTION_TOO_LONG", f"问题不能超过 {MAX_QUESTION} 字")
    return question


def _locate_session(raw, user_id: int) -> str:
    """处理会话ID：带ID则校验存在性，不带则新建（归属当前员工）。"""
    if raw is None:
        return db.create_conversation(user_id)
    if not isinstance(raw, str) or not raw:
        raise ApiError(400, "INVALID_PARAM", "session_id 格式不正确")
    if not db.has_conversation(raw):
        raise ApiError(404, "SESSION_NOT_FOUND", "会话不存在，请刷新页面重新开始")
    return raw


def _prepare(request: Request) -> tuple:
    """懒加载知识库 + 读取模型配置（/api/chat 与 /api/chat/confirm 共用）。"""
    try:
        ensure_kb(request.app)          # 首次访问/版本变化时构建知识库（懒加载）
    except Exception:
        raise ApiError(503, "KB_NOT_READY", "知识库未就绪，请检查 LLM_API_KEY 与制度文档")
    try:
        chat_cfg, _ = llm_gateway.load_config()
    except KeyError:
        raise ApiError(503, "LLM_NOT_CONFIGURED", "未配置 LLM_API_KEY 环境变量，无法调用大模型")
    except SystemExit:
        raise ApiError(503, "LLM_NOT_CONFIGURED", "未配置向量模型（EMBED_BASE_URL / EMBED_MODEL）")
    return request.app.state.kb, chat_cfg


def _event_stream(question: str, sid: str, kb, chat_cfg: dict, rules: str) -> Generator[str, None, None]:
    """SSE 主体：一帧帧 yield 给前端。"""
    yield _sse("session", {"session_id": sid})
    yield _sse("status", {"text": "正在检索巡店制度…"})

    # 混合检索（FAISS + BM25 + RRF，阶段1 核心复用）
    hits = hybrid_search(question, kb.index, kb.bm25, kb.texts, kb.embed_cfg)
    yield _sse("status", {"text": "正在生成回答…"})

    history = db.recent_messages(sid, HISTORY_ROUNDS * 2)
    prompt = build_prompt(question, hits, kb.chunks, history, rules=rules)

    answer = ""
    try:
        for delta in llm_gateway.llm_chat_stream(prompt, chat_cfg):
            answer += delta
            yield _sse("delta", {"text": delta})
    except APITimeoutError:
        yield _sse("error", {"code": "LLM_TIMEOUT", "message": "大模型响应超时，请重试"})
        return
    except Exception:
        yield _sse("error", {"code": "LLM_ERROR", "message": "大模型调用失败，请稍后重试"})
        return

    # 引用来源：文件名 + 片段预览 + 融合得分，回答底部展示
    sources = [
        {
            "doc": kb.chunks[idx]["doc"],
            "snippet": kb.texts[idx][:SNIPPET_LEN],
            "score": round(score, 4),
        }
        for idx, score in hits
    ]
    message_id = db.add_message(sid, "assistant", answer,
                                json.dumps(sources, ensure_ascii=False))
    yield _sse("sources", {"items": sources})
    yield _sse("done", {"message_id": message_id})


@router.post("/api/chat")
async def chat(request: Request, user: dict = Depends(get_current_user)):
    """对话入口：先完成全部校验（错误以 HTTP 状态码返回），再进入 SSE。

    agent_mode=true 走智能体循环（模型自主调工具：查制度/查记录/证据校验/建草稿），
    默认 false 走阶段2 固定流程问答。
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise ApiError(400, "BODY_NOT_JSON", "请求体不是合法 JSON")
    if not isinstance(body, dict):
        raise ApiError(400, "INVALID_PARAM", "请求体必须是 JSON 对象")

    question = _validate_question(body.get("question"))
    sid = _locate_session(body.get("session_id"), user["user_id"])
    agent_mode = body.get("agent_mode") is True

    kb, chat_cfg = _prepare(request)

    # 系统提示词：数据库里管理员维护的模板；固定流程再拼输出格式红线
    template = kb_store.get_prompt()

    # 用户消息落库：命中“规避检查”关键词则标记疑似违规（审计页标红）
    db.add_message(sid, "user", question, flagged=scan(question))

    if agent_mode:
        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(
            _agent_stream(question, sid, kb, chat_cfg, template, user["user_id"]),
            media_type="text/event-stream", headers=headers,
        )
    return StreamingResponse(
        _event_stream(question, sid, kb, chat_cfg, template + RULE_SUFFIX),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _relay_agent_events(events, sid: str) -> Generator[str, None, None]:
    """转发 Harness 事件流；answer 落库后按 2 字/帧模拟打字机输出。

    首问（run_agent_loop）与确认恢复（resume_agent_loop）共用——
    两条路径的 answer 都要落库，且 confirm_request / tool / error 原样透传。
    """
    for event, data in events:
        if event != "answer":
            yield _sse(event, data)
            continue
        text = data["text"]
        sources = data["sources"]
        message_id = db.add_message(sid, "assistant", text,
                                    json.dumps(sources, ensure_ascii=False))
        for i in range(0, len(text), 2):        # 非流式全量回答按 2 字/帧模拟打字机
            yield _sse("delta", {"text": text[i:i + 2]})
        yield _sse("sources", {"items": sources})
        yield _sse("done", {"message_id": message_id})


def _agent_stream(question: str, sid: str, kb, chat_cfg: dict, template: str,
                  user_id: int) -> Generator[str, None, None]:
    """智能体模式的 SSE 主体：启动 Harness 循环并转发事件。"""
    yield _sse("session", {"session_id": sid})
    yield _sse("status", {"text": "智能体已启动，正在决策…"})
    ctx = ToolContext(kb, user_id)
    history = db.recent_messages(sid, HISTORY_ROUNDS * 2)
    events = run_agent_loop(question, kb, chat_cfg, ctx,
                            history=history, rules=template, session_id=sid)
    yield from _relay_agent_events(events, sid)


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.post("/api/chat/confirm")
async def chat_confirm(request: Request, user: dict = Depends(get_current_user)):
    """HITL 确认恢复接口（提示词7 第 3 条）：建单前强制中断的另一半。

    Harness 挂起时下发了 confirm_token；员工点确认/拒绝后前端带 token 调本接口：
    - approve=true  → 执行挂起的建单工具，继续流式输出后续过程与最终回答；
    - approve=false → 不执行，把"用户拒绝"回填给模型，模型致歉收尾，绝不建单。
    校验失败用 HTTP 状态码（与 /api/chat 校验口径一致），通过后进入 SSE。
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise ApiError(400, "BODY_NOT_JSON", "请求体不是合法 JSON")
    if not isinstance(body, dict):
        raise ApiError(400, "INVALID_PARAM", "请求体必须是 JSON 对象")

    token = body.get("token")
    approve = body.get("approve")
    if not isinstance(token, str) or not token:
        raise ApiError(400, "INVALID_PARAM", "token 必须是非空字符串")
    if not isinstance(approve, bool):
        raise ApiError(400, "INVALID_PARAM", "approve 必须是布尔值")

    pending = db.get_agent_pending(token)
    if pending is None:
        raise ApiError(404, "CONFIRM_NOT_FOUND", "确认令牌不存在，可能已失效，请重新发起")
    if pending["user_id"] != user["user_id"]:
        raise ApiError(403, "CONFIRM_FORBIDDEN", "只能确认本人发起的整改单")
    if pending["status"] != "awaiting_confirm":
        raise ApiError(409, "CONFIRM_ALREADY", "该草稿已确认过，请勿重复操作")
    # 一次性令牌限时有效：超时未确认则作废，让流程状态可预期
    created = datetime.strptime(pending["created_at"], "%Y-%m-%d %H:%M:%S")
    age_min = (datetime.now() - created).total_seconds() / 60
    if age_min > db.CONFIRM_TTL_MIN:
        db.finish_agent_session(token, "expired")
        raise ApiError(410, "CONFIRM_EXPIRED", "确认已超时（10 分钟），请重新发起建单")

    kb, chat_cfg = _prepare(request)
    events = resume_agent_loop(pending, approve, chat_cfg, kb, user["user_id"])
    return StreamingResponse(_relay_agent_events(events, pending["session_id"]),
                             media_type="text/event-stream", headers=SSE_HEADERS)
