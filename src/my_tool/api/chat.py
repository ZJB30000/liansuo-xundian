"""POST /api/chat：SSE 流式问答接口（★本阶段新增核心）。

流程：校验 → 定位/新建会话 → 存用户消息 → 检索巡店制度 →
流式调大模型（delta 事件）→ 存助手消息 → sources/done 事件。
任何环节出错都推 SSE 的 error 事件（中文），保证前端永远能渲染。
"""

import json
from collections.abc import Generator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from openai import APITimeoutError

from my_tool.agents import llm_gateway
from my_tool.agents.prompts import build_prompt
from my_tool.api import db
from my_tool.api.errors import ApiError
from my_tool.api.kb_runtime import ensure_kb
from my_tool.services.kb_service import hybrid_search

router = APIRouter()

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


def _locate_session(raw) -> str:
    """处理会话ID：带ID则校验存在性，不带则新建。"""
    if raw is None:
        return db.create_conversation()
    if not isinstance(raw, str) or not raw:
        raise ApiError(400, "INVALID_PARAM", "session_id 格式不正确")
    if not db.has_conversation(raw):
        raise ApiError(404, "SESSION_NOT_FOUND", "会话不存在，请刷新页面重新开始")
    return raw


def _event_stream(question: str, sid: str, kb, chat_cfg: dict) -> Generator[str, None, None]:
    """SSE 主体：一帧帧 yield 给前端。"""
    yield _sse("session", {"session_id": sid})
    yield _sse("status", {"text": "正在检索巡店制度…"})

    # 混合检索（FAISS + BM25 + RRF，阶段1 核心复用）
    hits = hybrid_search(question, kb.index, kb.bm25, kb.texts, kb.embed_cfg)
    yield _sse("status", {"text": "正在生成回答…"})

    history = db.recent_messages(sid, HISTORY_ROUNDS * 2)
    prompt = build_prompt(question, hits, kb.chunks, history)

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
async def chat(request: Request):
    """对话入口：先完成全部校验（错误以 HTTP 状态码返回），再进入 SSE。"""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise ApiError(400, "BODY_NOT_JSON", "请求体不是合法 JSON")
    if not isinstance(body, dict):
        raise ApiError(400, "INVALID_PARAM", "请求体必须是 JSON 对象")

    question = _validate_question(body.get("question"))
    sid = _locate_session(body.get("session_id"))

    try:
        ensure_kb(request.app)          # 首次访问时构建知识库（懒加载）
    except Exception:
        raise ApiError(503, "KB_NOT_READY", "知识库未就绪，请检查 LLM_API_KEY 与 data/ 制度文档")
    kb = request.app.state.kb

    try:
        chat_cfg, _ = llm_gateway.load_config()
    except KeyError:
        raise ApiError(503, "LLM_NOT_CONFIGURED", "未配置 LLM_API_KEY 环境变量，无法调用大模型")
    except SystemExit:
        raise ApiError(503, "LLM_NOT_CONFIGURED", "未配置向量模型（EMBED_BASE_URL / EMBED_MODEL）")

    db.add_message(sid, "user", question)   # 用户消息先落库，保证历史完整
    return StreamingResponse(
        _event_stream(question, sid, kb, chat_cfg),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
