"""数据生命周期与证据包：保留策略、清理、导出可复核的审计证据。"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .storage import connect as connect_db
from .store import TraceStore


class RetentionPolicy:
    def __init__(
        self,
        trace_days: int = 90,
        report_versions: int = 10,
        audit_days: int = 365,
    ):
        self.trace_days = max(1, int(trace_days))
        self.report_versions = max(1, int(report_versions))
        self.audit_days = max(1, int(audit_days))

    def to_public(self) -> Dict[str, int]:
        return {
            "trace_days": self.trace_days,
            "report_versions": self.report_versions,
            "audit_days": self.audit_days,
        }


def _connect(db_path: str) -> sqlite3.Connection:
    return connect_db(db_path)


def _rows(connection: sqlite3.Connection, sql: str, params=()) -> List[sqlite3.Row]:
    try:
        return connection.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def preview(db_path: str, policy: RetentionPolicy) -> Dict[str, Any]:
    # 轨迹时间使用本地时区写入，audit_events 使用 UTC；分别计算截止时间。
    trace_cutoff = (datetime.now().astimezone() - timedelta(days=policy.trace_days)).isoformat()
    audit_cutoff = (datetime.now(timezone.utc) - timedelta(days=policy.audit_days)).isoformat()
    with _connect(db_path) as connection:
        traces = connection.execute(
            "SELECT COUNT(*) AS n FROM traces WHERE created_at < ?", (trace_cutoff,)
        ).fetchone()["n"]
        try:
            audit = connection.execute(
                "SELECT COUNT(*) AS n FROM audit_events WHERE created_at < ?", (audit_cutoff,)
            ).fetchone()["n"]
        except sqlite3.OperationalError:
            audit = 0
        total_versions = connection.execute(
            "SELECT COUNT(*) AS n FROM trace_reports"
        ).fetchone()["n"]
    return {
        "policy": policy.to_public(),
        "trace_cutoff": trace_cutoff,
        "audit_cutoff": audit_cutoff,
        "traces_to_purge": int(traces),
        "audit_events_to_purge": int(audit),
        "report_versions": int(total_versions),
    }


def purge(db_path: str, policy: RetentionPolicy, *, dry_run: bool = True) -> Dict[str, Any]:
    plan = preview(db_path, policy)
    if dry_run:
        plan["dry_run"] = True
        return plan

    trace_cutoff = plan["trace_cutoff"]
    audit_cutoff = plan["audit_cutoff"]
    deleted = {"traces": 0, "reports": 0, "audit_events": 0, "versions": 0}
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            "SELECT trace_id FROM traces WHERE created_at < ?", (trace_cutoff,)
        ).fetchall()
        trace_ids = [row["trace_id"] for row in rows]
        for trace_id in trace_ids:
            connection.execute("DELETE FROM trace_reports WHERE trace_id = ?", (trace_id,))
            connection.execute("DELETE FROM trace_owners WHERE trace_id = ?", (trace_id,))
            connection.execute("DELETE FROM traces WHERE trace_id = ?", (trace_id,))
            deleted["traces"] += 1

        # 每个 trace 只保留最近 N 个报告版本
        traces = connection.execute("SELECT DISTINCT trace_id FROM trace_reports").fetchall()
        for row in traces:
            trace_id = row["trace_id"]
            versions = connection.execute(
                "SELECT report_version FROM trace_reports WHERE trace_id = ? "
                "ORDER BY report_version DESC",
                (trace_id,),
            ).fetchall()
            for extra in versions[policy.report_versions:]:
                connection.execute(
                    "DELETE FROM trace_reports WHERE trace_id = ? AND report_version = ?",
                    (trace_id, extra["report_version"]),
                )
                deleted["versions"] += 1

        try:
            cursor = connection.execute(
                "DELETE FROM audit_events WHERE created_at < ?", (audit_cutoff,)
            )
            deleted["audit_events"] = cursor.rowcount
        except sqlite3.OperationalError:
            deleted["audit_events"] = 0
    plan.update({"dry_run": False, "deleted": deleted})
    return plan


def export_evidence(
    db_path: str,
    output_path: str,
    *,
    trace_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """导出 zip 证据包：轨迹、报告、版本链、策略与操作日志。"""

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    store = TraceStore(db_path)
    with _connect(db_path) as connection:
        if trace_ids is not None:
            if trace_ids:
                placeholders = ",".join("?" for _ in trace_ids)
                rows = connection.execute(
                    "SELECT * FROM traces WHERE trace_id IN (%s)" % placeholders,
                    trace_ids,
                ).fetchall()
            else:
                rows = []
        else:
            rows = connection.execute("SELECT * FROM traces ORDER BY created_at").fetchall()
        policies = [dict(row) for row in _rows(connection, "SELECT * FROM policies")]
        audit_events = [
            dict(row) for row in _rows(
                connection, "SELECT * FROM audit_events ORDER BY event_id"
            )
        ]

    manifest_traces = []
    integrity = {}
    html_files: Dict[str, str] = {}
    for row in rows:
        record = dict(row)
        trace_id = record["trace_id"]
        manifest_traces.append(
            {
                "trace_id": trace_id,
                "agent_id": record.get("agent_id"),
                "created_at": record.get("created_at"),
                "verdict": record.get("verdict"),
                "report_version": record.get("report_version"),
                "content_hash": record.get("content_hash"),
                "prev_hash": record.get("prev_hash"),
                "report": json.loads(record["report_json"]) if record.get("report_json") else None,
                "trajectory": json.loads(record["payload"]) if record.get("payload") else None,
                "history": store.report_history(trace_id, limit=200),
            }
        )
        integrity[trace_id] = store.verify_chain(trace_id)
        if record.get("report_html"):
            html_files[trace_id] = record["report_html"]

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": str(Path(db_path).resolve()),
        "trace_count": len(manifest_traces),
        "trace_ids": [item["trace_id"] for item in manifest_traces],
    }
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("traces.json", json.dumps(manifest_traces, ensure_ascii=False, indent=2))
        archive.writestr("integrity.json", json.dumps(integrity, ensure_ascii=False, indent=2))
        archive.writestr("policies.json", json.dumps(policies, ensure_ascii=False, indent=2))
        archive.writestr("audit_events.json", json.dumps(audit_events, ensure_ascii=False, indent=2))
        for trace_id, html in html_files.items():
            archive.writestr("reports/%s.html" % trace_id, html)
    return {
        "path": str(target),
        "bytes": target.stat().st_size,
        "trace_count": len(manifest_traces),
        "audit_events": len(audit_events),
        "policies": len(policies),
    }
