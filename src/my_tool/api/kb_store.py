"""知识库与提示词模板持久化（阶段3）：文档/切片入库、版本号、模板版本表。

与阶段2 的区别：切片不再只挂在内存里，而是存 SQLite——上传/删除由管理页
触发，问答检索读取库中切片；kb_version 变化驱动内存索引重建。
"""

import os
from pathlib import Path

from my_tool.api import db
from my_tool.services.kb_service import chunk_markdown

_REPO_ROOT = Path(__file__).resolve().parents[3]

# 默认系统提示词模板（巡店口径，管理员可在 /admin/prompts 修改，存库不写死）
DEFAULT_PROMPT_TEMPLATE = (
    "你是连锁巡店制度助手，只根据提供的巡店制度条文回答，"
    "不确定时明确说“制度中未找到”，回答末尾附引用来源，不得编造巡店标准。"
)


# ============================================================
# meta 版本号：知识库任何变更 +1，内存索引据此重建
# ============================================================
def _connect():
    return db._connect()


def get_kb_version() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key='kb_version'").fetchone()
    return int(row["value"]) if row else 0


def bump_kb_version() -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('kb_version', '1') "
            "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)"
        )


# ============================================================
# 文档与切片
# ============================================================
def seed_if_empty() -> int:
    """首次启动：若知识库为空，把 data/ 下的示例制度 md 导入（只切片不向量化，
    向量化延迟到下次问答时统一完成，避免启动就要求 API key）。返回导入文档数。"""
    with _connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM kb_documents").fetchone()["n"]
    if n:
        return 0
    kb_dir = Path(os.environ.get("KB_DIR", str(_REPO_ROOT / "data")))
    count = 0
    for p in sorted(kb_dir.glob("*.md")):
        with open(p, encoding="utf-8") as f:
            texts = chunk_markdown(f.read())
        if texts:
            add_document(p.name, "text/markdown", texts, uploaded_by=None)
            count += 1
    return count


def add_document(filename: str, mime: str, texts: list, uploaded_by: int = None) -> int:
    """写入文档与其切片（embedding 先留空），返回文档ID。"""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO kb_documents (filename, mime, chunk_count, status, uploaded_by) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (filename, mime, len(texts), uploaded_by),
        )
        doc_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO kb_chunks (doc_id, text) VALUES (?, ?)",
            [(doc_id, t) for t in texts],
        )
    bump_kb_version()
    return doc_id


def list_documents() -> list:
    """文档列表：ID、文件名、切片数、状态、上传时间。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, filename, mime, chunk_count, status, created_at "
            "FROM kb_documents ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_document(doc_id: int) -> bool:
    """删除文档及其切片；返回是否真的删了。"""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM kb_documents WHERE id = ?", (doc_id,))
        deleted = cur.rowcount > 0
        if deleted:
            conn.execute("DELETE FROM kb_chunks WHERE doc_id = ?", (doc_id,))
    if deleted:
        bump_kb_version()
    return deleted


def all_chunks() -> list:
    """全部切片（带文档名与已存向量），供建索引。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT c.id, c.text, c.embedding_json, d.filename AS doc "
            "FROM kb_chunks c JOIN kb_documents d ON d.id = c.doc_id ORDER BY c.id"
        ).fetchall()
    return [dict(r) for r in rows]


def save_embedding(chunk_id: int, embedding_json: str) -> None:
    """回写切片向量；全部就绪后把所属文档标记 ready。"""
    with _connect() as conn:
        conn.execute("UPDATE kb_chunks SET embedding_json = ? WHERE id = ?", (embedding_json, chunk_id))
        conn.execute(
            "UPDATE kb_documents SET status='ready' "
            "WHERE id = (SELECT doc_id FROM kb_chunks WHERE id = ?) "
            "AND NOT EXISTS (SELECT 1 FROM kb_chunks WHERE doc_id = "
            "  (SELECT doc_id FROM kb_chunks WHERE id = ?) AND embedding_json IS NULL)",
            (chunk_id, chunk_id),
        )


# ============================================================
# 提示词模板（版本化，仅一份启用）
# ============================================================
def get_prompt() -> str:
    """当前启用的模板；库里没有则返回默认模板。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT content FROM prompt_templates WHERE enabled = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return row["content"] if row else DEFAULT_PROMPT_TEMPLATE


def put_prompt(content: str, updated_by: int = None) -> int:
    """保存新模板：旧版本全部停用，新版本启用（留痕可回溯）。返回新版本ID。"""
    with _connect() as conn:
        conn.execute("UPDATE prompt_templates SET enabled = 0")
        cur = conn.execute(
            "INSERT INTO prompt_templates (content, enabled, updated_by) VALUES (?, 1, ?)",
            (content, updated_by),
        )
    return cur.lastrowid


def get_prompt_updated_at() -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT updated_at FROM prompt_templates WHERE enabled = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return row["updated_at"] if row else None
