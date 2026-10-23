"""单元测试（与 src/my_tool 结构对应）。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from my_tool.core.classifier import classify_record  # noqa: E402
from my_tool.core.rules import RULE_SETS, DEFAULT_RULE_VERSION  # noqa: E402
from my_tool.utils.validators import validate_record, is_valid  # noqa: E402


def make_record(**kwargs):
    base = {
        "business_id": "XD-2026-0001",
        "event_time": "2026-10-20 09:30",
        "store_id": "S-018",
        "description": "冷藏柜温度记录缺失，现场未找到当日记录",
        "rule_version": "v1",
    }
    base.update(kwargs)
    return base


def test_rule_sets_have_default_version():
    assert DEFAULT_RULE_VERSION in RULE_SETS
    assert RULE_SETS["v1"]


def test_classify_temperature_missing():
    advice = classify_record(make_record())
    assert advice.category == "温度记录缺失"
    assert advice.severity == "高"
    assert advice.needs_human is False
    assert 0 < advice.confidence <= 0.95


def test_classify_training_not_verified_always_human():
    advice = classify_record(make_record(description="培训签到齐全，但新员工仍不会温度记录"))
    assert advice.category == "培训能力未验证"
    assert advice.needs_human is True


def test_low_confidence_requires_human():
    advice = classify_record(make_record(description="卫生"), threshold=0.99)
    assert advice.needs_human is True
    assert "置信度" in advice.reason


def test_unmatched_text_goes_to_repair_queue():
    advice = classify_record(make_record(description="门店门口地面有轻微水渍"))
    assert advice.category == "未命中规则"
    assert advice.confidence == 0.0
    assert advice.needs_human is True


def test_version_conflict_flag_requires_human():
    advice = classify_record(make_record(version_conflict=True))
    assert advice.needs_human is True


def test_valid_record_passes():
    assert validate_record(make_record()) == []
    assert is_valid(make_record()) is True


def test_missing_field_detected():
    record = make_record()
    del record["store_id"]
    issues = validate_record(record)
    assert any("store_id" in i for i in issues)
    assert is_valid(record) is False


def test_bad_business_id_detected():
    issues = validate_record(make_record(business_id="XD2026-1"))
    assert any("业务编号" in i for i in issues)


def test_bad_event_time_detected():
    issues = validate_record(make_record(event_time="2026/10/20"))
    assert any("事件时间" in i for i in issues)


def test_advice_serializable():
    advice = classify_record(make_record())
    data = advice.to_dict()
    assert isinstance(data["basis"], list)
    assert data["business_id"] == "XD-2026-0001"
