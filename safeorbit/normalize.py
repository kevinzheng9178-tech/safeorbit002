"""归一化：补全成本、重建可用的父子关系、脱敏、生成证据摘要。"""

from __future__ import annotations

import hashlib
import json
from typing import List, Optional

from .models import Integrity, Trajectory
from .pii import redact_text, redact_value
from .pricing import PricingTable, estimate_cost


def compute_integrity(trajectory: Trajectory, prev_hash: Optional[str] = None) -> Integrity:
    """对轨迹内容做摘要，形成可校验的证据指纹。"""

    payload = trajectory.model_dump(mode="json")
    payload.pop("integrity", None)
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return Integrity(content_hash=digest, prev_hash=prev_hash)


def normalize(
    trajectory: Trajectory,
    pricing: PricingTable,
    redact: bool = False,
    prev_hash: Optional[str] = None,
) -> Trajectory:
    """返回归一化后的新对象，不修改调用方传入的轨迹。"""

    result = trajectory.model_copy(deep=True)
    warnings: List[str] = []

    span_ids = {span.span_id for span in result.spans}
    for span in result.spans:
        if span.parent_span_id is not None and span.parent_span_id not in span_ids:
            warnings.append(
                "span %s 的父节点 %s 不存在，已按根节点处理" % (span.span_id, span.parent_span_id)
            )

    unknown_models = set()
    for span in result.spans:
        if span.llm is None:
            continue
        if span.llm.cost_usd is None:
            span.llm.cost_usd = estimate_cost(span.llm.usage, span.llm.model, pricing)
        if span.llm.cost_usd is None:
            unknown_models.add(span.llm.model)

    if redact:
        _redact_trajectory(result)

    if warnings:
        result.metadata["normalization_warnings"] = warnings
    if unknown_models:
        result.metadata["unknown_pricing_models"] = sorted(unknown_models)

    result.integrity = compute_integrity(result, prev_hash=prev_hash)
    return result


def _redact_trajectory(trajectory: Trajectory) -> None:
    """就地脱敏轨迹中的内容字段。"""

    total = 0
    for span in trajectory.spans:
        span.input, count = redact_value(span.input)
        total += count
        span.output, count = redact_value(span.output)
        total += count
        span.attributes, count = redact_value(span.attributes)
        total += count

        if span.llm is not None:
            span.llm.messages, count = redact_value(span.llm.messages)
            total += count
            if span.llm.completion is not None:
                span.llm.completion, count = redact_text(span.llm.completion)
                total += count

        if span.tool is not None:
            span.tool.arguments, count = redact_value(span.tool.arguments)
            total += count
            span.tool.result, count = redact_value(span.tool.result)
            total += count

        if span.retrieval is not None:
            span.retrieval.query, count = redact_text(span.retrieval.query)
            total += count
            span.retrieval.documents, count = redact_value(span.retrieval.documents)
            total += count

        if span.handoff is not None and span.handoff.message is not None:
            span.handoff.message, count = redact_text(span.handoff.message)
            total += count

    if total > 0:
        trajectory.redacted = True
