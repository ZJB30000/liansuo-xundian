"""POST /api/chat：SSE 流式问答接口（★阶段3 改造：登录鉴权+违规标记+库内模板）。

流程：JWT 鉴权 → 校验 → 定位/新建会话（归属员工）→ 存用户消息（触发疑似
违规扫描）→ 检索巡店制度 → 流式调大模型 → 存助手消息 → sources/done 事件。
系统提示词从数据库读取（管理员可改，立即生效）；输出格式红线拼在模板后。
"""

import json
from collections.abc import Generator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from openai import APITimeoutError

from my_tool.agents import llm_gateway
from my_tool.agents.agent_loop import run_agent_loop
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

    try:
        ensure_kb(request.app)          # 首次访问/版本变化时构建知识库（懒加载）
    except Exception:
        raise ApiError(503, "KB_NOT_READY", "知识库未就绪，请检查 LLM_API_KEY 与制度文档")
    kb = request.app.state.kb

    try:
        chat_cfg, _ = llm_gateway.load_config()
    except KeyError:
        raise ApiError(503, "LLM_NOT_CONFIGURED", "未配置 LLM_API_KEY 环境变量，无法调用大模型")
    except SystemExit:
        raise ApiError(503, "LLM_NOT_CONFIGURED", "未配置向量模型（EMBED_BASE_URL / EMBED_MODEL）")

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


def _agent_stream(question: str, sid: str, kb, chat_cfg: dict, template: str,
                  user_id: int) -> Generator[str, None, None]:
    """智能体模式的 SSE 主体：转发 agent_loop 事件；answer 落库后模拟打字机输出。"""
    yield _sse("session", {"session_id": sid})
    yield _sse("status", {"text": "智能体已启动，正在决策…"})
    ctx = ToolContext(kb, user_id)
    history = db.recent_messages(sid, HISTORY_ROUNDS * 2)
    for event, data in run_agent_loop(question, kb, chat_cfg, ctx,
                                      history=history, rules=template):
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
