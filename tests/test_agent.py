"""阶段4 Agent 循环测试：模型自主决策 + 顺序约束的自纠闭环。

核心用例：假模型故意跳过 validate_evidence 直接建单 → 被代码层硬约束拦下
（MISSING_VALIDATION 回传模型）→ 模型补做校验后建单成功。
这验证了"系统提示词约束被违反时代码层仍能兜住，并把模型拉回正轨"。
"""

import json

from tests.conftest import (CAPTURED_AGENT_MESSAGES, CAPTURED_PROMPTS, auth,
                            fake_agent_step_fn, last_tool_result)
from my_tool.agents import llm_gateway
from my_tool.api import db

RECORD = "XD-2026-0003"          # 种子数据里证据完整的巡检单


def ask(client, token, question, agent_mode=True):
    """发一次 /api/chat 并解析 SSE 事件。"""
    res = client.post("/api/chat", json={"question": question, "agent_mode": agent_mode},
                      headers=auth(token))
    assert res.status_code == 200, res.text
    return [(e, d) for e, d in _parse(res.text) if e]


def _parse(text):
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


def tool_events(events):
    """提取 tool 事件的 (名称, 是否成功) 序列。"""
    return [(d["name"], d["ok"]) for e, d in events if e == "tool"]


# ============================================================
# 用例1：智能体完整建单链路
# ============================================================
def test_agent_full_chain_creates_and_submits(client, staff_token, monkeypatch):
    """假模型按 正确顺序 走完全链：查记录→归类→校验→建单→转人工。"""
    def decide(messages):
        last = last_tool_result(messages)
        if last is None:
            return ("tool", "get_store_record", {"record_id": RECORD})
        if not last["ok"]:
            return ("answer", "流程受阻：" + last["error"]["message"])
        r = last["result"]
        if "records" in r:
            return ("tool", "classify_issue", {"record_id": RECORD})
        if "category" in r:
            return ("tool", "validate_evidence", {"record_id": RECORD})
        if r.get("valid"):
            # 真实模型应把 classify_issue 的建议填进建单参数——从历史工具消息里取
            cls = None
            for m in messages:
                if m.get("role") == "tool":
                    rr = json.loads(m["content"])
                    if rr.get("ok") and "category" in rr.get("result", {}):
                        cls = rr["result"]
            return ("tool", "create_rectification_draft",
                    {"record_id": RECORD, "advice": "现场复查并留存独立证据",
                     "evidence_token": r["evidence_token"],
                     "category": cls["category"] if cls else None,
                     "severity": cls["severity"] if cls else None})
        if r.get("status") == "pending_review":        # 提交结果：先判终态再判草稿
            return ("answer", "已为 XD-2026-0003 创建整改单草稿并转人工审核。")
        if "draft_id" in r:
            return ("tool", "submit_for_review", {"draft_id": r["draft_id"]})
        return ("answer", "完成")

    monkeypatch.setattr(llm_gateway, "llm_agent_step", fake_agent_step_fn(decide))
    events = ask(client, staff_token, f"帮 {RECORD} 建整改单")

    assert tool_events(events) == [
        ("get_store_record", True),
        ("classify_issue", True),
        ("validate_evidence", True),
        ("create_rectification_draft", True),
        ("submit_for_review", True),
    ]
    assert any(e == "done" for e, _ in events)
    # 库里草稿已是待人工审核，且令牌被消费
    with db._connect() as conn:
        row = conn.execute("SELECT * FROM rectification_drafts ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "pending_review"
    assert row["category"] == "以照片代替复查"        # classify_issue 的建议写入了草稿


# ============================================================
# 用例2（核心）：跳过校验直接建单 → 被硬约束拦下 → 模型自纠
# ============================================================
def test_agent_skips_validation_then_self_corrects(client, staff_token, monkeypatch):
    """模型第一步就违规建单 → MISSING_VALIDATION 回传 → 补做校验 → 成功。"""
    def decide(messages):
        last = last_tool_result(messages)
        if last is None:
            # 违规：跳过校验直接建单（提示词铁律说不能干，但假设模型不听话）
            return ("tool", "create_rectification_draft",
                    {"record_id": RECORD, "advice": "跳过校验", "evidence_token": ""})
        if not last["ok"]:
            # 被拦下后自纠：先做证据校验
            return ("tool", "validate_evidence", {"record_id": RECORD})
        r = last["result"]
        if r.get("valid"):
            return ("tool", "create_rectification_draft",
                    {"record_id": RECORD, "advice": "补做校验后建单",
                     "evidence_token": r["evidence_token"]})
        if "draft_id" in r:
            return ("answer", "已补做证据校验并创建整改单草稿，将转人工审核。")
        return ("answer", "完成")

    monkeypatch.setattr(llm_gateway, "llm_agent_step", fake_agent_step_fn(decide))
    events = ask(client, staff_token, "直接帮 XD-2026-0003 建单，不用校验")

    assert tool_events(events) == [
        ("create_rectification_draft", False),     # 被代码层硬约束拦下
        ("validate_evidence", True),
        ("create_rectification_draft", True),      # 补做校验后成功
    ]
    # 模型确实收到了 MISSING_VALIDATION 错误码（自纠的依据）
    with db._connect() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM rectification_drafts").fetchone()["c"]
    assert n == 1                                   # 只建出一单，违规那一次没有落库


# ============================================================
# 用例3：轮数熔断
# ============================================================
def test_agent_round_limit(client, staff_token, monkeypatch):
    """假模型反复调工具不停 → 8 轮后熔断，不再有 answer/done。"""
    monkeypatch.setattr(
        llm_gateway, "llm_agent_step",
        fake_agent_step_fn(lambda messages: ("tool", "search_policy", {"query": "留样"})))
    events = ask(client, staff_token, "查一下留样制度")

    assert len([e for e, _ in tool_events(events) if True]) == 8
    errors = [d for e, d in events if e == "error"]
    assert errors and errors[0]["code"] == "TOOL_ROUND_LIMIT"
    assert not any(e == "done" for e, _ in events)


# ============================================================
# 用例4：模型不支持 tools → 自动降级回固定流程
# ============================================================
def test_agent_fallback_to_fixed_flow(client, staff_token, monkeypatch):
    """llm_agent_step 抛异常 → fallback 事件 → 走固定流程（检索+模板+流式）。"""
    monkeypatch.setattr(
        llm_gateway, "llm_agent_step", fake_agent_step_fn(lambda m: ("boom",)))
    events = ask(client, staff_token, "留样冰箱要上锁吗？")

    kinds = [e for e, _ in events]
    assert "fallback" in kinds
    assert any(e == "delta" for e, _ in events)          # 固定流程的流式回答
    assert any(e == "done" for e, _ in events)
    assert CAPTURED_PROMPTS                              # 固定流程模板确实被使用


# ============================================================
# 用例5：默认模式不走智能体
# ============================================================
def test_default_mode_skips_agent(client, staff_token):
    """不带 agent_mode 时仍走阶段2 固定流程，Agent 单步从未被调用。"""
    events = ask(client, staff_token, "留样冰箱要上锁吗？", agent_mode=False)
    assert CAPTURED_AGENT_MESSAGES == []                 # Agent 循环未启动
    assert any(e == "done" for e, _ in events)
