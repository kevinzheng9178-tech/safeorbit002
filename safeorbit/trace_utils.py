"""轨迹通用工具：树重建、耗时、查找。"""

from __future__ import annotations

from typing import Dict, List, Optional

from .models import Span, Trajectory


def spans_by_id(trajectory: Trajectory) -> Dict[str, Span]:
    return {span.span_id: span for span in trajectory.spans}


def children_map(trajectory: Trajectory) -> Dict[Optional[str], List[Span]]:
    """按 parent_span_id 分组；根节点的 key 为 None。"""
    grouped: Dict[Optional[str], List[Span]] = {}
    for span in trajectory.spans:
        grouped.setdefault(span.parent_span_id, []).append(span)
    return grouped


def root_spans(trajectory: Trajectory) -> List[Span]:
    ids = set(spans_by_id(trajectory))
    roots = [
        span
        for span in trajectory.spans
        if span.parent_span_id is None or span.parent_span_id not in ids
    ]
    return roots


def duration_ms(span: Span) -> Optional[float]:
    if span.end_time is None:
        return None
    delta = span.end_time - span.start_time
    return round(delta.total_seconds() * 1000.0, 3)


def trajectory_duration_ms(trajectory: Trajectory) -> Optional[float]:
    if trajectory.ended_at is not None:
        delta = trajectory.ended_at - trajectory.started_at
        return round(delta.total_seconds() * 1000.0, 3)

    finished = [s.end_time for s in trajectory.spans if s.end_time is not None]
    if not finished:
        return None
    start = min(s.start_time for s in trajectory.spans)
    end = max(finished)
    return round((end - start).total_seconds() * 1000.0, 3)
