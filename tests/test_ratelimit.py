"""阶段5（提示词9）同用户并发限流测试：闸门语义 + 接口 429 行为。

背景：glm-4-flash 免费额度，单账号并发刷请求会烧光额度——
超限直接 429 拒绝不排队（SSE 长连接排队只会积压）。
"""

import pytest
from my_tool.api import chat as chat_module
from my_tool.api.ratelimit import PerUserRateLimiter
from tests.conftest import auth


# ============================================================
# 闸门语义单测
# ============================================================
def test_acquire_within_limit():
    lim = PerUserRateLimiter(max_concurrent=2)
    assert lim.acquire("u1") and lim.acquire("u1")   # 到 2 个为止都放行


def test_acquire_rejects_when_full():
    lim = PerUserRateLimiter(max_concurrent=2)
    lim.acquire("u1"); lim.acquire("u1")
    assert not lim.acquire("u1")                     # 第 3 个被拒
    assert lim.acquire("u2")                         # 别的用户不受影响


def test_release_frees_slot():
    lim = PerUserRateLimiter(max_concurrent=1)
    lim.acquire("u1")
    assert not lim.acquire("u1")
    lim.release("u1")
    assert lim.acquire("u1")                         # 归还后可再拿
    lim.release("u1")
    assert "u1" not in lim._counts                   # 归零清键，字典不膨胀


# ============================================================
# 接口集成：超限 429，正常请求不受影响
# ============================================================
def test_chat_returns_429_when_limit_reached(client, staff_token, monkeypatch):
    """把上限调成 0 → 任何 /api/chat 请求都 429 RATE_LIMITED。"""
    monkeypatch.setattr(chat_module, "rate_limiter", PerUserRateLimiter(max_concurrent=0))
    res = client.post("/api/chat",
                      json={"question": "留样要留多少克？", "agent_mode": False},
                      headers=auth(staff_token))
    assert res.status_code == 429
    assert res.json()["code"] == "RATE_LIMITED"


def test_chat_sequential_requests_not_blocked(client, staff_token):
    """顺序请求（流结束后归还额度）不触发限流——默认上限 2，逐条发 3 条全 200。"""
    for _ in range(3):
        res = client.post("/api/chat",
                          json={"question": "留样冰箱要上锁吗？", "agent_mode": False},
                          headers=auth(staff_token))
        assert res.status_code == 200, res.text
    # 流结束（TestClient 消费完整个响应）后额度已归还
    assert chat_module.rate_limiter._counts == {}
