"""P0 内置规则：从 8 条开始，全部可定位到 span。"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .. import guard
from ..models import Dimension, RuleHit, Severity, Span, SpanKind, Trajectory
from ..pii import find_pii
from .base import AuditContext, BaseRule


def _tool_spans(trajectory: Trajectory) -> Iterable[Span]:
    for span in trajectory.spans:
        if span.kind == SpanKind.TOOL and span.tool is not None:
            yield span


def _cost_of(trajectory: Trajectory) -> Tuple[float, List[str]]:
    total = 0.0
    unknown: List[str] = []
    for span in trajectory.spans:
        if span.llm is None:
            continue
        if span.llm.cost_usd is None:
            unknown.append(span.llm.model)
        else:
            total += span.llm.cost_usd
    return round(total, 6), unknown


def _texts_of(span: Span) -> List[Tuple[str, str]]:
    """收集需要做敏感信息扫描的文本字段。"""

    texts: List[Tuple[str, str]] = []

    def add(label: str, value: Any) -> None:
        if isinstance(value, str) and value:
            texts.append((label, value))

    add("output", span.output)
    if span.llm is not None:
        add("completion", span.llm.completion)
        # 用户消息里的手机号 / 身份证同样是泄露风险，必须一起扫
        for index, message in enumerate(span.llm.messages):
            if isinstance(message, dict):
                add("llm.messages[%d].content" % index, message.get("content"))
    if span.tool is not None:
        add("tool.result", span.tool.result)
        for key, value in span.tool.arguments.items():
            add("tool.arguments.%s" % key, value)
    if span.retrieval is not None:
        add("retrieval.query", span.retrieval.query)
        for index, document in enumerate(span.retrieval.documents):
            if isinstance(document, dict):
                add("retrieval.documents[%d]" % index, document.get("content"))
    return texts


class CostBudgetRule(BaseRule):
    id = "cost_budget_exceeded"
    name = "成本超出预算"
    dimension = Dimension.COST
    severity = Severity.HIGH
    description = "本次轨迹的模型成本超过配置预算。"

    def evaluate(self, trajectory: Trajectory, context: AuditContext) -> List:
        budget = context.budget_usd or trajectory.metadata.get("budget_usd")
        if not budget:
            return []
        total, unknown = _cost_of(trajectory)
        if total <= float(budget):
            return []
        evidence = {"total_usd": total, "budget_usd": float(budget)}
        if unknown:
            evidence["unpriced_models"] = sorted(set(unknown))
        # 预算结论也要能定位：锚定到最贵的那一次模型调用
        priced = [
            span
            for span in trajectory.spans
            if span.llm is not None and span.llm.cost_usd is not None
        ]
        anchor = max(priced, key=lambda s: s.llm.cost_usd or 0.0).span_id if priced else None
        return [
            self.hit(
                "成本 $%.6f 超过预算 $%.6f" % (total, float(budget)),
                span_ref=anchor,
                evidence=evidence,
            )
        ]


class WriteToolUnconfirmedRule(BaseRule):
    id = "tool_write_unconfirmed"
    name = "写操作未经人工确认"
    dimension = Dimension.SAFETY
    severity = Severity.HIGH
    description = "写操作（退款、发券、扣库存等）未经过人工确认。"

    def evaluate(self, trajectory: Trajectory, context: AuditContext) -> List:
        hits = []
        for span in _tool_spans(trajectory):
            tool = span.tool
            if tool.side_effect != "write":
                continue
            if tool.blocked:
                hits.append(
                    self.hit(
                        "写操作已被内置防护拦截，未实际执行",
                        span_ref=span.span_id,
                        severity=Severity.LOW,
                        evidence={"tool_name": tool.tool_name, "blocked": True},
                    )
                )
            elif not tool.confirmed_by_human:
                hits.append(
                    self.hit(
                        "写操作未经过人工确认：%s" % tool.tool_name,
                        span_ref=span.span_id,
                        evidence={"tool_name": tool.tool_name, "arguments": tool.arguments},
                    )
                )
        return hits


class LargeWriteAmountRule(BaseRule):
    id = "tool_write_large_amount"
    name = "写操作金额异常"
    dimension = Dimension.SAFETY
    severity = Severity.HIGH
    description = "写操作涉及金额超过阈值。"

    _AMOUNT_KEYS = {"amount", "total", "price", "value", "refund_amount", "quantity"}

    def evaluate(self, trajectory: Trajectory, context: AuditContext) -> List:
        threshold = context.write_amount_threshold
        if threshold is None:
            return []
        hits = []
        for span in _tool_spans(trajectory):
            tool = span.tool
            if tool.side_effect != "write":
                continue
            for key, value in tool.arguments.items():
                if key.lower() not in self._AMOUNT_KEYS:
                    continue
                if isinstance(value, (int, float)) and value > threshold:
                    hits.append(
                        self.hit(
                            "写操作金额 %s=%s 超过阈值 %s" % (key, value, threshold),
                            span_ref=span.span_id,
                            evidence={
                                "tool_name": tool.tool_name,
                                "key": key,
                                "value": value,
                                "threshold": threshold,
                            },
                        )
                    )
        return hits


class ToolAllowlistRule(BaseRule):
    id = "tool_not_allowlisted"
    name = "工具不在白名单"
    dimension = Dimension.SAFETY
    severity = Severity.MEDIUM
    description = "调用了未在允许清单内的工具。"

    def evaluate(self, trajectory: Trajectory, context: AuditContext) -> List:
        if context.allowed_tools is None:
            return []
        hits = []
        for span in _tool_spans(trajectory):
            name = span.tool.tool_name
            if name not in context.allowed_tools:
                hits.append(
                    self.hit(
                        "工具 %s 不在白名单" % name,
                        span_ref=span.span_id,
                        evidence={"tool_name": name, "allowlist": sorted(context.allowed_tools)},
                    )
                )
        return hits


class PIILeakRule(BaseRule):
    id = "pii_leak"
    name = "敏感信息外泄"
    dimension = Dimension.SAFETY
    severity = Severity.HIGH
    description = "模型输出、工具返回或参数中出现敏感信息。"

    def evaluate(self, trajectory: Trajectory, context: AuditContext) -> List:
        hits = []
        for span in trajectory.spans:
            found = []
            for label, text in _texts_of(span):
                for pii_type, sample in find_pii(text):
                    found.append({"field": label, "type": pii_type, "sample": sample})
            if found:
                hits.append(
                    self.hit(
                        "发现 %d 处敏感信息" % len(found),
                        span_ref=span.span_id,
                        evidence={"matches": found[:10]},
                    )
                )
        return hits


class LoopDetectedRule(BaseRule):
    id = "loop_detected"
    name = "重复调用死循环"
    dimension = Dimension.COST
    severity = Severity.MEDIUM
    description = "相同工具与相同参数被反复调用。"

    def evaluate(self, trajectory: Trajectory, context: AuditContext) -> List:
        groups: Dict[str, List[Span]] = {}
        for span in _tool_spans(trajectory):
            key = "%s|%s" % (
                span.tool.tool_name,
                json.dumps(span.tool.arguments, sort_keys=True, ensure_ascii=False, default=str),
            )
            groups.setdefault(key, []).append(span)

        hits = []
        for key, spans in groups.items():
            if len(spans) >= context.loop_threshold:
                last = spans[-1]
                hits.append(
                    self.hit(
                        "工具 %s 以相同参数重复调用 %d 次" % (last.tool.tool_name, len(spans)),
                        span_ref=last.span_id,
                        evidence={"tool_name": last.tool.tool_name, "count": len(spans)},
                    )
                )
        return hits


class ContextTruncatedRule(BaseRule):
    id = "context_truncated"
    name = "上下文被截断"
    dimension = Dimension.QUALITY
    severity = Severity.LOW
    description = "轨迹存在被截断的字段，可能影响判断完整性。"

    def evaluate(self, trajectory: Trajectory, context: AuditContext) -> List:
        if not trajectory.truncated_fields:
            return []
        # 截断是轨迹级问题，但仍锚定到一个具体节点，方便点开查看
        anchor = None
        for span in trajectory.spans:
            if span.llm is not None and any(
                field.startswith("llm") for field in trajectory.truncated_fields
            ):
                anchor = span.span_id
                break
        if anchor is None and trajectory.spans:
            anchor = trajectory.spans[0].span_id
        return [
            self.hit(
                "轨迹存在截断字段：%s" % ", ".join(trajectory.truncated_fields),
                span_ref=anchor,
                evidence={"truncated_fields": trajectory.truncated_fields},
            )
        ]


class HandoffMissingContextRule(BaseRule):
    id = "handoff_missing_context"
    name = "Agent 交接缺少上下文"
    dimension = Dimension.QUALITY
    severity = Severity.MEDIUM
    description = "多 Agent 交接时未传递上下文信息。"

    def evaluate(self, trajectory: Trajectory, context: AuditContext) -> List:
        hits = []
        for span in trajectory.spans:
            if span.kind != SpanKind.HANDOFF or span.handoff is None:
                continue
            if not span.handoff.message:
                hits.append(
                    self.hit(
                        "%s → %s 的交接未携带上下文" % (span.handoff.from_agent, span.handoff.to_agent),
                        span_ref=span.span_id,
                        evidence={
                            "from_agent": span.handoff.from_agent,
                            "to_agent": span.handoff.to_agent,
                        },
                    )
                )
        return hits


class GuardContentRule(BaseRule):
    id = "guard.content"
    name = "模型内容安全"
    dimension = Dimension.SAFETY
    severity = Severity.HIGH
    description = "检测提示注入、数据外泄、输出泄露、RAG 注入与高密级检索。"

    def evaluate(self, trajectory: Trajectory, context: AuditContext) -> List:
        hits: List[RuleHit] = []
        for span in trajectory.spans:
            findings = []
            if span.llm is not None:
                findings.extend(guard.check_messages(span.llm.messages))
                if span.llm.completion:
                    findings.extend(guard.check_output(span.llm.completion))
            if span.retrieval is not None:
                findings.extend(
                    guard.check_retrieval(span.retrieval.query, span.retrieval.documents)
                )
            for item in span.attributes.get("guard_findings") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    findings.append(guard.GuardFinding(**item))
                except TypeError:
                    continue
            # PII 由 PIILeakRule 统一负责，避免重复告警
            findings = [item for item in findings if item.code != "pii_leak"]
            hits.extend(guard.to_rule_hits(findings, span.span_id))
        return hits


class BaselineNewToolRule(BaseRule):
    id = "baseline_new_tool"
    name = "出现基线外的新工具"
    dimension = Dimension.SAFETY
    severity = Severity.MEDIUM
    description = "本次调用了该 Agent 历史基线中从未出现的工具。"

    def evaluate(self, trajectory: Trajectory, context: AuditContext) -> List:
        baseline = context.baseline or {}
        if int(baseline.get("sample_size") or 0) < 5:
            return []
        known = set((baseline.get("tools") or {}).keys())
        hits = []
        seen = set()
        for span in _tool_spans(trajectory):
            name = span.tool.tool_name
            if name in known or name in seen:
                continue
            seen.add(name)
            hits.append(
                self.hit(
                    "工具 %s 不在该 Agent 的历史基线中" % name,
                    span_ref=span.span_id,
                    evidence={
                        "tool_name": name,
                        "baseline_sample_size": baseline.get("sample_size"),
                        "known_tools": sorted(known)[:20],
                    },
                )
            )
        return hits


class BaselineCostSpikeRule(BaseRule):
    id = "baseline_cost_spike"
    name = "成本显著偏离基线"
    dimension = Dimension.COST
    severity = Severity.MEDIUM
    description = "本次轨迹成本显著高于该 Agent 历史基线。"

    def evaluate(self, trajectory: Trajectory, context: AuditContext) -> List:
        baseline = context.baseline or {}
        if int(baseline.get("sample_size") or 0) < 5:
            return []
        p95 = float(baseline.get("cost_p95") or 0.0)
        total, _ = _cost_of(trajectory)
        threshold = max(p95 * 3.0, 0.01)
        if p95 <= 0 or total <= threshold:
            return []
        priced = [span for span in trajectory.spans if span.llm is not None and span.llm.cost_usd]
        anchor = max(priced, key=lambda s: s.llm.cost_usd or 0.0).span_id if priced else None
        return [
            self.hit(
                "本次成本 $%.6f 超过基线 p95（$%.6f）的 3 倍" % (total, p95),
                span_ref=anchor,
                evidence={
                    "total_usd": total,
                    "baseline_p95_usd": p95,
                    "threshold_usd": round(threshold, 6),
                    "baseline_sample_size": baseline.get("sample_size"),
                },
            )
        ]


def default_rules() -> List[BaseRule]:
    return [
        CostBudgetRule(),
        WriteToolUnconfirmedRule(),
        LargeWriteAmountRule(),
        ToolAllowlistRule(),
        PIILeakRule(),
        GuardContentRule(),
        LoopDetectedRule(),
        ContextTruncatedRule(),
        HandoffMissingContextRule(),
        BaselineNewToolRule(),
        BaselineCostSpikeRule(),
    ]
