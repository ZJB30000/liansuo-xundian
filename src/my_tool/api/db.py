"""数据模型层：SQLite 存储（会话/消息/用户）+ 自动迁移 + 种子数据。

沿用阶段1的 conversations / messages 两张表；阶段3 扩展：
users（登录账号）、conversations.user_id（问答归属）、messages.flagged（疑似违规标记）。
所有建表/加列/种子操作都幂等，旧库文件打开即自动迁移。
"""

import hashlib
import os
import secrets
import sqlite3
import uuid
from pathlib import Path

# 仓库根目录：src/my_tool/api/db.py → 上溯 3 级
_REPO_ROOT = Path(__file__).resolve().parents[3]

DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,                 -- 格式：salt$pbkdf2哈希（hex）
    role          TEXT NOT NULL DEFAULT 'staff', -- staff=员工 / admin=管理员
    created_at    TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,                 -- 会话ID（uuid）
    user_id    INTEGER,                          -- 归属员工（阶段3新增）
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL,                -- user / assistant
    content         TEXT NOT NULL,
    sources_json    TEXT,                         -- 助手消息的引用来源（JSON）
    flagged         INTEGER DEFAULT 0,            -- 1=疑似违规巡检查问答（阶段3新增）
    created_at      TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS kb_documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT NOT NULL,
    mime        TEXT,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending=待向量化 / ready=已就绪
    uploaded_by INTEGER,
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS kb_chunks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id         INTEGER NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    text           TEXT NOT NULL,
    embedding_json TEXT                            -- 向量 JSON，延迟回写
);
CREATE TABLE IF NOT EXISTS prompt_templates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 0,
    updated_by INTEGER,
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);
"""

# 迁移：给老库补新列（新库由 DDL 直接建出，无需迁移）
MIGRATIONS = [
    ("conversations", "user_id", "INTEGER"),
    ("messages", "flagged", "INTEGER DEFAULT 0"),
]

# 种子账号（课程演示用）：admin/admin123 管理员，zhangsan/123456 员工
SEED_USERS = [("admin", "admin123", "admin"), ("zhangsan", "123456", "staff")]
PBKDF2_ITERS = 100_000


def hash_password(password: str, salt: str = None) -> str:
    """PBKDF2 哈希，返回 "salt$hash"；盐不传则随机生成。"""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ITERS).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """按存储格式拆出盐再重算比对。"""
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return secrets.compare_digest(hash_password(password, salt), stored)


def db_path() -> Path:
    """数据库文件位置，可用环境变量 CHAT_DB 覆盖（测试写临时目录）。"""
    return Path(os.environ.get("CHAT_DB", str(_REPO_ROOT / "data" / "chat.db")))


def _connect() -> sqlite3.Connection:
    """每次操作独立建连；建表/迁移/种子全部幂等。"""
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")   # kb_chunks 级联删除依赖外键
    conn.executescript(DDL)
    # 旧库补列
    for table, column, decl in MIGRATIONS:
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    _seed(conn)
    return conn


def _seed(conn: sqlite3.Connection) -> None:
    """首次初始化：写入种子账号（meta.seeded 防止每次连接重复哈希）。"""
    row = conn.execute("SELECT value FROM meta WHERE key='seeded'").fetchone()
    if row:
        return
    for username, password, role in SEED_USERS:
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, hash_password(password), role),
        )
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('seeded', '1')")


# ============================================================
# 用户
# ============================================================
def get_user_by_name(username: str):
    """按用户名取用户行；不存在返回 None。"""
    with _connect() as conn:
        return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


# ============================================================
# 会话与消息（阶段1/2 接口保持兼容）
# ============================================================
def create_conversation(user_id: int = None) -> str:
    """新建会话（阶段3 起可带归属员工），返回会话ID。"""
    sid = uuid.uuid4().hex
    with _connect() as conn:
        conn.execute("INSERT INTO conversations (id, user_id) VALUES (?, ?)", (sid, user_id))
    return sid


def has_conversation(sid: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM conversations WHERE id = ?", (sid,)).fetchone()
    return row is not None


def add_message(sid: str, role: str, content: str, sources_json: str = None,
                flagged: bool = False) -> int:
    """追加一条消息；用户消息可带疑似违规标记。返回消息ID。"""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, content, sources_json, flagged) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, role, content, sources_json, 1 if flagged else 0),
        )
    return cur.lastrowid


def recent_messages(sid: str, limit: int = 12) -> list:
    """取某会话最近 limit 条消息，按时间正序返回 [{"role","content"}, ...]。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (sid, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ============================================================
# 审计查询（阶段3）
# ============================================================
def audit_query(user_id: int = None, start: str = None, end: str = None,
                flagged_only: bool = False) -> list:
    """按员工/日期筛选会话，聚合出首问、消息数、疑似违规数，供审计页展示。"""
    sql = (
        "SELECT c.id AS conversation_id, u.username, c.created_at, "
        "       (SELECT content FROM messages WHERE conversation_id=c.id AND role='user' "
        "        ORDER BY id LIMIT 1) AS first_question, "
        "       COUNT(m.id) AS message_count, "
        "       SUM(CASE WHEN m.flagged=1 THEN 1 ELSE 0 END) AS flagged_count "
        "FROM conversations c "
        "LEFT JOIN users u ON u.id = c.user_id "
        "LEFT JOIN messages m ON m.conversation_id = c.id "
        "WHERE 1=1"
    )
    params: list = []
    if user_id:
        sql += " AND c.user_id = ?"
        params.append(user_id)
    if start:
        sql += " AND date(c.created_at) >= date(?)"
        params.append(start)
    if end:
        sql += " AND date(c.created_at) <= date(?)"
        params.append(end)
    sql += " GROUP BY c.id"
    if flagged_only:
        sql += " HAVING flagged_count > 0"
    sql += " ORDER BY c.created_at DESC LIMIT 200"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
