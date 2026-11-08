"""P2 策略中心：按 Agent 生效的审计与拦截策略。

策略只描述“应该怎么判、能做什么”，执行位置由运行模式决定：
shadow 只记录与告警，enforce 才会真正阻断。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from .rules import AuditContext, BaseRule
from .storage import connect as connect_db


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Policy(BaseModel):
    policy_id: str
    agent_id: str = "*"
    name: str = "默认策略"
    mode: str = "shadow"                      # shadow | enforce
    budget_usd: Optional[float] = None
    allowed_tools: List[str] = Field(default_factory=list)   # 空表示不限制
    write_amount_threshold: Optional[float] = None
    loop_threshold: int = 3
    enabled_rules: List[str] = Field(default_factory=list)   # 空表示全部规则
    require_confirmation_for_writes: bool = True
    redact_content: bool = False
    note: str = ""
    created_by: str = "system"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, value: str) -> str:
        if value not in ("shadow", "enforce"):
            raise ValueError("mode 只能是 shadow 或 enforce")
        return value

    def to_context(self) -> AuditContext:
        return AuditContext(
            budget_usd=self.budget_usd,
            allowed_tools=set(self.allowed_tools) if self.allowed_tools else None,
            loop_threshold=self.loop_threshold,
            write_amount_threshold=self.write_amount_threshold,
        )

    def select_rules(self, rules: List[BaseRule]) -> List[BaseRule]:
        if not self.enabled_rules:
            return list(rules)
        allowed = set(self.enabled_rules)
        return [rule for rule in rules if rule.id in allowed]


def _row_to_policy(row: sqlite3.Row) -> Policy:
    return Policy(
        policy_id=row["policy_id"],
        agent_id=row["agent_id"],
        name=row["name"],
        mode=row["mode"],
        budget_usd=row["budget_usd"],
        allowed_tools=json.loads(row["allowed_tools"] or "[]"),
        write_amount_threshold=row["write_amount_threshold"],
        loop_threshold=int(row["loop_threshold"] or 3),
        enabled_rules=json.loads(row["enabled_rules"] or "[]"),
        require_confirmation_for_writes=bool(row["require_confirmation_for_writes"]),
        redact_content=bool(row["redact_content"]),
        note=row["note"] or "",
        created_by=row["created_by"] or "system",
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


class PolicyStore:
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
                CREATE TABLE IF NOT EXISTS policies (
                    policy_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    name TEXT,
                    mode TEXT,
                    budget_usd REAL,
                    allowed_tools TEXT,
                    write_amount_threshold REAL,
                    loop_threshold INTEGER,
                    enabled_rules TEXT,
                    require_confirmation_for_writes INTEGER,
                    redact_content INTEGER DEFAULT 0,
                    note TEXT,
                    created_by TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(policies)").fetchall()
            }
            if "redact_content" not in columns:
                connection.execute(
                    "ALTER TABLE policies ADD COLUMN redact_content INTEGER DEFAULT 0"
                )

    # ---------- 查询 ----------
    def list(self) -> List[Policy]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM policies ORDER BY agent_id = '*', agent_id, created_at"
            ).fetchall()
        return [_row_to_policy(row) for row in rows]

    def get(self, policy_id: str) -> Optional[Policy]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM policies WHERE policy_id = ?", (policy_id,)
            ).fetchone()
        return _row_to_policy(row) if row else None

    def resolve(self, agent_id: str) -> Optional[Policy]:
        """先找 Agent 专属策略，再回退到 * 默认策略。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM policies WHERE agent_id = ? ORDER BY updated_at DESC LIMIT 1",
                (agent_id,),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM policies WHERE agent_id = '*' ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
        return _row_to_policy(row) if row else None

    def find_by_agent(self, agent_id: str) -> Optional[Policy]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM policies WHERE agent_id = ? ORDER BY updated_at DESC LIMIT 1",
                (agent_id,),
            ).fetchone()
        return _row_to_policy(row) if row else None

    # ---------- 写操作 ----------
    def upsert(
        self,
        *,
        agent_id: str,
        name: str = "",
        mode: str = "shadow",
        budget_usd: Optional[float] = None,
        allowed_tools: Optional[List[str]] = None,
        write_amount_threshold: Optional[float] = None,
        loop_threshold: int = 3,
        enabled_rules: Optional[List[str]] = None,
        require_confirmation_for_writes: bool = True,
        redact_content: bool = False,
        note: str = "",
        created_by: str = "system",
        policy_id: Optional[str] = None,
    ) -> Policy:
        agent_id = (agent_id or "*").strip() or "*"
        if mode not in ("shadow", "enforce"):
            raise ValueError("mode 只能是 shadow 或 enforce")
        now = _now()
        existing = self.get(policy_id) if policy_id else None
        if existing is not None:
            policy = existing.model_copy(
                update={
                    "agent_id": agent_id,
                    "name": name or existing.name,
                    "mode": mode,
                    "budget_usd": budget_usd,
                    "allowed_tools": allowed_tools or [],
                    "write_amount_threshold": write_amount_threshold,
                    "loop_threshold": loop_threshold,
                    "enabled_rules": enabled_rules or [],
                    "require_confirmation_for_writes": require_confirmation_for_writes,
                    "redact_content": redact_content,
                    "note": note,
                    "updated_at": now,
                }
            )
        else:
            policy = Policy(
                policy_id="pol-" + uuid4().hex[:10],
                agent_id=agent_id,
                name=name or ("%s 策略" % agent_id),
                mode=mode,
                budget_usd=budget_usd,
                allowed_tools=allowed_tools or [],
                write_amount_threshold=write_amount_threshold,
                loop_threshold=loop_threshold,
                enabled_rules=enabled_rules or [],
                require_confirmation_for_writes=require_confirmation_for_writes,
                redact_content=redact_content,
                note=note,
                created_by=created_by,
                created_at=now,
                updated_at=now,
            )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO policies
                (policy_id, agent_id, name, mode, budget_usd, allowed_tools,
                 write_amount_threshold, loop_threshold, enabled_rules,
                 require_confirmation_for_writes, redact_content, note, created_by,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy.policy_id,
                    policy.agent_id,
                    policy.name,
                    policy.mode,
                    policy.budget_usd,
                    json.dumps(policy.allowed_tools, ensure_ascii=False),
                    policy.write_amount_threshold,
                    policy.loop_threshold,
                    json.dumps(policy.enabled_rules, ensure_ascii=False),
                    1 if policy.require_confirmation_for_writes else 0,
                    1 if policy.redact_content else 0,
                    policy.note,
                    policy.created_by,
                    policy.created_at.isoformat(),
                    policy.updated_at.isoformat(),
                ),
            )
        return policy

    def delete(self, policy_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM policies WHERE policy_id = ?", (policy_id,))
        return cursor.rowcount > 0

    def ensure_default(self) -> Policy:
        """演示环境兜底：至少有一条可见的默认策略。"""

        existing = self.resolve("*")
        if existing is not None:
            return existing
        return self.upsert(
            agent_id="*",
            name="默认策略（影子模式）",
            mode="shadow",
            note="由系统自动创建：只记录与告警，不阻断业务。",
            created_by="system",
        )
