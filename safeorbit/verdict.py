"""裁决合成：把规则命中与裁判结论合成一个总体判定。"""

from __future__ import annotations

from typing import List

from .models import RuleHit, Severity, VerdictLevel

_WEIGHTS = {
    Severity.CRITICAL: 40,
    Severity.HIGH: 25,
    Severity.MEDIUM: 10,
    Severity.LOW: 3,
    Severity.INFO: 0,
}


def compose_verdict(hits: List[RuleHit]) -> VerdictLevel:
    """硬规则优先，软结论（裁判）只负责提示复核。"""

    if any(hit.severity in (Severity.CRITICAL, Severity.HIGH) for hit in hits):
        return VerdictLevel.FAIL
    if any(hit.severity == Severity.MEDIUM for hit in hits):
        return VerdictLevel.WARN
    if any(hit.source == "judge" and hit.needs_review for hit in hits):
        return VerdictLevel.REVIEW
    return VerdictLevel.PASS


def score_hits(hits: List[RuleHit]) -> int:
    """100 分制扣分，只用于报告观感，不作为裁决依据。"""

    score = 100
    for hit in hits:
        score -= _WEIGHTS.get(hit.severity, 0)
        if hit.source == "judge":
            score -= 2
    return max(0, min(100, score))
