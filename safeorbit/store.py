"""SQLite 轨迹存储与实时事件总线。"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .models import AuditReport, Trajectory
from .normalize import compute_integrity
from .storage import connect as connect_db

logger = logging.getLogger("safeorbit.store")

# PRAGMA / ALTER 只允许内部已知表名，避免任何字符串拼接进入 DDL。
_ALLOWED_TABLES = frozenset({"traces", "trace_reports"})


class TraceStore:
    """P1 的最小持久化：一条轨迹 + 一份报告，够用即可。"""

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
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    agent_id TEXT,
                    created_at TEXT,
                    verdict TEXT,
                    cost_usd REAL,
                    hits_count INTEGER,
                    payload TEXT,
                    report_json TEXT,
                    report_html TEXT
                )
                """
            )

            connection.execute(
                "CREATE TABLE IF NOT EXISTS trace_owners "
                "(trace_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS trace_schema_migrations (version INTEGER PRIMARY KEY)"
            )
            if connection.execute(
                "SELECT 1 FROM trace_schema_migrations WHERE version = 1"
            ).fetchone() is None:
                connection.execute(
                    "INSERT INTO trace_owners (trace_id, agent_id) "
                    "SELECT trace_id, agent_id FROM traces "
                    "WHERE agent_id IS NOT NULL AND TRIM(agent_id) != '' AND agent_id != '*'"
                )
                connection.execute("INSERT INTO trace_schema_migrations VALUES (1)")

            # 旧库可能缺少聚合列，索引只在列齐备时创建，保持幂等。
            existing_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(traces)").fetchall()
            }
            for name, columns in (
                ("idx_traces_created_at", "created_at DESC"),
                ("idx_traces_verdict_created", "verdict, created_at DESC"),
                ("idx_traces_agent_created", "agent_id, created_at DESC"),
            ):
                required = {item.strip().split()[0] for item in columns.split(",")}
                if required <= existing_columns:
                    connection.execute(
                        "CREATE INDEX IF NOT EXISTS %s ON traces(%s)" % (name, columns)
                    )

            # 报告版本与证据链：每条轨迹保留历史报告，版本之间用内容哈希串联。
            self._ensure_columns(
                connection,
                "traces",
                (
                    ("session_id", "TEXT"),
                    ("created_at", "TEXT"),
                    ("verdict", "TEXT"),
                    ("cost_usd", "REAL"),
                    ("hits_count", "INTEGER"),
                    ("payload", "TEXT"),
                    ("report_json", "TEXT"),
                    ("report_html", "TEXT"),
                    ("agent_version", "TEXT"),
                    ("content_hash", "TEXT"),
                    ("prev_hash", "TEXT"),
                    ("report_version", "INTEGER DEFAULT 1"),
                    ("updated_at", "TEXT"),
                    ("tenant_id", "TEXT DEFAULT 'default'"),
                ),
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trace_reports (
                    trace_id TEXT NOT NULL,
                    report_version INTEGER NOT NULL,
                    agent_id TEXT,
                    agent_version TEXT,
                    verdict TEXT,
                    cost_usd REAL,
                    hits_count INTEGER,
                    content_hash TEXT,
                    prev_hash TEXT,
                    created_at TEXT,
                    tenant_id TEXT DEFAULT 'default',
                    payload TEXT,
                    report_json TEXT,
                    report_html TEXT,
                    PRIMARY KEY (trace_id, report_version)
                )
                """
            )
            self._ensure_columns(
                connection,
                "trace_reports",
                (("tenant_id", "TEXT DEFAULT 'default'"),),
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_trace_reports_trace "
                "ON trace_reports(trace_id, report_version DESC)"
            )
            self._backfill_reports(connection)

    @staticmethod
    def _ensure_columns(connection: sqlite3.Connection, table: str, columns) -> None:
        if table not in _ALLOWED_TABLES:
            raise ValueError("不允许对未登记的表执行 schema 变更：%s" % table)
        existing = {
            row["name"] for row in connection.execute("PRAGMA table_info(%s)" % table).fetchall()
        }
        for name, declaration in columns:
            if name not in existing:
                logger.info("schema 迁移：%s 新增列 %s", table, name)
                connection.execute(
                    "ALTER TABLE %s ADD COLUMN %s %s" % (table, name, declaration)
                )

    @staticmethod
    def _backfill_reports(connection: sqlite3.Connection) -> None:
        """旧库升级：把现有轨迹补成版本 1，避免历史报告突然消失。"""

        if connection.execute("SELECT 1 FROM trace_reports LIMIT 1").fetchone() is not None:
            return
        rows = connection.execute("SELECT * FROM traces").fetchall()
        for row in rows:
            data = dict(row)
            payload = data.get("payload") or "{}"
            digest = data.get("content_hash")
            if not digest:
                try:
                    digest = compute_integrity(
                        Trajectory.model_validate(json.loads(payload))
                    ).content_hash
                except Exception as exc:
                    # 证据链断裂必须留痕：trace_id + 异常类型，便于事后定位是数据损坏还是 schema 不兼容
                    logger.warning(
                        "回填报告时无法重算内容哈希，证据链将标记为不可校验 trace_id=%s error=%s",
                        data.get("trace_id"),
                        type(exc).__name__,
                    )
                    digest = ""
            connection.execute(
                """
                INSERT OR IGNORE INTO trace_reports
                (trace_id, report_version, agent_id, agent_version, verdict, cost_usd,
                 hits_count, content_hash, prev_hash, created_at, tenant_id, payload,
                 report_json, report_html)
                VALUES (?, 1, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    data.get("trace_id"),
                    data.get("agent_id"),
                    data.get("agent_version"),
                    data.get("verdict"),
                    data.get("cost_usd"),
                    data.get("hits_count"),
                    digest,
                    data.get("created_at"),
                    data.get("tenant_id") or "default",
                    payload,
                    data.get("report_json"),
                    data.get("report_html"),
                ),
            )
            connection.execute(
                "UPDATE traces SET content_hash = ?, report_version = 1 WHERE trace_id = ?",
                (digest, data.get("trace_id")),
            )

    @staticmethod
    def _claim(connection: sqlite3.Connection, trace_id: str, agent_id: str) -> None:
        if not trace_id or not agent_id or not agent_id.strip() or agent_id == "*":
            raise ValueError("trace_id 和明确的 agent_id 必须提供")
        existing = connection.execute(
            "SELECT agent_id FROM traces WHERE trace_id = ?", (trace_id,)
        ).fetchone()
        if existing is not None and existing["agent_id"] != agent_id:
            logger.warning(
                "轨迹归属冲突 trace_id=%s existing=%s incoming=%s",
                trace_id, existing["agent_id"], agent_id,
            )
            raise ValueError("轨迹归属冲突或旧轨迹归属缺失，禁止认领")
        owner = connection.execute(
            "SELECT agent_id FROM trace_owners WHERE trace_id = ?", (trace_id,)
        ).fetchone()
        if owner is not None:
            if owner["agent_id"] != agent_id:
                logger.warning(
                    "轨迹已被其他 Agent 认领 trace_id=%s owner=%s incoming=%s",
                    trace_id, owner["agent_id"], agent_id,
                )
                raise ValueError("轨迹已归属其他 Agent")
        else:
            connection.execute(
                "INSERT INTO trace_owners (trace_id, agent_id) VALUES (?, ?)",
                (trace_id, agent_id),
            )

    def claim(self, trace_id: str, agent_id: str) -> None:
        """同步绑定归属；冲突时抛 ValueError，不改变已有绑定。"""
        self.claim_many([(trace_id, agent_id)])

    def claim_many(self, pairs: Iterable[Tuple[str, str]]) -> None:
        """整批绑定归属，任一冲突则整批回滚。"""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for trace_id, agent_id in pairs:
                self._claim(connection, trace_id, agent_id)

    def save(self, trajectory: Trajectory, report: AuditReport, report_html: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._claim(connection, trajectory.trace_id, trajectory.agent_id)
            previous = connection.execute(
                "SELECT report_version, content_hash FROM trace_reports "
                "WHERE trace_id = ? ORDER BY report_version DESC LIMIT 1",
                (trajectory.trace_id,),
            ).fetchone()
            version = int(previous["report_version"]) + 1 if previous else 1
            prev_hash = previous["content_hash"] if previous else None

            # 链哈希只覆盖内容字段；integrity 本身不参与摘要，因此这里补 prev_hash 不会破坏校验。
            if trajectory.integrity is not None and trajectory.integrity.prev_hash is None:
                trajectory.integrity.prev_hash = prev_hash
            content_hash = (
                trajectory.integrity.content_hash
                if trajectory.integrity is not None and trajectory.integrity.content_hash
                else compute_integrity(trajectory).content_hash
            )
            report.compliance["integrity_hash"] = content_hash
            if prev_hash:
                report.compliance["prev_hash"] = prev_hash
            report.compliance["report_version"] = version

            payload_text = json.dumps(trajectory.model_dump(mode="json"), ensure_ascii=False)
            report_text = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
            now = datetime.now().astimezone().isoformat()
            tenant_id = trajectory.tenant_id or "default"
            connection.execute(
                """
                INSERT INTO trace_reports
                (trace_id, report_version, agent_id, agent_version, verdict, cost_usd,
                 hits_count, content_hash, prev_hash, created_at, tenant_id, payload,
                 report_json, report_html)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trajectory.trace_id,
                    version,
                    trajectory.agent_id,
                    trajectory.agent_version,
                    report.verdict.value,
                    report.cost.total_usd,
                    len(report.hits),
                    content_hash,
                    prev_hash,
                    report.generated_at.isoformat(),
                    tenant_id,
                    payload_text,
                    report_text,
                    report_html,
                ),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO traces
                (trace_id, session_id, agent_id, agent_version, created_at, verdict, cost_usd,
                 hits_count, payload, report_json, report_html, content_hash, prev_hash,
                 report_version, updated_at, tenant_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trajectory.trace_id,
                    trajectory.session_id,
                    trajectory.agent_id,
                    trajectory.agent_version,
                    report.generated_at.isoformat(),
                    report.verdict.value,
                    report.cost.total_usd,
                    len(report.hits),
                    payload_text,
                    report_text,
                    report_html,
                    content_hash,
                    prev_hash,
                    version,
                    now,
                    tenant_id,
                ),
            )
        logger.debug(
            "轨迹已入库 trace_id=%s agent_id=%s version=%d verdict=%s",
            trajectory.trace_id, trajectory.agent_id, version, report.verdict.value,
        )

    @staticmethod
    def _trace_filters(
        agent_id: Optional[str] = None,
        verdict: Optional[str] = None,
        search: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Tuple[str, List[Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if verdict:
            clauses.append("verdict = ?")
            params.append(verdict)
        if search:
            pattern = "%" + search.strip() + "%"
            clauses.append("(trace_id LIKE ? OR agent_id LIKE ? OR session_id LIKE ?)")
            params.extend([pattern, pattern, pattern])
        if start:
            clauses.append("created_at >= ?")
            params.append(start)
        if end:
            clauses.append("created_at <= ?")
            params.append(end)
        return (" WHERE " + " AND ".join(clauses) if clauses else "", params)

    def trace_ids(self, tenant_id: Optional[str] = None) -> List[str]:
        query = "SELECT trace_id FROM traces"
        params: List[Any] = []
        if tenant_id:
            query += " WHERE tenant_id = ?"
            params.append(tenant_id)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [row["trace_id"] for row in rows]

    def baseline_drift_count(self, tenant_id: Optional[str] = None) -> int:
        """命中基线偏离规则的轨迹数，用于 HOTL 漂移信号。"""

        where = " WHERE tenant_id = ?" if tenant_id else " WHERE 1=1"
        params: List[Any] = [tenant_id] if tenant_id else []
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM traces"
                + where
                + " AND (report_json LIKE '%baseline_new_tool%'"
                + " OR report_json LIKE '%baseline_cost_spike%')",
                params,
            ).fetchone()
        return int(row["n"] if row else 0)

    def count_traces(
        self,
        agent_id: Optional[str] = None,
        verdict: Optional[str] = None,
        search: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> int:
        where, params = self._trace_filters(agent_id, verdict, search, start, end, tenant_id)
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS n FROM traces" + where, params).fetchone()
        return int(row["n"] if row else 0)

    def list_traces(
        self,
        limit: int = 50,
        offset: int = 0,
        agent_id: Optional[str] = None,
        verdict: Optional[str] = None,
        search: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        where, params = self._trace_filters(agent_id, verdict, search, start, end, tenant_id)
        params = list(params) + [max(1, min(int(limit), 500)), max(0, int(offset))]
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT trace_id, session_id, agent_id, agent_version, created_at, verdict,
                       cost_usd, hits_count, report_version, content_hash, prev_hash, tenant_id
                FROM traces""" + where + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def traces_after(
        self,
        agent_id: str,
        since: str,
        session_id: Optional[str] = None,
        limit: int = 50,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = (
            "SELECT trace_id, session_id, agent_id, created_at, verdict, cost_usd, hits_count "
            "FROM traces WHERE agent_id = ? AND created_at >= ?"
        )
        params: List[Any] = [agent_id, since]
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if tenant_id:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        query += " ORDER BY created_at ASC LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def agent_stats(
        self,
        agent_id: str,
        exclude_trace_id: Optional[str] = None,
        limit: int = 100,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """计算该 Agent 的历史基线：工具集合、成本分位、风险率。"""

        query = (
            "SELECT trace_id, cost_usd, hits_count, verdict, payload FROM traces "
            "WHERE agent_id = ?"
        )
        params: List[Any] = [agent_id]
        if tenant_id:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()

        costs: List[float] = []
        tools: Dict[str, int] = {}
        hits_total = 0
        fails = 0
        for row in rows:
            if exclude_trace_id and row["trace_id"] == exclude_trace_id:
                continue
            costs.append(float(row["cost_usd"] or 0.0))
            hits_total += int(row["hits_count"] or 0)
            if row["verdict"] in ("fail", "review"):
                fails += 1
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError):
                continue
            for span in payload.get("spans") or []:
                if span.get("kind") != "tool":
                    continue
                tool = span.get("tool") or {}
                name = tool.get("tool_name") or span.get("name")
                if name:
                    tools[str(name)] = tools.get(str(name), 0) + 1

        sample = len(costs)
        if sample == 0:
            return {
                "sample_size": 0,
                "tools": {},
                "cost_p50": 0.0,
                "cost_p95": 0.0,
                "cost_max": 0.0,
                "avg_hits": 0.0,
                "fail_rate": 0.0,
            }
        ordered = sorted(costs)

        def percentile(value: float) -> float:
            index = int(round((value / 100.0) * (len(ordered) - 1)))
            return ordered[max(0, min(len(ordered) - 1, index))]

        return {
            "sample_size": sample,
            "tools": tools,
            "cost_p50": round(percentile(50), 6),
            "cost_p95": round(percentile(95), 6),
            "cost_max": round(max(ordered), 6),
            "avg_hits": round(hits_total / sample, 2),
            "fail_rate": round(fails / sample, 4),
        }

    def agent_versions(self, agent_id: str, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = """
                SELECT COALESCE(NULLIF(agent_version, ''), '未标注') AS version,
                       COUNT(*) AS traces,
                       SUM(CASE WHEN verdict IN ('fail', 'review') THEN 1 ELSE 0 END) AS risk_traces,
                       COALESCE(SUM(cost_usd), 0) AS cost_usd,
                       COALESCE(AVG(hits_count), 0) AS avg_hits,
                       MAX(created_at) AS latest_at
                FROM traces WHERE agent_id = ?
        """
        params: List[Any] = [agent_id]
        if tenant_id:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        query += " GROUP BY version ORDER BY latest_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {
                "version": row["version"],
                "traces": int(row["traces"] or 0),
                "risk_traces": int(row["risk_traces"] or 0),
                "cost_usd": round(float(row["cost_usd"] or 0.0), 6),
                "avg_hits": round(float(row["avg_hits"] or 0.0), 2),
                "latest_at": row["latest_at"],
            }
            for row in rows
        ]

    def last_integrity(self, trace_id: str) -> Optional[Tuple[int, str]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT report_version, content_hash FROM trace_reports "
                "WHERE trace_id = ? ORDER BY report_version DESC LIMIT 1",
                (trace_id,),
            ).fetchone()
        if row is None:
            return None
        return int(row["report_version"]), row["content_hash"] or ""

    def report_history(self, trace_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT report_version, verdict, cost_usd, hits_count, content_hash,
                       prev_hash, created_at
                FROM trace_reports WHERE trace_id = ?
                ORDER BY report_version DESC LIMIT ?
                """,
                (trace_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_report_version(self, trace_id: str, version: int) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM trace_reports WHERE trace_id = ? AND report_version = ?",
                (trace_id, int(version)),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ("payload", "report_json"):
            if result.get(key):
                result[key] = json.loads(result[key])
        return result

    def verify_chain(self, trace_id: str) -> Dict[str, Any]:
        """逐版本重算内容哈希，并校验 prev_hash 是否与上一版一致。"""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM trace_reports WHERE trace_id = ? ORDER BY report_version ASC",
                (trace_id,),
            ).fetchall()
        if not rows:
            return {"trace_id": trace_id, "valid": False, "reason": "没有可校验的报告版本", "versions": []}

        expected_prev: Optional[str] = None
        versions = []
        valid = True
        for row in rows:
            actual = ""
            try:
                trajectory = Trajectory.model_validate(json.loads(row["payload"] or "{}"))
                actual = compute_integrity(trajectory).content_hash
            except Exception as exc:
                logger.warning(
                    "校验证据链时无法重算哈希 trace_id=%s version=%s error=%s",
                    trace_id, row["report_version"], type(exc).__name__,
                )
                actual = ""
            hash_ok = bool(actual) and actual == (row["content_hash"] or "")
            link_ok = (
                (expected_prev is None and not row["prev_hash"])
                or (expected_prev is not None and row["prev_hash"] == expected_prev)
            )
            valid = valid and hash_ok and link_ok
            versions.append(
                {
                    "report_version": int(row["report_version"]),
                    "created_at": row["created_at"],
                    "hash_ok": hash_ok,
                    "link_ok": link_ok,
                    "content_hash": row["content_hash"],
                    "prev_hash": row["prev_hash"],
                }
            )
            expected_prev = row["content_hash"]
        return {"trace_id": trace_id, "valid": valid, "versions": versions}

    def overview(self, detail_limit: int = 500, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """全量聚合，供汇总看板与汇总报告使用；单连接内完成，不新增表。"""

        detail_limit = max(0, int(detail_limit))
        where = " WHERE tenant_id = ?" if tenant_id else ""
        params: List[Any] = [tenant_id] if tenant_id else []
        and_clause = " AND " if where else " WHERE "
        with self._connect() as connection:
            totals_row = connection.execute(
                """
                SELECT COUNT(*) AS trace_count,
                       COALESCE(SUM(cost_usd), 0) AS total_cost,
                       COALESCE(SUM(hits_count), 0) AS total_hits,
                       MIN(created_at) AS first_seen,
                       MAX(created_at) AS last_seen
                FROM traces""" + where,
                params,
            ).fetchone()
            verdict_rows = connection.execute(
                "SELECT verdict, COUNT(*) AS trace_count FROM traces" + where + " GROUP BY verdict",
                params,
            ).fetchall()
            agent_rows = connection.execute(
                """
                SELECT agent_id,
                       COUNT(*) AS trace_count,
                       COALESCE(SUM(cost_usd), 0) AS total_cost,
                       COALESCE(SUM(hits_count), 0) AS total_hits,
                       SUM(CASE WHEN verdict IN ('fail', 'review') THEN 1 ELSE 0 END) AS risk_count,
                       MAX(created_at) AS last_seen
                FROM traces""" + where + """
                GROUP BY agent_id
                ORDER BY risk_count DESC, trace_count DESC, agent_id ASC
                LIMIT 20
                """,
                params,
            ).fetchall()
            daily_rows = connection.execute(
                """
                SELECT substr(created_at, 1, 10) AS day,
                       COUNT(*) AS trace_count,
                       COALESCE(SUM(cost_usd), 0) AS total_cost
                FROM traces""" + where + """
                GROUP BY day
                """,
                params,
            ).fetchall()
            risk_total = connection.execute(
                "SELECT COUNT(*) AS trace_count FROM traces"
                + where
                + and_clause
                + "verdict IN ('fail', 'review')",
                params,
            ).fetchone()["trace_count"]
            risk_rows = connection.execute(
                """
                SELECT trace_id, agent_id, created_at, verdict, cost_usd, hits_count, report_json
                FROM traces"""
                + where
                + and_clause
                + """verdict IN ('fail', 'review')
                ORDER BY created_at DESC
                LIMIT ?""",
                params + [detail_limit],
            ).fetchall()
            cost_rows = connection.execute(
                """
                SELECT trace_id, agent_id, created_at, verdict, cost_usd, hits_count
                FROM traces"""
                + where
                + """
                ORDER BY cost_usd DESC, created_at DESC
                LIMIT 10""",
                params,
            ).fetchall()

        verdict_counts = {"pass": 0, "warn": 0, "fail": 0, "review": 0}
        for row in verdict_rows:
            key = row["verdict"] or ""
            if key in verdict_counts:
                verdict_counts[key] = int(row["trace_count"])

        daily_counts: Dict[str, Dict[str, Any]] = {}
        for row in daily_rows:
            day = row["day"] or ""
            daily_counts[day] = {
                "traces": int(row["trace_count"]),
                "cost_usd": round(float(row["total_cost"] or 0.0), 6),
            }
        latest_day = max(daily_counts, default="")
        today = datetime.now().astimezone().date()
        end = today
        if latest_day:
            try:
                end = max(end, datetime.strptime(latest_day, "%Y-%m-%d").date())
            except ValueError:
                end = today
        daily = []
        for offset in range(13, -1, -1):
            day = (end - timedelta(days=offset)).isoformat()
            point = daily_counts.get(day, {"traces": 0, "cost_usd": 0.0})
            daily.append({"date": day, "traces": point["traces"], "cost_usd": point["cost_usd"]})

        agents = [
            {
                "agent_id": row["agent_id"] or "",
                "traces": int(row["trace_count"]),
                "cost_usd": round(float(row["total_cost"] or 0.0), 6),
                "hits": int(row["total_hits"]),
                "risk_traces": int(row["risk_count"] or 0),
                "latest_at": row["last_seen"],
            }
            for row in agent_rows
        ]

        risks = []
        for row in risk_rows:
            risks.append(
                {
                    "trace_id": row["trace_id"],
                    "agent_id": row["agent_id"] or "",
                    "created_at": row["created_at"],
                    "verdict": row["verdict"],
                    "cost_usd": round(float(row["cost_usd"] or 0.0), 6),
                    "hits_count": int(row["hits_count"] or 0),
                    "hits": _top_hits(row["report_json"]),
                }
            )

        return {
            "scope": "all",
            "generated_at": datetime.now().astimezone().isoformat(),
            "totals": {
                "traces": int(totals_row["trace_count"]),
                "cost_usd": round(float(totals_row["total_cost"] or 0.0), 6),
                "hits": int(totals_row["total_hits"]),
                "risk_traces": int(risk_total),
                "first_seen": totals_row["first_seen"],
                "last_seen": totals_row["last_seen"],
            },
            "verdicts": verdict_counts,
            "agents": agents,
            "daily": daily,
            "risks": risks,
            "top_cost": [
                {
                    "trace_id": row["trace_id"],
                    "agent_id": row["agent_id"] or "",
                    "created_at": row["created_at"],
                    "verdict": row["verdict"],
                    "cost_usd": round(float(row["cost_usd"] or 0.0), 6),
                    "hits_count": int(row["hits_count"] or 0),
                }
                for row in cost_rows
            ],
            "detail_limit": detail_limit,
            "detail_truncated": int(risk_total) > len(risks),
        }

    def get(self, trace_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM traces WHERE trace_id = ?", (trace_id,)
            ).fetchone()
        if row is None:
            return None
        if tenant_id and (row["tenant_id"] or "default") != tenant_id:
            return None
        result = dict(row)
        for key in ("payload", "report_json"):
            if result.get(key):
                result[key] = json.loads(result[key])
        result["report_history"] = self.report_history(trace_id)
        return result

    def session_cost(
        self,
        session_id: Optional[str],
        agent_id: str,
        tenant_id: Optional[str] = None,
    ) -> float:
        """指定 Agent 的会话累计成本，不跨 Agent 聚合。"""

        if not agent_id or not agent_id.strip() or agent_id == "*":
            raise ValueError("必须提供明确的 agent_id")
        if not session_id:
            return 0.0
        query = (
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM traces "
            "WHERE session_id = ? AND agent_id = ?"
        )
        params: List[Any] = [session_id, agent_id]
        if tenant_id:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return float(row["total"] if row else 0.0)


def _top_hits(report_json: Optional[str]) -> List[Dict[str, str]]:
    """风险明细只保留前 3 个命中的名称/级别/说明，不返回 evidence。"""

    if not report_json:
        return []
    try:
        report = json.loads(report_json)
    except (TypeError, ValueError):
        return []
    hits = report.get("hits") if isinstance(report, dict) else None
    if not isinstance(hits, list):
        return []
    result = []
    for hit in hits[:3]:
        if not isinstance(hit, dict):
            continue
        result.append(
            {
                "rule_name": str(hit.get("rule_name") or ""),
                "severity": str(hit.get("severity") or ""),
                "message": str(hit.get("message") or ""),
            }
        )
    return result


class EventBus:
    """进程内事件总线，够 P1 单进程演示使用。"""

    def __init__(self) -> None:
        self._subscribers: List[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    async def publish(self, event: Dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)
