"""SQLite 连接统一入口：开启 WAL 与忙等待，降低并发写入冲突。"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: str, *, row_factory: bool = True) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=15)
    if row_factory:
        connection.row_factory = sqlite3.Row
    # WAL 让读写并发；busy_timeout 避免瞬时写锁直接报错。
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=15000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection
