"""阶段4 智能体工具测试：调用顺序硬约束是重点。

最关键的用例：跳过 validate_evidence 直接 create_rectification_draft 必须被拒——
系统提示词是软约束，代码层（证据令牌）是硬约束，这里验证硬约束真的拦得住。
"""

import json

import pytest

from my_tool.agents.tools import TOOL_SCHEMAS, ToolContext, run_tool
from my_tool.api import db

CTX = ToolContext(kb=None, user_id=2)   # search_policy 之外的工具不依赖知识库


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """每个测试独立临时库（种子巡检记录随库自动播种），不碰真实 data/chat.db。"""
    monkeypatch.setenv("CHAT_DB", str(tmp_path / "chat.db"))


def tool(name, args):
    """快捷调用：返回统一结果结构。"""
    return run_tool(name, args, CTX)


def get_token(business_id="XD-2026-0003"):
    """走正常校验拿一个真实令牌。"""
    out = tool("validate_evidence", {"record_id": business_id})
    assert out["ok"] and out["result"]["valid"], out
    return out["result"]["evidence_token"]


# ============================================================
# 工具声明规范
# ============================================================
def test_schemas_function_calling_format():
    """六个工具齐全、OpenAI function calling 结构完整、巡店口径无报销字样。"""
    names = [t["function"]["name"] for t in TOOL_SCHEMAS]
    assert names == [
        "search_policy", "get_store_record", "classify_issue",
        "validate_evidence", "create_rectification_draft", "submit_for_review",
    ]
    for t in TOOL_SCHEMAS:
        assert t["type"] == "function"
        fn = t["function"]
        assert fn["description"] and isinstance(fn["parameters"], dict)
        blob = json.dumps(t, ensure_ascii=False)
        assert "报销" not in blob          # 口径红线：老师模板是报销，必须全部换巡店
    create_fn = TOOL_SCHEMAS[4]["function"]
    assert set(create_fn["parameters"]["required"]) == {"record_id", "advice", "evidence_token"}


# ============================================================
# 查询与归类
# ============================================================
def test_get_store_record_by_id_and_missing():
    ok = tool("get_store_record", {"record_id": "XD-2026-0001"})
    assert ok["ok"] and ok["result"]["records"][0]["store_id"] == "S-018"
    bad = tool("get_store_record", {"record_id": "XD-9999"})
    assert bad == {"ok": False, "error": {"code": "RECORD_NOT_FOUND", "message": bad["error"]["message"]}}
    none = tool("get_store_record", {})
    assert none["error"]["code"] == "INVALID_PARAM"


def test_classify_issue_hits_temperature_rule():
    out = tool("classify_issue", {"record_id": "XD-2026-0001"})
    assert out["ok"]
    res = out["result"]
    assert res["category"] == "温度记录缺失"
    assert res["severity"] == "高"
    assert res["business_id"] == "XD-2026-0001"


# ============================================================
# 证据校验：签发 / 不通过分支
# ============================================================
def test_validate_evidence_fail_branches():
    # 缺温度记录（描述涉及温度但 has_temperature_log=0）
    out = tool("validate_evidence", {"record_id": "XD-2026-0001"})
    assert out["ok"] and out["result"]["valid"] is False
    assert any("温度记录" in m for m in out["result"]["missing"])
    assert out["result"]["evidence_token"] is None
    # 缺现场照片
    out2 = tool("validate_evidence", {"record_id": "XD-2026-0002"})
    assert out2["result"]["valid"] is False
    assert any("照片" in m for m in out2["result"]["missing"])


def test_validate_evidence_pass_issues_token():
    out = tool("validate_evidence", {"record_id": "XD-2026-0003"})
    res = out["result"]
    assert res["valid"] is True and len(res["evidence_token"]) >= 32


# ============================================================
# 顺序硬约束：建单必须先校验（本批测试的核心）
# ============================================================
def test_create_draft_without_validation_rejected():
    """跳过校验直接建单 → MISSING_VALIDATION，且库里没有草稿产生。"""
    out = tool("create_rectification_draft",
               {"record_id": "XD-2026-0003", "advice": "整改", "evidence_token": ""})
    assert out["error"]["code"] == "MISSING_VALIDATION"


def test_create_draft_with_forged_token_rejected():
    """伪造令牌 → EVIDENCE_TOKEN_INVALID。"""
    out = tool("create_rectification_draft",
               {"record_id": "XD-2026-0003", "advice": "整改", "evidence_token": "fake-token"})
    assert out["error"]["code"] == "EVIDENCE_TOKEN_INVALID"


def test_create_draft_token_single_use_and_bound_to_record():
    """令牌一次性 + 归属绑定：用过即作废；拿到别的单上无效。"""
    token = get_token("XD-2026-0003")
    ok = tool("create_rectification_draft",
              {"record_id": "XD-2026-0003", "advice": "现场复查并留存独立证据",
               "evidence_token": token, "category": "以照片代替复查", "severity": "高"})
    assert ok["ok"] and ok["result"]["status"] == "draft"
    # 同令牌再建一次：已使用 → 拒绝
    again = tool("create_rectification_draft",
                 {"record_id": "XD-2026-0003", "advice": "再建一张", "evidence_token": token})
    assert again["error"]["code"] == "EVIDENCE_TOKEN_INVALID"
    # A 单令牌用到 B 单：归属不符 → 拒绝
    token_b = get_token("XD-2026-0004")
    stolen = tool("create_rectification_draft",
                  {"record_id": "XD-2026-0003", "advice": "盗用令牌", "evidence_token": token_b})
    assert stolen["error"]["code"] == "EVIDENCE_TOKEN_INVALID"


def test_create_draft_expired_token_rejected():
    """令牌超过 10 分钟 → 拒绝。"""
    token = get_token("XD-2026-0004")
    with db._connect() as conn:
        conn.execute("UPDATE evidence_tokens SET created_at=datetime('now','localtime','-20 minutes') "
                     "WHERE token=?", (token,))
    out = tool("create_rectification_draft",
               {"record_id": "XD-2026-0004", "advice": "整改", "evidence_token": token})
    assert out["error"]["code"] == "EVIDENCE_TOKEN_INVALID"


# ============================================================
# 提交人工审核：终态约束
# ============================================================
def test_submit_for_review_full_chain():
    """校验→建单→提交 全链路通过；重复提交与不存在单据被拒。"""
    token = get_token("XD-2026-0003")
    draft = tool("create_rectification_draft",
                 {"record_id": "XD-2026-0003", "advice": "现场复查并留存独立证据",
                  "evidence_token": token})
    draft_id = draft["result"]["draft_id"]
    submitted = tool("submit_for_review", {"draft_id": draft_id})
    assert submitted["ok"] and submitted["result"]["status"] == "pending_review"
    row = db.get_draft(draft_id)
    assert row["status"] == "pending_review" and row["token"] == token
    # 重复提交
    dup = tool("submit_for_review", {"draft_id": draft_id})
    assert dup["error"]["code"] == "DRAFT_NOT_DRAFT"
    # 不存在
    ghost = tool("submit_for_review", {"draft_id": 99999})
    assert ghost["error"]["code"] == "DRAFT_NOT_FOUND"


# ============================================================
# 统一入口兜底
# ============================================================
def test_run_tool_never_raises():
    """未知工具、非法参数都不抛异常，统一折叠成错误码返回给模型。"""
    assert run_tool("no_such_tool", {}, CTX)["error"]["code"] == "UNKNOWN_TOOL"
    assert run_tool("validate_evidence", "not-a-dict", CTX)["error"]["code"] == "RECORD_NOT_FOUND"
    assert run_tool("classify_issue", {"record_id": None}, CTX)["error"]["code"] == "RECORD_NOT_FOUND"
