"""全量汇总聚合与汇总报告契约：使用临时库，不触碰业务数据。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from safeorbit.models import Dimension, RuleHit, Severity, Trajectory, VerdictLevel
from safeorbit.pricing import load_pricing
from safeorbit.report import build_report
from safeorbit.store import TraceStore
from safeorbit.summary import build_summary_report, render_summary_html

# (agent, verdict, 条数, 单条成本)
SPECS = (
    ("agent-a", "pass", 20, 0.01),
    ("agent-a", "warn", 6, 0.02),
    ("agent-a", "fail", 2, 0.03),
    ("agent-a", "review", 2, 0.04),
    ("agent-b", "pass", 8, 0.05),
    ("agent-b", "warn", 2, 0.06),
    ("agent-b", "fail", 14, 0.07),
    ("agent-b", "review", 1, 0.08),
    ("agent-c", "pass", 5, 0.09),
    ("agent-c", "warn", 2, 0.10),
    ("agent-c", "fail", 1, 0.11),
    ("agent-c", "review", 2, 0.12),
)

_HITS_BY_VERDICT = {"pass": 0, "warn": 2, "fail": 4, "review": 4}


def _hits(verdict: str, trace_id: str):
    hits = []
    for index in range(_HITS_BY_VERDICT[verdict]):
        hits.append(
            RuleHit(
                rule_id="rule_%d" % index,
                rule_name="规则 %d" % index,
                dimension=Dimension.SAFETY,
                severity=Severity.HIGH if verdict == "fail" else Severity.MEDIUM,
                span_ref="span-%s" % trace_id,
                message="轨迹 %s 命中 user@example.com" % trace_id,
                evidence={"raw": "不应出现在汇总明细中的证据原文"},
            )
        )
    return hits


def _seed(store: TraceStore):
    base = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    records = []
    index = 0
    pricing = load_pricing()
    for agent_id, verdict, count, cost in SPECS:
        for _ in range(count):
            created = base - timedelta(days=(index % 10) * 2)
            trace_id = "trace-%03d" % index
            trajectory = Trajectory(
                trace_id=trace_id, agent_id=agent_id, session_id="summary",
                started_at=created, received_at=created,
            )
            report = build_report(trajectory, [], pricing)
            report.generated_at = created
            report.verdict = VerdictLevel(verdict)
            report.cost.total_usd = cost
            report.hits = _hits(verdict, trace_id)
            store.save(trajectory, report, "<html></html>")
            records.append(
                {
                    "trace_id": trace_id,
                    "agent_id": agent_id,
                    "verdict": verdict,
                    "cost": cost,
                    "created_at": created.isoformat(),
                    "hits": len(report.hits),
                }
            )
            index += 1
    return records


def test_overview_aggregates_all_history(tmp_path):
    store = TraceStore(str(tmp_path / "traces.db"))
    records = _seed(store)
    assert len(records) > 50

    overview = store.overview()

    assert overview["scope"] == "all"
    assert overview["totals"]["traces"] == len(records)
    assert overview["totals"]["cost_usd"] == round(sum(r["cost"] for r in records), 6)
    assert overview["totals"]["hits"] == sum(r["hits"] for r in records)
    assert overview["detail_limit"] == 500
    assert overview["detail_truncated"] is False

    expected_verdicts = {"pass": 0, "warn": 0, "fail": 0, "review": 0}
    for record in records:
        expected_verdicts[record["verdict"]] += 1
    assert overview["verdicts"] == expected_verdicts
    assert sum(overview["verdicts"].values()) == len(records)

    risk_records = [r for r in records if r["verdict"] in ("fail", "review")]
    assert len(overview["risks"]) == len(risk_records)
    assert [row["created_at"] for row in overview["risks"]] == sorted(
        [row["created_at"] for row in overview["risks"]], reverse=True
    )
    for row in overview["risks"]:
        assert row["verdict"] in ("fail", "review")
        assert row["hits_count"] == _HITS_BY_VERDICT[row["verdict"]]
        assert len(row["hits"]) == 3
        assert all(set(hit) == {"rule_name", "severity", "message"} for hit in row["hits"])

    expected_agents = {}
    for record in records:
        item = expected_agents.setdefault(
            record["agent_id"], {"traces": 0, "risk_traces": 0, "hits": 0, "cost_usd": 0.0}
        )
        item["traces"] += 1
        item["risk_traces"] += 1 if record["verdict"] in ("fail", "review") else 0
        item["hits"] += record["hits"]
        item["cost_usd"] += record["cost"]
    assert [row["agent_id"] for row in overview["agents"]] == ["agent-b", "agent-a", "agent-c"]
    for row in overview["agents"]:
        expected = expected_agents[row["agent_id"]]
        assert row["traces"] == expected["traces"]
        assert row["risk_traces"] == expected["risk_traces"]
        assert row["hits"] == expected["hits"]
        assert row["cost_usd"] == round(expected["cost_usd"], 6)

    top_cost = overview["top_cost"]
    assert len(top_cost) == 10
    costs = [row["cost_usd"] for row in top_cost]
    assert costs == sorted(costs, reverse=True)


def test_overview_daily_window_is_zero_filled(tmp_path):
    store = TraceStore(str(tmp_path / "traces.db"))
    records = _seed(store)
    overview = store.overview()

    daily = overview["daily"]
    assert len(daily) == 14
    dates = [point["date"] for point in daily]
    assert dates == sorted(dates)
    expected = {}
    for record in records:
        day = record["created_at"][:10]
        if day in dates:
            expected[day] = expected.get(day, 0) + 1
    for point in daily:
        assert point["traces"] == expected.get(point["date"], 0)
    assert any(point["traces"] == 0 for point in daily)
    assert sum(point["traces"] for point in daily) < len(records)


def test_overview_detail_limit_truncates(tmp_path):
    store = TraceStore(str(tmp_path / "traces.db"))
    records = _seed(store)
    risky = len([r for r in records if r["verdict"] in ("fail", "review")])

    overview = store.overview(detail_limit=5)

    assert len(overview["risks"]) == 5
    assert overview["detail_limit"] == 5
    assert overview["detail_truncated"] is True
    assert risky > 5
    assert overview["totals"]["risk_traces"] == risky


def test_overview_empty_store_returns_zeroes(tmp_path):
    store = TraceStore(str(tmp_path / "empty.db"))
    overview = store.overview()

    assert overview["totals"]["traces"] == 0
    assert overview["totals"]["cost_usd"] == 0.0
    assert overview["totals"]["hits"] == 0
    assert overview["totals"]["first_seen"] is None
    assert overview["verdicts"] == {"pass": 0, "warn": 0, "fail": 0, "review": 0}
    assert overview["agents"] == []
    assert overview["risks"] == []
    assert overview["top_cost"] == []
    assert len(overview["daily"]) == 14
    assert all(point["traces"] == 0 for point in overview["daily"])
    assert overview["detail_truncated"] is False


def test_summary_report_build_and_render(tmp_path):
    store = TraceStore(str(tmp_path / "traces.db"))
    records = _seed(store)

    report = build_summary_report(store.overview())
    assert re.match(r"^sum-\d{8}-\d{6}-[0-9a-f]{8}$", report.report_id)
    assert report.scope == "all"
    assert report.totals["traces"] == len(records)
    assert [item.verdict for item in report.verdicts] == ["pass", "warn", "fail", "review"]
    assert next(item for item in report.verdicts if item.verdict == "fail").label == "不通过"
    assert len(report.risks) == len([r for r in records if r["verdict"] in ("fail", "review")])

    html = render_summary_html(report)
    assert "Content-Security-Policy" in html
    assert "轨迹审计汇总报告" in html
    assert "近 14 天趋势" in html
    assert "user@example.com" not in html
    assert "us***om" in html
    assert "不应出现在汇总明细中的证据原文" not in html
