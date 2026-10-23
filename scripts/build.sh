#!/usr/bin/env bash
# 构建脚本：语法检查 + 打包 + 自检
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[1/4] 语法检查"
python3 -m compileall -q src examples tests

echo "[2/4] 运行测试"
PYTHONPATH=src python3 -m pytest -q

echo "[3/4] 运行示例"
PYTHONPATH=src python3 examples/demo.py > /dev/null

echo "[4/4] 检查交付物"
for f in docs/01-业务挖掘报告.md docs/01-业务挖掘报告.docx \
         docs/02-行业分析.docx docs/04-设计方案.docx docs/05-报价单.xlsx \
         docs/assets/03-流程图-现状.jpg docs/assets/03-流程图-数智化转型后.jpg; do
  test -f "$f" || { echo "缺少交付物: $f"; exit 1; }
done

echo "构建完成：所有检查通过"
