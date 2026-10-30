# 更新记录

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)：主版本号.次版本号.修订号。

## [Unreleased]

### 计划
- 归类一致率比对口径确定后，补充一致率测评脚本
- 节点耗时打点与 5 天闭环口径的可视化报表
- 阶段3：整改状态机（超时升级）与照片上传（对象存储）

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
