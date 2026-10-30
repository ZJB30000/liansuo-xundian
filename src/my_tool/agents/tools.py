"""智能体工具集（阶段4 · ★核心复用模块）：Function Calling 规范的工具注册表与硬校验。

把"固定流程问答"升级为"自主决策智能体"的核心：模型根据 TOOL_SCHEMAS 自主决定
调用哪个工具、什么顺序；但调用顺序红线在代码层强制执行——
create_rectification_draft 必须携带 validate_evidence 签发的一次性 evidence_token，
跳过校验直接建单一律拒绝（MISSING_VALIDATION）。
提示词约束是软约束（引导模型走对流程），这里是硬约束（绕不过去），双保险。

边界红线：工具只到"建草稿 + 转人工审核"为止，没有自动派发处罚、自动判合格的工具。
"""

import secrets

from my_tool.api import db
from my_tool.core.classifier import classify_record
from my_tool.services.kb_service import hybrid_search

SNIPPET_LEN = 60        # 制度片段预览长度（字），与 chat.py 口径一致
DESC_MIN_LEN = 8        # 巡检描述最小长度（字），过短视为描述不完整


class ToolError(Exception):
    """工具执行的业务错误：code 返回给模型用于自纠，message 是中文说明。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ============================================================
# 工具声明（OpenAI Function Calling 规范）：模型据此决定调用什么、传什么参数
# ============================================================
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_policy",
            "description": "检索连锁巡店制度条款。回答任何制度问题、给出整改建议依据前，应先调用本工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要检索的制度关键词或问题"},
                    "top_k": {"type": "integer", "description": "返回片段数，1-5，默认3"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_store_record",
            "description": "查询门店巡检记录。可按巡检单号（如 XD-2026-0001）或门店编号（如 S-018）查询。",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {"type": "string", "description": "巡检单号，如 XD-2026-0001"},
                    "store_id": {"type": "string", "description": "门店编号，如 S-018"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "classify_issue",
            "description": "对一条巡检记录给出问题归类与严重度建议（只是建议，最终由人工定级）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {"type": "string", "description": "巡检单号，如 XD-2026-0001"},
                    "description": {"type": "string", "description": "可选：补充或修正后的问题描述"},
                },
                "required": ["record_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_evidence",
            "description": "校验巡检记录的证据完整性（描述、现场照片、温度记录）。通过后签发一次性证据令牌，是创建整改单草稿的前置条件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {"type": "string", "description": "巡检单号，如 XD-2026-0001"},
                },
                "required": ["record_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_rectification_draft",
            "description": "创建整改单草稿（不会派发）。必须先通过 validate_evidence，并把它返回的 evidence_token 原样传入。",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {"type": "string", "description": "巡检单号，如 XD-2026-0001"},
                    "advice": {"type": "string", "description": "整改建议（需有制度依据）"},
                    "evidence_token": {"type": "string", "description": "validate_evidence 返回的证据令牌，原样传入"},
                    "category": {"type": "string", "description": "问题类别（建议先调用 classify_issue 获取）"},
                    "severity": {"type": "string", "description": "严重度：高/中/低"},
                },
                "required": ["record_id", "advice", "evidence_token"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_for_review",
            "description": "把整改单草稿提交人工审核。这是流程终点，提交后等待人工确认，不能自动定级或处罚。",
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {"type": "integer", "description": "整改单草稿ID"},
                },
                "required": ["draft_id"],
            },
        },
    },
]


class ToolContext:
    """工具运行上下文：知识库（search_policy 用）+ 当前员工ID（建单归属审计用）。"""

    def __init__(self, kb, user_id: int = None):
        self.kb = kb
        self.user_id = user_id


# ============================================================
# 工具实现：每个函数返回 dict（成功）或抛 ToolError（业务失败）
# ============================================================
def _find_one(record_id) -> dict:
    """按巡检单号取唯一记录；单号为空或查不到抛 RECORD_NOT_FOUND（空单号不能匹配全部）。"""
    bid = str(record_id or "").strip()
    if not bid:
        raise ToolError("RECORD_NOT_FOUND", "巡检单号不能为空")
    rows = db.find_records(business_id=bid)
    if not rows:
        raise ToolError("RECORD_NOT_FOUND", f"未找到巡检单号为 {record_id} 的记录，请确认单号")
    return rows[0]


def _tool_search_policy(ctx: ToolContext, args: dict) -> dict:
    """检索巡店制度（复用阶段1 的混合检索：FAISS+BM25+RRF）。"""
    query = str(args.get("query", "")).strip()
    if not query:
        raise ToolError("INVALID_PARAM", "query 不能为空")
    if ctx.kb is None:
        raise ToolError("KB_NOT_READY", "知识库未就绪，请稍后再试")
    try:
        top_k = max(1, min(int(args.get("top_k", 3)), 5))
    except (TypeError, ValueError):
        top_k = 3
    hits = hybrid_search(query, ctx.kb.index, ctx.kb.bm25, ctx.kb.texts, ctx.kb.embed_cfg, k=top_k)
    items = [
        {
            "snippet": ctx.kb.texts[idx][:SNIPPET_LEN],
            "doc": ctx.kb.chunks[idx]["doc"],
            "score": round(score, 4),
        }
        for idx, score in hits
    ]
    return {"items": items}


def _tool_get_store_record(ctx: ToolContext, args: dict) -> dict:
    """按单号或门店编号查巡检记录；两者都不传则报参数错误。"""
    business_id = str(args.get("record_id") or "").strip()
    store_id = str(args.get("store_id") or "").strip()
    if not business_id and not store_id:
        raise ToolError("INVALID_PARAM", "record_id 与 store_id 至少传一个")
    rows = db.find_records(business_id=business_id or None, store_id=store_id or None)
    if not rows:
        raise ToolError("RECORD_NOT_FOUND", "未找到对应的巡检记录，请确认单号或门店编号")
    return {"records": rows}


def _tool_classify_issue(ctx: ToolContext, args: dict) -> dict:
    """问题归类与严重度建议（复用阶段1 的规则归类模块，只建议不裁决）。"""
    rec = _find_one(args.get("record_id"))
    description = str(args.get("description") or rec["description"]).strip()
    advice = classify_record(
        {
            "business_id": rec["business_id"],
            "description": description,
            "version_conflict": bool(rec["version_conflict"]),
            "system_unavailable": bool(rec["system_unavailable"]),
        }
    )
    return advice.to_dict()


def _tool_validate_evidence(ctx: ToolContext, args: dict) -> dict:
    """证据完整性校验：描述、现场照片、温度记录（问题涉及温度时必须附）。

    通过后签发一次性令牌（10 分钟有效）；不通过返回 valid=False 与缺项清单，
    不抛异常——让模型把缺项转告用户，引导补证据。
    """
    rec = _find_one(args.get("record_id"))
    missing = []
    if len(str(rec["description"]).strip()) < DESC_MIN_LEN:
        missing.append("问题描述过于简略")
    if not rec["has_photo"]:
        missing.append("未附现场照片")
    if "温度" in rec["description"] and not rec["has_temperature_log"]:
        missing.append("问题涉及温度但未附温度记录")
    if missing:
        return {"valid": False, "evidence_token": None, "missing": missing,
                "message": "证据不完整，请通知巡店员补齐后再创建整改单"}
    token = secrets.token_hex(16)
    db.save_evidence_token(token, rec["id"])
    return {"valid": True, "evidence_token": token, "missing": [],
            "message": "证据校验通过，令牌10分钟内有效且只能使用一次"}


def _tool_create_rectification_draft(ctx: ToolContext, args: dict) -> dict:
    """创建整改单草稿——代码层硬校验的落点。

    evidence_token 必须来自 validate_evidence 的真实签发：存在、未用、未过期、
    归属同一巡检单。缺令牌、伪造令牌、拿别单的令牌一律拒绝。
    """
    token = str(args.get("evidence_token") or "").strip()
    advice = str(args.get("advice") or "").strip()
    if not token:
        raise ToolError(
            "MISSING_VALIDATION",
            "缺少证据令牌：必须先调用 validate_evidence 并通过校验，才能创建整改单草稿",
        )
    if not advice:
        raise ToolError("INVALID_PARAM", "advice（整改建议）不能为空")
    rec = _find_one(args.get("record_id"))
    if not db.consume_evidence_token(token, rec["id"]):
        raise ToolError(
            "EVIDENCE_TOKEN_INVALID",
            "证据令牌无效、已使用或已过期：请重新调用 validate_evidence 获取新令牌",
        )
    draft_id = db.create_draft(
        rec["id"], token, advice,
        category=str(args.get("category") or "").strip() or None,
        severity=str(args.get("severity") or "").strip() or None,
        created_by=ctx.user_id,
    )
    return {
        "draft_id": draft_id,
        "status": "draft",
        "message": "整改单草稿已创建（未派发）。请调用 submit_for_review 提交人工审核",
    }


def _tool_submit_for_review(ctx: ToolContext, args: dict) -> dict:
    """草稿转人工审核（流程终点）：只有 draft 状态可提交，防止重复提交/越权改状态。"""
    try:
        draft_id = int(args.get("draft_id", 0))
    except (TypeError, ValueError):
        raise ToolError("INVALID_PARAM", "draft_id 必须是整数")
    row = db.get_draft(draft_id)
    if row is None:
        raise ToolError("DRAFT_NOT_FOUND", f"整改单草稿 {draft_id} 不存在")
    if row["status"] != "draft":
        raise ToolError("DRAFT_NOT_DRAFT", "该整改单不是草稿状态，不能重复提交")
    db.submit_draft(draft_id)
    return {"draft_id": draft_id, "status": "pending_review",
            "message": "已提交人工审核，定级与处置由人工确认"}


# 工具名 → 实现函数的分发表
_TOOL_FUNCS = {
    "search_policy": _tool_search_policy,
    "get_store_record": _tool_get_store_record,
    "classify_issue": _tool_classify_issue,
    "validate_evidence": _tool_validate_evidence,
    "create_rectification_draft": _tool_create_rectification_draft,
    "submit_for_review": _tool_submit_for_review,
}


def run_tool(name: str, arguments, ctx: ToolContext) -> dict:
    """统一入口：分发执行并把错误折叠成 {"error": {code, message}} 返回给模型。

    永不抛异常——智能体循环把返回值以 role=tool 消息回传，模型看到错误码
    （如 MISSING_VALIDATION）可自行补救（先去做证据校验）。
    """
    try:
        fn = _TOOL_FUNCS.get(name)
        if fn is None:
            raise ToolError("UNKNOWN_TOOL", f"未知工具：{name}")
        args = arguments if isinstance(arguments, dict) else {}
        return {"ok": True, "result": fn(ctx, args)}
    except ToolError as e:
        return {"ok": False, "error": {"code": e.code, "message": e.message}}
    except Exception:
        return {"ok": False, "error": {"code": "TOOL_ERROR", "message": "工具执行失败，请稍后重试"}}
