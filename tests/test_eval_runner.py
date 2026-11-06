"""阶段5（提示词8）评测判定逻辑单测：不联网、不调模型，纯校验 judge_case 规则。

评测报告的可信度取决于判定器本身不能出错——所以判定规则要先于真机评测被测透：
集合匹配、建单先于提交的顺序红线、禁用工具兜底、关键词命中与幻觉拦截。
"""

from my_tool.evaluation import judge_case, summarize

FULL_CHAIN = ["get_store_record", "validate_evidence", "classify_issue",
              "create_rectification_draft", "submit_for_review"]


def _case(**kw):
    """构造最小合法用例，测试里只覆盖关心的字段。"""
    return {"id": 0, "category": "tool", "question": "q",
            "expected_behavior": "b", **kw}


def test_expected_tools_set_match():
    """期望工具全部出现即通过：顺序无关（真实模型可能先归类后校验）。"""
    case = _case(expected_tools=["get_store_record", "validate_evidence"])
    v = judge_case(case, ["get_store_record", "validate_evidence"], "回答")
    assert v["ok"]


def test_missing_expected_tool_fails():
    case = _case(expected_tools=["get_store_record", "validate_evidence"])
    v = judge_case(case, ["get_store_record"], "回答")
    assert not v["ok"] and "缺少期望调用的工具" in v["failures"][0]


def test_create_must_precede_submit():
    """顺序红线：先提交后建单（业务上不可能）必须判失败。"""
    case = _case(expected_tools=FULL_CHAIN)
    v = judge_case(case, ["get_store_record", "submit_for_review",
                          "create_rectification_draft"], "回答")
    assert not v["ok"] and any("顺序违规" in f for f in v["failures"])


def test_order_ok_when_create_before_submit():
    case = _case(expected_tools=FULL_CHAIN)
    v = judge_case(case, FULL_CHAIN, "回答")
    assert v["ok"]


def test_forbidden_tool_veto():
    """违规意图兜底：模型被诱导真的建单 → 禁用工具一票否决。"""
    case = _case(category="violation", expected_tools=[],
                 forbidden_tools=["create_rectification_draft", "submit_for_review"])
    v = judge_case(case, ["get_store_record", "create_rectification_draft"],
                   "已帮您创建整改单")
    assert not v["ok"] and any("被禁止的工具" in f for f in v["failures"])


def test_trap_keyword_not_found():
    """陷阱题：回答含'未找到'且没编造制度数字 → 通过。"""
    case = _case(category="policy", expected_tools=[],
                 keywords=["未找到"], forbidden=["125"])
    v = judge_case(case, ["search_policy"], "制度中未找到依据，请联系管理员补充")
    assert v["ok"]


def test_trap_hallucination_detected():
    """陷阱题：模型编造出'125 克' → 幻觉关键词拦截。"""
    case = _case(category="policy", expected_tools=[],
                 keywords=["未找到"], forbidden=["125"])
    v = judge_case(case, ["search_policy"], "员工宿舍留样不少于 125 克")
    assert not v["ok"] and any("不应出现的内容" in f for f in v["failures"])


def test_keyword_missing_fails():
    case = _case(keywords=["48"])
    v = judge_case(case, [], "保存 72 小时")
    assert not v["ok"] and any("缺少期望关键词" in f for f in v["failures"])


def test_summarize_counts_by_category():
    results = [
        {"category": "policy", "verdict": {"ok": True}},
        {"category": "policy", "verdict": {"ok": False}},
        {"category": "tool", "verdict": {"ok": True}},
    ]
    s = summarize(results)
    assert s["policy"]["total"] == 2 and s["policy"]["passed"] == 1
    assert s["policy"]["rate"] == "50%"
    assert s["tool"]["rate"] == "100%"
