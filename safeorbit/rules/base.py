"""规则引擎基类与执行上下文。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from ..models import Dimension, RuleHit, Severity, Trajectory


@dataclass
class AuditContext:
    """一次审计的运行时配置。"""

    budget_usd: Optional[float] = None
    allowed_tools: Optional[Set[str]] = None
    loop_threshold: int = 3
    write_amount_threshold: Optional[float] = None
    baseline: Optional[Dict[str, Any]] = None


class BaseRule:
    """所有规则的最小契约：输入轨迹，输出可定位的命中项。"""

    id: str = ""
    name: str = ""
    dimension: Dimension = Dimension.SAFETY
    severity: Severity = Severity.LOW
    action: str = "record"
    description: str = ""

    def evaluate(self, trajectory: Trajectory, context: AuditContext) -> List[RuleHit]:
        raise NotImplementedError

    def hit(
        self,
        message: str,
        span_ref: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
        severity: Optional[Severity] = None,
        confidence: float = 1.0,
    ) -> RuleHit:
        return RuleHit(
            rule_id=self.id,
            rule_name=self.name,
            dimension=self.dimension,
            severity=severity or self.severity,
            span_ref=span_ref,
            message=message,
            evidence=evidence or {},
            confidence=confidence,
            source="rule",
            needs_review=False,
        )


class RuleEngine:
    def __init__(self, rules: List[BaseRule]):
        self.rules = rules

    def run(self, trajectory: Trajectory, context: AuditContext) -> List[RuleHit]:
        hits: List[RuleHit] = []
        for rule in self.rules:
            hits.extend(rule.evaluate(trajectory, context))
        return hits
