# 连锁巡店与运营改进 · my-tool

面向连锁餐饮的巡店制度问答与整改建议助手（课程《FDE 工程技术与创新实践》二期项目）。
项目源自六岗位访谈与 36 条事实：AI 负责证据结构化、问题归类建议、制度问答与整改单草稿；
**定级、复查与处置仍由人工完成**（HITL）。

> 边界：不自动扣罚、不自动发布合格结论、不用整改照片代替复查、不自动定级。

本 README 即仓库主入口（含完整安装/演示流程与提示词 1~10 交付对照表）。

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

### 4.1 src 交付对照（问答助手归档 + MVP）

| 要求 | 落地 | 查证方式 |
|---|---|---|
| 问答助手归档 | 阶段2 完整可运行的问答助手（RAG 问答 + SSE 流式 + 出处引用 + BM25 降级）归档于 git 标签 **`qa-assistant-v1`**；其能力同时保留为当前代码的降级路径（LLM 不可用时自动切回固定流程问答） | `git checkout qa-assistant-v1` 即可回到该版本运行 |
| MVP：工程架构 | 分层架构：api（接口/鉴权/SSE）· agents（编排）· services（检索）· core（规则）· utils | `src/my_tool/` 五个模块与 tests/ 一一对应 |
| MVP：Agent 框架 | 自研 Harness 循环：模型决策 → 工具执行 → 结果回填 → 再决策，最多 5 轮熔断 | `agents/agent_loop.py`；tests/test_agent.py |
| MVP：知识库 | 制度文档切片 + FAISS 向量 + BM25 关键词混合检索（RRF 融合），向量接口不可用自动降级 | `services/kb_service.py`；tests/test_rag.py |
| MVP：人工确认/审核点（≥3 处） | 见下表，共 **4 处** | 每处均有对应测试与真机验证 |

**人工确认/审核点（4 处）：**

| # | 环节 | 位置 | 测试 |
|---|---|---|---|
| 1 | 建整改单前强制确认（HITL）：AI 只能出草稿预览，员工点「确认建单」后才创建 | `agents/agent_loop.py`（挂起）+ `api/chat.py /api/chat/confirm` | tests/test_agent.py 三段式用例 |
| 2 | 证据校验门禁：无有效一次性证据令牌一律拒绝建单 | `agents/tools.py:validate_evidence / create_rectification_draft` | tests/test_agent_tools.py |
| 3 | 草稿提交人工审核：定级与处置由人工完成，AI 不自动裁决 | `agents/tools.py:submit_for_review` | tests/test_agent_tools.py |
| 4 | 管理后台审计：管理员复核对话与违规标红、规则版本由人工升版 | `api/templates/admin/` | tests/test_admin.py |

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
