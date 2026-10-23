#!/usr/bin/env bash
# 部署脚本（课程作业版）：只做打包与文档发布准备，不连接真实业务系统。
# 说明：本项目为建议模式，不写回巡店系统、不触发扣罚、不发布合格结论。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TARGET="${1:-dist}"
mkdir -p "$TARGET"

echo "打包含 src 与 docs ..."
tar -czf "$TARGET/store-audit-assistant-$(date +%Y%m%d).tar.gz" src docs config data examples README.md requirements.txt

echo "产物："
ls -lh "$TARGET"

cat <<'EOF'

上线前置检查（未完成前不得进入试点）：
  1. 只读接口可用，快照兜底与按业务编号去重已验证
  2. 严重度版本口径、归类一致率比对口径已由业务侧裁定
  3. 各人工确认点（签收、核对、复查、删除发起权）已落人
  4. 角色—数据—操作矩阵已评审，禁止借用账号
EOF
