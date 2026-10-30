"""管理后台接口（阶段3）：知识库上传/列表/删除、提示词模板读写。仅管理员。"""

import io
import json

from fastapi import APIRouter, Depends, File, Request, UploadFile

from my_tool.api import kb_store
from my_tool.api.errors import ApiError
from my_tool.api.security import require_admin
from my_tool.services.kb_service import chunk_markdown

router = APIRouter(dependencies=[Depends(require_admin)])   # 整组接口仅管理员

MAX_UPLOAD_BYTES = 5 * 1024 * 1024     # 上限 5MB，课程项目足够
MAX_PROMPT_LEN = 2000


def _extract_text(filename: str, mime: str, raw: bytes) -> str:
    """按扩展名提取纯文本：md 直接解码；pdf 用 pypdf 逐页提取。"""
    name = filename.lower()
    if name.endswith(".md") or mime == "text/markdown":
        return raw.decode("utf-8", errors="strict")
    if name.endswith(".pdf") or mime == "application/pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            raise ApiError(400, "PDF_PARSE_FAILED", "PDF 解析失败，可能为扫描件，请先转成可复制文本的 PDF 或 markdown")
    raise ApiError(415, "UNSUPPORTED_FILE", "仅支持 markdown(.md) 或 PDF(.pdf) 文件")


@router.post("/api/admin/kb/upload")
async def upload_document(file: UploadFile = File(...), admin: dict = Depends(require_admin)):
    """上传制度文档：提取文本 → 500字切片 → 入库（向量化延迟到下次问答）。"""
    raw = await file.read()
    if not raw:
        raise ApiError(400, "EMPTY_FILE", "上传文件为空")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ApiError(400, "FILE_TOO_LARGE", "文件不能超过 5MB")

    text = _extract_text(file.filename, file.content_type or "", raw)
    texts = chunk_markdown(text)
    if not texts:
        raise ApiError(400, "EMPTY_CONTENT", "文档内容为空，无法切片")

    doc_id = kb_store.add_document(file.filename, file.content_type, texts, admin["user_id"])
    return {
        "code": "OK",
        "message": "上传成功",
        "doc_id": doc_id,
        "filename": file.filename,
        "chunks": len(texts),
    }


@router.get("/api/admin/kb/documents")
async def list_documents():
    """文档列表：含切片数与向量化状态。"""
    return {"code": "OK", "items": kb_store.list_documents()}


@router.delete("/api/admin/kb/documents/{doc_id}")
async def delete_document(doc_id: int):
    """删除文档及切片，触发索引重建（kb_version +1）。"""
    if not kb_store.delete_document(doc_id):
        raise ApiError(404, "DOC_NOT_FOUND", "文档不存在或已删除")
    return {"code": "OK", "message": "文档已删除"}


@router.get("/api/admin/prompt")
async def get_prompt():
    """读当前启用的系统提示词模板。"""
    return {"code": "OK", "content": kb_store.get_prompt(),
            "updated_at": kb_store.get_prompt_updated_at()}


@router.put("/api/admin/prompt")
async def put_prompt(request: Request, admin: dict = Depends(require_admin)):
    """保存新模板（版本化留痕），下一次问答立即生效。"""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise ApiError(400, "BODY_NOT_JSON", "请求体不是合法 JSON")
    content = (body or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise ApiError(400, "EMPTY_PROMPT", "模板内容不能为空")
    if len(content) > MAX_PROMPT_LEN:
        raise ApiError(400, "PROMPT_TOO_LONG", f"模板不能超过 {MAX_PROMPT_LEN} 字")

    version_id = kb_store.put_prompt(content.strip(), admin["user_id"])
    return {"code": "OK", "message": "模板已保存，立即生效", "version_id": version_id}
