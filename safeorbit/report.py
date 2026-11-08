"""审计报告构建与单文件 HTML 渲染。"""

from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from .judge import collect_tool_stats
from .models import (
    AuditReport,
    CostBreakdown,
    RuleHit,
    SpanKind,
    SpanStatus,
    Trajectory,
    VerdictLevel,
)
from .pricing import PricingTable
from .trace_utils import trajectory_duration_ms
from .verdict import score_hits

_DISCLAIMER = (
    "本报告由 SafeOrbit 生成。结论来源分为规则判定（确定性、可复现）与语义裁判（建议人工复核），"
    "报告中已分别标注。采集覆盖率与字段截断情况见“覆盖与完整性”。"
    "本报告用于审计取证与风险评估，不替代人工判断。"
)

_VERDICT_LABEL = {
    VerdictLevel.PASS: "通过",
    VerdictLevel.WARN: "有风险",
    VerdictLevel.FAIL: "不通过",
    VerdictLevel.REVIEW: "待复核",
}

_VERDICT_COLOR = {
    VerdictLevel.PASS: "#16a34a",
    VerdictLevel.WARN: "#d97706",
    VerdictLevel.FAIL: "#dc2626",
    VerdictLevel.REVIEW: "#2563eb",
}


def build_report(
    trajectory: Trajectory,
    hits: List[RuleHit],
    pricing: PricingTable,
    judge_status: str = "skipped",
    judge_note: str = "",
    budget_usd: Optional[float] = None,
) -> AuditReport:
    from .verdict import compose_verdict

    verdict = compose_verdict(hits)
    ordered = sorted(
        hits,
        key=lambda h: (_severity_rank(h), h.dimension.value, h.rule_id),
    )

    cost = _cost_breakdown(trajectory, budget_usd)
    safety = _safety_summary(trajectory, hits)
    quality = _quality_summary(trajectory, hits, judge_status, judge_note)
    compliance = _compliance_summary(trajectory)
    coverage = _coverage(trajectory, pricing)

    unknown_pricing = coverage.get("unknown_pricing_models", [])
    summary = _summary_text(trajectory, verdict, ordered, cost, unknown_pricing)

    evidence_index: Dict[str, str] = {}
    for span in trajectory.spans:
        evidence_index[span.span_id] = span.evidence_ref or "trace://%s/%s" % (
            trajectory.trace_id,
            span.span_id,
        )

    return AuditReport(
        report_id="rpt-%s-%s" % (trajectory.trace_id, datetime.now().strftime("%Y%m%d%H%M%S")),
        trace_id=trajectory.trace_id,
        title="Agent 审计报告 · %s" % trajectory.agent_id,
        agent_id=trajectory.agent_id,
        agent_version=trajectory.agent_version,
        generated_at=datetime.now().astimezone(),
        verdict=verdict,
        summary=summary,
        cost=cost,
        safety=safety,
        quality=quality,
        compliance=compliance,
        hits=ordered,
        evidence_index=evidence_index,
        coverage=coverage,
        disclaimer=_DISCLAIMER + " 评分 %d 分仅供参考。" % score_hits(hits),
    )


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------
def _severity_rank(hit: RuleHit) -> int:
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    return order.get(hit.severity.value, 5)


def _cost_breakdown(trajectory: Trajectory, budget_usd: Optional[float]) -> CostBreakdown:
    by_model: Dict[str, float] = {}
    by_span: List[Dict[str, Any]] = []
    total = 0.0

    for span in trajectory.spans:
        if span.llm is None or span.llm.cost_usd is None:
            continue
        cost = span.llm.cost_usd
        total += cost
        by_model[span.llm.model] = round(by_model.get(span.llm.model, 0.0) + cost, 6)
        by_span.append(
            {
                "span_id": span.span_id,
                "name": span.name,
                "model": span.llm.model,
                "cost_usd": cost,
                "tokens": span.llm.usage.prompt + span.llm.usage.completion,
            }
        )

    by_span.sort(key=lambda item: item["cost_usd"], reverse=True)
    budget = budget_usd or trajectory.metadata.get("budget_usd")
    return CostBreakdown(
        total_usd=round(total, 6),
        by_model={key: round(value, 6) for key, value in by_model.items()},
        by_span=by_span,
        budget_usd=float(budget) if budget else None,
        over_budget=bool(budget) and total > float(budget),
    )


def _safety_summary(trajectory: Trajectory, hits: List[RuleHit]) -> Dict[str, Any]:
    stats = collect_tool_stats(trajectory)
    safety_hits = [h for h in hits if h.dimension.value == "safety"]
    pii_hits = [h for h in hits if h.rule_id == "pii_leak"]
    return {
        **stats,
        "safety_findings": len(safety_hits),
        "high_risk_findings": len(
            [h for h in safety_hits if h.severity.value in ("high", "critical")]
        ),
        "pii_findings": len(pii_hits),
    }


def _quality_summary(
    trajectory: Trajectory,
    hits: List[RuleHit],
    judge_status: str,
    judge_note: str,
) -> Dict[str, Any]:
    return {
        "judge_status": judge_status,
        "judge_note": judge_note,
        "judge_findings": len([h for h in hits if h.source == "judge"]),
        "error_spans": len([s for s in trajectory.spans if s.status == SpanStatus.ERROR]),
        "truncated_fields": list(trajectory.truncated_fields),
    }


def _compliance_summary(trajectory: Trajectory) -> Dict[str, Any]:
    integrity = trajectory.integrity
    return {
        "source": trajectory.source,
        "sampled": trajectory.sampled,
        "redacted": trajectory.redacted,
        "integrity_hash": integrity.content_hash if integrity else None,
        "prev_hash": integrity.prev_hash if integrity else None,
        "received_at": trajectory.received_at.isoformat(),
    }


def _coverage(trajectory: Trajectory, pricing: PricingTable) -> Dict[str, Any]:
    by_kind: Dict[str, int] = {}
    llm_without_usage = 0
    unknown_models = set()

    for span in trajectory.spans:
        by_kind[span.kind.value] = by_kind.get(span.kind.value, 0) + 1
        if span.kind == SpanKind.LLM and span.llm is not None:
            if span.llm.usage.prompt == 0 and span.llm.usage.completion == 0:
                llm_without_usage += 1
            if span.llm.model not in pricing.models:
                unknown_models.add(span.llm.model)

    return {
        "span_count": len(trajectory.spans),
        "by_kind": by_kind,
        "sampled": trajectory.sampled,
        "truncated_fields": list(trajectory.truncated_fields),
        "llm_spans_without_usage": llm_without_usage,
        "unknown_pricing_models": sorted(unknown_models) or trajectory.metadata.get(
            "unknown_pricing_models", []
        ),
        "normalization_warnings": trajectory.metadata.get("normalization_warnings", []),
        "baseline": trajectory.metadata.get("baseline") or {},
        "duration_ms": trajectory_duration_ms(trajectory),
    }


def _summary_text(
    trajectory: Trajectory,
    verdict: VerdictLevel,
    hits: List[RuleHit],
    cost: CostBreakdown,
    unknown_pricing: List[str],
) -> str:
    if verdict == VerdictLevel.FAIL:
        reasons = [h.message for h in hits if h.severity.value in ("high", "critical")][:3]
        head = "本次运行判定为不通过，主要问题：%s。" % "；".join(reasons)
    elif verdict == VerdictLevel.WARN:
        reasons = [h.message for h in hits if h.severity.value == "medium"][:3]
        head = "本次运行存在风险，建议处理：%s。" % "；".join(reasons)
    elif verdict == VerdictLevel.REVIEW:
        head = "硬规则未发现明确问题，语义裁判提出 %d 条待复核建议。" % len(
            [h for h in hits if h.source == "judge"]
        )
    else:
        head = "本次运行未发现明确问题。"

    tail = "共 %d 个轨迹节点，模型成本 $%.6f。" % (len(trajectory.spans), cost.total_usd)
    if unknown_pricing:
        tail += " 部分模型缺少价格配置（%s），成本为不完全统计。" % "、".join(unknown_pricing)
    return head + tail


# --------------------------------------------------------------------------
# HTML 渲染
# --------------------------------------------------------------------------
def report_to_json(report: AuditReport) -> str:
    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)


def render_html(report: AuditReport) -> str:
    color = _VERDICT_COLOR[report.verdict]
    label = _VERDICT_LABEL[report.verdict]
    hits_html = _render_hits(report.hits)
    cost_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>$%.6f</td></tr>"
        % (html.escape(model), html.escape(_model_token_share(report, model)), amount)
        for model, amount in report.cost.by_model.items()
    ) or "<tr><td colspan='3' class='muted'>没有可计价的模型调用</td></tr>"

    top_spans = "".join(
        "<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td>$%.6f</td><td>%s</td></tr>"
        % (
            html.escape(item["span_id"]),
            html.escape(item.get("name", "")),
            html.escape(item.get("model", "")),
            item.get("cost_usd", 0.0),
            item.get("tokens", 0),
        )
        for item in report.cost.by_span[:5]
    ) or "<tr><td colspan='5' class='muted'>没有可计价的模型调用</td></tr>"

    coverage = report.coverage
    kinds = "、".join(
        "%s %d" % (html.escape(kind), count) for kind, count in sorted(coverage.get("by_kind", {}).items())
    ) or "无"
    warnings = coverage.get("normalization_warnings") or []
    warnings_html = (
        "<ul class='warnings'>%s</ul>" % "".join("<li>%s</li>" % html.escape(w) for w in warnings)
        if warnings
        else "<p class='muted'>无</p>"
    )
    unknown_pricing = coverage.get("unknown_pricing_models") or []
    unknown_html = (
        "<p class='warn-text'>以下模型未配置价格，成本为不完全统计：%s</p>"
        % html.escape("、".join(unknown_pricing))
        if unknown_pricing
        else ""
    )

    evidence_rows = "".join(
        "<tr><td><code>%s</code></td><td><code>%s</code></td></tr>"
        % (html.escape(span_id), html.escape(ref))
        for span_id, ref in list(report.evidence_index.items())[:20]
    )

    stored_hash = report.compliance.get("integrity_hash") or "未生成"
    prev_hash = report.compliance.get("prev_hash") or "无（首条轨迹）"

    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --bg: #f5f6f8; --card: #ffffff; --ink: #1f2937; --muted: #6b7280;
    --line: #e5e7eb; --accent: {color};
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--ink);
    font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; line-height: 1.6; }}
  .page {{ max-width: 1040px; margin: 0 auto; padding: 32px 24px 64px; }}
  .hero {{ background: var(--card); border-radius: 14px; padding: 28px 32px;
    border: 1px solid var(--line); box-shadow: 0 8px 24px rgba(15, 23, 42, .05); }}
  .eyebrow {{ font-size: 12px; letter-spacing: .16em; color: var(--muted); text-transform: uppercase; }}
  h1 {{ margin: 8px 0 6px; font-size: 26px; }}
  .meta {{ color: var(--muted); font-size: 13px; }}
  .badge {{ display: inline-block; margin-top: 14px; padding: 6px 14px; border-radius: 999px;
    color: #fff; background: var(--accent); font-weight: 600; font-size: 14px; }}
  .summary {{ margin-top: 16px; font-size: 15px; }}
  .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin: 20px 0 8px; }}
  .metric {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px; }}
  .metric .k {{ color: var(--muted); font-size: 12px; }}
  .metric .v {{ font-size: 22px; font-weight: 700; margin-top: 4px; }}
  section {{ background: var(--card); border: 1px solid var(--line); border-radius: 14px;
    padding: 22px 24px; margin-top: 18px; }}
  h2 {{ font-size: 17px; margin: 0 0 14px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }}
  th {{ color: var(--muted); font-weight: 600; background: #fafafa; }}
  code {{ font-family: "Cascadia Code", Consolas, monospace; font-size: 12px;
    background: #f3f4f6; padding: 2px 5px; border-radius: 4px; }}
  .muted {{ color: var(--muted); }}
  .warn-text {{ color: #b45309; font-size: 13px; }}
  .sev {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }}
  .sev-critical, .sev-high {{ background: #fee2e2; color: #b91c1c; }}
  .sev-medium {{ background: #fef3c7; color: #b45309; }}
  .sev-low, .sev-info {{ background: #e0f2fe; color: #0369a1; }}
  .source {{ font-size: 11px; color: var(--muted); }}
  .finding {{ padding: 14px 0; border-bottom: 1px solid var(--line); }}
  .finding:last-child {{ border-bottom: 0; }}
  .finding .msg {{ font-weight: 600; }}
  .finding .ev {{ margin-top: 6px; font-size: 12px; color: var(--muted); white-space: pre-wrap; word-break: break-all; }}
  .warnings {{ margin: 0; padding-left: 18px; color: #b45309; font-size: 13px; }}
  footer {{ margin-top: 24px; color: var(--muted); font-size: 12px; text-align: center; }}
  @media (max-width: 820px) {{ .grid {{ grid-template-columns: repeat(2, 1fr); }} }}
</style>
</head>
<body>
<div class="page">
  <div class="hero">
    <div class="eyebrow">SafeOrbit · Agent Audit</div>
    <h1>{title}</h1>
    <div class="meta">轨迹 <code>{trace_id}</code> · Agent <code>{agent_id}</code>{agent_version} · 生成于 {generated_at}</div>
    <div class="badge">{verdict_label}</div>
    <div class="summary">{summary}</div>
  </div>

  <div class="grid">
    <div class="metric"><div class="k">模型成本</div><div class="v">${total_cost}</div></div>
    <div class="metric"><div class="k">轨迹节点</div><div class="v">{span_count}</div></div>
    <div class="metric"><div class="k">工具调用</div><div class="v">{tool_calls}</div></div>
    <div class="metric"><div class="k">判定分</div><div class="v">{score}</div></div>
  </div>

  <section>
    <h2>结论与发现 · {hit_count} 项</h2>
    {hits}
  </section>

  <section>
    <h2>成本构成</h2>
    {unknown_pricing}
    <table>
      <thead><tr><th>模型</th><th>Token 占比</th><th>成本</th></tr></thead>
      <tbody>{cost_rows}</tbody>
    </table>
    <h2 style="margin-top:20px;">成本最高的调用</h2>
    <table>
      <thead><tr><th>Span</th><th>步骤</th><th>模型</th><th>成本</th><th>Token</th></tr></thead>
      <tbody>{top_spans}</tbody>
    </table>
  </section>

  <section>
    <h2>安全</h2>
    <table>
      <tbody>
        <tr><th>工具调用 / 写操作</th><td>{tool_calls} / {write_calls}</td></tr>
        <tr><th>被内置防护拦截</th><td>{blocked_calls}</td></tr>
        <tr><th>敏感信息命中</th><td>{pii_findings}</td></tr>
        <tr><th>高风险发现</th><td>{high_risk_findings}</td></tr>
      </tbody>
    </table>
  </section>

  <section>
    <h2>质量与合规</h2>
    <table>
      <tbody>
        <tr><th>语义裁判</th><td>{judge_status} · {judge_note}</td></tr>
        <tr><th>裁判待复核结论</th><td>{judge_findings}</td></tr>
        <tr><th>失败节点</th><td>{error_spans}</td></tr>
        <tr><th>采集来源</th><td>{source}</td></tr>
        <tr><th>是否采样 / 脱敏</th><td>{sampled} / {redacted}</td></tr>
        <tr><th>内容指纹</th><td><code>{content_hash}</code></td></tr>
        <tr><th>前序指纹</th><td><code>{prev_hash}</code></td></tr>
      </tbody>
    </table>
  </section>

  <section>
    <h2>覆盖与完整性</h2>
    <p class="muted">节点总数 {span_count}；类型分布：{kinds}。</p>
    <p class="muted">LLM 调用缺少 token 用量：{llm_without_usage} 个；采样：{sampled}；截断字段：{truncated}。</p>
    <h2 style="margin-top:18px;">归一化警告</h2>
    {warnings_html}
  </section>

  <section>
    <h2>证据索引（前 20 条）</h2>
    <table>
      <thead><tr><th>Span</th><th>证据地址</th></tr></thead>
      <tbody>{evidence_rows}</tbody>
    </table>
  </section>

  <section>
    <h2>免责声明</h2>
    <p class="muted" style="font-size:13px;">{disclaimer}</p>
  </section>

  <footer>SafeOrbit · 报告 {report_id} · 由审计内核自动生成</footer>
</div>
</body>
</html>
""".format(
        title=html.escape(report.title),
        trace_id=html.escape(report.trace_id),
        agent_id=html.escape(report.agent_id),
        agent_version=(" · 版本 <code>%s</code>" % html.escape(report.agent_version)) if report.agent_version else "",
        generated_at=report.generated_at.strftime("%Y-%m-%d %H:%M:%S"),
        verdict_label=label,
        color=color,
        summary=html.escape(report.summary),
        total_cost="%.6f" % report.cost.total_usd,
        span_count=coverage.get("span_count", 0),
        tool_calls=report.safety.get("tool_calls", 0),
        score=score_hits(report.hits),
        hit_count=len(report.hits),
        hits=hits_html,
        cost_rows=cost_rows,
        top_spans=top_spans,
        unknown_pricing=unknown_html,
        write_calls=report.safety.get("write_calls", 0),
        blocked_calls=report.safety.get("blocked_calls", 0),
        pii_findings=report.safety.get("pii_findings", 0),
        high_risk_findings=report.safety.get("high_risk_findings", 0),
        judge_status=html.escape(str(report.quality.get("judge_status", ""))),
        judge_note=html.escape(str(report.quality.get("judge_note", ""))),
        judge_findings=report.quality.get("judge_findings", 0),
        error_spans=report.quality.get("error_spans", 0),
        source=html.escape(str(report.compliance.get("source", ""))),
        sampled=report.compliance.get("sampled"),
        redacted=report.compliance.get("redacted"),
        content_hash=html.escape(str(stored_hash)),
        prev_hash=html.escape(str(prev_hash)),
        kinds=html.escape(kinds),
        llm_without_usage=coverage.get("llm_spans_without_usage", 0),
        truncated=html.escape("、".join(coverage.get("truncated_fields", [])) or "无"),
        warnings_html=warnings_html,
        evidence_rows=evidence_rows or "<tr><td colspan='2' class='muted'>无</td></tr>",
        disclaimer=html.escape(report.disclaimer),
        report_id=html.escape(report.report_id),
    )


def _render_hits(hits: List[RuleHit]) -> str:
    if not hits:
        return "<p class='muted'>未发现需要关注的问题。</p>"

    blocks = []
    for hit in hits:
        evidence = json.dumps(hit.evidence, ensure_ascii=False, indent=2, default=str)
        source = "规则判定" if hit.source == "rule" else "语义裁判（建议复核）"
        span = '<code>%s</code>' % html.escape(hit.span_ref) if hit.span_ref else "<span class='muted'>未绑定 span</span>"
        blocks.append(
            """<div class="finding">
  <span class="sev sev-{sev}">{sev_label}</span>
  <span class="source"> · {dimension} · {source}{review}</span>
  <div class="msg" style="margin-top:6px;">{message}</div>
  <div class="source" style="margin-top:4px;">定位：{span}</div>
  <div class="ev">{evidence}</div>
</div>""".format(
                sev=hit.severity.value,
                sev_label=hit.severity.value.upper(),
                dimension=hit.dimension.value,
                source=source,
                review="（需复核）" if hit.needs_review else "",
                message=html.escape(hit.message),
                span=span,
                evidence=html.escape(evidence[:600]),
            )
        )
    return "".join(blocks)


def _model_token_share(report: AuditReport, model: str) -> str:
    total = sum(item.get("tokens", 0) for item in report.cost.by_span) or 0
    tokens = sum(item.get("tokens", 0) for item in report.cost.by_span if item.get("model") == model)
    if total == 0:
        return "—"
    return "%.1f%%" % (tokens / total * 100.0)
