"""程序入口。

用法：
    python -m my_tool.main --record data/sample_records.json
    python -m my_tool.main --record data/sample_records.json --threshold 0.8
"""

import argparse
import json
import sys

from .core.classifier import classify_record
from .core.rules import DEFAULT_RULE_VERSION, RULE_SETS
from .utils.validators import validate_record


def load_records(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("records", [])
    return data


def run(path: str, threshold: float, rule_version: str) -> int:
    records = load_records(path)
    bad = 0
    for record in records:
        issues = validate_record(record)
        if issues:
            bad += 1
            print(json.dumps({
                "business_id": record.get("business_id", ""),
                "status": "转人工修复队列",
                "issues": issues,
                "note": "歧义记录不进入自动推荐样本（f33）",
            }, ensure_ascii=False, indent=2))
            continue
        advice = classify_record(record, rule_version=rule_version, threshold=threshold)
        print(json.dumps(advice.to_dict(), ensure_ascii=False, indent=2))
    print(f"\n共 {len(records)} 条记录，其中 {bad} 条需人工修复。")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="巡店评分 AI 助手（建议模式，不写回业务系统）")
    parser.add_argument("--record", required=True, help="巡店记录 JSON 文件路径")
    parser.add_argument("--threshold", type=float, default=0.6, help="置信度阈值，低于阈值转人工")
    parser.add_argument("--rule-version", default=DEFAULT_RULE_VERSION,
                        choices=sorted(RULE_SETS.keys()), help="规则库版本")
    args = parser.parse_args(argv)
    return run(args.record, args.threshold, args.rule_version)


if __name__ == "__main__":
    sys.exit(main())
