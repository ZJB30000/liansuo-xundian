"""问题归类建议。

输入一条巡店记录，输出问题类别、严重度建议、置信度与是否转人工。
本模块只产出“建议”，不写回业务系统，不做扣罚与合格判定。
"""

from dataclasses import dataclass, asdict

from .rules import RULE_SETS, DEFAULT_RULE_VERSION

# 转人工的四类情形（f44）：资料缺少依据、版本冲突、能力未验证、系统不可用
HUMAN_REQUIRED_CODES = {"training_not_verified", "evidence_missing", "photo_as_review"}
HUMAN_REQUIRED_FLAGS = ("version_conflict", "system_unavailable")


@dataclass
class Advice:
    business_id: str
    category: str
    severity: str
    confidence: float
    needs_human: bool
    rule_version: str
    advice: str
    basis: tuple
    reason: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data["basis"] = list(self.basis)
        return data


def _pick_rule(text: str, rule_version: str):
    """按关键词命中数量挑选规则，命中越多越可信。"""
    rules = RULE_SETS[rule_version]
    best, best_hits = None, 0
    for rule in rules.values():
        hits = sum(1 for kw in rule.keywords if kw in text)
        if hits > best_hits:
            best, best_hits = rule, hits
    return best, best_hits


def classify_record(record: dict, rule_version: str = DEFAULT_RULE_VERSION,
                    threshold: float = 0.6) -> Advice:
    """对一条巡店记录给出归类建议。

    record 需包含 business_id 与 description；
    version_conflict / system_unavailable 为可选布尔标记。
    """
    text = str(record.get("description", ""))
    business_id = str(record.get("business_id", ""))
    rule, hits = _pick_rule(text, rule_version)

    if rule is None:
        return Advice(
            business_id=business_id,
            category="未命中规则",
            severity="待定",
            confidence=0.0,
            needs_human=True,
            rule_version=rule_version,
            advice="转人工修复队列，不进入自动推荐样本。",
            basis=(),
            reason="规则库未命中，按低置信处理（f33）",
        )

    # 关键词命中越多、文本越长，置信度越高；上限 0.95，避免绝对化
    density = hits / max(1, len(text) / 12)
    confidence = min(0.95, round(0.45 + 0.35 * min(hits, 3) + 0.15 * min(density, 1.0), 2))

    flags = [f for f in HUMAN_REQUIRED_FLAGS if record.get(f)]
    needs_human = (
        rule.code in HUMAN_REQUIRED_CODES
        or confidence < threshold
        or bool(flags)
    )

    reasons = [f"命中规则 {rule.code}"]
    if rule.code in HUMAN_REQUIRED_CODES:
        reasons.append("该类别必须人工确认")
    if flags:
        reasons.append("标记：" + "、".join(flags))
    if confidence < threshold:
        reasons.append(f"置信度低于阈值 {threshold}")
    if not reasons[1:]:
        reasons.append("证据字段完整")

    return Advice(
        business_id=business_id,
        category=rule.category,
        severity=rule.severity,
        confidence=confidence,
        needs_human=needs_human,
        rule_version=rule_version,
        advice=rule.advice,
        basis=rule.basis,
        reason="；".join(reasons),
    )
