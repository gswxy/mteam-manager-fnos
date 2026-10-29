"""SQLite 数据库连接与迁移。

使用 WAL 模式；迁移采用 schema_version 表 + 增量 ALTER，升级不丢数据。
参考实现：M-Team-Helper backend/database.py（MIT, spellyaohui）。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)

# 迁移步骤：每个版本一个函数，按版本顺序执行
MIGRATIONS: dict[int, str] = {}


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- M-Team 账号（API Key 加密存储）
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    api_url TEXT NOT NULL DEFAULT 'https://api.m-team.cc',
    api_key_enc TEXT NOT NULL,
    uid TEXT,
    username TEXT,
    is_default INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    uploaded INTEGER NOT NULL DEFAULT 0,
    downloaded INTEGER NOT NULL DEFAULT 0,
    share_rate REAL NOT NULL DEFAULT 0,
    bonus REAL NOT NULL DEFAULT 0,
    seeding_count INTEGER NOT NULL DEFAULT 0,
    seeding_size INTEGER NOT NULL DEFAULT 0,
    member_class TEXT,
    profile_json TEXT,
    last_sync TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 种子缓存（来自 M-Team 搜索/列表）
CREATE TABLE IF NOT EXISTS torrents (
    id TEXT PRIMARY KEY,               -- M-Team 种子 ID
    name TEXT NOT NULL,
    small_descr TEXT,
    category TEXT,
    size INTEGER NOT NULL DEFAULT 0,
    seeders INTEGER NOT NULL DEFAULT 0,
    leechers INTEGER NOT NULL DEFAULT 0,
    times_completed INTEGER NOT NULL DEFAULT 0,
    discount TEXT NOT NULL DEFAULT 'NORMAL',
    discount_end TEXT,
    douban_id TEXT,
    douban_rating REAL,
    imdb_id TEXT,
    imdb_rating REAL,
    poster TEXT,
    created_date TEXT,
    labels TEXT,
    extra TEXT,                         -- 原始 JSON（status 等完整字段）
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_torrents_douban ON torrents(douban_id);
CREATE INDEX IF NOT EXISTS idx_torrents_imdb ON torrents(imdb_id);
CREATE INDEX IF NOT EXISTS idx_torrents_created ON torrents(created_date);

-- 下载任务（手动 + 自动统一追踪）
CREATE TABLE IF NOT EXISTS download_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    torrent_id TEXT NOT NULL,
    torrent_name TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'manual',   -- manual | auto
    rule_id INTEGER,
    rule_name TEXT,
    hit_reason TEXT,                          -- 自动任务命中原因
    category TEXT,                            -- movie/tv/anime/show/other
    save_path TEXT NOT NULL,
    fnos_task_id INTEGER,                     -- 飞牛下载中心任务 ID
    status TEXT NOT NULL DEFAULT 'pending',
    progress REAL NOT NULL DEFAULT 0,
    dl_speed INTEGER NOT NULL DEFAULT 0,
    up_speed INTEGER NOT NULL DEFAULT 0,
    uploaded INTEGER NOT NULL DEFAULT 0,
    downloaded INTEGER NOT NULL DEFAULT 0,
    ratio REAL,
    discount TEXT,
    labels TEXT,
    protected INTEGER NOT NULL DEFAULT 0,     -- 永久保留
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    removed_at TEXT,
    remove_reason TEXT,
    freed_bytes INTEGER,
    meta TEXT,
    UNIQUE(account_id, torrent_id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON download_tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_fnos ON download_tasks(fnos_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_source ON download_tasks(source);

-- 自动下载规则
CREATE TABLE IF NOT EXISTS auto_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'preference',  -- preference | free | seeding
    enabled INTEGER NOT NULL DEFAULT 0,
    config TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 清理日志
CREATE TABLE IF NOT EXISTS cleanup_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    torrent_name TEXT,
    reason TEXT NOT NULL,
    detail TEXT,
    freed_bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 应用事件（供界面展示）
CREATE TABLE IF NOT EXISTS event_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL DEFAULT 'info',
    source TEXT NOT NULL DEFAULT 'system',
    message TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 收藏
CREATE TABLE IF NOT EXISTS favorites (
    account_id INTEGER NOT NULL,
    torrent_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (account_id, torrent_id)
);
"""


def get_conn() -> sqlite3.Connection:
    return _connect(settings.db_path)


@contextmanager
def db():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(SCHEMA_V1)
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        conn.commit()
        _run_migrations(conn)
    finally:
        conn.close()


def _run_migrations(conn: sqlite3.Connection) -> None:
    """增量迁移：为未来版本保留。当前 1.0.0 => version 1。"""
    row = conn.execute("SELECT MAX(version) v FROM schema_version").fetchone()
    current = row["v"] or 0
    for version in sorted(MIGRATIONS):
        if version > current:
            logger.info("applying migration v%s", version)
            conn.executescript(MIGRATIONS[version])
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            conn.commit()


# ---------------- settings helpers ----------------

def get_setting(key: str, default=None):
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return row["value"]


def set_setting(key: str, value) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?,?,datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
            (key, json.dumps(value, ensure_ascii=False)),
        )


def add_event(level: str, source: str, message: str, detail: str | None = None) -> None:
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO event_logs (level, source, message, detail) VALUES (?,?,?,?)",
                (level, source, message, detail),
            )
            conn.execute(
                "DELETE FROM event_logs WHERE id <= (SELECT MAX(id)-2000 FROM event_logs)"
            )
    except Exception:  # 事件记录失败不影响主流程
        logger.exception("failed to write event")
