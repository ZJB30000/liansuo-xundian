"""可运行示例：对样例巡店记录给出归类建议。

运行：
    python examples/demo.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from my_tool.core.classifier import classify_record  # noqa: E402
from my_tool.utils.validators import validate_record  # noqa: E402


def main():
    path = os.path.join(ROOT, "data", "sample_records.json")
    with open(path, encoding="utf-8") as f:
        records = json.load(f)["records"]

    for record in records:
        print("=" * 60)
        print(f"业务编号：{record['business_id']}")
        issues = validate_record(record)
        if issues:
            print("校验未通过，转人工修复队列：")
            for issue in issues:
                print("  -", issue)
            continue
        advice = classify_record(record).to_dict()
        print(f"归类建议：{advice['category']}（严重度：{advice['severity']}）")
        print(f"置信度：{advice['confidence']}｜转人工：{advice['needs_human']}")
        print(f"建议动作：{advice['advice']}")
        print(f"依据：{'、'.join(advice['basis']) or '无'}")


if __name__ == "__main__":
    main()
