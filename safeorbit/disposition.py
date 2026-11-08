"""P2 处置闭环：提案 → 审批 → 执行。

影子模式下只记录处置动作，不真正阻断；只有 enforce 模式执行时才生效。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from .storage import connect as connect_db

logger = logging.getLogger("safeorbit.disposition")

ACTIONS = ("isolate", "block_tool", "require_approval", "notify", "ignore")
ACTION_TEXT = {
    "isolate": "隔离会话",
    "block_tool": "阻断工具",
    "require_approval": "要求人工审批",
    "notify": "通知值班人",
    "ignore": "忽略",
}
STATUS_TEXT = {
    "proposed": "待审批",
    "approved": "已批准",
    "rejected": "已驳回",
    "executed": "已执行",
    "failed": "执行失败",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Disposition(BaseModel):
    disposition_id: str
    trace_id: Optional[str] = None
    agent_id: str = "*"
    session_id: Optional[str] = None
    action: str = "require_approval"
    target: Optional[str] = None
    reason: str = ""
    severity: str = "medium"
    rule_id: Optional[str] = None
    status: str = "proposed"
    created_mode: str = "shadow"
    executed_mode: Optional[str] = None
    requested_by: str = "system"
    decided_by: Optional[str] = None
    decision_note: str = ""
    execution_note: str = ""
    evidence: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
    decided_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None

    def to_public(self) -> Dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["action_text"] = ACTION_TEXT.get(self.action, self.action)
        payload["status_text"] = STATUS_TEXT.get(self.status, self.status)
        return payload


def _row_to_disposition(row: sqlite3.Row) -> Disposition:
    return Disposition(
        disposition_id=row["disposition_id"],
        trace_id=row["trace_id"],
        agent_id=row["agent_id"],
        session_id=row["session_id"],
        action=row["action"],
        target=row["target"],
        reason=row["reason"] or "",
        severity=row["severity"] or "medium",
        rule_id=row["rule_id"],
        status=row["status"],
        created_mode=row["created_mode"] or "shadow",
        executed_mode=row["executed_mode"],
        requested_by=row["requested_by"] or "system",
        decided_by=row["decided_by"],
        decision_note=row["decision_note"] or "",
        execution_note=row["execution_note"] or "",
        evidence=json.loads(row["evidence"] or "{}"),
        created_at=datetime.fromisoformat(row["created_at"]),
        decided_at=datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None,
        executed_at=datetime.fromisoformat(row["executed_at"]) if row["executed_at"] else None,
    )


class DispositionStore:
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
                CREATE TABLE IF NOT EXISTS dispositions (
                    disposition_id TEXT PRIMARY KEY,
                    trace_id TEXT,
                    agent_id TEXT,
                    session_id TEXT,
                    action TEXT,
                    target TEXT,
                    reason TEXT,
                    severity TEXT,
                    rule_id TEXT,
                    status TEXT,
                    created_mode TEXT,
                    executed_mode TEXT,
                    requested_by TEXT,
                    decided_by TEXT,
                    decision_note TEXT,
                    execution_note TEXT,
                    evidence TEXT,
                    created_at TEXT,
                    decided_at TEXT,
                    executed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS isolated_sessions (
                    session_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    disposition_id TEXT,
                    reason TEXT,
                    until TEXT,
                    created_at TEXT,
                    active INTEGER DEFAULT 1,
                    PRIMARY KEY (agent_id, session_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS blocked_tools (
                    agent_id TEXT,
                    tool_name TEXT,
                    disposition_id TEXT,
                    reason TEXT,
                    created_at TEXT,
                    active INTEGER DEFAULT 1,
                    PRIMARY KEY (agent_id, tool_name)
                )
                """
            )

            connection.execute(
                "CREATE TABLE IF NOT EXISTS disposition_schema_migrations "
                "(version INTEGER PRIMARY KEY)"
            )
            if connection.execute(
                "SELECT 1 FROM disposition_schema_migrations WHERE version = 1"
            ).fetchone() is None:
                ambiguous = connection.execute(
                    "SELECT 1 FROM isolated_sessions WHERE agent_id IS NULL "
                    "OR TRIM(agent_id) = '' OR agent_id = '*' "
                    "OR session_id IS NULL OR TRIM(session_id) = '' LIMIT 1"
                ).fetchone()
                if ambiguous is not None:
                    raise ValueError("旧隔离记录归属不明确，已停止迁移；请人工处理，原数据保留")
                columns = connection.execute("PRAGMA table_info(isolated_sessions)").fetchall()
                primary_key = [
                    row["name"] for row in sorted(columns, key=lambda row: row["pk"]) if row["pk"]
                ]
                if primary_key != ["agent_id", "session_id"]:
                    connection.execute(
                        "CREATE TABLE isolated_sessions_v1 ("
                        "session_id TEXT NOT NULL, agent_id TEXT NOT NULL, "
                        "disposition_id TEXT, reason TEXT, until TEXT, created_at TEXT, "
                        "active INTEGER DEFAULT 1, PRIMARY KEY (agent_id, session_id))"
                    )
                    connection.execute(
                        "INSERT INTO isolated_sessions_v1 "
                        "(session_id, agent_id, disposition_id, reason, until, created_at, active) "
                        "SELECT session_id, agent_id, disposition_id, reason, until, created_at, active "
                        "FROM isolated_sessions"
                    )
                    connection.execute("DROP TABLE isolated_sessions")
                    connection.execute("ALTER TABLE isolated_sessions_v1 RENAME TO isolated_sessions")
                connection.execute("INSERT INTO disposition_schema_migrations VALUES (1)")

    @staticmethod
    def _require_agent(agent_id: str) -> None:
        if not agent_id or not agent_id.strip() or agent_id == "*":
            raise ValueError("会话隔离必须提供明确的 agent_id")

    # ---------- 提案 ----------
    def propose(
        self,
        *,
        agent_id: str,
        action: str,
        reason: str,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        target: Optional[str] = None,
        severity: str = "medium",
        rule_id: Optional[str] = None,
        created_mode: str = "shadow",
        requested_by: str = "system",
        evidence: Optional[Dict[str, Any]] = None,
    ) -> Disposition:
        if action not in ACTIONS:
            raise ValueError("未知处置动作：%s" % action)
        disposition = Disposition(
            disposition_id="dsp-" + uuid4().hex[:10],
            trace_id=trace_id,
            agent_id=agent_id or "*",
            session_id=session_id,
            action=action,
            target=target,
            reason=reason,
            severity=severity,
            rule_id=rule_id,
            created_mode=created_mode,
            requested_by=requested_by,
            evidence=evidence or {},
            created_at=_now(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO dispositions
                (disposition_id, trace_id, agent_id, session_id, action, target, reason,
                 severity, rule_id, status, created_mode, executed_mode, requested_by,
                 decided_by, decision_note, execution_note, evidence, created_at,
                 decided_at, executed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    disposition.disposition_id,
                    disposition.trace_id,
                    disposition.agent_id,
                    disposition.session_id,
                    disposition.action,
                    disposition.target,
                    disposition.reason,
                    disposition.severity,
                    disposition.rule_id,
                    disposition.status,
                    disposition.created_mode,
                    None,
                    disposition.requested_by,
                    None,
                    "",
                    "",
                    json.dumps(disposition.evidence, ensure_ascii=False),
                    disposition.created_at.isoformat(),
                    None,
                    None,
                ),
            )
        return disposition

    def auto_propose_from_report(
        self,
        *,
        trace_id: str,
        agent_id: str,
        session_id: Optional[str],
        verdict: str,
        hits: List[Dict[str, Any]],
        mode: str,
    ) -> Optional[Disposition]:
        """报告生成后的自动处置建议：严重问题隔离会话，一般问题转人工复核。"""

        if verdict not in ("fail", "review"):
            return None
        if trace_id and self.has_for_trace(trace_id):
            return None
        ranked = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
        top = max(hits, key=lambda hit: ranked.get(str(hit.get("severity")), 0), default=None)
        if top is None:
            return None
        severity = str(top.get("severity") or "medium")
        if severity == "critical" and session_id:
            action = "isolate"
            reason = "检测到严重风险，建议隔离会话并停止后续工具调用"
        elif severity in ("critical", "high"):
            action = "require_approval"
            reason = "检测到高风险问题，需人工审批后再继续"
        else:
            action = "notify"
            reason = "检测到风险，建议通知值班人复核"
        return self.propose(
            agent_id=agent_id,
            action=action,
            reason=reason,
            trace_id=trace_id,
            session_id=session_id,
            severity=severity,
            rule_id=top.get("rule_id"),
            created_mode=mode,
            requested_by="system",
            evidence={"verdict": verdict, "hit": top},
        )

    # ---------- 查询 ----------
    def list(self, status: Optional[str] = None, limit: int = 100) -> List[Disposition]:
        query = "SELECT * FROM dispositions"
        params: List[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_disposition(row) for row in rows]

    def get(self, disposition_id: str) -> Optional[Disposition]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM dispositions WHERE disposition_id = ?", (disposition_id,)
            ).fetchone()
        return _row_to_disposition(row) if row else None

    def has_for_trace(self, trace_id: str, action: Optional[str] = None) -> bool:
        """同一轨迹重复审计时只生成一条自动处置，避免刷屏。"""

        query = "SELECT 1 FROM dispositions WHERE trace_id = ?"
        params: List[Any] = [trace_id]
        if action:
            query += " AND action = ?"
            params.append(action)
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return row is not None

    # ---------- 审批 ----------
    def decide(
        self,
        disposition_id: str,
        *,
        approved: bool,
        decided_by: str,
        note: str = "",
    ) -> Disposition:
        disposition = self.get(disposition_id)
        if disposition is None:
            raise ValueError("处置提案不存在")
        if disposition.status != "proposed":
            raise ValueError("只有待审批的提案可以审批，当前状态：%s" % disposition.status)
        status = "approved" if approved else "rejected"
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE dispositions SET status = ?, decided_by = ?, decision_note = ?, decided_at = ?
                WHERE disposition_id = ?
                """,
                (status, decided_by, note, now.isoformat(), disposition_id),
            )
        disposition = self.get(disposition_id)
        assert disposition is not None
        return disposition

    # ---------- 执行 ----------
    def execute(
        self,
        disposition_id: str,
        *,
        mode: str,
        executed_by: str,
        isolation_minutes: int = 30,
    ) -> Disposition:
        disposition = self.get(disposition_id)
        if disposition is None:
            raise ValueError("处置提案不存在")
        if disposition.status != "approved":
            raise ValueError("只有已批准的提案可以执行，当前状态：%s" % disposition.status)

        if mode == "shadow":
            note = "影子模式：已记录处置动作，未实际阻断业务"
        elif disposition.action == "isolate":
            if not disposition.session_id:
                note = "缺少 session_id，无法隔离会话；已记录动作"
            else:
                self._require_agent(disposition.agent_id)
                until = (_now() + timedelta(minutes=isolation_minutes)).isoformat()
                with self._connect() as connection:
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO isolated_sessions
                        (session_id, agent_id, disposition_id, reason, until, created_at, active)
                        VALUES (?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            disposition.session_id,
                            disposition.agent_id,
                            disposition_id,
                            disposition.reason,
                            until,
                            _now().isoformat(),
                        ),
                    )
                note = "已隔离会话 %s，有效期 %d 分钟" % (disposition.session_id, isolation_minutes)
        elif disposition.action == "block_tool":
            if not disposition.target:
                note = "缺少目标工具，无法阻断；已记录动作"
            else:
                with self._connect() as connection:
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO blocked_tools
                        (agent_id, tool_name, disposition_id, reason, created_at, active)
                        VALUES (?, ?, ?, ?, ?, 1)
                        """,
                        (
                            disposition.agent_id,
                            disposition.target,
                            disposition_id,
                            disposition.reason,
                            _now().isoformat(),
                        ),
                    )
                note = "已阻断 Agent %s 的工具 %s" % (disposition.agent_id, disposition.target)
        else:
            note = "处置动作 %s 无需运行时阻断，已记录" % disposition.action

        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE dispositions SET status = 'executed', executed_mode = ?,
                    execution_note = ?, executed_at = ?
                WHERE disposition_id = ?
                """,
                (mode, note, now.isoformat(), disposition_id),
            )
        disposition = self.get(disposition_id)
        assert disposition is not None
        logger.warning(
            "处置已执行 id=%s action=%s agent_id=%s mode=%s note=%s",
            disposition_id, disposition.action, disposition.agent_id, mode, note,
        )
        return disposition

    # ---------- 运行时查询 ----------
    def is_session_isolated(
        self, session_id: Optional[str], agent_id: str
    ) -> Optional[Dict[str, Any]]:
        self._require_agent(agent_id)
        if not session_id:
            return None
        now = _now().isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM isolated_sessions
                WHERE session_id = ? AND agent_id = ? AND active = 1
                    AND (until IS NULL OR until > ?)
                """,
                (session_id, agent_id, now),
            ).fetchone()
        return dict(row) if row else None

    def is_tool_blocked(self, agent_id: str, tool_name: str) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM blocked_tools
                WHERE (agent_id = ? OR agent_id = '*') AND tool_name = ? AND active = 1
                """,
                (agent_id, tool_name),
            ).fetchone()
        return dict(row) if row else None

    def release_isolation(self, session_id: str, agent_id: str) -> bool:
        self._require_agent(agent_id)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE isolated_sessions SET active = 0 WHERE session_id = ? AND agent_id = ?",
                (session_id, agent_id),
            )
        return cursor.rowcount > 0

    def release_tool(self, agent_id: str, tool_name: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE blocked_tools SET active = 0 WHERE agent_id = ? AND tool_name = ?",
                (agent_id, tool_name),
            )
        return cursor.rowcount > 0

    def expire_isolations(self) -> int:
        """到期隔离自动失效，避免看板长期显示已过期的限制。"""

        now = _now().isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE isolated_sessions SET active = 0 "
                "WHERE active = 1 AND until IS NOT NULL AND until <= ?",
                (now,),
            )
        return cursor.rowcount

    def runtime_summary(self) -> Dict[str, Any]:
        self.expire_isolations()
        now = _now().isoformat()
        with self._connect() as connection:
            isolated = connection.execute(
                "SELECT session_id, agent_id, reason, until FROM isolated_sessions "
                "WHERE active = 1 AND (until IS NULL OR until > ?)",
                (now,),
            ).fetchall()
            blocked = connection.execute(
                "SELECT agent_id, tool_name, reason FROM blocked_tools WHERE active = 1"
            ).fetchall()
        return {
            "isolated_sessions": [dict(row) for row in isolated],
            "blocked_tools": [dict(row) for row in blocked],
        }
