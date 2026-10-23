# 巡店评分 AI 助手（Store Audit Assistant）

面向连锁餐饮的巡店评分与整改建议助手：AI 只做**证据结构化、问题归类建议、培训建议与审计台账**，最终定级、复查结论和现场处置仍由人工完成。项目源自课程作业的业务需求发现报告，所有能力均为**待验证方案**，不是已上线产品。

> 边界：不自动扣罚、不自动发布合格结论、不用整改照片代替复查。

## 一、这是什么

连锁餐饮总部依靠督导巡店发现问题，但存在三件事反复发生：

1. 不同督导对同一问题的严重度判定不一致，门店只知道扣分、不知道该怎么改；
2. 培训签到被当成“已经学会”，整改照片被当成“已经复查”；
3. 巡店系统、培训平台、门店档案三套系统状态割裂，无法连续追溯。

本项目把切入点收敛到「问题归类建议 + 培训建议」，先做历史资料回放，再进入 10 店并行试点。

## 二、文档入口（作业交付物）

| 交付物 | 文件 | 说明 |
| --- | --- | --- |
| 业务挖掘 | [docs/01-业务挖掘报告.md](docs/01-业务挖掘报告.md) / [.docx](docs/01-业务挖掘报告.docx) | 六岗位访谈、36 条事实、6 条候选需求 |
| 行业分析 | [docs/02-行业分析.docx](docs/02-行业分析.docx) | 行业背景、共性痛点、同类产品、技术条件 |
| 流程图（现状） | [docs/assets/03-流程图-现状.jpg](docs/assets/03-流程图-现状.jpg) | 七泳道人工现状图 |
| 流程图（转型后） | [docs/assets/03-流程图-数智化转型后.jpg](docs/assets/03-流程图-数智化转型后.jpg) | 数智化转型后 To-Be 图 |
| 设计方案 | [docs/04-设计方案.docx](docs/04-设计方案.docx) / [docs/design.md](docs/design.md) | 范围边界、架构、模块、权限、验收 |
| 报价单 | [docs/05-报价单.xlsx](docs/05-报价单.xlsx) | 首期 13 万元拆分与付款节点 |

## 三、安装

```bash
git clone <your-repo-url>
cd my-tool
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 四、运行

```bash
# 示例：对样例巡店记录给出归类与严重度建议
python examples/demo.py

# 或作为模块运行（源码在 src/ 下，需要指定 PYTHONPATH）
PYTHONPATH=src python -m my_tool.main --record data/sample_records.json

# 调整置信度阈值
PYTHONPATH=src python -m my_tool.main --record data/sample_records.json --threshold 0.8
```

输出示例：

```json
{
  "business_id": "XD-2026-0001",
  "category": "温度记录缺失",
  "severity": "高",
  "confidence": 0.95,
  "needs_human": false,
  "rule_version": "v1",
  "advice": "现场复测温度并补记；对当班员工做一次温度记录演练，签到不作为学会依据。",
  "basis": ["f11", "f21"],
  "reason": "命中规则 temperature_missing；证据字段完整"
}
```

## 五、测试

```bash
pip install -r requirements.txt
PYTHONPATH=src pytest -q
```

## 六、目录结构

```text
my-tool/
├── README.md
├── LICENSE
├── CHANGELOG.md
├── CONTRIBUTING.md
├── requirements.txt
├── src/my_tool/        # 归类建议与校验的骨架实现
├── docs/               # 作业交付物与设计文档
├── tests/              # 单元测试
├── examples/           # 可运行示例
├── scripts/            # 构建与部署脚本
├── config/             # 默认参数
├── data/               # 样例数据
└── .github/            # CI 与模板
```

## 七、许可证

MIT，详见 [LICENSE](LICENSE)。
