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
CREATE TABLE IF NOT EXISTS store_records (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id         TEXT UNIQUE NOT NULL,      -- 巡检单号，如 XD-2026-0001
    store_id            TEXT NOT NULL,             -- 门店编号
    description         TEXT NOT NULL,             -- 巡检发现问题描述
    has_photo           INTEGER DEFAULT 1,         -- 证据：是否已附现场照片
    has_temperature_log INTEGER DEFAULT 1,         -- 证据：是否已附温度记录
    version_conflict    INTEGER DEFAULT 0,         -- 归类用标记：版本冲突
    system_unavailable  INTEGER DEFAULT 0,         -- 归类用标记：系统不可用
    created_at          TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS evidence_tokens (
    token      TEXT PRIMARY KEY,                  -- 一次性证据令牌（validate_evidence 签发）
    record_id  INTEGER NOT NULL REFERENCES store_records(id),
    used       INTEGER DEFAULT 0,                 -- 一次性：建单后立即作废
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS rectification_drafts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id  INTEGER NOT NULL REFERENCES store_records(id),
    token      TEXT NOT NULL,                     -- 建单使用的证据令牌（审计可追溯）
    category   TEXT,                              -- 归类建议（classify_issue 的结果）
    severity   TEXT,                              -- 严重度建议
    advice     TEXT NOT NULL,                     -- 整改建议
    status     TEXT NOT NULL DEFAULT 'draft',     -- draft=草稿 / pending_review=待人工审核
    created_by INTEGER,                           -- 发起对话的员工
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    reviewed_at TEXT
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

# 种子巡检记录（课程演示用）：与 data/sample_records.json 同口径，补充证据字段。
# XD-2026-0001 缺温度记录、XD-2026-0002 缺照片——用于演示证据校验不通过分支
SEED_RECORDS = [
    ("XD-2026-0001", "S-018", "冷藏柜温度记录缺失，现场未找到当日记录", 1, 0),
    ("XD-2026-0002", "S-042", "培训签到齐全，但新员工仍不会温度记录", 0, 1),
    ("XD-2026-0003", "S-067", "整改已上传照片，未见独立复查证据", 1, 1),
    ("XD-2026-0004", "S-103", "操作台卫生不达标，角落有积水与油渍", 1, 1),
]


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
    _seed_records(conn)
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


def _seed_records(conn: sqlite3.Connection) -> None:
    """种子巡检记录：幂等（business_id 唯一约束 + OR IGNORE），老库打开自动补。"""
    for business_id, store_id, desc, photo, temp_log in SEED_RECORDS:
        conn.execute(
            "INSERT OR IGNORE INTO store_records "
            "(business_id, store_id, description, has_photo, has_temperature_log) "
            "VALUES (?, ?, ?, ?, ?)",
            (business_id, store_id, desc, photo, temp_log),
        )


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


# ============================================================
# 巡检记录（阶段4：智能体工具 get_store_record / classify_issue / validate_evidence）
# ============================================================
def find_records(business_id: str = None, store_id: str = None) -> list:
    """按巡检单号或门店编号查巡检记录；两个条件都不传返回全部（限 50 条）。"""
    sql, params = "SELECT * FROM store_records WHERE 1=1", []
    if business_id:
        sql += " AND business_id = ?"
        params.append(business_id)
    if store_id:
        sql += " AND store_id = ?"
        params.append(store_id)
    sql += " ORDER BY id LIMIT 50"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_record(record_id: int):
    """按内部主键取单条巡检记录；不存在返回 None。"""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM store_records WHERE id = ?", (record_id,)).fetchone()
    return dict(row) if row else None


# ============================================================
# 证据令牌（阶段4 硬校验：一次性、10 分钟过期、归属记录绑定）
# ============================================================
TOKEN_TTL_MIN = 10   # 令牌有效期（分钟）


def save_evidence_token(token: str, record_id: int) -> None:
    """validate_evidence 通过后签发令牌。"""
    with _connect() as conn:
        conn.execute("INSERT INTO evidence_tokens (token, record_id) VALUES (?, ?)",
                     (token, record_id))


def consume_evidence_token(token: str, record_id: int) -> bool:
    """一次性消费令牌：存在、未使用、未过期、归属记录匹配才有效，用掉即作废。

    create_rectification_draft 的代码层硬校验——模型跳过 validate_evidence
    或伪造令牌时在这里被拦下，与系统提示词约束构成双保险。
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT used, record_id, "
            "       (julianday('now','localtime') - julianday(created_at)) * 1440.0 AS age_min "
            "FROM evidence_tokens WHERE token = ?",
            (token,),
        ).fetchone()
        if row is None or row["used"] or row["record_id"] != record_id:
            return False
        if row["age_min"] is None or row["age_min"] > TOKEN_TTL_MIN:
            return False
        conn.execute("UPDATE evidence_tokens SET used = 1 WHERE token = ?", (token,))
    return True


# ============================================================
# 整改单草稿（阶段4：只建草稿不派发，终态由人工确认）
# ============================================================
def create_draft(record_id: int, token: str, advice: str, category: str = None,
                 severity: str = None, created_by: int = None) -> int:
    """创建整改单草稿（status=draft），返回草稿ID。"""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO rectification_drafts "
            "(record_id, token, advice, category, severity, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (record_id, token, advice, category, severity, created_by),
        )
    return cur.lastrowid


def get_draft(draft_id: int):
    """按ID取整改单草稿；不存在返回 None。"""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM rectification_drafts WHERE id = ?", (draft_id,)).fetchone()
    return dict(row) if row else None


def submit_draft(draft_id: int) -> None:
    """草稿 → 待人工审核（只有 draft 状态可提交，调用方先检查）。"""
    with _connect() as conn:
        conn.execute(
            "UPDATE rectification_drafts SET status='pending_review', "
            "reviewed_at=datetime('now','localtime') WHERE id = ?",
            (draft_id,),
        )
