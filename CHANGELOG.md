# 更新记录

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)：主版本号.次版本号.修订号。

## [0.5.0] - 2026-11-06

### 新增（阶段5：Agent 编排 → 评测 → 上线检查 → 结构整理，老师提示词 7~10）
- **Agent Harness**（提示词7）`agents/agent_loop.py`：模型决策→执行工具→结果回填→再决策，最多 5 轮熔断；每轮落 `agent_logs`（工具名/参数/耗时/结果摘要）；`create_rectification_draft` 调用前 HITL 强制中断——SSE 推 `confirm_request`（草稿预览+一次性令牌），员工确认后经 `POST /api/chat/confirm` 恢复执行；前端确认卡片（确认/拒绝）
- **项目评测**（提示词8）`data/eval_cases.json` 30 条评测用例（制度/工具调用/违规意图/多轮上下文，含 3 条陷阱题）+ `scripts/run_eval.py` 评测运行器（工具序列判定+关键词断言+`--strict` 门禁）+ 真机 glm-4-flash 评测报告 `docs/eval_report.md`（22/30，评测抓出 2 个真实弱点）
- **上线检查**（提示词9）`docs/launch_checklist.md` 25 项（5 维度×5 项，每项含负责人/验证方法/通过标准/状态，达标 17/25）；落地：`core/prompt_guard.py` 注入防护（4 类模式检测+防御声明）、`api/ratelimit.py` 同用户并发限流（429）、LLM 超时兜底测试；测试增至 105 项
- **结构整理**（提示词10）：仓库更名 `liansuo-xundian`，报销原型与早期流程图归档 `archive/`，主 README 重写为上交门面

### 变更
- `api/chat.py`：限流接入、注入防御双路接入（智能体/固定流程）、answer 事件转打字机 delta
- 轮数熔断上限 8 → 5；种子巡检记录调整为证据完整性梯度（0001 缺温度/0002 缺照片/0003、0004 齐全）

## [Unreleased]

### 计划
- 归类一致率比对口径确定后，补充一致率测评脚本
- 节点耗时打点与 5 天闭环口径的可视化报表
- 阶段4：整改状态机（超时升级）与照片上传（对象存储）

## [0.4.0] - 2026-10-30

### 新增（管理后台，老师提示词5 巡店口径）
- 登录鉴权 `api/security.py` + `api/auth.py`：PBKDF2 密码哈希、JWT（12h）、接口级 staff/admin 权限校验；种子账号 admin/admin123、zhangsan/123456
- 知识库管理 `api/kb_admin.py` + `api/kb_store.py`：上传 markdown/PDF → 500 字切片 → 入库（向量化延迟到下次问答批量完成）→ 列表可删除；切片与向量持久化 SQLite，kb_version 驱动内存索引重建
- 提示词模板管理：默认巡店口径模板存数据库（版本化留痕），管理员修改后立即对问答生效；输出格式红线（引用出处/拒答）为固定后缀不可绕过
- 对话审计 `api/audit.py` + `api/moderation.py`：按员工/时间筛选，命中“规避检查”关键词的问答自动标记疑似违规
- 管理页面（Jinja2 服务端渲染）：/login、/admin/kb、/admin/prompts、/admin/audit，SaaS 风格
- /api/chat 改造：需登录、会话归属员工、用户消息违规扫描
- 测试：tests/conftest.py 公共夹具 + test_admin.py（共 40 项全部离线通过）

### 变更
- 知识库切片从“启动读 data/*.md”改为“库表持久化 + 版本号重建”，首次启动自动播种示例制度

## [0.3.0] - 2026-10-30

### 新增
- 聊天服务 `src/my_tool/api/`：FastAPI 应用装配、`POST /api/chat` SSE 流式接口、`GET /api/health` 健康检查、聊天单页（原生 HTML+JS，打字机效果 + “正在检索巡店制度…”状态 + 回答底部引用来源）
- 统一错误契约 `api/errors.py`：全部接口失败返回 `{"code","message"}`，中文信息（EMPTY_QUESTION / QUESTION_TOO_LONG / SESSION_NOT_FOUND / KB_NOT_READY / LLM_TIMEOUT / LLM_ERROR 等）
- 会话存储 `api/db.py`：SQLite（conversations / messages，沿用阶段1表结构），每次请求携带最近 6 轮历史
- 知识库运行时 `api/kb_runtime.py`：启动后首次访问构建 FAISS+BM25 双路索引并驻留内存，切片带来源文件名
- LLM 网关新增流式接口 `llm_chat_stream`（打字机效果依赖）；提示词扩展多轮历史与《来源文件名》标注
- 接口测试 `tests/test_api.py`（10 项，LLM/向量全离线假实现）

## [0.2.0] - 2026-10-30

### 新增
- LLM 网关 `src/my_tool/agents/llm_gateway.py`：对话/向量调用统一封装，供应商可配置（硅基流动/DeepSeek/智谱），业务代码不直接触碰外部 API
- 知识库服务 `src/my_tool/services/kb_service.py`：制度文档 500 字切片（按标题分节 + 50 字重叠）、FAISS 向量检索 + BM25 关键词检索、RRF 混合融合取 top3
- 制度问答提示词 `src/my_tool/agents/prompts.py`：只依据检索片段回答、必须附出处、无依据拒答（人工红线落点）
- 阶段1 裸问答原型示例 `examples/rag_prototype.py`（命令行，无 Web）与示例制度文档 `data/巡店制度示例.md`
- RAG 链路离线单元测试 `tests/test_rag.py`（假向量替换外部调用，CI 可跑）

### 变更
- RAG 原型核心逻辑从单脚本迁入 `src/my_tool/`（对应课程框架的 src 源码目录），examples 只保留命令行编排
- requirements.txt 新增 openai、faiss-cpu、rank_bm25、jieba、numpy

## [0.1.0] - 2026-10-23

### 新增
- 业务需求发现报告：36 条访谈事实、6 条候选需求（P0/P1/P2）
- 行业分析文档：行业背景、共性痛点、同类产品、技术条件与差异点
- 两张泳道流程图：人工现状（As-Is）与数智化转型后（To-Be）
- 设计方案：范围边界、总体架构、功能模块、权限审计、人工兜底、实施计划
- 报价单：首期 13 万元拆分、付款节点与工期
- 归类建议骨架实现：规则版本化、置信度阈值、转人工判定
- 单元测试与 GitHub Actions 持续集成

### 说明
- 所有指标为目标值，非承诺；方案为待验证假设
- 首期不做自动扣罚与合格结论发布
