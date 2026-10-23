"""归类规则库。

规则与访谈事实一一对应，版本化维护：业务侧确认口径后升版，
旧版本不删除，历史建议仍可按当时的版本重建审计链。
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rule:
    """单条归类规则。"""

    code: str
    category: str
    severity: str  # 高 / 中 / 低
    keywords: tuple
    basis: tuple = field(default=())  # 对应的访谈事实编号
    advice: str = ""


RULE_V1 = {
    "temperature_missing": Rule(
        code="temperature_missing",
        category="温度记录缺失",
        severity="高",
        keywords=("温度", "冷藏", "冷冻", "记录不全"),
        basis=("f11", "f21"),
        advice="现场复测温度并补记；对当班员工做一次温度记录演练，签到不作为学会依据。",
    ),
    "training_not_verified": Rule(
        code="training_not_verified",
        category="培训能力未验证",
        severity="高",
        keywords=("签到", "培训", "不会", "未掌握"),
        basis=("f11", "f15"),
        advice="暂停自动建议，转培训协调员核对；补一次实操验证后再判定。",
    ),
    "photo_as_review": Rule(
        code="photo_as_review",
        category="以照片代替复查",
        severity="高",
        keywords=("照片", "上传", "复查"),
        basis=("f40", "f42"),
        advice="照片只能触发复查，需现场复查并留下独立证据。",
    ),
    "evidence_missing": Rule(
        code="evidence_missing",
        category="证据不足",
        severity="中",
        keywords=("缺少", "无依据", "未附"),
        basis=("f44",),
        advice="补齐原始证据或转人工确认，不自行选择其中一版作为事实。",
    ),
    "hygiene_basic": Rule(
        code="hygiene_basic",
        category="基础卫生不达标",
        severity="中",
        keywords=("卫生", "消毒", "清洁", "异物"),
        basis=("f10",),
        advice="按门店卫生 SOP 整改，约定复查时间。",
    ),
    "label_expired": Rule(
        code="label_expired",
        category="标签与效期问题",
        severity="中",
        keywords=("标签", "效期", "过期"),
        basis=("f10",),
        advice="下架并登记，追溯进货批次。",
    ),
}

RULE_SETS = {"v1": RULE_V1}

DEFAULT_RULE_VERSION = "v1"
