"""P1 测试：OTLP 解析、网关轨迹构建、存储与事件总线。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from safeorbit.gateway import build_gateway_trajectory, mock_completion
from safeorbit.models import SpanKind, SpanStatus, VerdictLevel
from safeorbit.normalize import normalize
from safeorbit.otel import parse_otlp_json
from safeorbit.pricing import load_pricing
from safeorbit.report import build_report, render_html
from safeorbit.rules import AuditContext, RuleEngine, default_rules
from safeorbit.store import EventBus, TraceStore


# --------------------------------------------------------------------------
# OTLP 解析
# --------------------------------------------------------------------------
def _otlp_fixture() -> dict:
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "ecs-cs-agent"}}]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "otel-trace-1",
                                "spanId": "a1",
                                "name": "chat",
                                "startTimeUnixNano": "1730512800000000000",
                                "endTimeUnixNano": "1730512801500000000",
                                "attributes": [
                                    {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                                    {"key": "gen_ai.system", "value": {"stringValue": "deepseek"}},
                                    {"key": "gen_ai.request.model", "value": {"stringValue": "deepseek-chat"}},
                                    {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "900"}},
                                    {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "120"}},
                                    {
                                        "key": "gen_ai.prompt",
                                        "value": {"stringValue": '[{"role":"user","content":"我要退款"}]'},
                                    },
                                    {"key": "gen_ai.completion", "value": {"stringValue": "已为您处理。"}},
                                ],
                            },
                            {
                                "traceId": "otel-trace-1",
                                "spanId": "a2",
                                "parentSpanId": "a1",
                                "name": "execute_tool refund",
                                "startTimeUnixNano": "1730512802000000000",
                                "endTimeUnixNano": "1730512802500000000",
                                "attributes": [
                                    {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
                                    {"key": "gen_ai.tool.name", "value": {"stringValue": "refund"}},
                                    {
                                        "key": "gen_ai.tool.call.arguments",
                                        "value": {"stringValue": '{"order_id":"O-1","amount":10000}'},
                                    },
                                    {
                                        "key": "safeorbit.tool.side_effect",
                                        "value": {"stringValue": "write"},
                                    },
                                ],
                            },
                        ]
                    }
                ],
            }
        ]
    }


def test_parse_otlp_groups_by_trace_and_maps_fields():
    trajectories = parse_otlp_json(_otlp_fixture())

    assert len(trajectories) == 1
    trajectory = trajectories[0]
    assert trajectory.trace_id == "otel-trace-1"
    assert trajectory.agent_id == "ecs-cs-agent"
    assert trajectory.source == "otel"
    assert len(trajectory.spans) == 2

    llm = next(span for span in trajectory.spans if span.kind == SpanKind.LLM)
    assert llm.llm.model == "deepseek-chat"
    assert llm.llm.usage.prompt == 900
    assert llm.llm.usage.completion == 120
    assert llm.llm.messages[0]["content"] == "我要退款"

    tool = next(span for span in trajectory.spans if span.kind == SpanKind.TOOL)
    assert tool.tool.tool_name == "refund"
    assert tool.tool.arguments["amount"] == 10000
    assert tool.tool.side_effect == "write"
    assert tool.parent_span_id == "a1"


def test_otlp_trajectory_can_be_audited_end_to_end():
    trajectory = normalize(parse_otlp_json(_otlp_fixture())[0], load_pricing())
    hits = RuleEngine(default_rules()).run(
        trajectory, AuditContext(write_amount_threshold=1000)
    )
    rule_ids = {hit.rule_id for hit in hits}

    assert "tool_write_unconfirmed" in rule_ids
    assert "tool_write_large_amount" in rule_ids
    assert all(hit.span_ref for hit in hits)

    report = build_report(trajectory, hits, load_pricing())
    assert report.verdict == VerdictLevel.FAIL
    assert "refund" in render_html(report)


# --------------------------------------------------------------------------
# 网关轨迹
# --------------------------------------------------------------------------
def _gateway_trajectory():
    request_body = {
        "model": "deepseek-chat",
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "你是电商客服。"},
            {"role": "user", "content": "我的手机号 13800138000，要退款。"},
        ],
    }
    response_body = mock_completion(request_body)
    now = datetime.now().astimezone()
    return build_gateway_trajectory(
        request_body,
        response_body,
        agent_id="ecs-cs-agent",
        session_id="sess-1",
        trace_id="gw-trace-1",
        started_at=now,
        ended_at=now,
    )


def test_gateway_trajectory_captures_model_layer():
    trajectory = normalize(_gateway_trajectory(), load_pricing())

    assert trajectory.source == "gateway"
    assert trajectory.trace_id == "gw-trace-1"
    assert len(trajectory.spans) == 1

    span = trajectory.spans[0]
    assert span.kind == SpanKind.LLM
    assert span.llm.model == "deepseek-chat"
    assert span.llm.usage.prompt > 0
    assert span.llm.cost_usd is not None
    assert span.llm.completion


def test_gateway_trajectory_detects_pii_in_user_message():
    trajectory = normalize(_gateway_trajectory(), load_pricing())
    hits = RuleEngine(default_rules()).run(trajectory, AuditContext())
    rule_ids = {hit.rule_id for hit in hits}

    assert "pii_leak" in rule_ids


def test_redaction_masks_gateway_user_message():
    trajectory = normalize(_gateway_trajectory(), load_pricing(), redact=True)
    messages = trajectory.spans[0].llm.messages
    assert "13800138000" not in messages[1]["content"]


# --------------------------------------------------------------------------
# 存储与事件总线
# --------------------------------------------------------------------------
def test_store_roundtrip(tmp_path: Path):
    store = TraceStore(str(tmp_path / "test.db"))
    trajectory = normalize(_gateway_trajectory(), load_pricing())
    hits = RuleEngine(default_rules()).run(trajectory, AuditContext())
    report = build_report(trajectory, hits, load_pricing(), judge_status="skipped")

    store.save(trajectory, report, render_html(report))

    listed = store.list_traces()
    assert len(listed) == 1
    assert listed[0]["trace_id"] == "gw-trace-1"

    record = store.get("gw-trace-1")
    assert record is not None
    assert record["report_json"]["trace_id"] == "gw-trace-1"
    assert "<!DOCTYPE html>" in record["report_html"]
    assert store.get("missing") is None


def test_event_bus_publishes_to_subscribers():
    async def scenario():
        bus = EventBus()
        queue = bus.subscribe()
        await bus.publish({"trace_id": "t1", "severity": "high"})
        item = await asyncio.wait_for(queue.get(), timeout=1)
        bus.unsubscribe(queue)
        return item

    item = asyncio.run(scenario())
    assert item["trace_id"] == "t1"
