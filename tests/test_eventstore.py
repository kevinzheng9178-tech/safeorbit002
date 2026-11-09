"""批次 A 固定夹具：事件规范化、幂等、乱序、覆盖与租户隔离。

所有轨迹由测试构造（synthetic_fixture），不代表真实客户数据或线上检出效果。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from safeorbit.eventstore import TraceEventStore, derive_records
from safeorbit.models import (
    LLMAttrs,
    RetrievalAttrs,
    Span,
    SpanKind,
    SpanStatus,
    ToolAttrs,
    Trajectory,
)

BASE = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


def at(seconds: int) -> datetime:
    return BASE + timedelta(seconds=seconds)


def agent_span() -> Span:
    return Span(
        span_id="s-agent", kind=SpanKind.AGENT, name="处理客户咨询",
        start_time=at(0), end_time=at(30), output="完成",
    )


def llm_span() -> Span:
    return Span(
        span_id="s-llm", parent_span_id="s-agent", kind=SpanKind.LLM, name="compose",
        start_time=at(1), end_time=at(3),
        llm=LLMAttrs(provider="deepseek", model="deepseek-chat", completion="好的"),
    )


def rag_span() -> Span:
    return Span(
        span_id="s-rag", parent_span_id="s-agent", kind=SpanKind.RETRIEVAL, name="客户档案检索",
        start_time=at(4), end_time=at(5),
        retrieval=RetrievalAttrs(query="客户资料", documents=[{"id": "doc-8821"}]),
    )


def tool_span(
    span_id: str = "s-tool",
    *,
    tool_name: str = "send_email",
    arguments=None,
    status: SpanStatus = SpanStatus.OK,
    blocked: bool = False,
) -> Span:
    return Span(
        span_id=span_id, parent_span_id="s-agent", kind=SpanKind.TOOL, name=tool_name,
        start_time=at(6), end_time=at(8), status=status,
        tool=ToolAttrs(
            tool_name=tool_name,
            arguments=arguments if arguments is not None else {
                "to": "Ext@Example.com", "subject": "客户资料",
            },
            result="sent", side_effect="write", blocked=blocked,
        ),
    )


def human_span() -> Span:
    return Span(
        span_id="s-human", parent_span_id="s-agent", kind=SpanKind.HUMAN, name="审批取消",
        start_time=at(9), end_time=at(10), output="已取消",
    )


def make_trajectory(
    spans,
    *,
    trace_id: str = "trace-a",
    tenant_id: str = "tenant-a",
    **overrides,
) -> Trajectory:
    defaults = dict(
        trace_id=trace_id, tenant_id=tenant_id, agent_id="cs-agent",
        agent_version="1.2.0", framework="langchain",
        started_at=at(0), ended_at=at(30), received_at=at(31), spans=list(spans),
    )
    defaults.update(overrides)
    return Trajectory(**defaults)


@pytest.fixture
def store(tmp_path):
    return TraceEventStore(str(tmp_path / "events.db"))


def by_span(trajectory, **kwargs):
    derived = derive_records(trajectory, **kwargs)
    return {event["span_id"]: event for event in derived["events"]}, derived


def test_derive_maps_semantic_actions_and_resources():
    trajectory = make_trajectory(
        [agent_span(), llm_span(), rag_span(), tool_span(), human_span()]
    )
    events, derived = by_span(trajectory, policy_ref="p1@2026-01-01", mode="shadow")

    assert events["s-agent"]["action_family"] == "task.run"
    assert events["s-llm"]["action_family"] == "model.invoke"
    assert events["s-rag"]["action_family"] == "data.read"
    assert events["s-tool"]["action_family"] == "tool.invoke"
    assert events["s-tool"]["action"] == "send_email"
    assert events["s-human"]["action_family"] == "approval.decide"
    assert all(event["outcome"] == "succeeded" for event in events.values())
    assert all(event["provenance"] == "native" for event in events.values())
    assert events["s-tool"]["policy_ref"] == "p1@2026-01-01"
    assert events["s-tool"]["mode"] == "shadow"

    # 工具参数只保留摘要与字段名，原文不进入事件表
    tool_attrs = events["s-tool"]["attributes"]["tool"]
    assert tool_attrs["argument_keys"] == ["subject", "to"]
    assert "Ext@Example.com" not in json.dumps(tool_attrs, ensure_ascii=False)

    emails = [item for item in derived["resources"] if item["resource_type"] == "email"]
    assert emails and emails[0]["canonical_key"].startswith("sha256:")
    assert "ext@example.com" not in emails[0]["canonical_key"]
    assert events["s-tool"]["resource_ref"] == emails[0]["resource_id"]
    assert "Ext@Example.com" not in json.dumps(derived, ensure_ascii=False)

    assert len(derived["evidences"]) == 5
    assert all(len(item["content_hash"]) == 64 for item in derived["evidences"])
    assert all(item["source_type"] == "trace" for item in derived["evidences"])
    assert derived["coverage"]["capture_status"] == "complete"
    assert derived["coverage"]["known_gaps"] == []


def test_ingest_is_idempotent_and_coverage_is_stable(store):
    trajectory = make_trajectory([agent_span(), tool_span()])
    first = store.ingest(trajectory)
    second = store.ingest(trajectory)

    assert first["inserted"] == 2 and first["duplicates"] == 0
    assert second["inserted"] == 0 and second["duplicates"] == 2
    assert store.counts(tenant_id="tenant-a")["events"] == 2
    assert second["coverage_version"] == first["coverage_version"] == 1


def test_child_arrives_before_parent(store):
    child = Trajectory(
        trace_id="trace-late", tenant_id="tenant-a", agent_id="cs-agent",
        started_at=at(10), received_at=at(11),
        spans=[tool_span(span_id="s-tool", arguments={"path": "/tmp/export.csv"})],
    )
    first = store.ingest(child)
    tree = store.assemble_trace("trace-late", tenant_id="tenant-a")
    assert tree["events"][0]["parent_missing"] is True
    assert tree["unresolved_parents"] == ["s-agent"]
    assert any("父节点未观测到" in gap for gap in tree["known_gaps"])
    assert first["coverage"]["capture_status"] == "partial"

    parent = Trajectory(
        trace_id="trace-late", tenant_id="tenant-a", agent_id="cs-agent",
        started_at=at(0), received_at=at(12), spans=[agent_span()],
    )
    second = store.ingest(parent)
    tree = store.assemble_trace("trace-late", tenant_id="tenant-a")
    assert tree["unresolved_parents"] == []
    assert tree["events"][0]["parent_missing"] is False
    assert second["coverage_version"] > first["coverage_version"]
    assert second["coverage"]["capture_status"] == "complete"
    assert second["coverage"]["time_window"]["start"] < second["coverage"]["time_window"]["end"]


def test_outcome_mapping_distinguishes_failure_and_block(store):
    trajectory = make_trajectory([
        tool_span("s-fail", status=SpanStatus.ERROR),
        tool_span("s-blocked", blocked=True),
        tool_span("s-skip", status=SpanStatus.SKIPPED),
    ])
    store.ingest(trajectory)
    events = {
        event["span_id"]: event for event in
        store.list_events(tenant_id="tenant-a", trace_id="trace-a")["events"]
    }
    assert events["s-fail"]["outcome"] == "failed"
    assert events["s-blocked"]["outcome"] == "blocked"
    assert events["s-skip"]["outcome"] == "unknown"


def test_sampling_truncation_redaction_and_empty_trace(store):
    sampled = make_trajectory(
        [tool_span()], sampled=True, truncated_fields=["tool.result"], redacted=True,
    )
    result = store.ingest(sampled)
    gaps = result["coverage"]["known_gaps"]
    assert result["coverage"]["capture_status"] == "partial"
    assert any("采样" in gap for gap in gaps)
    assert any("截断" in gap for gap in gaps)
    assert any("脱敏" in gap for gap in gaps)
    # 覆盖按版本追加，不覆盖上一版
    coverage = store.coverage("trace-a", tenant_id="tenant-a")
    assert coverage["coverage_version"] == 1

    empty = make_trajectory([], trace_id="trace-empty")
    empty_result = store.ingest(empty)
    assert empty_result["coverage"]["capture_status"] == "unknown"
    assert "没有可解析的行为事件" in empty_result["coverage"]["known_gaps"]


def test_tenant_isolation_on_events_evidence_and_coverage(store):
    span = tool_span()
    store.ingest(make_trajectory([span], tenant_id="tenant-a"))
    store.ingest(make_trajectory([span], tenant_id="tenant-b"))

    assert store.counts(tenant_id="tenant-a")["events"] == 1
    assert store.counts(tenant_id="tenant-b")["events"] == 1
    events_a = store.list_events(tenant_id="tenant-a")["events"]
    assert {event["tenant_id"] for event in events_a} == {"tenant-a"}

    evidence_id = events_a[0]["evidence_ids"][0]
    assert store.evidence(evidence_id, tenant_id="tenant-a") is not None
    assert store.evidence(evidence_id, tenant_id="tenant-b") is None
    assert store.coverage("trace-a", tenant_id="tenant-b") is not None  # 租户各自一份
    assert store.assemble_trace("trace-a", tenant_id="tenant-a")["total"] == 1


def test_query_filters_and_pagination(store):
    spans = [
        tool_span("s-read", tool_name="get_order", arguments={"order_id": "A-1"}),
        tool_span("s-write", tool_name="send_email", arguments={"to": "a@example.com"}),
        tool_span("s-other", tool_name="send_email", arguments={"to": "b@example.com"},
                  status=SpanStatus.ERROR),
    ]
    store.ingest(make_trajectory(spans))

    assert store.list_events(tenant_id="tenant-a", tool="send_email")["total"] == 2
    assert store.list_events(tenant_id="tenant-a", outcome="failed")["total"] == 1
    assert store.list_events(tenant_id="tenant-a", actor="cs-agent")["total"] == 3
    page = store.list_events(tenant_id="tenant-a", limit=1, offset=1)
    assert len(page["events"]) == 1 and page["truncated"] is True
    early = store.list_events(
        tenant_id="tenant-a", start=at(0).isoformat(), end=at(6).isoformat()
    )
    assert all(event["event_time"] <= at(6).isoformat() for event in early["events"])
