"""会话与消息存储（SQLite 单文件）。

沿用阶段1表结构的 conversations（会话）/ messages（消息）两张表，
由 /api/chat 读写；每次请求取最近 6 轮（12 条）历史拼入提示词。
"""

import os
import sqlite3
import uuid
from pathlib import Path

# 仓库根目录：src/my_tool/api/db.py → 上溯 3 级
_REPO_ROOT = Path(__file__).resolve().parents[3]

DDL = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,                 -- 会话ID（uuid）
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL,                -- user / assistant
    content         TEXT NOT NULL,
    sources_json    TEXT,                         -- 助手消息的引用来源（JSON）
    created_at      TEXT DEFAULT (datetime('now', 'localtime'))
);
"""


def db_path() -> Path:
    """数据库文件位置，可用环境变量 CHAT_DB 覆盖（测试写临时目录）。"""
    return Path(os.environ.get("CHAT_DB", str(_REPO_ROOT / "data" / "chat.db")))


def _connect() -> sqlite3.Connection:
    """每次操作独立建连：课程项目并发量极低，简单可靠优先。"""
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)          # 建表语句带 IF NOT EXISTS，重复执行无副作用
    return conn


def create_conversation() -> str:
    """新建会话，返回会话ID。"""
    sid = uuid.uuid4().hex
    with _connect() as conn:
        conn.execute("INSERT INTO conversations (id) VALUES (?)", (sid,))
    return sid


def has_conversation(sid: str) -> bool:
    """校验前端带来的会话ID是否存在，防止伪造。"""
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM conversations WHERE id = ?", (sid,)).fetchone()
    return row is not None


def add_message(sid: str, role: str, content: str, sources_json: str = None) -> int:
    """追加一条消息，返回消息ID（done 事件要带回给前端）。"""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, content, sources_json) VALUES (?, ?, ?, ?)",
            (sid, role, content, sources_json),
        )
    return cur.lastrowid


def recent_messages(sid: str, limit: int = 12) -> list:
    """取某会话最近 limit 条消息，按时间正序返回 [{"role","content"}, ...]。

    最近 6 轮 = 12 条（一轮 = 用户问 + 助手答）。
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (sid, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
