"""端到端最小测试：导入 → 归一化 → 规则 → 裁决 → 报告。"""

from __future__ import annotations

from pathlib import Path

import pytest

from safeorbit.importers import load_trajectory
from safeorbit.models import SpanKind, VerdictLevel
from safeorbit.normalize import normalize
from safeorbit.pricing import load_pricing
from safeorbit.report import build_report, render_html, report_to_json
from safeorbit.rules import AuditContext, RuleEngine, default_rules
from safeorbit.trace_utils import root_spans
from safeorbit.verdict import compose_verdict

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load(name: str):
    pricing = load_pricing()
    trajectory = normalize(load_trajectory(str(FIXTURES / name)), pricing)
    return trajectory, pricing


def test_normal_trajectory_is_clean_and_priced():
    trajectory, pricing = _load("demo_normal.json")

    assert len(trajectory.spans) == 6
    assert len(root_spans(trajectory)) == 1
    assert trajectory.integrity is not None
    assert trajectory.integrity.content_hash

    costs = [span.llm.cost_usd for span in trajectory.spans if span.llm is not None]
    assert all(cost is not None and cost > 0 for cost in costs)


def test_unsafe_refund_triggers_hard_rules():
    trajectory, pricing = _load("demo_unsafe_refund.json")
    context = AuditContext(
        budget_usd=0.0003,
        allowed_tools={"get_order", "send_message"},
        write_amount_threshold=1000,
    )
    hits = RuleEngine(default_rules()).run(trajectory, context)
    rule_ids = {hit.rule_id for hit in hits}

    assert "tool_write_unconfirmed" in rule_ids
    assert "tool_write_large_amount" in rule_ids
    assert "pii_leak" in rule_ids
    assert "tool_not_allowlisted" in rule_ids
    assert "cost_budget_exceeded" in rule_ids

    every_hit_has_span = all(hit.span_ref for hit in hits if hit.source == "rule")
    assert every_hit_has_span
    assert compose_verdict(hits) == VerdictLevel.FAIL


def test_loop_and_handoff_rules():
    trajectory, _ = _load("demo_loop.json")
    hits = RuleEngine(default_rules()).run(trajectory, AuditContext())
    rule_ids = {hit.rule_id for hit in hits}

    assert "loop_detected" in rule_ids
    assert "handoff_missing_context" in rule_ids
    assert "context_truncated" in rule_ids
    assert compose_verdict(hits) == VerdictLevel.WARN


def test_report_can_render_html_with_span_reference():
    trajectory, pricing = _load("demo_unsafe_refund.json")
    hits = RuleEngine(default_rules()).run(
        trajectory,
        AuditContext(budget_usd=0.0003, write_amount_threshold=1000),
    )
    report = build_report(trajectory, hits, pricing, judge_status="skipped")

    payload = report_to_json(report)
    assert "demo-unsafe-refund-001" in payload
    assert "tool_write_unconfirmed" in payload

    html = render_html(report)
    assert "Agent 审计报告" in html
    assert "s3" in html
    assert report.verdict == VerdictLevel.FAIL


def test_redaction_masks_pii():
    raw = load_trajectory(str(FIXTURES / "demo_unsafe_refund.json"))
    trajectory = normalize(raw, load_pricing(), redact=True)

    target = next(span for span in trajectory.spans if span.span_id == "s4")
    assert "13800138000" not in (target.llm.completion or "")
    assert trajectory.redacted is True


def test_duplicate_span_id_is_rejected(tmp_path: Path):
    duplicate = {
        "schema_version": "1.0",
        "trace_id": "dup-001",
        "agent_id": "demo",
        "started_at": "2026-11-02T10:00:00+08:00",
        "spans": [
            {"span_id": "x", "kind": "agent", "name": "a", "start_time": "2026-11-02T10:00:00+08:00"},
            {"span_id": "x", "kind": "agent", "name": "b", "start_time": "2026-11-02T10:00:01+08:00"},
        ],
    }
    path = tmp_path / "dup.json"
    path.write_text(__import__("json").dumps(duplicate), encoding="utf-8")

    with pytest.raises(ValueError):
        load_trajectory(str(path))
