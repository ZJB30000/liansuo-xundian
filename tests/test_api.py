"""阶段2 接口层测试：不访问真实大模型，LLM 与向量全部替换为假实现。

覆盖：健康检查、首页、SSE 事件顺序、来源展示、消息落库、历史截取、
错误契约（空问题/超长/会话不存在/非法JSON/大模型超时）。
与 src/my_tool/api 结构对应。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from openai import APITimeoutError  # noqa: E402

from my_tool.agents import llm_gateway  # noqa: E402
from my_tool.api import db  # noqa: E402
from my_tool.api.app import create_app  # noqa: E402
from my_tool.services import kb_service  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def _fake_embed(texts, cfg=None):
    """确定性假向量：相同文本得到相同向量。"""
    rng = np.random.RandomState(42)
    v = rng.rand(len(texts), 16).astype("float32")
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def _fake_stream(prompt, chat_cfg):
    """假流式回答：三段输出，模拟 LLM 逐段生成。"""
    yield "按制度要求，"
    yield "留样必须冷藏并上锁，"
    yield "钥匙由店长保管。"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """每个测试独立的 app + 独立 SQLite，全部外部依赖替换为假实现。"""
    monkeypatch.setenv("CHAT_DB", str(tmp_path / "chat.db"))      # 测试库隔离
    monkeypatch.setenv("KB_DIR", DATA_DIR)                        # 指向真实示例制度
    monkeypatch.setattr(kb_service, "embed_texts", _fake_embed)   # 向量化离线
    monkeypatch.setattr(llm_gateway, "llm_chat_stream", _fake_stream)  # LLM 离线
    monkeypatch.setattr(
        llm_gateway, "load_config",
        lambda: ({"base_url": "x", "model": "m", "api_key": "k"},
                 {"base_url": "x", "model": "e", "api_key": "k"}))
    return TestClient(create_app())


def _parse_sse(text):
    """把 SSE 文本解析成 [(event, data_dict), ...]，供断言事件顺序。"""
    out = []
    for frame in text.strip().split("\n\n"):
        event, data = None, None
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            if line.startswith("data:"):
                data = json.loads(line[5:].strip())
        out.append((event, data))
    return out


def test_home_serves_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "连锁巡店助手" in res.text


def test_health_ready(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == "OK"
    assert body["kb_chunks"] >= 5
    assert "巡店制度示例.md" in body["kb_docs"][0]


def test_chat_stream_happy_path(client):
    res = client.post("/api/chat", json={"question": "留样冰箱要上锁吗"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(res.text)
    kinds = [e for e, _ in events]
    # 事件顺序：session 开头，done 结尾，中间有 status/delta/sources
    assert kinds[0] == "session"
    assert kinds[-1] == "done"
    for need in ("status", "delta", "sources"):
        assert need in kinds

    # 校验各事件内容
    session_id = events[0][1]["session_id"]
    assert session_id
    assert any("正在检索巡店制度" in d["text"] for e, d in events if e == "status")
    answer = "".join(d["text"] for e, d in events if e == "delta")
    assert "上锁" in answer
    src = [d for e, d in events if e == "sources"][0]["items"][0]
    assert src["doc"] == "巡店制度示例.md"          # 来源带文件名
    assert len(src["snippet"]) > 0                  # 来源带片段


def test_chat_persists_messages(client):
    client.post("/api/chat", json={"question": "留样冰箱要上锁吗"})
    import sqlite3
    conn = sqlite3.connect(str(db.db_path()))
    rows = conn.execute("SELECT role, content, sources_json FROM messages ORDER BY id").fetchall()
    conn.close()
    assert rows[0][0] == "user" and "留样" in rows[0][1]     # 用户消息已落库
    assert rows[1][0] == "assistant" and "巡店制度示例.md" in rows[1][2]  # 助手消息含来源


def test_recent_messages_trims_to_12(client, tmp_path):
    sid = db.create_conversation()
    for i in range(14):                       # 14 条消息
        db.add_message(sid, "user", f"问题{i}")
    history = db.recent_messages(sid, 12)     # 只取最近 12 条
    assert len(history) == 12
    assert history[-1]["content"] == "问题13"  # 正序，最后一条最新


def test_empty_question(client):
    res = client.post("/api/chat", json={"question": "   "})
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "EMPTY_QUESTION"
    assert body["message"] == "问题不能为空"


def test_question_too_long(client):
    res = client.post("/api/chat", json={"question": "长" * 501})
    assert res.status_code == 400
    assert res.json()["code"] == "QUESTION_TOO_LONG"


def test_session_not_found(client):
    res = client.post("/api/chat", json={"session_id": "not-exist", "question": "留样要多少克"})
    assert res.status_code == 404
    body = res.json()
    assert body["code"] == "SESSION_NOT_FOUND"
    assert "会话不存在" in body["message"]


def test_bad_json(client):
    res = client.post("/api/chat", content="不是json",
                      headers={"Content-Type": "application/json"})
    assert res.status_code == 400
    assert res.json()["code"] == "BODY_NOT_JSON"


def test_llm_timeout_returns_sse_error(client, monkeypatch):
    """大模型超时 → SSE error 事件（中文），而不是 HTTP 错误。"""
    def raise_timeout(prompt, cfg):
        raise APITimeoutError(request=__import__("httpx").Request("POST", "http://test"))
    monkeypatch.setattr(llm_gateway, "llm_chat_stream", raise_timeout)

    res = client.post("/api/chat", json={"question": "晨检记录谁负责"})
    events = _parse_sse(res.text)
    err = [d for e, d in events if e == "error"][0]
    assert err["code"] == "LLM_TIMEOUT"
    assert err["message"] == "大模型响应超时，请重试"
