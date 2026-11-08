"""独立于人类会话的 Agent 身份与一次性接入凭据。"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .storage import connect as connect_db

SCOPES = frozenset({"models:read", "gateway:invoke", "tools:check", "traces:write"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_agent_id(agent_id: str) -> None:
    if not isinstance(agent_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", agent_id):
        raise ValueError("Agent ID 仅允许 1-128 位字母、数字、下划线和连字符，首位须为字母或数字")


class AgentAuthError(Exception):
    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.status_code = status_code


class AgentStore:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        return connect_db(str(self.db_path))

    def _init(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT 'default'
                )
            """)
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(agents)").fetchall()
            }
            if "tenant_id" not in columns:
                connection.execute(
                    "ALTER TABLE agents ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
                )
            connection.execute("""
                CREATE TABLE IF NOT EXISTS agent_keys (
                    key_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
                    key_hash TEXT UNIQUE NOT NULL,
                    prefix TEXT NOT NULL,
                    scopes TEXT NOT NULL,
                    expires_at TEXT,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    last_seen TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS agent_keys_agent ON agent_keys(agent_id)")
            connection.execute("CREATE TABLE IF NOT EXISTS agent_schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            connection.execute("INSERT OR IGNORE INTO agent_schema_migrations VALUES (1, ?)", (_now().isoformat(),))

    def create_agent(self, agent_id: str, name: str = "", tenant_id: str = "default") -> Dict[str, Any]:
        _validate_agent_id(agent_id)
        if not isinstance(name, str):
            raise ValueError("Agent 名称必须是字符串")
        tenant_id = (tenant_id or "default").strip() or "default"
        result = {
            "agent_id": agent_id,
            "name": name or agent_id,
            "enabled": True,
            "created_at": _now().isoformat(),
            "tenant_id": tenant_id,
        }
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO agents (agent_id, name, enabled, created_at, tenant_id) VALUES (?, ?, 1, ?, ?)",
                    (agent_id, result["name"], result["created_at"], tenant_id),
                )
            except sqlite3.IntegrityError:
                raise ValueError("Agent 已存在") from None
        return result

    def list_agents(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            if tenant_id:
                rows = connection.execute(
                    "SELECT * FROM agents WHERE tenant_id = ? ORDER BY created_at, agent_id",
                    (tenant_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM agents ORDER BY created_at, agent_id"
                ).fetchall()
        return [dict(row, enabled=bool(row["enabled"])) for row in rows]

    def set_enabled(self, agent_id: str, enabled: bool) -> Dict[str, Any]:
        _validate_agent_id(agent_id)
        if not isinstance(enabled, bool):
            raise ValueError("enabled 必须为布尔值")
        with self._connect() as connection:
            if not connection.execute("UPDATE agents SET enabled = ? WHERE agent_id = ?", (int(enabled), agent_id)).rowcount:
                raise ValueError("Agent 不存在")
            row = connection.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
        return dict(row, enabled=bool(row["enabled"]))

    def issue_key(self, agent_id: str, scopes: List[str], expires_at: Optional[str] = None) -> Dict[str, Any]:
        _validate_agent_id(agent_id)
        if (not isinstance(scopes, (list, tuple, set)) or not scopes
                or any(not isinstance(scope, str) or scope not in SCOPES for scope in scopes)):
            raise ValueError("必须指定合法的 Agent 权限范围")
        if expires_at is not None:
            try:
                expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if expires.tzinfo is None or expires <= _now():
                    raise ValueError()
            except (AttributeError, TypeError, ValueError):
                raise ValueError("有效期必须为未来带时区的 ISO 8601 时间") from None
            expires_at = expires.astimezone(timezone.utc).isoformat()
        token = "so_agent_" + secrets.token_urlsafe(32)
        result = {"key_id": secrets.token_hex(16), "agent_id": agent_id, "prefix": token[:16],
                  "scopes": sorted(set(scopes)), "expires_at": expires_at, "revoked": False,
                  "last_seen": None, "created_at": _now().isoformat()}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("SELECT 1 FROM agents WHERE agent_id = ?", (agent_id,)).fetchone():
                raise ValueError("Agent 不存在")
            connection.execute("""INSERT INTO agent_keys
                (key_id, agent_id, key_hash, prefix, scopes, expires_at, revoked, last_seen, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, NULL, ?)""",
                (result["key_id"], agent_id, hashlib.sha256(token.encode("utf-8")).hexdigest(),
                 result["prefix"], json.dumps(result["scopes"]), expires_at, result["created_at"]))
        return dict(result, key=token)

    def list_keys(self, agent_id: str) -> List[Dict[str, Any]]:
        _validate_agent_id(agent_id)
        with self._connect() as connection:
            rows = connection.execute("""SELECT key_id, agent_id, prefix, scopes, expires_at, revoked,
                last_seen, created_at FROM agent_keys WHERE agent_id = ? ORDER BY created_at, key_id""", (agent_id,)).fetchall()
        return [dict(row, scopes=json.loads(row["scopes"]), revoked=bool(row["revoked"])) for row in rows]

    def revoke_key(self, agent_id: str, key_id: str) -> None:
        _validate_agent_id(agent_id)
        with self._connect() as connection:
            if not connection.execute("UPDATE agent_keys SET revoked = 1 WHERE agent_id = ? AND key_id = ?", (agent_id, key_id)).rowcount:
                raise ValueError("Agent Key 不存在")

    def authenticate(self, token: str, scope: str) -> Dict[str, Any]:
        if not isinstance(token, str) or not token or len(token) > 256:
            raise AgentAuthError("无效 Agent Key")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("""SELECT k.*, a.enabled, a.tenant_id AS agent_tenant_id FROM agent_keys k
                JOIN agents a ON a.agent_id = k.agent_id WHERE k.key_hash = ?""", (token_hash,)).fetchone()
            if row is None or row["revoked"]:
                raise AgentAuthError("无效或已撤销的 Agent Key")
            now = _now()
            if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) <= now:
                raise AgentAuthError("Agent Key 已过期")
            if not row["enabled"]:
                raise AgentAuthError("Agent 已禁用", status_code=403)
            if scope not in SCOPES or scope not in json.loads(row["scopes"]):
                raise AgentAuthError("Agent Key 权限不足", status_code=403)
            connection.execute("UPDATE agent_keys SET last_seen = ? WHERE key_id = ?", (now.isoformat(), row["key_id"]))
        return {"agent_id": row["agent_id"], "key_id": row["key_id"]}

    def tenant_of(self, agent_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT tenant_id FROM agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
        return (row["tenant_id"] if row else None) or "default"

    def scopes_for_key(self, key_id: str) -> List[str]:
        if not key_id:
            return []
        with self._connect() as connection:
            row = connection.execute(
                "SELECT scopes FROM agent_keys WHERE key_id = ? AND revoked = 0",
                (key_id,),
            ).fetchone()
        return json.loads(row["scopes"]) if row else []

    def agent_ids_for_tenant(self, tenant_id: str) -> List[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT agent_id FROM agents WHERE tenant_id = ?", (tenant_id,)
            ).fetchall()
        return [row["agent_id"] for row in rows]
