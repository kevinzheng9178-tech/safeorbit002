"""写操作审批链：提案 → 人工批准 → 一次性票据。

设计约束：
- 票据绑定 agent_id、工具名与参数哈希，参数被改写即失效；
- 票据有有效期且只能消费一次；
- 客户端自报的 confirmed_by_human 不再作为可信依据。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from .storage import connect as connect_db

STATUS_TEXT = {
    "proposed": "待审批",
    "approved": "已批准",
    "rejected": "已驳回",
    "used": "已使用",
    "expired": "已过期",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def arguments_hash(arguments: Optional[Dict[str, Any]]) -> str:
    canonical = json.dumps(
        arguments or {},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Approval(BaseModel):
    approval_id: str
    agent_id: str
    session_id: Optional[str] = None
    trace_id: Optional[str] = None
    tool_name: str
    arguments_hash: str
    reason: str = ""
    status: str = "proposed"
    requested_by: str = "runtime"
    decided_by: Optional[str] = None
    decision_note: str = ""
    created_at: datetime = Field(default_factory=_now)
    expires_at: Optional[datetime] = None
    decided_at: Optional[datetime] = None
    used_at: Optional[datetime] = None
    used_trace_id: Optional[str] = None

    def to_public(self) -> Dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["status_text"] = STATUS_TEXT.get(self.status, self.status)
        return payload


def _row_to_approval(row: sqlite3.Row) -> Approval:
    return Approval(
        approval_id=row["approval_id"],
        agent_id=row["agent_id"],
        session_id=row["session_id"],
        trace_id=row["trace_id"],
        tool_name=row["tool_name"],
        arguments_hash=row["arguments_hash"],
        reason=row["reason"] or "",
        status=row["status"],
        requested_by=row["requested_by"] or "runtime",
        decided_by=row["decided_by"],
        decision_note=row["decision_note"] or "",
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        decided_at=datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None,
        used_at=datetime.fromisoformat(row["used_at"]) if row["used_at"] else None,
        used_trace_id=row["used_trace_id"],
    )


class ApprovalStore:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        return connect_db(str(self.db_path))

    def _init(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    session_id TEXT,
                    trace_id TEXT,
                    tool_name TEXT NOT NULL,
                    arguments_hash TEXT NOT NULL,
                    reason TEXT,
                    status TEXT NOT NULL,
                    requested_by TEXT,
                    decided_by TEXT,
                    decision_note TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    decided_at TEXT,
                    used_at TEXT,
                    used_trace_id TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_approvals_status_created "
                "ON approvals(status, created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_approvals_agent_tool "
                "ON approvals(agent_id, tool_name, arguments_hash, status)"
            )

    def create(
        self,
        *,
        agent_id: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        reason: str = "",
        requested_by: str = "runtime",
    ) -> Approval:
        digest = arguments_hash(arguments)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM approvals
                WHERE agent_id = ? AND tool_name = ? AND arguments_hash = ?
                  AND status IN ('proposed', 'approved')
                ORDER BY created_at DESC LIMIT 1
                """,
                (agent_id, tool_name, digest),
            ).fetchone()
            if existing is not None:
                item = _row_to_approval(existing)
                if item.status == "approved" and item.expires_at and item.expires_at <= _now():
                    connection.execute(
                        "UPDATE approvals SET status = 'expired' WHERE approval_id = ?",
                        (item.approval_id,),
                    )
                else:
                    return item
            approval = Approval(
                approval_id="apr-" + uuid4().hex[:12],
                agent_id=agent_id,
                session_id=session_id,
                trace_id=trace_id,
                tool_name=tool_name,
                arguments_hash=digest,
                reason=reason or "写操作需要人工审批",
                status="proposed",
                requested_by=requested_by,
                created_at=_now(),
            )
            connection.execute(
                """
                INSERT INTO approvals
                (approval_id, agent_id, session_id, trace_id, tool_name, arguments_hash,
                 reason, status, requested_by, decided_by, decision_note, created_at,
                 expires_at, decided_at, used_at, used_trace_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '', ?, NULL, NULL, NULL, NULL)
                """,
                (
                    approval.approval_id,
                    approval.agent_id,
                    approval.session_id,
                    approval.trace_id,
                    approval.tool_name,
                    approval.arguments_hash,
                    approval.reason,
                    approval.status,
                    approval.requested_by,
                    approval.created_at.isoformat(),
                ),
            )
        return approval

    def get(self, approval_id: str) -> Optional[Approval]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        return _row_to_approval(row) if row else None

    def list(self, status: Optional[str] = None, limit: int = 100) -> List[Approval]:
        self.expire()
        query = "SELECT * FROM approvals"
        params: List[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_approval(row) for row in rows]

    def decide(
        self,
        approval_id: str,
        *,
        approved: bool,
        decided_by: str,
        note: str = "",
        ttl_minutes: int = 15,
    ) -> Approval:
        item = self.get(approval_id)
        if item is None:
            raise ValueError("审批单不存在")
        if item.status != "proposed":
            raise ValueError("只有待审批的申请可以审批，当前状态：%s" % item.status)
        now = _now()
        status = "approved" if approved else "rejected"
        expires_at = (now + timedelta(minutes=max(1, int(ttl_minutes)))).isoformat() if approved else None
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE approvals SET status = ?, decided_by = ?, decision_note = ?,
                    decided_at = ?, expires_at = ?
                WHERE approval_id = ? AND status = 'proposed'
                """,
                (status, decided_by, note, now.isoformat(), expires_at, approval_id),
            )
        updated = self.get(approval_id)
        assert updated is not None
        return updated

    def consume(
        self,
        approval_id: str,
        *,
        agent_id: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """校验并消费票据；成功返回 (True, 说明)，失败返回 (False, 原因)。"""

        digest = arguments_hash(arguments)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                return False, "审批单不存在"
            item = _row_to_approval(row)
            if item.agent_id != agent_id:
                return False, "审批单不属于当前 Agent"
            if item.tool_name != tool_name:
                return False, "审批单的工具与本次调用不一致"
            if item.arguments_hash != digest:
                return False, "工具参数与审批时不一致，请重新申请"
            if item.status == "used":
                return False, "审批单已被使用，请重新申请"
            if item.status == "rejected":
                return False, "审批单已被驳回"
            if item.status != "approved":
                return False, "审批单尚未批准"
            if item.expires_at is not None and item.expires_at <= _now():
                connection.execute(
                    "UPDATE approvals SET status = 'expired' WHERE approval_id = ?",
                    (approval_id,),
                )
                return False, "审批单已过期，请重新申请"
            connection.execute(
                """
                UPDATE approvals SET status = 'used', used_at = ?, used_trace_id = ?
                WHERE approval_id = ? AND status = 'approved'
                """,
                (_now().isoformat(), trace_id, approval_id),
            )
        return True, "已通过一次性人工审批"

    def expire(self) -> int:
        now = _now().isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE approvals SET status = 'expired' "
                "WHERE status = 'approved' AND expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            )
        return cursor.rowcount
