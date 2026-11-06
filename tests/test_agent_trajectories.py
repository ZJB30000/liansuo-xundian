"""阶段5（提示词7）：三条典型对话流程的预期轨迹测试。

老师模板（报销助手）轨迹 → 本项目（巡店系统）轨迹的映射：
  a. "出差上海3天住宿标准是多少"   → "留样要留多少克？"（只查制度，不碰记录/校验/建单）
  b. "帮我报上个月去北京的差旅"     → "帮 XD-2026-0003 建整改单"（查制度→查记录→校验→归类→建草稿→确认）
  c. "这个发票能报吗"              → "XD-2026-0002 这次巡检能合格吗？"（只校验+给结论）

每条轨迹用假模型按预期决策走一遍 Harness，断言工具调用序列、是否触发
建单确认、轮次日志——证明智能体对三类意图的编排行为与设计一致。
"""

import json

from tests.conftest import auth, fake_agent_step_fn, last_tool_result
from tests.test_agent import (_parse, confirm_events, draft_count, tool_events,
                              confirm)
from my_tool.agents import llm_gateway
from my_tool.api import db

OK = "XD-2026-0003"      # 种子里证据完整的巡检单（轨迹 b 用）
BAD = "XD-2026-0002"     # 种子里缺现场照片的巡检单（轨迹 c 用）


# ============================================================
# 轨迹 a：制度咨询类 —— 只查制度，不调记录/校验/建单工具
# ============================================================
def test_trajectory_a_policy_only(client, staff_token, monkeypatch):
    """预期轨迹：search_policy → 直接回答附出处；全程 1 轮 1 次工具。"""
    def decide(messages):
        last = last_tool_result(messages)
        if last is None:
            return ("tool", "search_policy", {"query": "留样 克数 保存时间"})
        r = last["result"]
        if r.get("items"):
            return ("answer", "根据《门店食品安全制度》，留样不少于 125 克，冷藏保存 48 小时以上。")
        return ("answer", "制度里没有找到相关条款。")

    monkeypatch.setattr(llm_gateway, "llm_agent_step", fake_agent_step_fn(decide))
    res = client.post("/api/chat",
                      json={"question": "留样要留多少克？", "agent_mode": True},
                      headers=auth(staff_token))
    events = [(e, d) for e, d in _parse(res.text) if e]

    # 工具只有 search_policy，其余一概没碰——查询型意图不该走到建单链路
    names = [n for n, _ in tool_events(events)]
    assert names == ["search_policy"]
    assert not {"get_store_record", "classify_issue",
                "validate_evidence", "create_rectification_draft"} & set(names)
    # 直接回答 + 引用来源，无建单确认
    # （接口层把 answer 事件转成打字机 delta + sources，SSE 流里没有 answer 事件）
    text = "".join(d["text"] for e, d in events if e == "delta")
    assert "125" in text
    srcs = [d for e, d in events if e == "sources"]
    assert srcs and srcs[0]["items"], "制度回答必须附出处"
    assert confirm_events(events) == []
    assert any(e == "done" for e, _ in events)
    assert draft_count() == 0
    # 轮次日志：1 行，第 1 轮
    logs = [l for l in db.agent_logs_query(limit=10) if l["tool_name"] == "search_policy"]
    assert logs and logs[0]["round"] == 1 and logs[0]["ok"] == 1


# ============================================================
# 轨迹 b：任务办理类 —— 查记录→查制度→校验→归类→建草稿→确认
# ============================================================
def test_trajectory_b_full_task_with_confirm(client, staff_token, monkeypatch):
    """预期轨迹（对应"报差旅"）：查记录→查制度→校验→归类→挂起确认→建单→转人工。"""
    def decide(messages):
        last = last_tool_result(messages)
        if last is None:
            return ("tool", "get_store_record", {"record_id": OK})          # ① 查记录
        if not last["ok"]:
            return ("answer", "流程受阻：" + last["error"]["message"])
        r = last["result"]
        if "records" in r:
            return ("tool", "search_policy", {"query": "整改照片 复查 制度要求"})  # ② 查制度
        if "items" in r:
            return ("tool", "validate_evidence", {"record_id": OK})         # ③ 校验证据
        if r.get("valid"):
            return ("tool", "classify_issue", {"record_id": OK})            # ④ 归类定级
        if "category" in r:
            # ⑤ 建草稿→挂起确认。证据令牌要从历史里 validate_evidence 的结果取
            #    （令牌签发在两轮前，当前轮是归类结果，里面没有令牌）
            tok = None
            for m in messages:
                if m.get("role") == "tool":
                    rr = json.loads(m["content"])
                    if rr.get("ok") and rr.get("result", {}).get("evidence_token"):
                        tok = rr["result"]["evidence_token"]
            return ("tool", "create_rectification_draft",                   # ⑤ 建草稿→挂起确认
                    {"record_id": OK, "advice": "现场复查并留存独立证据",
                     "evidence_token": tok,
                     "category": r["category"], "severity": r["severity"]})
        if r.get("status") == "pending_review":
            return ("answer", "已创建整改单草稿并转人工审核，请注意查收复核结果。")
        if "draft_id" in r:
            return ("tool", "submit_for_review", {"draft_id": r["draft_id"]})  # ⑥ 转人工
        return ("answer", "完成")

    monkeypatch.setattr(llm_gateway, "llm_agent_step", fake_agent_step_fn(decide))
    res = client.post("/api/chat",
                      json={"question": f"帮 {OK} 建整改单", "agent_mode": True},
                      headers=auth(staff_token))
    events = [(e, d) for e, d in _parse(res.text) if e]

    # 第一段：四个准备工具按序执行，第 5 步建单被 HITL 拦截挂起
    assert tool_events(events) == [
        ("get_store_record", True),
        ("search_policy", True),
        ("validate_evidence", True),
        ("classify_issue", True),
    ]
    reqs = confirm_events(events)
    assert len(reqs) == 1
    preview = reqs[0]["preview"]
    assert preview["record_id"] == OK
    assert preview["category"] == "以照片代替复查"       # 归类建议随卡片展示给员工
    assert draft_count() == 0                          # 确认前未建单

    # 员工确认 → 建单 + 转人工 + 收尾
    events2 = confirm(client, staff_token, reqs[0]["token"], approve=True)
    assert tool_events(events2) == [
        ("create_rectification_draft", True),
        ("submit_for_review", True),
    ]
    assert any(e == "done" for e, _ in events2)
    assert draft_count() == 1

    # 轮次日志完整轨迹：6 次工具执行，轮号跨确认周期连续 1~6
    logs = [l for l in reversed(db.agent_logs_query(limit=20))]
    logs = [l for l in logs if l["session_id"]]
    assert [l["tool_name"] for l in logs] == [
        "get_store_record", "search_policy", "validate_evidence",
        "classify_issue", "create_rectification_draft", "submit_for_review"]
    assert [l["round"] for l in logs] == [1, 2, 3, 4, 5, 6]


# ============================================================
# 轨迹 c：判断咨询类 —— 只校验证据，给结论，不建单
# ============================================================
def test_trajectory_c_validate_only(client, staff_token, monkeypatch):
    """预期轨迹（对应"发票能报吗"）：validate_evidence → 给结论；不建单。"""
    def decide(messages):
        last = last_tool_result(messages)
        if last is None:
            return ("tool", "validate_evidence", {"record_id": BAD})
        r = last["result"]
        if not r.get("valid"):
            return ("answer", "按制度这次巡检不能合格：证据不齐全（" +
                    "、".join(r["missing"]) + "），请现场补齐后再提交复查。")
        return ("answer", "证据齐全，可以提交人工复核。")

    monkeypatch.setattr(llm_gateway, "llm_agent_step", fake_agent_step_fn(decide))
    res = client.post("/api/chat",
                      json={"question": f"{BAD} 这次巡检能合格吗？", "agent_mode": True},
                      headers=auth(staff_token))
    events = [(e, d) for e, d in _parse(res.text) if e]

    # 只有 1 次校验工具；校验不通过（缺照片）→ 结论明确 + 不建单
    assert tool_events(events) == [("validate_evidence", True)]
    # 接口层把 answer 转成打字机 delta，断言拼接后的完整文本
    text = "".join(d["text"] for e, d in events if e == "delta")
    assert "不能合格" in text and "照片" in text
    assert confirm_events(events) == []
    assert draft_count() == 0
    assert any(e == "done" for e, _ in events)
    # 轮次日志 1 行
    logs = [l for l in db.agent_logs_query(limit=10) if l["tool_name"] == "validate_evidence"]
    assert logs and logs[0]["round"] == 1
