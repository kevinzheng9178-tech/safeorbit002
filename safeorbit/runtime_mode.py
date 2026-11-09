"""全局运行模式：网页可切换、落库持久化，显式启动参数优先。"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .storage import connect

VALID_MODES = ("shadow", "enforce")
_KEY = "global_mode"


class RuntimeModeError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class RuntimeMode:
    """shadow 只记录；enforce 才允许阻断。

    优先级：启动参数（--mode / SAFEORBIT_MODE）> 网页保存值 > 默认 shadow。
    启动参数代表显式意图，出现时网页开关只读，避免重启后被网页值悄悄覆盖。
    """

    def __init__(self, db_path: str, *, startup_mode: Optional[str] = None):
        self.db_path = str(db_path)
        raw = startup_mode if startup_mode is not None else os.getenv("SAFEORBIT_MODE", "")
        mode = str(raw or "").strip().lower()
        self._startup_mode = mode if mode in VALID_MODES else None
        self._init()

    def _connect(self):
        return connect(self.db_path)

    def _init(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_by TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _stored(self) -> Optional[str]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM runtime_settings WHERE key = ?", (_KEY,)
            ).fetchone()
        value = str(row["value"]).lower() if row else ""
        return value if value in VALID_MODES else None

    def current(self) -> str:
        if self._startup_mode:
            return self._startup_mode
        return self._stored() or "shadow"

    def locked(self) -> bool:
        return self._startup_mode is not None

    def status(self) -> Dict[str, Any]:
        source = "startup" if self.locked() else ("admin" if self._stored() else "default")
        return {"mode": self.current(), "source": source, "locked": self.locked()}

    def set(self, mode: str, *, updated_by: str = "") -> Dict[str, Any]:
        value = str(mode or "").strip().lower()
        if value not in VALID_MODES:
            raise RuntimeModeError("运行模式只能是 shadow 或 enforce")
        if self._startup_mode:
            raise RuntimeModeError(
                "当前模式由启动参数锁定，请去掉 --mode / SAFEORBIT_MODE 后再从网页切换", 409
            )
        previous = self.current()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runtime_settings (key, value, updated_by, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                (_KEY, value, updated_by,
                 datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
        result = self.status()
        result["previous"] = previous
        return result
