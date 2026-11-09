"""轨迹行为事件存储：把 span 规范化成可查询的行为事实。

设计边界：
- 只记录轨迹中可观测的事实，不做因果推断，也不把时间先后当因果；
- 模型提取或规则提取的内容必须通过 provenance 标明，不能冒充原始事实；
- 事件、证据、资源、覆盖各自独立 ID；重复导入按 (租户, trace, span) 幂等；
- 晚到数据只追加，不覆盖已有事件；覆盖状态按版本追加，便于回放当时看到了什么。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

from .models import Span, SpanKind, SpanStatus, Trajectory
from .pii import redact_text
from .storage import connect as connect_db

logger = logging.getLogger("safeorbit.events")

SCHEMA_VERSION = "1.0"

_OUTCOME_BY_STATUS = {
    SpanStatus.OK.value: "succeeded",
    SpanStatus.ERROR.value: "failed",
    SpanStatus.TIMEOUT.value: "failed",
    SpanStatus.BLOCKED.value: "blocked",
    SpanStatus.SKIPPED.value: "unknown",
}

_EMAIL_KEYS = ("to", "recipient", "email", "mail_to")
_URL_KEYS = ("url", "endpoint", "href", "webhook", "base_url")
_PATH_KEYS = ("path", "file", "file_path", "filename")
_DATASET_KEYS = ("table", "dataset", "collection", "index")
_REPO_KEYS = ("repo", "repository", "project")
# 邮箱与文件路径按受控摘要保存：既能跨事件关联，又不把原文写进事件表。
_PII_RESOURCE_TYPES = frozenset({"email", "file"})


def _resource_key(resource_type: str, value: Any) -> str:
    text = str(value).strip()
    if resource_type in _PII_RESOURCE_TYPES:
        seed = text.lower() if resource_type == "email" else text
        return "sha256:" + _digest(resource_type, seed)[:32]
    return text


def _digest(*parts: str) -> str:
    canonical = "|".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    return prefix + _digest(*parts)[:32]


def _iso(value: Optional[datetime]) -> str:
    return value.isoformat() if value is not None else ""


def _short(value: Any, limit: int = 200) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(str(text).split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _pick_argument(arguments: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        for actual, value in arguments.items():
            if str(actual).lower() == key and value not in (None, ""):
                return value
    return None


def _canonical_url(value: Any) -> Optional[str]:
    try:
        parsed = urlsplit(str(value))
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    # 只保留定位所需的 scheme/host/path，丢弃 query 与 fragment，避免把参数当资源身份。
    return "%s://%s%s" % (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "")


def _resource_for_span(span: Span) -> Optional[Tuple[str, str]]:
    """给 span 推导一个稳定资源身份；推导不出来时返回 None，不猜测。"""

    if span.tool is not None:
        arguments = span.tool.arguments or {}
        email = _pick_argument(arguments, _EMAIL_KEYS)
        if email is not None:
            return "email", _resource_key("email", email)
        url = _pick_argument(arguments, _URL_KEYS)
        canonical = _canonical_url(url) if url is not None else None
        if canonical:
            return "endpoint", canonical
        path = _pick_argument(arguments, _PATH_KEYS)
        if path is not None:
            return "file", _resource_key("file", path)
        dataset = _pick_argument(arguments, _DATASET_KEYS)
        if dataset is not None:
            return "dataset", _short(dataset, 160)
        repo = _pick_argument(arguments, _REPO_KEYS)
        if repo is not None:
            return "repository", _short(repo, 160)
        return "tool", span.tool.tool_name
    if span.retrieval is not None:
        for document in span.retrieval.documents or []:
            if not isinstance(document, dict):
                continue
            identifier = document.get("id") or document.get("source") or document.get("url")
            if identifier:
                return "document", _short(identifier, 200)
        return "tool", "retrieval"
    if span.llm is not None:
        return "model", span.llm.model
    if span.handoff is not None:
        return "agent", span.handoff.to_agent
    if span.kind == SpanKind.AGENT:
        return "task", _short(span.name, 160)
    return None


def _action_for_span(span: Span) -> Tuple[str, str]:
    if span.tool is not None:
        return "tool.invoke", span.tool.tool_name
    if span.llm is not None:
        return "model.invoke", span.llm.model
    if span.retrieval is not None:
        return "data.read", "retrieval"
    if span.handoff is not None:
        return "message.send", span.handoff.to_agent
    if span.kind == SpanKind.HUMAN:
        return "approval.decide", _short(span.name, 120)
    if span.kind == SpanKind.AGENT:
        return "task.run", _short(span.name, 120)
    return "system.event", _short(span.name, 120)


def _outcome_for_span(span: Span) -> str:
    if span.tool is not None and span.tool.blocked:
        return "blocked"
    return _OUTCOME_BY_STATUS.get(span.status.value, "unknown")


def _attributes_for_span(span: Span, trajectory: Trajectory) -> Dict[str, Any]:
    attributes: Dict[str, Any] = {
        "kind": span.kind.value,
        "name": span.name,
        "status": span.status.value,
        "framework": trajectory.framework or "unknown",
        "agent_version": trajectory.agent_version or "",
        "event_schema_version": SCHEMA_VERSION,
    }
    if span.end_time is not None:
        attributes["duration_ms"] = round(
            max((span.end_time - span.start_time).total_seconds() * 1000, 0.0), 1
        )
    if span.error:
        attributes["error"] = redact_text(_short(span.error, 240))[0]
    if span.llm is not None:
        attributes["llm"] = {
            "provider": span.llm.provider,
            "model": span.llm.model,
            "finish_reason": span.llm.finish_reason,
            "prompt_version": str(trajectory.metadata.get("prompt_version") or ""),
            # 不保存思维链，只保留可公开的完成内容摘要长度
            "completion_chars": len(span.llm.completion or ""),
        }
    if span.tool is not None:
        arguments = span.tool.arguments or {}
        attributes["tool"] = {
            "side_effect": span.tool.side_effect,
            "blocked": span.tool.blocked,
            "permission": span.tool.permission,
            "confirmed_by_human": span.tool.confirmed_by_human,
            # 参数只保留字段名与摘要，原文留在轨迹 payload 中按保留策略管理
            "argument_keys": sorted(str(key) for key in arguments)[:40],
            "arguments_hash": _digest(
                json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
            ),
            "result_summary": redact_text(_short(span.tool.result, 160))[0],
            "tool_manifest_version": str(trajectory.metadata.get("tool_manifest_version") or ""),
        }
    if span.retrieval is not None:
        attributes["retrieval"] = {
            "top_k": span.retrieval.top_k,
            "document_count": len(span.retrieval.documents or []),
        }
    if span.handoff is not None:
        attributes["handoff"] = {
            "from_agent": span.handoff.from_agent,
            "to_agent": span.handoff.to_agent,
        }
    return attributes


def _event_time(span: Span, fallback: datetime) -> datetime:
    return span.start_time or fallback


def _evidence_payload(span: Span) -> str:
    return json.dumps(span.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, default=str)


def derive_records(
    trajectory: Trajectory,
    *,
    policy_ref: Optional[str] = None,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """把一条轨迹推导成事件、证据、资源与覆盖；纯函数，便于固定夹具复核。"""

    tenant_id = trajectory.tenant_id or "default"
    observed_at = trajectory.received_at
    events: List[Dict[str, Any]] = []
    evidences: List[Dict[str, Any]] = []
    resources: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for span in trajectory.spans:
        event_time = _event_time(span, trajectory.started_at)
        evidence_id = _stable_id("evd_", tenant_id, trajectory.trace_id, span.span_id)
        event_id = _stable_id("evt_", tenant_id, trajectory.trace_id, span.span_id)
        action_family, action = _action_for_span(span)
        resource = _resource_for_span(span)
        resource_ref = ""
        if resource is not None:
            resource_type, resource_key = resource
            resource_ref = _stable_id("res_", tenant_id, resource_type, resource_key)
            entry = resources.get((resource_type, resource_key))
            if entry is None:
                resources[(resource_type, resource_key)] = {
                    "resource_id": resource_ref,
                    "tenant_id": tenant_id,
                    "resource_type": resource_type,
                    "canonical_key": resource_key,
                    "environment": str(trajectory.metadata.get("environment") or "unknown"),
                    "first_seen": _iso(event_time),
                    "last_seen": _iso(event_time),
                }
            else:
                if _iso(event_time) < entry["first_seen"]:
                    entry["first_seen"] = _iso(event_time)
                if _iso(event_time) > entry["last_seen"]:
                    entry["last_seen"] = _iso(event_time)

        events.append(
            {
                "event_id": event_id,
                "tenant_id": tenant_id,
                "trace_id": trajectory.trace_id,
                "span_id": span.span_id,
                "parent_span_id": span.parent_span_id,
                "event_time": _iso(event_time),
                "observed_at": _iso(observed_at),
                "actor_ref": trajectory.agent_id,
                "action_family": action_family,
                "action": action,
                "resource_ref": resource_ref or None,
                "outcome": _outcome_for_span(span),
                "evidence_ids": [evidence_id],
                "provenance": "native",
                "schema_version": SCHEMA_VERSION,
                "policy_ref": policy_ref,
                "mode": mode,
                "attributes": _attributes_for_span(span, trajectory),
            }
        )
        evidences.append(
            {
                "evidence_id": evidence_id,
                "tenant_id": tenant_id,
                "trace_id": trajectory.trace_id,
                "span_id": span.span_id,
                "source_type": "trace",
                "content_ref": "trace://%s/span/%s" % (trajectory.trace_id, span.span_id),
                "content_hash": hashlib.sha256(
                    _evidence_payload(span).encode("utf-8")
                ).hexdigest(),
                "captured_at": _iso(observed_at),
                "sensitivity": "redacted" if trajectory.redacted else "unknown",
                "redaction_version": "redact_v1" if trajectory.redacted else "",
                "retention_class": "trace",
                "schema_version": SCHEMA_VERSION,
            }
        )

    coverage = _derive_coverage(trajectory, events)
    return {
        "events": events,
        "evidences": evidences,
        "resources": list(resources.values()),
        "coverage": coverage,
    }


def _derive_coverage(trajectory: Trajectory, events: List[Dict[str, Any]]) -> Dict[str, Any]:
    tenant_id = trajectory.tenant_id or "default"
    gaps: List[str] = []
    span_ids = {span.span_id for span in trajectory.spans}

    if trajectory.sampled:
        gaps.append("轨迹声明为采样采集：仅覆盖采样到的事件")
    if trajectory.truncated_fields:
        gaps.append("字段被截断：%s" % "、".join(sorted(trajectory.truncated_fields)))
    if trajectory.redacted:
        gaps.append("原文已脱敏：原始取值不可用")
    missing_parents = sorted(
        {
            span.parent_span_id
            for span in trajectory.spans
            if span.parent_span_id and span.parent_span_id not in span_ids
        }
    )
    if missing_parents:
        gaps.append("父节点未观测到：%s" % "、".join(missing_parents))
    skipped = [span.span_id for span in trajectory.spans if span.status == SpanStatus.SKIPPED]
    if skipped:
        gaps.append("事件被跳过：%s" % "、".join(sorted(skipped)))
    if not events:
        gaps.append("没有可解析的行为事件")

    if not events:
        status = "unknown"
    elif gaps:
        status = "partial"
    else:
        status = "complete"

    start = trajectory.started_at
    end = trajectory.ended_at
    if end is None and trajectory.spans:
        end = max((span.end_time or span.start_time for span in trajectory.spans))
    instrumentation = (
        trajectory.metadata.get("instrumentation_version")
        or trajectory.framework
        or "unknown"
    )
    return {
        "coverage_id": "",
        "tenant_id": tenant_id,
        "trace_id": trajectory.trace_id,
        "scope": "trace",
        "time_window": {"start": _iso(start), "end": _iso(end)},
        "sampling_policy": "sampled" if trajectory.sampled else "full",
        "capture_status": status,
        "known_gaps": gaps,
        "instrumentation_version": str(instrumentation),
        "schema_version": SCHEMA_VERSION,
    }


class TraceEventStore:
    """行为事件、证据、资源与覆盖的本地存储；全部按租户作用域读取。"""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        return connect_db(str(self.db_path))

    def _init(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trace_events (
                    event_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    span_id TEXT NOT NULL,
                    parent_span_id TEXT,
                    event_time TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    actor_ref TEXT NOT NULL,
                    action_family TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource_ref TEXT,
                    outcome TEXT NOT NULL,
                    evidence_ids TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    policy_ref TEXT,
                    mode TEXT,
                    attributes TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(tenant_id, trace_id, span_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trace_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    span_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    content_ref TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    sensitivity TEXT NOT NULL,
                    redaction_version TEXT,
                    retention_class TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(tenant_id, trace_id, span_id, source_type)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS resources (
                    resource_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    canonical_key TEXT NOT NULL,
                    environment TEXT,
                    first_seen TEXT,
                    last_seen TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(tenant_id, resource_type, canonical_key)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trace_coverage (
                    coverage_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    coverage_version INTEGER NOT NULL,
                    scope TEXT NOT NULL,
                    time_window TEXT NOT NULL,
                    sampling_policy TEXT NOT NULL,
                    capture_status TEXT NOT NULL,
                    known_gaps TEXT NOT NULL,
                    instrumentation_version TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(tenant_id, trace_id, coverage_version)
                )
                """
            )
            for name, columns in (
                ("idx_trace_events_trace", "tenant_id, trace_id, event_time"),
                ("idx_trace_events_time", "tenant_id, event_time"),
                ("idx_trace_events_actor", "tenant_id, actor_ref, event_time"),
                ("idx_trace_events_action", "tenant_id, action_family, event_time"),
                ("idx_trace_events_resource", "tenant_id, resource_ref"),
                ("idx_trace_events_outcome", "tenant_id, outcome"),
                ("idx_resources_type", "tenant_id, resource_type"),
            ):
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS %s ON %s(%s)"
                    % (name, "resources" if name == "idx_resources_type" else "trace_events", columns)
                )

    # ---------- 写入 ----------
    def ingest(
        self,
        trajectory: Trajectory,
        *,
        policy_ref: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """幂等写入；重复导入不会重复生成事件，晚到数据只追加。"""

        derived = derive_records(trajectory, policy_ref=policy_ref, mode=mode)
        now = datetime.now().astimezone().isoformat()
        inserted = 0
        duplicates = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for event in derived["events"]:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO trace_events
                    (event_id, tenant_id, trace_id, span_id, parent_span_id, event_time,
                     observed_at, actor_ref, action_family, action, resource_ref, outcome,
                     evidence_ids, provenance, schema_version, policy_ref, mode, attributes,
                     created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["event_id"], event["tenant_id"], event["trace_id"],
                        event["span_id"], event["parent_span_id"], event["event_time"],
                        event["observed_at"], event["actor_ref"], event["action_family"],
                        event["action"], event["resource_ref"], event["outcome"],
                        json.dumps(event["evidence_ids"], ensure_ascii=False),
                        event["provenance"], event["schema_version"], event["policy_ref"],
                        event["mode"],
                        json.dumps(event["attributes"], ensure_ascii=False, default=str),
                        now,
                    ),
                )
                if cursor.rowcount:
                    inserted += 1
                else:
                    duplicates += 1

            for evidence in derived["evidences"]:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO trace_evidence
                    (evidence_id, tenant_id, trace_id, span_id, source_type, content_ref,
                     content_hash, captured_at, sensitivity, redaction_version,
                     retention_class, schema_version, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence["evidence_id"], evidence["tenant_id"],
                        evidence["trace_id"], evidence["span_id"],
                        evidence["source_type"], evidence["content_ref"],
                        evidence["content_hash"], evidence["captured_at"],
                        evidence["sensitivity"], evidence["redaction_version"],
                        evidence["retention_class"], evidence["schema_version"], now,
                    ),
                )

            for resource in derived["resources"]:
                connection.execute(
                    """
                    INSERT INTO resources
                    (resource_id, tenant_id, resource_type, canonical_key, environment,
                     first_seen, last_seen, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(resource_id) DO UPDATE SET
                        last_seen = CASE
                            WHEN excluded.last_seen > resources.last_seen THEN excluded.last_seen
                            ELSE resources.last_seen END,
                        first_seen = CASE
                            WHEN resources.first_seen IS NULL OR resources.first_seen = ''
                                THEN excluded.first_seen
                            WHEN excluded.first_seen < resources.first_seen THEN excluded.first_seen
                            ELSE resources.first_seen END
                    """,
                    (
                        resource["resource_id"], resource["tenant_id"],
                        resource["resource_type"], resource["canonical_key"],
                        resource["environment"], resource["first_seen"],
                        resource["last_seen"], now,
                    ),
                )

            coverage = self._reconcile_coverage(connection, derived["coverage"])
            coverage_version = self._append_coverage(connection, coverage, now)

        logger.debug(
            "行为事件入库 trace_id=%s inserted=%d duplicates=%d coverage_v=%d",
            trajectory.trace_id, inserted, duplicates, coverage_version,
        )
        return {
            "trace_id": trajectory.trace_id,
            "events": len(derived["events"]),
            "inserted": inserted,
            "duplicates": duplicates,
            "coverage_version": coverage_version,
            "coverage": coverage,
        }

    @staticmethod
    def _reconcile_coverage(connection, coverage: Dict[str, Any]) -> Dict[str, Any]:
        """覆盖状态按“已落库的完整事件集”重算，避免晚到数据把缺口洗掉。"""

        rows = connection.execute(
            "SELECT span_id, parent_span_id, event_time FROM trace_events "
            "WHERE tenant_id = ? AND trace_id = ?",
            (coverage["tenant_id"], coverage["trace_id"]),
        ).fetchall()
        latest = connection.execute(
            "SELECT known_gaps FROM trace_coverage WHERE tenant_id = ? AND trace_id = ? "
            "ORDER BY coverage_version DESC LIMIT 1",
            (coverage["tenant_id"], coverage["trace_id"]),
        ).fetchone()
        sticky_prefixes = ("轨迹声明为采样", "字段被截断", "原文已脱敏")
        gaps: List[str] = []
        for gap in coverage["known_gaps"]:
            if gap.startswith("父节点未观测到") or gap == "没有可解析的行为事件":
                continue
            gaps.append(gap)
        if latest is not None:
            try:
                previous = json.loads(latest["known_gaps"] or "[]")
            except (TypeError, ValueError):
                previous = []
            for gap in previous:
                if isinstance(gap, str) and gap.startswith(sticky_prefixes) and gap not in gaps:
                    gaps.append(gap)

        span_ids = {row["span_id"] for row in rows}
        missing = sorted(
            {
                row["parent_span_id"]
                for row in rows
                if row["parent_span_id"] and row["parent_span_id"] not in span_ids
            }
        )
        if missing:
            gaps.append("父节点未观测到：%s" % "、".join(missing))
        if not rows:
            gaps.append("没有可解析的行为事件")

        times = [row["event_time"] for row in rows if row["event_time"]]
        result = dict(coverage)
        result["known_gaps"] = gaps
        if times:
            result["time_window"] = {"start": min(times), "end": max(times)}
        result["capture_status"] = (
            "unknown" if not rows else ("partial" if gaps else "complete")
        )
        return result

    @staticmethod
    def _append_coverage(connection, coverage: Dict[str, Any], now: str) -> int:
        latest = connection.execute(
            "SELECT * FROM trace_coverage WHERE tenant_id = ? AND trace_id = ? "
            "ORDER BY coverage_version DESC LIMIT 1",
            (coverage["tenant_id"], coverage["trace_id"]),
        ).fetchone()
        if latest is not None:
            same = (
                latest["time_window"] == json.dumps(coverage["time_window"], ensure_ascii=False)
                and latest["sampling_policy"] == coverage["sampling_policy"]
                and latest["capture_status"] == coverage["capture_status"]
                and latest["known_gaps"] == json.dumps(coverage["known_gaps"], ensure_ascii=False)
                and latest["instrumentation_version"] == coverage["instrumentation_version"]
            )
            if same:
                return int(latest["coverage_version"])
            version = int(latest["coverage_version"]) + 1
        else:
            version = 1
        coverage_id = _stable_id(
            "cov_", coverage["tenant_id"], coverage["trace_id"], str(version)
        )
        connection.execute(
            """
            INSERT INTO trace_coverage
            (coverage_id, tenant_id, trace_id, coverage_version, scope, time_window,
             sampling_policy, capture_status, known_gaps, instrumentation_version,
             schema_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                coverage_id, coverage["tenant_id"], coverage["trace_id"], version,
                coverage["scope"],
                json.dumps(coverage["time_window"], ensure_ascii=False),
                coverage["sampling_policy"], coverage["capture_status"],
                json.dumps(coverage["known_gaps"], ensure_ascii=False),
                coverage["instrumentation_version"], coverage["schema_version"], now,
            ),
        )
        return version

    # ---------- 读取 ----------
    def list_events(
        self,
        *,
        tenant_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        actor: Optional[str] = None,
        tool: Optional[str] = None,
        action_family: Optional[str] = None,
        resource: Optional[str] = None,
        outcome: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        clauses: List[str] = []
        params: List[Any] = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if trace_id:
            clauses.append("trace_id = ?")
            params.append(trace_id)
        if actor:
            clauses.append("actor_ref = ?")
            params.append(actor)
        if tool:
            clauses.append("action = ?")
            params.append(tool)
        if action_family:
            clauses.append("action_family = ?")
            params.append(action_family)
        if resource:
            clauses.append("resource_ref = ?")
            params.append(resource)
        if outcome:
            clauses.append("outcome = ?")
            params.append(outcome)
        if start:
            clauses.append("event_time >= ?")
            params.append(start)
        if end:
            clauses.append("event_time <= ?")
            params.append(end)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        with self._connect() as connection:
            total = connection.execute(
                "SELECT COUNT(*) AS n FROM trace_events" + where, params
            ).fetchone()["n"]
            rows = connection.execute(
                "SELECT * FROM trace_events" + where
                + " ORDER BY event_time ASC, created_at ASC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
        items = [self._decode_event(row) for row in rows]
        return {
            "events": items,
            "total": int(total),
            "limit": limit,
            "offset": offset,
            "truncated": int(total) > offset + len(items),
        }

    @staticmethod
    def _decode_event(row) -> Dict[str, Any]:
        item = dict(row)
        for key, fallback in (("evidence_ids", []), ("attributes", {})):
            try:
                item[key] = json.loads(item.get(key) or "null") or fallback
            except (TypeError, ValueError):
                item[key] = fallback
        return item

    def assemble_trace(
        self, trace_id: str, *, tenant_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """按事件时间装配父子关系；父节点未观测到时明确标记，不自动补造。"""

        result = self.list_events(
            tenant_id=tenant_id, trace_id=trace_id, limit=500, offset=0
        )
        events = result["events"]
        by_id = {event["span_id"]: event for event in events}
        children: Dict[str, List[str]] = {}
        unresolved: List[str] = []
        for index, event in enumerate(events):
            event["sequence"] = index
            event["children"] = []
            event["parent_missing"] = False
            parent = event.get("parent_span_id")
            if parent:
                if parent in by_id:
                    children.setdefault(parent, []).append(event["span_id"])
                else:
                    event["parent_missing"] = True
                    if parent not in unresolved:
                        unresolved.append(parent)
        for event in events:
            event["children"] = children.get(event["span_id"], [])
        gaps = list((self.coverage(trace_id, tenant_id=tenant_id) or {}).get("known_gaps") or [])
        if unresolved:
            gaps.append("父节点未观测到：%s" % "、".join(sorted(unresolved)))
        return {
            "trace_id": trace_id,
            "events": events,
            "total": result["total"],
            "truncated": result["truncated"],
            "unresolved_parents": sorted(unresolved),
            "known_gaps": gaps,
        }

    def coverage(
        self, trace_id: str, *, tenant_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM trace_coverage WHERE trace_id = ?"
        params: List[Any] = [trace_id]
        if tenant_id:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        query += " ORDER BY coverage_version DESC LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        if row is None:
            return None
        item = dict(row)
        for key in ("time_window", "known_gaps"):
            try:
                item[key] = json.loads(item.get(key) or "null") or ({} if key == "time_window" else [])
            except (TypeError, ValueError):
                item[key] = {} if key == "time_window" else []
        return item

    def evidence(self, evidence_id: str, *, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM trace_evidence WHERE evidence_id = ?"
        params: List[Any] = [evidence_id]
        if tenant_id:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return dict(row) if row is not None else None

    def list_resources(
        self, *, tenant_id: Optional[str] = None, limit: int = 200
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM resources"
        params: List[Any] = []
        if tenant_id:
            query += " WHERE tenant_id = ?"
            params.append(tenant_id)
        query += " ORDER BY last_seen DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def counts(self, *, tenant_id: Optional[str] = None) -> Dict[str, int]:
        where = " WHERE tenant_id = ?" if tenant_id else ""
        params: List[Any] = [tenant_id] if tenant_id else []
        with self._connect() as connection:
            result = {}
            for name, table in (("events", "trace_events"), ("evidence", "trace_evidence"),
                                ("resources", "resources"), ("coverage", "trace_coverage")):
                row = connection.execute(
                    "SELECT COUNT(*) AS n FROM " + table + where, params
                ).fetchone()
                result[name] = int(row["n"] if row else 0)
        return result

