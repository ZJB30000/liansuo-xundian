"""阶段5（提示词8）评测用例集的结构校验：保证 30 条评测数据的合法性与完备性。

评测集 data/eval_cases.json 是阶段5 评测运行器的输入——结构错了评测就会跑歪。
本测试用纯静态校验（不调模型、不调接口）把错误挡在上游：
- 数量与分类配比（老师模板：10 制度 + 10 工具 + 5 违规 + 5 多轮）
- 字段类型与必填约束（question / expected_tools / expected_behavior / category）
- 工具名必须真实存在于六工具注册表，防止评测集写错名
- 陷阱题恰好 3 条且都要求"未找到"并禁编造具体制度数字
- 违规意图类一律禁止 create/submit（违规操作绝不允许真的被执行）
- 多轮上下文类必须带合法 context（user/assistant 交替、最后一轮是 user）
"""

import json
import pathlib

import pytest

from my_tool.agents.tools import TOOL_SCHEMAS

CASES_PATH = pathlib.Path(__file__).resolve().parents[1] / "data" / "eval_cases.json"
VALID_TOOLS = {s["function"]["name"] for s in TOOL_SCHEMAS}
CATEGORIES = {"policy", "tool", "violation", "multi_turn"}
REQUIRED_FIELDS = {"question", "expected_tools", "expected_behavior", "category"}


@pytest.fixture(scope="module")
def cases():
    """加载评测集，先保证是合法 JSON 数组。"""
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, list), "评测集必须是 JSON 数组（老师模板要求）"
    return data


def test_30_cases(cases):
    assert len(cases) == 30, f"应为 30 条评测用例，实际 {len(cases)} 条"


def test_case_ids_unique_and_ordered(cases):
    ids = [c["id"] for c in cases]
    assert len(set(ids)) == 30, "用例 id 不能重复"
    assert ids == sorted(ids), "id 应从 1 递增排列，便于对报告定位"


def test_required_fields_and_types(cases):
    for c in cases:
        missing = REQUIRED_FIELDS - set(c)
        assert not missing, f"用例 {c.get('id')} 缺字段：{missing}"
        assert isinstance(c["question"], str) and c["question"].strip(), \
            f"用例 {c['id']} 的 question 不能为空"
        assert isinstance(c["expected_behavior"], str) and c["expected_behavior"].strip(), \
            f"用例 {c['id']} 的 expected_behavior 不能为空"
        assert c["category"] in CATEGORIES, f"用例 {c['id']} 的 category 非法：{c['category']}"
        assert isinstance(c["expected_tools"], list), f"用例 {c['id']} 的 expected_tools 必须是数组"


def test_category_counts_match_template(cases):
    """老师模板配比：10 制度 + 10 工具 + 5 违规 + 5 多轮。"""
    counts = {}
    for c in cases:
        counts[c["category"]] = counts.get(c["category"], 0) + 1
    assert counts == {"policy": 10, "tool": 10, "violation": 5, "multi_turn": 5}


def test_tool_names_exist_in_registry(cases):
    """expected_tools / forbidden_tools 里的工具名必须是六工具注册表里的真名。"""
    for c in cases:
        for field in ("expected_tools", "forbidden_tools", ):
            for name in c.get(field, []):
                assert name in VALID_TOOLS, \
                    f"用例 {c['id']} 的 {field} 含未知工具：{name}（注册表里没有）"


def test_exactly_3_trap_questions_all_expect_not_found(cases):
    """制度类里恰好 3 条陷阱题：期望回答'未找到'，且禁止编造具体制度数字。"""
    traps = [c for c in cases
             if c["category"] == "policy" and "未找到" in c.get("keywords", [])]
    assert len(traps) == 3, f"陷阱题应恰好 3 条，实际 {len(traps)} 条"
    for c in traps:
        assert c.get("forbidden"), f"陷阱题 {c['id']} 必须配 forbidden 关键词（防编造具体制度内容）"


def test_violation_cases_never_create_or_submit(cases):
    """违规意图类：无论模型怎么被诱导，建单/提交工具一律不允许真的被调用。"""
    for c in cases:
        if c["category"] == "violation":
            assert "create_rectification_draft" in c["forbidden_tools"], \
                f"违规用例 {c['id']} 必须禁止 create_rectification_draft"
            assert "submit_for_review" in c["forbidden_tools"], \
                f"违规用例 {c['id']} 必须禁止 submit_for_review"


def test_multi_turn_cases_have_valid_context(cases):
    """多轮上下文类必须带 context（评测运行器先发 context 各轮、再发 question）。

    context 存的是之前的问答轮，必须以 assistant 的回答结尾——
    指代类问题（"它缺什么证据"）依赖上一问已有回答才衔接得上。
    """
    for c in cases:
        if c["category"] != "multi_turn":
            continue
        ctx = c.get("context")
        assert isinstance(ctx, list) and len(ctx) >= 2, \
            f"多轮用例 {c['id']} 必须带至少一轮问答的 context"
        for turn in ctx:
            assert turn.get("role") in ("user", "assistant"), \
                f"用例 {c['id']} context 的 role 非法：{turn.get('role')}"
            assert isinstance(turn.get("content"), str) and turn["content"].strip(), \
                f"用例 {c['id']} context 的 content 不能为空"
        assert ctx[-1]["role"] == "assistant", \
            f"用例 {c['id']} 的 context 必须以 assistant 回答结尾（指代才有上文可依）"
        assert ctx[0]["role"] == "user", \
            f"用例 {c['id']} 的 context 必须以 user 提问开头"


def test_policy_cases_may_only_search_policy(cases):
    """制度类（含陷阱题）只允许 search_policy，其余五工具全部禁止。"""
    forbidden_five = ["get_store_record", "classify_issue", "validate_evidence",
                      "create_rectification_draft", "submit_for_review"]
    for c in cases:
        if c["category"] != "policy":
            continue
        assert c.get("forbidden_tools") == forbidden_five, \
            f"制度用例 {c['id']} 应禁止除 search_policy 外的全部工具"


def test_tool_case_13_is_full_chain(cases):
    """工具类第 13 条必须覆盖任务办理全链（校验→归类→建单→转人工）。

    get_store_record 不在期望里：查记录已内置在 validate_evidence 中，
    模型跳过显式查询属于合理优化（评测器对顺序与缺失的判定见 judge_case）。
    """
    c13 = next(c for c in cases if c["id"] == 13)
    assert set(c13["expected_tools"]) == {
        "validate_evidence", "classify_issue",
        "create_rectification_draft", "submit_for_review"}


def test_keywords_are_strings(cases):
    for c in cases:
        for field in ("keywords", "forbidden"):
            for kw in c.get(field, []):
                assert isinstance(kw, str) and kw, \
                    f"用例 {c['id']} 的 {field} 关键词必须是非空字符串"
