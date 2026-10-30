"""阶段2/3 员工问答接口测试：登录、SSE 事件顺序、来源、落库、错误契约。

LLM 与向量全部替换为假实现（见 conftest），不访问真实大模型。
"""

import json
import os
import sqlite3

import pytest  # noqa: F401
from openai import APITimeoutError

from my_tool.agents import llm_gateway
from my_tool.api import db
from tests.conftest import auth, parse_sse


def test_home_serves_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "连锁巡店助手" in res.text


def test_health_ready(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == "OK"
    assert body["kb_chunks"] >= 5                 # 启动时已播种示例制度
    assert "巡店制度示例.md" in body["kb_docs"][0]


def test_chat_requires_login(client):
    """阶段3：未登录调 /api/chat → 401 中文错误。"""
    res = client.post("/api/chat", json={"question": "留样冰箱要上锁吗"})
    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHORIZED"


def test_chat_stream_happy_path(client, staff_token):
    res = client.post("/api/chat", json={"question": "留样冰箱要上锁吗"}, headers=auth(staff_token))
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(res.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "session"                  # session 开头
    assert kinds[-1] == "done"                    # done 结尾
    for need in ("status", "delta", "sources"):
        assert need in kinds

    session_id = events[0][1]["session_id"]
    assert session_id
    assert any("正在检索巡店制度" in d["text"] for e, d in events if e == "status")
    answer = "".join(d["text"] for e, d in events if e == "delta")
    assert "上锁" in answer
    src = [d for e, d in events if e == "sources"][0]["items"][0]
    assert src["doc"] == "巡店制度示例.md"          # 来源带文件名
    assert len(src["snippet"]) > 0                  # 来源带片段


def test_chat_prompt_uses_template_and_suffix(client, staff_token):
    """系统提示词 = 数据库模板 + 不可违背的输出约束。"""
    client.post("/api/chat", json={"question": "留样要多少克"}, headers=auth(staff_token))
    from tests.conftest import CAPTURED_PROMPTS
    assert len(CAPTURED_PROMPTS) == 1
    assert "巡店制度" in CAPTURED_PROMPTS[0]        # 默认模板（巡店口径）
    assert "输出约束" in CAPTURED_PROMPTS[0]        # 红线后缀已拼接


def test_chat_persists_messages(client, staff_token):
    client.post("/api/chat", json={"question": "留样冰箱要上锁吗"}, headers=auth(staff_token))
    conn = sqlite3.connect(str(db.db_path()))
    rows = conn.execute("SELECT role, content, sources_json FROM messages ORDER BY id").fetchall()
    conn.close()
    assert rows[0][0] == "user" and "留样" in rows[0][1]                 # 用户消息已落库
    assert rows[1][0] == "assistant" and "巡店制度示例.md" in rows[1][2]  # 助手消息含来源


def test_recent_messages_trims_to_12(client, tmp_path):
    sid = db.create_conversation()
    for i in range(14):
        db.add_message(sid, "user", f"问题{i}")
    history = db.recent_messages(sid, 12)     # 只取最近 12 条（6 轮）
    assert len(history) == 12
    assert history[-1]["content"] == "问题13"


def test_empty_question(client, staff_token):
    res = client.post("/api/chat", json={"question": "   "}, headers=auth(staff_token))
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "EMPTY_QUESTION"
    assert body["message"] == "问题不能为空"


def test_question_too_long(client, staff_token):
    res = client.post("/api/chat", json={"question": "长" * 501}, headers=auth(staff_token))
    assert res.status_code == 400
    assert res.json()["code"] == "QUESTION_TOO_LONG"


def test_session_not_found(client, staff_token):
    res = client.post("/api/chat", json={"session_id": "not-exist", "question": "留样要多少克"},
                      headers=auth(staff_token))
    assert res.status_code == 404
    body = res.json()
    assert body["code"] == "SESSION_NOT_FOUND"
    assert "会话不存在" in body["message"]


def test_bad_json(client, staff_token):
    res = client.post("/api/chat", content="不是json",
                      headers={"Content-Type": "application/json", **auth(staff_token)})
    assert res.status_code == 400
    assert res.json()["code"] == "BODY_NOT_JSON"


def test_llm_timeout_returns_sse_error(client, staff_token, monkeypatch):
    """大模型超时 → SSE error 事件（中文），而不是 HTTP 错误。"""
    def raise_timeout(prompt, cfg):
        raise APITimeoutError(request=__import__("httpx").Request("POST", "http://test"))
    monkeypatch.setattr(llm_gateway, "llm_chat_stream", raise_timeout)

    res = client.post("/api/chat", json={"question": "晨检记录谁负责"}, headers=auth(staff_token))
    events = parse_sse(res.text)
    err = [d for e, d in events if e == "error"][0]
    assert err["code"] == "LLM_TIMEOUT"
    assert err["message"] == "大模型响应超时，请重试"
