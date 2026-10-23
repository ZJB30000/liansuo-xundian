"""通用校验：业务编号、必填字段、版本字段。"""

import re

BUSINESS_ID_PATTERN = re.compile(r"^[A-Z]{2,4}-\d{4}-\d{4}$")
REQUIRED_FIELDS = ("business_id", "event_time", "store_id", "description")


def validate_record(record: dict) -> list:
    """返回问题列表，空列表表示通过校验。

    校验范围对应 f33：主编号、事件时间、版本字段。
    """
    issues = []

    for name in REQUIRED_FIELDS:
        if not str(record.get(name, "")).strip():
            issues.append(f"缺少必填字段：{name}")

    business_id = str(record.get("business_id", ""))
    if business_id and not BUSINESS_ID_PATTERN.match(business_id):
        issues.append(f"业务编号格式不规范：{business_id}")

    event_time = str(record.get("event_time", ""))
    if event_time and not re.match(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?$", event_time):
        issues.append(f"事件时间格式应为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM：{event_time}")

    if "rule_version" in record and not str(record["rule_version"]).strip():
        issues.append("版本字段为空")

    return issues


def is_valid(record: dict) -> bool:
    return not validate_record(record)
