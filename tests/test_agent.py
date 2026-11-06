"""阶段4/5 Agent Harness 测试：自主决策 + 顺序约束 + HITL 强制确认 + 轮次日志。

阶段5（提示词7）新增行为：
- create_rectification_draft 调用前强制中断（confirm_request 事件），
  员工通过 /api/chat/confirm 确认后才真正执行（HITL：Human-in-the-loop）；
- 每次工具执行落一行 agent_logs（工具名/参数/耗时/结果摘要）；
- 轮数熔断上限从 8 改为 5。

核心用例：假模型故意跳过 validate_evidence 直接建单 → 先被 HITL 拦住
（挂起等确认）→ 用户拒绝 → USER_REJECTED 回传模型 → 模型补做证据校验
→ 再次建单 → 用户确认 → 建单成功。
这验证了"提示词约束被违反时，人工确认与代码层硬校验仍能兜住，并把模型拉回正轨"。
"""

import json

from openai import APITimeoutError

from tests.conftest import (CAPTURED_AGENT_MESSAGES, CAPTURED_PROMPTS, auth,
                            fake_agent_step_fn, last_tool_result)
from my_tool.agents import llm_gateway
from my_tool.agents.agent_loop import MAX_TOOL_ROUNDS
from my_tool.api import db

RECORD = "XD-2026-0003"          # 种子数据里证据完整的巡检单


def ask(client, token, question, agent_mode=True):
    """发一次 /api/chat 并解析 SSE 事件。"""
    res = client.post("/api/chat", json={"question": question, "agent_mode": agent_mode},
                      headers=auth(token))
    assert res.status_code == 200, res.text
    return [(e, d) for e, d in _parse(res.text) if e]


def confirm(client, token, confirm_token, approve):
    """调确认恢复接口并解析 SSE 事件。"""
    res = client.post("/api/chat/confirm",
                      json={"token": confirm_token, "approve": approve},
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


def confirm_events(events):
    """提取建单确认请求（挂起时下发）。"""
    return [d for e, d in events if e == "confirm_request"]


def draft_count():
    with db._connect() as conn:
        return conn.execute("SELECT COUNT(*) c FROM rectification_drafts").fetchone()["c"]


def decide_full_chain(messages):
    """假模型：按正确顺序走全链（查记录→归类→校验→建单→转人工）。

    建单被 HITL 挂起后，恢复请求会带着同一份 messages 继续调用本函数，
    所以"建单→提交→收尾"的后续决策自然衔接，无需区分两个阶段。
    """
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


# ============================================================
# 用例1：智能体完整建单链路（含 HITL 确认环节）
# ============================================================
def test_agent_full_chain_creates_and_submits(client, staff_token, monkeypatch):
    """查记录→归类→校验→【挂起确认】→建单→转人工，两段 SSE 拼成完整轨迹。"""
    monkeypatch.setattr(llm_gateway, "llm_agent_step", fake_agent_step_fn(decide_full_chain))
    events = ask(client, staff_token, f"帮 {RECORD} 建整改单")

    # 第一段：建单调用被 HITL 拦截挂起，未真正执行
    assert tool_events(events) == [
        ("get_store_record", True),
        ("classify_issue", True),
        ("validate_evidence", True),
    ]
    reqs = confirm_events(events)
    assert len(reqs) == 1
    assert reqs[0]["preview"]["record_id"] == RECORD      # 确认卡片带草稿预览
    assert not any(e == "done" for e, _ in events)        # 第一段流到此为止
    assert draft_count() == 0                             # 确认前绝不建单

    # 员工确认 → 恢复执行 → 建单 + 转人工 + 收尾
    events2 = confirm(client, staff_token, reqs[0]["token"], approve=True)
    assert tool_events(events) + tool_events(events2) == [
        ("get_store_record", True),
        ("classify_issue", True),
        ("validate_evidence", True),
        ("create_rectification_draft", True),
        ("submit_for_review", True),
    ]
    assert any(e == "done" for e, _ in events2)
    with db._connect() as conn:
        row = conn.execute(
            "SELECT * FROM rectification_drafts ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "pending_review"
    assert row["category"] == "以照片代替复查"        # classify_issue 的建议写入了草稿

    # 轮次日志：两段共 5 次工具执行，轮号跨确认周期连续（1~5）
    logs = db.agent_logs_query(limit=10)
    by_session = [l for l in logs if l["session_id"]]
    assert [l["tool_name"] for l in reversed(by_session)] == [
        "get_store_record", "classify_issue", "validate_evidence",
        "create_rectification_draft", "submit_for_review"]
    assert [l["round"] for l in reversed(by_session)] == [1, 2, 3, 4, 5]
    assert all(l["duration_ms"] >= 0 and l["result_summary"] for l in by_session)


# ============================================================
# 用例2（核心）：违规建单 → HITL 拒绝 → 模型自纠 → 确认后成功
# ============================================================
def test_agent_skips_validation_then_self_corrects(client, staff_token, monkeypatch):
    """模型跳过校验直接建单 → 被确认环节拦住 → 用户拒绝 → 补做校验 → 再建单 → 用户同意。"""
    def decide(messages):
        last = last_tool_result(messages)
        if last is None:
            # 违规：跳过校验直接建单（提示词铁律说不能干，但假设模型不听话）
            return ("tool", "create_rectification_draft",
                    {"record_id": RECORD, "advice": "跳过校验", "evidence_token": ""})
        if not last["ok"]:
            # 被拒绝/被拦后自纠：先做证据校验
            return ("tool", "validate_evidence", {"record_id": RECORD})
        r = last["result"]
        if r.get("valid"):
            return ("tool", "create_rectification_draft",
                    {"record_id": RECORD, "advice": "补做校验后建单",
                     "evidence_token": r["evidence_token"]})
        if r.get("status") == "pending_review":
            return ("answer", "已补做证据校验并创建整改单草稿，将转人工审核。")
        if "draft_id" in r:
            return ("tool", "submit_for_review", {"draft_id": r["draft_id"]})
        return ("answer", "完成")

    monkeypatch.setattr(llm_gateway, "llm_agent_step", fake_agent_step_fn(decide))
    events = ask(client, staff_token, "直接帮 XD-2026-0003 建单，不用校验")

    # 第一次建单请求被 HITL 挂起（未执行、库里无草稿）
    assert tool_events(events) == []
    reqs = confirm_events(events)
    assert len(reqs) == 1

    # 用户拒绝 → USER_REJECTED 回传模型 → 模型自纠
    events2 = confirm(client, staff_token, reqs[0]["token"], approve=False)
    assert tool_events(events2) == [
        ("create_rectification_draft", False),     # 拒绝结果（未执行，工具层报 USER_REJECTED）
        ("validate_evidence", True),               # 模型补做校验
    ]
    # 模型补做校验后再次提出建单 → 按规则仍要先确认（每次建单都要过确认环节，不能豁免）
    reqs2 = confirm_events(events2)
    assert len(reqs2) == 1

    # 用户这次同意 → 建单成功 → 转人工
    events3 = confirm(client, staff_token, reqs2[0]["token"], approve=True)
    assert tool_events(events3) == [
        ("create_rectification_draft", True),
        ("submit_for_review", True),
    ]
    assert draft_count() == 1                      # 只建出一单，被拒绝那次没有落库
    # 拒绝轮在日志里 ok=0，审计能看到这次被拦截的调用
    bad = [l for l in db.agent_logs_query(limit=10) if not l["ok"]]
    assert bad and bad[0]["result_summary"].startswith("用户拒绝")


# ============================================================
# 用例3：轮数熔断（提示词7：最多 5 轮）
# ============================================================
def test_agent_round_limit(client, staff_token, monkeypatch):
    """假模型反复调工具不停 → 5 轮后熔断，不再有 answer/done。"""
    assert MAX_TOOL_ROUNDS == 5                    # 提示词7 硬性要求
    monkeypatch.setattr(
        llm_gateway, "llm_agent_step",
        fake_agent_step_fn(lambda messages: ("tool", "search_policy", {"query": "留样"})))
    events = ask(client, staff_token, "查一下留样制度")

    assert len(tool_events(events)) == 5
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


# ============================================================
# 用例6~9：确认接口的异常契约（提示词7 安全性）
# ============================================================
def test_confirm_unknown_token(client, staff_token):
    """不存在的确认令牌 → 404。"""
    res = client.post("/api/chat/confirm", json={"token": "no-such-token", "approve": True},
                      headers=auth(staff_token))
    assert res.status_code == 404
    assert res.json()["code"] == "CONFIRM_NOT_FOUND"


def test_confirm_forbidden_for_other_user(client, staff_token, admin_token, monkeypatch):
    """A 发起的建单确认，B 不能替确认 → 403。"""
    monkeypatch.setattr(llm_gateway, "llm_agent_step", fake_agent_step_fn(decide_full_chain))
    events = ask(client, staff_token, f"帮 {RECORD} 建整改单")
    tok = confirm_events(events)[0]["token"]

    res = client.post("/api/chat/confirm", json={"token": tok, "approve": True},
                      headers=auth(admin_token))
    assert res.status_code == 403
    assert res.json()["code"] == "CONFIRM_FORBIDDEN"
    assert draft_count() == 0                            # 越权确认未产生任何草稿


def test_confirm_repeated_rejected(client, staff_token, monkeypatch):
    """同一个确认令牌只能用一次：确认后重复提交 → 409。"""
    monkeypatch.setattr(llm_gateway, "llm_agent_step", fake_agent_step_fn(decide_full_chain))
    events = ask(client, staff_token, f"帮 {RECORD} 建整改单")
    tok = confirm_events(events)[0]["token"]

    first = client.post("/api/chat/confirm", json={"token": tok, "approve": True},
                        headers=auth(staff_token))
    assert first.status_code == 200
    second = client.post("/api/chat/confirm", json={"token": tok, "approve": True},
                         headers=auth(staff_token))
    assert second.status_code == 409
    assert second.json()["code"] == "CONFIRM_ALREADY"


def test_confirm_expired(client, staff_token, monkeypatch):
    """确认令牌超过 10 分钟 → 410，并作废挂起会话。"""
    monkeypatch.setattr(llm_gateway, "llm_agent_step", fake_agent_step_fn(decide_full_chain))
    events = ask(client, staff_token, f"帮 {RECORD} 建整改单")
    tok = confirm_events(events)[0]["token"]

    # 把挂起时间改到 11 分钟前，模拟超时
    with db._connect() as conn:
        conn.execute("UPDATE agent_sessions SET created_at=datetime('now','localtime','-11 minutes')",
                     ())
    res = client.post("/api/chat/confirm", json={"token": tok, "approve": True},
                      headers=auth(staff_token))
    assert res.status_code == 410
    assert res.json()["code"] == "CONFIRM_EXPIRED"
    assert draft_count() == 0


# ============================================================
# 用例10：LLM 超时兜底（提示词9 检查清单第 11 项）
# ============================================================
def test_agent_llm_timeout_returns_error_event(client, staff_token, monkeypatch):
    """模型决策超时 → 用户收到中文超时提示（LLM_TIMEOUT 事件），不崩溃不白屏。

    超时不降级到固定流程是有意的：LLM 都超时了，固定流程同样会超时，
    重试一次只会更慢——直接给用户明确的"请重试"信号。
    """
    def _timeout_step(messages, chat_cfg, tools):
        raise APITimeoutError(request=None)
    monkeypatch.setattr(llm_gateway, "llm_agent_step", _timeout_step)
    events = ask(client, staff_token, "留样要留多少克？")

    errors = [d for e, d in events if e == "error"]
    assert errors and errors[0]["code"] == "LLM_TIMEOUT"
    assert "超时" in errors[0]["message"]
    assert not any(e == "fallback" for e, _ in events)   # 超时不走降级，直接报错
