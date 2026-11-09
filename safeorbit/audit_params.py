"""审计参数：网页可视化配置，落库持久化，未配置时回落到默认值。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .storage import connect

_KEY = "audit_params"

# 每个参数都给出默认值、类型与允许区间；前端据此渲染并校验
FIELDS: Dict[str, Dict[str, Any]] = {
    "timeout_s": {"default": 30.0, "kind": "float", "min": 5.0, "max": 180.0},
    "max_retries": {"default": 2, "kind": "int", "min": 0, "max": 5},
    "max_tokens": {"default": 3200, "kind": "int", "min": 400, "max": 8000},
    "max_input_chars": {"default": 20000, "kind": "int", "min": 2000, "max": 60000},
    "kb_limit": {"default": 4, "kind": "int", "min": 1, "max": 8},
}


class AuditParamsError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AuditParams:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
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

    def _stored(self) -> Dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM runtime_settings WHERE key = ?", (_KEY,)
            ).fetchone()
        if not row:
            return {}
        try:
            data = json.loads(row["value"])
        except (TypeError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {name: data[name] for name in FIELDS if name in data}

    def current(self) -> Dict[str, Any]:
        values = {name: spec["default"] for name, spec in FIELDS.items()}
        values.update(self._stored())
        return values

    def public(self) -> Dict[str, Any]:
        return {
            "params": self.current(),
            "defaults": {name: spec["default"] for name, spec in FIELDS.items()},
            "limits": {
                name: {"min": spec["min"], "max": spec["max"], "kind": spec["kind"]}
                for name, spec in FIELDS.items()
            },
        }

    def save(self, values: Any, *, updated_by: str = "") -> Dict[str, Any]:
        if not isinstance(values, dict):
            raise AuditParamsError("参数必须是对象")
        unknown = [name for name in values if name not in FIELDS]
        if unknown:
            raise AuditParamsError("未知参数：%s" % "、".join(sorted(unknown)))
        merged = self.current()
        for name, raw in values.items():
            merged[name] = self._coerce(name, raw)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runtime_settings (key, value, updated_by, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                (_KEY, json.dumps(merged, ensure_ascii=False), updated_by,
                 datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
        result = self.public()
        result["updated_by"] = updated_by
        return result

    @staticmethod
    def _coerce(name: str, raw: Any) -> Any:
        spec = FIELDS[name]
        if isinstance(raw, bool) or raw is None:
            raise AuditParamsError("%s 必须是数字" % name)
        try:
            value = float(raw) if spec["kind"] == "float" else int(raw)
        except (TypeError, ValueError):
            raise AuditParamsError("%s 必须是数字" % name) from None
        if spec["kind"] == "int" and isinstance(raw, float) and not raw.is_integer():
            raise AuditParamsError("%s 必须是整数" % name)
        if not spec["min"] <= value <= spec["max"]:
            raise AuditParamsError(
                "%s 超出允许范围 %s–%s" % (name, spec["min"], spec["max"])
            )
        return value


def apply_to_config(params: Dict[str, Any], config) -> Any:
    """把参数写入裁判配置；配置对象由调用方持有，避免跨请求共享。"""

    config.timeout_s = float(params.get("timeout_s", config.timeout_s))
    config.max_retries = int(params.get("max_retries", config.max_retries))
    config.max_tokens = int(params.get("max_tokens", config.max_tokens))
    config.max_input_chars = int(params.get("max_input_chars", config.max_input_chars))
    config.kb_limit = int(params.get("kb_limit", getattr(config, "kb_limit", 4)))
    return config
