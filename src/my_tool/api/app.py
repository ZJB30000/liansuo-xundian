"""FastAPI 应用装配（★阶段3）：鉴权、管理后台、知识库懒加载、SSR 页面。

启动：PYTHONPATH=src python -m uvicorn my_tool.api.app:app --port 8000
启动时初始化数据库（建表/迁移/种子账号/默认提示词模板）并把 data/ 下
的示例制度导入知识库（库非空则跳过）；索引在首次访问时构建。
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from my_tool.api import db, kb_store
from my_tool.api.audit import router as audit_router
from my_tool.api.auth import router as auth_router
from my_tool.api.chat import router as chat_router
from my_tool.api.errors import ApiError, install_error_handlers
from my_tool.api.kb_admin import router as kb_admin_router
from my_tool.api.kb_runtime import ensure_kb, init_kb_state
from my_tool.api.pages import router as pages_router

# 静态页位置：src/my_tool/api/static/
_STATIC = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    """创建 FastAPI 应用：统一错误处理 + 全部路由 + 启动初始化。"""
    app = FastAPI(title="连锁巡店助手 API", version="0.4.0")
    install_error_handlers(app)

    app.include_router(auth_router)         # 登录鉴权
    app.include_router(chat_router)         # 员工问答（需登录）
    app.include_router(kb_admin_router)     # 知识库管理（需管理员）
    app.include_router(audit_router)        # 对话审计（需管理员）
    app.include_router(pages_router)        # 页面：/login /admin/kb /admin/prompts /admin/audit
    init_kb_state(app)

    @app.get("/")
    async def index():
        """员工问答页（静态，未登录由前端跳 /login）。"""
        return FileResponse(str(_STATIC / "index.html"))

    @app.get("/api/health")
    async def health():
        """健康检查：知识库状态探针（前端/脚本自检用）。"""
        try:
            ensure_kb(app)
        except Exception:
            raise ApiError(503, "KB_NOT_READY", "知识库未就绪，请检查 LLM_API_KEY 与制度文档")
        kb = app.state.kb
        return {
            "code": "OK",
            "message": "服务正常",
            "kb_docs": sorted({c["doc"] for c in kb.chunks}),
            "kb_chunks": len(kb.chunks),
        }

    @app.on_event("startup")
    async def startup():
        # 数据库初始化：建表、旧库迁移、种子账号与默认提示词模板
        db._connect()
        # 首次启动把 data/ 的示例制度导入知识库（库非空则跳过，只切片不向量化）
        kb_store.seed_if_empty()

    return app


app = create_app()
