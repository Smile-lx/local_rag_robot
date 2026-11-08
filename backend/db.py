"""SQLite 数据层：连接管理、DDL 初始化与通用查询助手。

约定：
- 时间统一 UTC ISO8601，统一用 now_iso() 生成；
- 每次操作取独立连接、用完即关（sqlite3 连接不可跨线程复用）；
- 外键约束按连接生效，统一在 get_conn() 内开启 PRAGMA foreign_keys=ON。
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DB_BUSY_TIMEOUT_MS, DB_PATH, JOURNAL_MODE, PROJECT_ROOT

__all__ = ["get_conn", "init_db", "query_all", "query_one", "execute", "now_iso"]

# 项目根下的 DDL 脚本（幂等：CREATE TABLE / INDEX IF NOT EXISTS）
SCHEMA_PATH: Path = PROJECT_ROOT / "sql" / "schema.sql"


def now_iso() -> str:
    """返回当前 UTC 时间的 ISO8601 字符串（含时区偏移）。"""
    return datetime.now(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    """新建 SQLite 连接：行以 sqlite3.Row 访问，并开启外键约束。

    连接加固（2026-10-25）：统一设置 busy_timeout（并发写等待而非立即
    报 database is locked）；JOURNAL_MODE 显式配置时切换日志模式（如
    WAL，默认留空保持 SQLite 缺省）。

    调用方负责关闭连接（推荐 ``with closing(get_conn()) as conn:``）。
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")
    if JOURNAL_MODE:
        conn.execute(f"PRAGMA journal_mode={JOURNAL_MODE}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """执行 sql/schema.sql 建 4 表与索引（幂等，可重复调用）。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn = get_conn()
    try:
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()


def query_all(sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
    """执行只读查询，返回全部行（sqlite3.Row 列表）。"""
    with closing(get_conn()) as conn:
        return conn.execute(sql, params).fetchall()


def query_one(sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
    """执行只读查询，返回第一行，无结果时返回 None。"""
    with closing(get_conn()) as conn:
        return conn.execute(sql, params).fetchone()


def execute(sql: str, params: Sequence[Any] = ()) -> int:
    """执行写语句并提交，返回 lastrowid（非 INSERT 语句该返回值为 0）。"""
    with closing(get_conn()) as conn, conn:
        cursor = conn.execute(sql, params)
        return int(cursor.lastrowid) if cursor.lastrowid is not None else 0
