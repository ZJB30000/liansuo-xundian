"""页面路由（阶段3）：登录页、聊天页静态文件 + 管理后台 SSR 页面。

管理后台用 Jinja2 服务端渲染初始数据（文档列表/当前模板），页面内的
增删改仍走 /api/* 接口；接口层强制 JWT+角色校验，页面仅做跳转引导。
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates

from my_tool.api import kb_store

router = APIRouter()
_STATIC = Path(__file__).resolve().parent / "static"
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


@router.get("/login")
async def login_page():
    """登录页（静态）。"""
    return FileResponse(str(_STATIC / "login.html"))


@router.get("/admin/kb")
async def admin_kb(request: Request):
    """知识库管理页：SSR 渲染当前文档列表。"""
    return templates.TemplateResponse(
        request, "admin/kb.html", {"documents": kb_store.list_documents()})


@router.get("/admin/prompts")
async def admin_prompts(request: Request):
    """提示词模板页：SSR 渲染当前启用模板与更新时间。"""
    return templates.TemplateResponse(
        request, "admin/prompts.html",
        {"content": kb_store.get_prompt(), "updated_at": kb_store.get_prompt_updated_at()})


@router.get("/admin/audit")
async def admin_audit(request: Request):
    """对话审计页：筛选条件在前端，结果由 JS 拉取接口渲染。"""
    return templates.TemplateResponse(request, "admin/audit.html", {})
