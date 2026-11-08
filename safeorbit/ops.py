"""运维支撑：Prometheus 指标与治理操作审计日志。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .storage import connect as connect_db


class Metrics:
    """进程内计数器；单进程部署足够，多实例时由 Prometheus 聚合。"""

    def __init__(self) -> None:
        self.counters: Dict[str, float] = {}

    def inc(self, name: str, value: float = 1.0) -> None:
        self.counters[name] = self.counters.get(name, 0.0) + value

    def get(self, name: str) -> float:
        return self.counters.get(name, 0.0)

    def render(self, extra: Optional[Dict[str, float]] = None) -> str:
        lines = []
        for name, value in sorted(self.counters.items()):
            lines.append("# TYPE safeorbit_%s counter" % name)
            lines.append("safeorbit_%s %s" % (name, value))
        for name, value in sorted((extra or {}).items()):
            lines.append("# TYPE safeorbit_%s gauge" % name)
            lines.append("safeorbit_%s %s" % (name, value))
        return "\n".join(lines) + "\n"


class AuditLog:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        return connect_db(str(self.db_path))

    def _init(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor TEXT,
                    action TEXT,
                    target TEXT,
                    detail TEXT,
                    tenant_id TEXT DEFAULT 'default',
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_events_created "
                "ON audit_events(created_at DESC)"
            )

    def log(
        self,
        actor: str,
        action: str,
        target: str = "",
        detail: Optional[Dict[str, Any]] = None,
        tenant_id: str = "default",
    ) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO audit_events (actor, action, target, detail, tenant_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        actor,
                        action,
                        target,
                        json.dumps(detail or {}, ensure_ascii=False, default=str),
                        tenant_id,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
        except Exception:
            # 审计日志失败不能影响主流程
            return

    def list(self, limit: int = 100, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM audit_events"
        params: List[Any] = []
        if tenant_id:
            query += " WHERE tenant_id = ?"
            params.append(tenant_id)
        query += " ORDER BY event_id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item.get("detail") or "{}")
            except (TypeError, ValueError):
                item["detail"] = {}
            result.append(item)
        return result
