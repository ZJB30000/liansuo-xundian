"""测试公共夹具：假向量、假流式 LLM、独立临时库、登录令牌。

与 src/my_tool 结构对应：接口层测试共用这里，保证外部依赖全部离线。
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from my_tool.agents import llm_gateway  # noqa: E402
from my_tool.api.app import create_app  # noqa: E402
from my_tool.services import kb_service  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# 假流式 LLM 会把收到的完整提示词记录在这里，供“模板立即生效”用例断言
CAPTURED_PROMPTS: list = []
# 假单步 Agent 会把收到的完整消息列表记录在这里，供 Agent 循环用例断言
CAPTURED_AGENT_MESSAGES: list = []


@pytest.fixture(autouse=True)
def _clear_captured():
    """每个测试清空捕获，避免串扰。"""
    CAPTURED_PROMPTS.clear()
    CAPTURED_AGENT_MESSAGES.clear()
    yield


def _fake_embed(texts, cfg=None):
    """确定性假向量：相同文本得到相同向量。"""
    rng = np.random.RandomState(42)
    v = rng.rand(len(texts), 16).astype("float32")
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def _fake_stream(prompt, chat_cfg):
    """假流式回答：三段输出，并把提示词记入 CAPTURED_PROMPTS。"""
    CAPTURED_PROMPTS.append(prompt)
    yield "按制度要求，"
    yield "留样必须冷藏并上锁，"
    yield "钥匙由店长保管。"


# ============================================================
# 阶段4：假单步 Agent（Function Calling）
# ============================================================
class FakeToolCall:
    """OpenAI tool_call 的最小替身：id / function.name / function.arguments。"""

    def __init__(self, name: str, arguments: dict):
        self.id = "call_" + name
        self.function = SimpleNamespace(
            name=name, arguments=__import__("json").dumps(arguments, ensure_ascii=False))

    def model_dump(self):
        return {"id": self.id, "type": "function",
                "function": {"name": self.function.name, "arguments": self.function.arguments}}


class FakeAgentMsg:
    """assistant 消息替身：要么带 tool_calls，要么带最终 content。"""

    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


def fake_agent_step_fn(decide):
    """按 decide(messages) 动态决策的假单步模型；decide 返回
    ("tool", name, args) / ("answer", text) / ("boom",)。"""
    def _step(messages, chat_cfg, tools):
        CAPTURED_AGENT_MESSAGES.append(messages)
        action = decide(messages)
        if action[0] == "answer":
            return FakeAgentMsg(content=action[1])
        if action[0] == "boom":
            raise RuntimeError("模拟模型不支持 tools 参数")
        return FakeAgentMsg(tool_calls=[FakeToolCall(action[1], action[2])])
    return _step


def last_tool_result(messages: list):
    """取消息列表里最近一条 role=tool 的结果（假模型决策依据）。"""
    for m in reversed(messages):
        if m.get("role") == "tool":
            return __import__("json").loads(m["content"])
    return None


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """独立 app + 独立 SQLite；启动事件会建库、播种账号与示例制度。"""
    monkeypatch.setenv("CHAT_DB", str(tmp_path / "chat.db"))   # 测试库隔离
    monkeypatch.setattr(kb_service, "embed_texts", _fake_embed)          # 检索侧向量化离线
    monkeypatch.setattr(llm_gateway, "embed_texts", _fake_embed)         # 索引构建向量化离线
    monkeypatch.setattr(llm_gateway, "llm_chat_stream", _fake_stream)    # LLM 离线
    monkeypatch.setattr(
        llm_gateway, "load_config",
        lambda: ({"base_url": "x", "model": "m", "api_key": "k"},
                 {"base_url": "x", "model": "e", "api_key": "k"}))
    with TestClient(create_app()) as c:      # with 触发 startup：建库+播种
        yield c


def login(client: TestClient, username: str, password: str) -> str:
    """登录拿 token 的快捷函数。"""
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["token"]


@pytest.fixture()
def staff_token(client):
    """员工令牌（zhangsan）。"""
    return login(client, "zhangsan", "123456")


@pytest.fixture()
def admin_token(client):
    """管理员令牌（admin）。"""
    return login(client, "admin", "admin123")


def auth(token: str) -> dict:
    """构造 Authorization 头。"""
    return {"Authorization": f"Bearer {token}"}


def parse_sse(text: str) -> list:
    """把 SSE 文本解析成 [(event, data_dict), ...]，供断言事件顺序。"""
    out = []
    for frame in text.strip().split("\n\n"):
        event, data = None, None
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            if line.startswith("data:"):
                data = __import__("json").loads(line[5:].strip())
        out.append((event, data))
    return out
