# 课程作业仓库 · FDE 工程技术与创新实践

本仓库包含两个课程项目：

| 项目 | 位置 | 说明 |
| --- | --- | --- |
| 巡店评分 AI 助手（第二节课作业） | `docs/`、`src/` | AI 只做证据结构化、问题归类建议、培训建议与审计台账；定级、复查与处置仍由人工完成 |
| 报销智能助手（前一项目） | [`baoxiao-assistant/`](baoxiao-assistant/README.md) | 纯前端 MVP，员工自助查询报销制度与本人单据状态 |

---

## 一、巡店评分 AI 助手

面向连锁餐饮的巡店评分与整改建议助手。项目源自六岗位访谈与 36 条事实，所有能力均为**待验证方案**，不是已上线产品。

> 边界：不自动扣罚、不自动发布合格结论、不用整改照片代替复查。

### 1. 作业交付物（均在 docs/）

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
├── docs/               # 作业交付物与设计文档
│   └── assets/         # 两张流程图
├── src/my_tool/        # 源码主目录
│   ├── core/           # 归类建议与校验（规则版本化、置信度阈值、转人工）
│   ├── agents/         # 智能体编排层：LLM 网关（流式/供应商可配置）、提示词
│   ├── services/       # 服务层：制度知识库（切片 + FAISS/BM25 混合检索）
│   ├── api/            # 接口层：登录鉴权(JWT)、/api/chat(SSE)、知识库/提示词/审计管理、SSR 后台页
│   └── utils/          # 通用校验工具
├── tests/              # 单元测试（与 src 结构对应）
├── examples/           # 可运行示例：demo.py（归类建议）、rag_prototype.py（制度问答）
├── scripts/            # 构建与部署脚本
├── config/             # 默认参数
├── data/               # 样例数据：巡店记录、示例制度文档、SQLite 会话库
├── .github/            # CI 与协作模板
└── baoxiao-assistant/  # 前一项目：报销智能助手（纯前端 MVP）
```

## 二、报销智能助手

见 [baoxiao-assistant/README.md](baoxiao-assistant/README.md)。纯前端实现，双击 `index.html` 即可运行。

## 三、许可证

MIT，详见 [LICENSE](LICENSE)。
