"""多轨迹汇总报告：模型定义与单文件 HTML 渲染。"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from .models import VerdictLevel
from .pii import redact_text
from .report import _VERDICT_COLOR, _VERDICT_LABEL

_VERDICT_LABEL_BY_VALUE = {level.value: label for level, label in _VERDICT_LABEL.items()}
_VERDICT_COLOR_BY_VALUE = {level.value: color for level, color in _VERDICT_COLOR.items()}
_VERDICT_ORDER = (
    VerdictLevel.PASS,
    VerdictLevel.WARN,
    VerdictLevel.FAIL,
    VerdictLevel.REVIEW,
)


class VerdictStat(BaseModel):
    verdict: str
    label: str
    count: int
    ratio: float


class AgentSummary(BaseModel):
    agent_id: str
    traces: int
    risk_traces: int
    hits: int
    cost_usd: float
    latest_at: Optional[str] = None


class DailyPoint(BaseModel):
    date: str
    traces: int
    cost_usd: float


class RiskTraceRow(BaseModel):
    trace_id: str
    agent_id: str
    created_at: Optional[str] = None
    verdict: str
    cost_usd: float
    hits_count: int
    hits: List[Dict[str, str]] = Field(default_factory=list)


class SummaryReport(BaseModel):
    schema_version: str = "1.0"
    report_id: str
    generated_at: datetime
    scope: str = "all"
    range_start: Optional[str] = None
    range_end: Optional[str] = None
    totals: Dict[str, Any] = Field(default_factory=dict)
    verdicts: List[VerdictStat] = Field(default_factory=list)
    agents: List[AgentSummary] = Field(default_factory=list)
    daily: List[DailyPoint] = Field(default_factory=list)
    risks: List[RiskTraceRow] = Field(default_factory=list)
    top_cost: List[Dict[str, Any]] = Field(default_factory=list)
    detail_limit: int = 500
    detail_truncated: bool = False
    disclaimer: str = ""


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return round(float(value or 0.0), 6)
    except (TypeError, ValueError):
        return 0.0


def build_summary_report(overview: Dict[str, Any]) -> SummaryReport:
    now = datetime.now().astimezone()
    totals = dict(overview.get("totals") or {})
    total_traces = _int(totals.get("traces"))
    counts = overview.get("verdicts") or {}

    verdicts = []
    for level in _VERDICT_ORDER:
        count = _int(counts.get(level.value))
        verdicts.append(
            VerdictStat(
                verdict=level.value,
                label=_VERDICT_LABEL_BY_VALUE.get(level.value, level.value),
                count=count,
                ratio=round(count / total_traces, 6) if total_traces else 0.0,
            )
        )

    agents = [
        AgentSummary(
            agent_id=str(row.get("agent_id") or ""),
            traces=_int(row.get("traces")),
            risk_traces=_int(row.get("risk_traces")),
            hits=_int(row.get("hits")),
            cost_usd=_float(row.get("cost_usd")),
            latest_at=row.get("latest_at"),
        )
        for row in overview.get("agents") or []
    ]

    daily = [
        DailyPoint(
            date=str(row.get("date") or ""),
            traces=_int(row.get("traces")),
            cost_usd=_float(row.get("cost_usd")),
        )
        for row in overview.get("daily") or []
    ]

    risks = []
    for row in overview.get("risks") or []:
        hits = []
        for hit in row.get("hits") or []:
            hits.append(
                {
                    "rule_name": str(hit.get("rule_name") or ""),
                    "severity": str(hit.get("severity") or ""),
                    "message": str(hit.get("message") or ""),
                }
            )
        risks.append(
            RiskTraceRow(
                trace_id=str(row.get("trace_id") or ""),
                agent_id=str(row.get("agent_id") or ""),
                created_at=row.get("created_at"),
                verdict=str(row.get("verdict") or ""),
                cost_usd=_float(row.get("cost_usd")),
                hits_count=_int(row.get("hits_count")),
                hits=hits,
            )
        )

    detail_limit = _int(overview.get("detail_limit")) or 500
    return SummaryReport(
        report_id="sum-%s-%s" % (now.strftime("%Y%m%d-%H%M%S"), uuid4().hex[:8]),
        generated_at=now,
        range_start=totals.get("first_seen"),
        range_end=totals.get("last_seen"),
        totals={
            "traces": total_traces,
            "cost_usd": _float(totals.get("cost_usd")),
            "hits": _int(totals.get("hits")),
            "risk_traces": _int(totals.get("risk_traces")),
        },
        verdicts=verdicts,
        agents=agents,
        daily=daily,
        risks=risks,
        top_cost=[dict(row) for row in overview.get("top_cost") or []],
        detail_limit=detail_limit,
        detail_truncated=bool(overview.get("detail_truncated")),
        disclaimer=_disclaimer(detail_limit),
    )


def _disclaimer(detail_limit: int) -> str:
    return (
        "本报告为 SafeOrbit 全量轨迹聚合结果，统计范围为全部历史轨迹，"
        "不局限于最近列表。生成时间按轨迹创建时间的字符串日期分日，不跨时区换算。"
        "风险明细最多保留 %d 条，超出部分仅计入统计；明细仅展示每条轨迹前 3 个命中，"
        "不包含证据原文。成本仅合计已计价调用，未配置价格的模型不推算金额。"
        "历史轨迹如经重新审计不会回写，汇总口径可能与单条旧报告不同。"
        "本报告用于审计取证与风险评估，不替代人工判断。" % detail_limit
    )


def _safe_text(value: Any) -> str:
    text, _ = redact_text(str(value if value is not None else ""))
    return html.escape(text)


def _safe_time(value: Optional[str]) -> str:
    text = str(value or "")
    return _safe_text(text.replace("T", " ")[:19])


def _usd(value: Any) -> str:
    return "%.6f" % _float(value)


def _ratio_bar(ratio: float, color: str) -> str:
    width = max(0.0, min(1.0, ratio)) * 100.0
    return (
        "<div class='bar'><span style='width:%.1f%%;background:%s'></span></div>" % (width, color)
    )


def _count_bar(count: int, maximum: int) -> str:
    width = (float(count) / float(maximum) * 100.0) if maximum else 0.0
    return "<div class='bar'><span style='width:%.1f%%'></span></div>" % width


def _trend_rows(daily: List[DailyPoint]) -> str:
    if not daily:
        return "<tr><td colspan='4' class='muted'>没有可展示的趋势数据</td></tr>"
    maximum = max([point.traces for point in daily] + [1])
    rows = []
    for point in daily:
        rows.append(
            "<tr><td>%s</td><td>%d</td><td>$%s</td><td>%s</td></tr>"
            % (
                _safe_text(point.date),
                point.traces,
                _usd(point.cost_usd),
                _count_bar(point.traces, maximum),
            )
        )
    return "".join(rows)


def _verdict_rows(verdicts: List[VerdictStat]) -> str:
    rows = []
    for stat in verdicts:
        color = _VERDICT_COLOR_BY_VALUE.get(stat.verdict, "#6b7280")
        rows.append(
            "<tr><td><span class='badge' style='background:%s'>%s</span></td>"
            "<td>%d</td><td>%s</td><td>%.1f%%</td></tr>"
            % (
                color,
                _safe_text(stat.label),
                stat.count,
                _ratio_bar(stat.ratio, color),
                stat.ratio * 100.0,
            )
        )
    return "".join(rows)


def _agent_rows(agents: List[AgentSummary]) -> str:
    if not agents:
        return "<tr><td colspan='6' class='muted'>暂无 Agent 数据</td></tr>"
    rows = []
    for agent in agents:
        rows.append(
            "<tr><td><code>%s</code></td><td>%d</td><td>%d</td><td>%d</td><td>$%s</td><td>%s</td></tr>"
            % (
                _safe_text(agent.agent_id or "未知"),
                agent.traces,
                agent.risk_traces,
                agent.hits,
                _usd(agent.cost_usd),
                _safe_time(agent.latest_at),
            )
        )
    return "".join(rows)


def _risk_rows(risks: List[RiskTraceRow], truncated: bool, detail_limit: int) -> str:
    if not risks:
        return "<tr><td colspan='7' class='muted'>没有不通过或待复核的轨迹</td></tr>"
    rows = []
    for risk in risks:
        label = _VERDICT_LABEL_BY_VALUE.get(risk.verdict, risk.verdict)
        hit_blocks = []
        for hit in risk.hits:
            hit_blocks.append(
                "<div class='hit'><span class='sev sev-%s'>%s</span> <b>%s</b>：%s</div>"
                % (
                    _safe_text(hit.get("severity") or "info"),
                    _safe_text((hit.get("severity") or "info").upper()),
                    _safe_text(hit.get("rule_name") or "未命名规则"),
                    _safe_text(hit.get("message") or ""),
                )
            )
        rows.append(
            "<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td>%d</td><td>$%s</td><td>%s</td><td>%s</td></tr>"
            % (
                _safe_text(risk.trace_id),
                _safe_text(risk.agent_id or "未知"),
                _safe_text(label),
                risk.hits_count,
                _usd(risk.cost_usd),
                _safe_time(risk.created_at),
                "".join(hit_blocks) or "<span class='muted'>无命中摘要</span>",
            )
        )
    if truncated:
        rows.append(
            "<tr><td colspan='7' class='muted'>风险明细仅展示前 %d 条，更多记录已计入上方统计。</td></tr>"
            % detail_limit
        )
    return "".join(rows)


def _cost_rows(rows_data: List[Dict[str, Any]]) -> str:
    if not rows_data:
        return "<tr><td colspan='6' class='muted'>没有可计价的轨迹</td></tr>"
    rows = []
    for row in rows_data:
        label = _VERDICT_LABEL_BY_VALUE.get(str(row.get("verdict") or ""), str(row.get("verdict") or ""))
        rows.append(
            "<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td>%d</td><td>$%s</td><td>%s</td></tr>"
            % (
                _safe_text(row.get("trace_id") or ""),
                _safe_text(row.get("agent_id") or "未知"),
                _safe_text(label),
                _int(row.get("hits_count")),
                _usd(row.get("cost_usd")),
                _safe_time(row.get("created_at")),
            )
        )
    return "".join(rows)


_CSS = """
  :root {
    --bg: #f5f6f8; --card: #ffffff; --ink: #1f2937; --muted: #6b7280;
    --line: #e5e7eb; --accent: #2563eb;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
    font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; line-height: 1.6; }
  .page { max-width: 1100px; margin: 0 auto; padding: 32px 24px 64px; }
  .hero { background: var(--card); border-radius: 14px; padding: 28px 32px;
    border: 1px solid var(--line); box-shadow: 0 8px 24px rgba(15, 23, 42, .05); }
  .eyebrow { font-size: 12px; letter-spacing: .16em; color: var(--muted); text-transform: uppercase; }
  h1 { margin: 8px 0 6px; font-size: 26px; }
  .meta { color: var(--muted); font-size: 13px; }
  .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin: 20px 0 8px; }
  .metric { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px; }
  .metric .k { color: var(--muted); font-size: 12px; }
  .metric .v { font-size: 22px; font-weight: 700; margin-top: 4px; font-variant-numeric: tabular-nums; }
  section { background: var(--card); border: 1px solid var(--line); border-radius: 14px;
    padding: 22px 24px; margin-top: 18px; }
  h2 { font-size: 17px; margin: 0 0 14px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--muted); font-weight: 600; background: #fafafa; white-space: nowrap; }
  code { font-family: "Cascadia Code", Consolas, monospace; font-size: 12px;
    background: #f3f4f6; padding: 2px 5px; border-radius: 4px; }
  .muted { color: var(--muted); }
  .badge { display: inline-block; padding: 2px 9px; border-radius: 999px; color: #fff;
    font-size: 12px; font-weight: 600; white-space: nowrap; }
  .bar { height: 8px; width: 100%; min-width: 60px; background: #e5e7eb; border-radius: 999px;
    overflow: hidden; margin-top: 5px; }
  .bar span { display: block; height: 8px; background: var(--accent); border-radius: 999px; }
  .sev { display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 11px; font-weight: 600; }
  .sev-critical, .sev-high { background: #fee2e2; color: #b91c1c; }
  .sev-medium { background: #fef3c7; color: #b45309; }
  .sev-low, .sev-info { background: #e0f2fe; color: #0369a1; }
  .hit { margin: 2px 0; }
  footer { margin-top: 24px; color: var(--muted); font-size: 12px; text-align: center; }
  @media (max-width: 820px) { .grid { grid-template-columns: repeat(2, 1fr); } }
"""


def render_summary_html(report: SummaryReport) -> str:
    totals = report.totals or {}
    generated_at = report.generated_at.strftime("%Y-%m-%d %H:%M:%S")
    scope_text = "全部历史轨迹" if report.scope == "all" else report.scope
    lines = [
        "<!DOCTYPE html>",
        "<html lang=\"zh-CN\">",
        "<head>",
        "<meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">",
        "<title>轨迹审计汇总报告</title>",
        "<style>",
        _CSS,
        "</style>",
        "</head>",
        "<body>",
        "<div class=\"page\">",
        "<div class=\"hero\">",
        "<div class=\"eyebrow\">SafeOrbit · Audit Summary</div>",
        "<h1>轨迹审计汇总报告</h1>",
        "<div class=\"meta\">报告 <code>%s</code> · 生成于 %s</div>"
        % (_safe_text(report.report_id), _safe_text(generated_at)),
        "<div class=\"meta\">统计范围：%s · 数据区间 %s → %s</div>"
        % (_safe_text(scope_text), _safe_time(report.range_start), _safe_time(report.range_end)),
        "</div>",
        "<div class=\"grid\">",
        "<div class=\"metric\"><div class=\"k\">轨迹总数</div><div class=\"v\">%d</div></div>"
        % _int(totals.get("traces")),
        "<div class=\"metric\"><div class=\"k\">不通过</div><div class=\"v\" style=\"color:#dc2626\">%d</div></div>"
        % _verdict_count(report, "fail"),
        "<div class=\"metric\"><div class=\"k\">有风险 / 待复核</div><div class=\"v\" style=\"color:#d97706\">%d</div></div>"
        % (_verdict_count(report, "warn") + _verdict_count(report, "review")),
        "<div class=\"metric\"><div class=\"k\">累计成本</div><div class=\"v\">$%s</div></div>"
        % _usd(totals.get("cost_usd")),
        "</div>",
        "<section><h2>判定分布</h2><table>",
        "<thead><tr><th>判定</th><th>数量</th><th>占比</th><th>比例</th></tr></thead>",
        "<tbody>%s</tbody>" % _verdict_rows(report.verdicts),
        "</table></section>",
        "<section><h2>Agent 风险排名</h2><table>",
        "<thead><tr><th>Agent</th><th>轨迹</th><th>风险轨迹</th><th>发现</th><th>成本</th><th>最近</th></tr></thead>",
        "<tbody>%s</tbody>" % _agent_rows(report.agents),
        "</table></section>",
        "<section><h2>近 14 天趋势</h2><table>",
        "<thead><tr><th>日期</th><th>轨迹</th><th>成本</th><th>分布</th></tr></thead>",
        "<tbody>%s</tbody>" % _trend_rows(report.daily),
        "</table></section>",
        "<section><h2>风险轨迹明细 · 上限 %d 条</h2><table>"
        % report.detail_limit,
        "<thead><tr><th>轨迹</th><th>Agent</th><th>判定</th><th>命中</th><th>成本</th><th>时间</th><th>命中摘要</th></tr></thead>",
        "<tbody>%s</tbody>" % _risk_rows(report.risks, report.detail_truncated, report.detail_limit),
        "</table></section>",
        "<section><h2>成本 Top 10</h2><table>",
        "<thead><tr><th>轨迹</th><th>Agent</th><th>判定</th><th>命中</th><th>成本</th><th>时间</th></tr></thead>",
        "<tbody>%s</tbody>" % _cost_rows(report.top_cost),
        "</table></section>",
        "<section><h2>统计口径与免责声明</h2>",
        "<p class=\"muted\" style=\"font-size:13px;\">%s</p>" % _safe_text(report.disclaimer),
        "</section>",
        "<footer>SafeOrbit · 汇总报告 %s · 由审计内核自动生成</footer>"
        % _safe_text(report.report_id),
        "</div>",
        "</body>",
        "</html>",
    ]
    return "\n".join(lines)


def _verdict_count(report: SummaryReport, verdict: str) -> int:
    for stat in report.verdicts:
        if stat.verdict == verdict:
            return stat.count
    return 0
