"""FastAPI 应用装配（★阶段2核心）：路由、静态页、知识库懒加载。

启动：PYTHONPATH=src python -m uvicorn my_tool.api.app:app --port 8000
知识库在第一次访问时构建并驻留内存（构建要调向量化 API，失败不阻塞启动，
服务可先起来，health 里能看到 KB_NOT_READY 提示）。
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from my_tool.api import db
from my_tool.api.chat import router as chat_router
from my_tool.api.errors import ApiError, install_error_handlers
from my_tool.api.kb_runtime import ensure_kb, init_kb_state

# 静态页位置：src/my_tool/api/static/index.html
_STATIC = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    """创建 FastAPI 应用：统一错误处理 + /api/chat + 健康检查 + 首页。"""
    app = FastAPI(title="连锁巡店助手 API", version="0.3.0")
    install_error_handlers(app)
    app.include_router(chat_router)
    init_kb_state(app)

    @app.get("/")
    async def index():
        """聊天单页（原生 HTML+JS）。"""
        return FileResponse(str(_STATIC / "index.html"))

    @app.get("/api/health")
    async def health():
        """健康检查：同时充当知识库状态探针（前端/脚本自检用）。"""
        try:
            ensure_kb(app)
        except Exception:
            raise ApiError(503, "KB_NOT_READY", "知识库未就绪，请检查 LLM_API_KEY 与 data/ 制度文档")
        kb = app.state.kb
        return {
            "code": "OK",
            "message": "服务正常",
            "kb_docs": sorted({c["doc"] for c in kb.chunks}),
            "kb_chunks": len(kb.chunks),
        }

    @app.on_event("startup")
    async def startup():
        db.db_path().parent.mkdir(parents=True, exist_ok=True)

    return app


app = create_app()
