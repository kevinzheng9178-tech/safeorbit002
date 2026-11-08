"""审计五视图与 HOTL 监管指标。"""

from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import datetime, timedelta

import httpx
import pytest

from safeorbit.models import (
    Dimension,
    LLMAttrs,
    RetrievalAttrs,
    RuleHit,
    Severity,
    Span,
    SpanKind,
    SpanStatus,
    ToolAttrs,
    Trajectory,
    VerdictLevel,
)
from safeorbit.pricing import load_pricing
from safeorbit.report import build_report

TRACE_ID = "views-trace-1"


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFEORBIT_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("SAFEORBIT_REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("SAFEORBIT_CODE_REPORT_DIR", str(tmp_path / "code"))
    monkeypatch.setenv("SAFEORBIT_SUMMARY_REPORT_DIR", str(tmp_path / "summaries"))
    monkeypatch.setenv("SAFEORBIT_UPSTREAM_MOCK", "1")
    monkeypatch.setenv("SAFEORBIT_COOKIE_SECURE", "0")
    monkeypatch.setenv("SAFEORBIT_AUTH", "on")
    monkeypatch.setenv("SAFEORBIT_MODELS_CONFIG", "")
    monkeypatch.delenv("SAFEORBIT_JUDGE_PROFILE", raising=False)
    sys.modules.pop("safeorbit.server", None)
    module = importlib.import_module("safeorbit.server")
    module.AUTH_STORE.create_user("admin", "Test-password-strong1!", role="admin")
    yield module
    sys.modules.pop("safeorbit.server", None)


def _seed(server):
    start = datetime.now().astimezone() - timedelta(seconds=3)
    spans = [
        Span(
            span_id="s-llm",
            kind=SpanKind.LLM,
            name="plan",
            start_time=start,
            end_time=start + timedelta(milliseconds=200),
            llm=LLMAttrs(
                provider="deepseek",
                model="deepseek-chat",
                messages=[{"role": "user", "content": "帮我把订单 O-9 退款"}],
                completion="好的，先查询订单再退款。",
            ),
        ),
        Span(
            span_id="s-rag",
            kind=SpanKind.RETRIEVAL,
            name="kb",
            start_time=start + timedelta(milliseconds=210),
            end_time=start + timedelta(milliseconds=400),
            retrieval=RetrievalAttrs(query="退款政策", documents=[{"id": "doc-1"}, {"id": "doc-2"}]),
        ),
        Span(
            span_id="s-read",
            kind=SpanKind.TOOL,
            name="get_order",
            start_time=start + timedelta(milliseconds=410),
            end_time=start + timedelta(milliseconds=600),
            status=SpanStatus.OK,
            tool=ToolAttrs(
                tool_name="get_order",
                arguments={"order_id": "O-9"},
                result={"status": "paid"},
                side_effect="read",
                permission="orders:read",
            ),
        ),
        Span(
            span_id="s-write",
            kind=SpanKind.TOOL,
            name="refund_order",
            start_time=start + timedelta(milliseconds=610),
            end_time=start + timedelta(milliseconds=900),
            status=SpanStatus.OK,
            tool=ToolAttrs(
                tool_name="refund_order",
                arguments={"order_id": "O-9", "path": "/orders/O-9/refund"},
                result={"refund_id": "R-1"},
                side_effect="write",
                permission="orders:write",
            ),
        ),
    ]
    trajectory = Trajectory(
        trace_id=TRACE_ID,
        agent_id="agent-a",
        session_id="sess-1",
        started_at=start,
        ended_at=start + timedelta(seconds=1),
        spans=spans,
        received_at=datetime.now().astimezone(),
    )
    server.STORE.save(trajectory, report_for(trajectory), "<html></html>")


def report_for(trajectory):
    report = build_report(trajectory, [], load_pricing())
    report.verdict = VerdictLevel.REVIEW
    report.hits = [
        RuleHit(
            rule_id="baseline_new_tool",
            rule_name="出现历史未使用的工具",
            dimension=Dimension.SAFETY,
            severity=Severity.MEDIUM,
            span_ref="s-write",
            message="refund_order 未在历史基线中出现",
        )
    ]
    return report


def _client(server):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url="http://test",
        headers={"X-SafeOrbit-CSRF": "1"},
    )


async def _login(client):
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "Test-password-strong1!"}
    )
    assert response.status_code == 200


def test_audit_views_cover_five_questions(server):
    _seed(server)

    async def scenario():
        async with _client(server) as client:
            await _login(client)
            response = await client.get("/api/traces/%s/audit-views" % TRACE_ID)
            assert response.status_code == 200
            body = response.json()

            assert len(body["timeline"]) == 4
            assert body["summary"] == {
                "steps": 4, "tools": 2, "writes": 1, "blocked": 0, "resources": 2, "approvals": 0,
            }

            resources = body["resources"]
            actions = {(item["resource"], item["action"], item["risk"]) for item in resources}
            assert ("/orders/O-9/refund", "write", "write") in actions
            assert ("O-9", "read", "read") in actions

            assert len(body["permissions"]) == 2
            write_permission = next(item for item in body["permissions"] if item["tool"] == "refund_order")
            assert write_permission["permission"] == "orders:write"
            assert write_permission["source"] == "策略权限"

            decision = next(item for item in body["decisions"] if item["tool"] == "refund_order")
            assert "退款" in decision["prompt"]
            assert "命中 2 条" in decision["context"]
            assert decision["arguments_hash"]
            assert decision["blocked"] is False

            impact = body["impact"]
            assert impact["reversible"] is False
            assert len(impact["items"]) == 1
            assert impact["items"][0]["tool"] == "refund_order"

    asyncio.run(scenario())


def test_audit_views_marks_approval_and_blocked(server):
    _seed(server)
    approval = server.APPROVALS.create(
        agent_id="agent-a",
        tool_name="refund_order",
        arguments={"order_id": "O-9", "path": "/orders/O-9/refund"},
        trace_id=TRACE_ID,
        reason="写操作需要审批",
    )
    server.APPROVALS.decide(approval.approval_id, approved=True, decided_by="admin", note="金额已核对")

    async def scenario():
        async with _client(server) as client:
            await _login(client)
            body = (await client.get("/api/traces/%s/audit-views" % TRACE_ID)).json()
            permission = next(item for item in body["permissions"] if item["tool"] == "refund_order")
            assert permission["source"] == "审批票据"
            assert permission["approval_ids"] == [approval.approval_id]
            assert body["summary"]["approvals"] == 1

    asyncio.run(scenario())


def test_audit_views_requires_auth(server):
    _seed(server)

    async def scenario():
        async with _client(server) as client:
            assert (await client.get("/api/traces/%s/audit-views" % TRACE_ID)).status_code == 401

    asyncio.run(scenario())


def test_audit_metrics_reports_hotl_signals(server):
    _seed(server)
    approved = server.APPROVALS.create(
        agent_id="agent-a", tool_name="refund_order", arguments={"order_id": "O-9"}, trace_id=TRACE_ID
    )
    server.APPROVALS.decide(approved.approval_id, approved=True, decided_by="admin")
    rejected = server.APPROVALS.create(
        agent_id="agent-a", tool_name="delete_account", arguments={"user_id": "U-1"}
    )
    server.APPROVALS.decide(rejected.approval_id, approved=False, decided_by="admin", note="未授权")

    async def scenario():
        async with _client(server) as client:
            await _login(client)
            response = await client.get("/api/audit/metrics")
            assert response.status_code == 200
            body = response.json()
            assert body["traces"] == 1
            assert body["escalation_rate"] == 1.0
            assert body["drift_traces"] == 1
            assert body["drift_rate"] == 1.0
            assert body["decided"] == 2
            assert body["rejection_rate"] == 0.5
            assert body["pending"] == 0
            assert body["avg_decision_seconds"] is not None
            assert body["cost_7d"] >= 0

    asyncio.run(scenario())
