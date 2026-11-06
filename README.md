# 连锁巡店与运营改进 · my-tool

面向连锁餐饮的巡店制度问答与整改建议助手（课程《FDE 工程技术与创新实践》二期项目）。
项目源自六岗位访谈与 36 条事实：AI 负责证据结构化、问题归类建议、制度问答与整改单草稿；
**定级、复查与处置仍由人工完成**（HITL）。

> 边界：不自动扣罚、不自动发布合格结论、不用整改照片代替复查、不自动定级。

仓库主入口（含完整安装/演示流程与提示词 1~10 交付对照表）见 [上级目录 README](../README.md)；
本文件是 my-tool 子目录的技术说明。仓库根还有早期纯前端原型，已归档至 `archive/`。

### 1. 阶段交付物（docs/）

| 交付物 | 文件 | 说明 |
| --- | --- | --- |
| 业务挖掘 | [docs/01-业务挖掘报告.md](docs/01-业务挖掘报告.md) / [.docx](docs/01-业务挖掘报告.docx) | 六岗位访谈、36 条事实、6 条候选需求 |
| 行业分析 | [docs/02-行业分析.docx](docs/02-行业分析.docx) | 行业背景、共性痛点、同类产品、技术条件 |
| 流程图（现状） | [docs/assets/03-流程图-现状.jpg](docs/assets/03-流程图-现状.jpg) | 七泳道业务流程图 |
| 流程图（转型后） | [docs/assets/03-流程图-数智化转型后.jpg](docs/assets/03-流程图-数智化转型后.jpg) | 数智化转型后 To-Be 图 |
| 设计方案 | [docs/04-设计方案.docx](docs/04-设计方案.docx) / [docs/design.md](docs/design.md) | 范围边界、架构、模块、权限、验收 |
| 报价单 | [docs/05-报价单.xlsx](docs/05-报价单.xlsx) | 首期 13 万元拆分与付款节点 |

文档索引见 [docs/README.md](docs/README.md)，接口约定见 [docs/api.md](docs/api.md)。

### 2. 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. 运行

```bash
# 示例：对样例巡店记录给出归类与严重度建议
python examples/demo.py

# 或作为模块运行（源码在 src/ 下，需要指定 PYTHONPATH）
PYTHONPATH=src python -m my_tool.main --record data/sample_records.json

# 阶段1：巡店制度问答（RAG，需配置大模型 API）
export LLM_API_KEY="你的key"        # 默认硅基流动，可 LLM_PROVIDER=deepseek/zhipu 切换
python examples/rag_prototype.py data/巡店制度示例.md

# 阶段2+3：网页聊天 + 管理后台（登录、知识库、提示词模板、审计）
PYTHONPATH=src python -m uvicorn my_tool.api.app:app --port 8000
# 浏览器打开 http://127.0.0.1:8000 → 登录页
#   员工账号 zhangsan / 123456 → 巡店制度问答
#   管理员账号 admin / admin123 → /admin/kb 知识库管理、/admin/prompts 提示词模板、/admin/audit 对话审计
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

### 4. 测试

```bash
pip install -r requirements.txt
PYTHONPATH=src pytest -q
```

### 5. 目录结构

```text
.
├── docs/               # 作业交付物与设计文档（评测报告、上线检查清单也在 docs/）
│   └── assets/         # 两张流程图
├── src/my_tool/        # 源码主目录
│   ├── core/           # 归类建议与校验（规则版本化、置信度阈值、转人工）+ 提示词注入防护
│   ├── agents/         # 智能体编排层：Harness 循环、LLM 网关（流式/供应商可配置）、六工具
│   ├── services/       # 服务层：制度知识库（切片 + FAISS/BM25 混合检索）
│   ├── api/            # 接口层：登录鉴权(JWT)、/api/chat(SSE+限流)、HITL 确认、知识库/提示词/审计管理、SSR 后台页
│   └── utils/          # 通用校验工具
├── tests/              # 单元测试（105 项，与 src 结构对应）
├── examples/           # 可运行示例：demo.py（归类建议）、rag_prototype.py（制度问答）
├── scripts/            # 构建部署脚本 + run_eval.py（30 条评测运行器）
├── config/             # 默认参数
├── data/               # 样例数据：巡店记录、示例制度文档、评测用例（30 条）、SQLite 会话库
└── .github/            # CI 与协作模板
```

## 许可证

MIT，详见 [LICENSE](LICENSE)。
