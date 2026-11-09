"""SafeOrbit 核心数据模型。

三条硬约定：
1. 字段只增不改，变更通过 schema_version 管理；
2. 所有内容字段允许为空（脱敏 / 截断 / 采样都必须如实标注）；
3. 每条审计结论都必须能定位到具体 span，否则不叫审计，只叫打分。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# 基础枚举
# --------------------------------------------------------------------------
class SpanKind(str, Enum):
    """轨迹节点类型。"""

    AGENT = "agent"            # Agent 节点 / 任务
    LLM = "llm"                # 模型调用
    TOOL = "tool"              # 工具调用（审计价值最高）
    RETRIEVAL = "retrieval"    # 检索 / RAG
    HANDOFF = "handoff"        # 多 Agent 交接
    HUMAN = "human"            # 人工介入
    SYSTEM = "system"          # 框架内部事件


class SpanStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"        # 被探针 / 内置防护拦截
    SKIPPED = "skipped"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class VerdictLevel(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    REVIEW = "review"


class Dimension(str, Enum):
    COST = "cost"
    SAFETY = "safety"
    QUALITY = "quality"
    COMPLIANCE = "compliance"


# --------------------------------------------------------------------------
# 各类 Span 的专属载荷
# --------------------------------------------------------------------------
class TokenUsage(BaseModel):
    """token 用量。区分缓存与推理 token，保证成本口径不漂移。"""

    prompt: int = 0
    completion: int = 0
    cached: int = 0
    reasoning: int = 0


class LLMAttrs(BaseModel):
    provider: str = "unknown"
    model: str = "unknown"
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    completion: Optional[str] = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    temperature: Optional[float] = None
    finish_reason: Optional[str] = None
    cost_usd: Optional[float] = None


class ToolAttrs(BaseModel):
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    side_effect: Literal["read", "write", "unknown"] = "unknown"
    permission: Optional[str] = None
    confirmed_by_human: bool = False
    blocked: bool = False


class RetrievalAttrs(BaseModel):
    query: str = ""
    documents: List[Dict[str, Any]] = Field(default_factory=list)
    top_k: Optional[int] = None


class HandoffAttrs(BaseModel):
    from_agent: str
    to_agent: str
    message: Optional[str] = None


# --------------------------------------------------------------------------
# 轨迹
# --------------------------------------------------------------------------
class Span(BaseModel):
    """一个轨迹节点。

    spans 采用平铺 + parent_span_id 引用，而不是嵌套结构：
    流式采集时更容易落盘，树可以在归一化阶段重建。
    """

    span_id: str
    parent_span_id: Optional[str] = None
    kind: SpanKind
    name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    status: SpanStatus = SpanStatus.OK
    input: Any = None
    output: Any = None
    error: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)
    llm: Optional[LLMAttrs] = None
    tool: Optional[ToolAttrs] = None
    retrieval: Optional[RetrievalAttrs] = None
    handoff: Optional[HandoffAttrs] = None
    evidence_ref: Optional[str] = None


class Integrity(BaseModel):
    """证据链：内容摘要 + 前序哈希，保证轨迹不可静默篡改。"""

    content_hash: str
    prev_hash: Optional[str] = None
    signed_by: Optional[str] = None


class Trajectory(BaseModel):
    schema_version: str = "1.0"
    trace_id: str
    session_id: Optional[str] = None
    tenant_id: Optional[str] = None
    agent_id: str
    agent_version: Optional[str] = None
    framework: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    status: SpanStatus = SpanStatus.OK
    spans: List[Span] = Field(default_factory=list)
    source: Literal["file", "otel", "gateway", "mcp", "sdk"] = "file"
    # 完整性声明：审计产品必须诚实，采样 / 截断 / 脱敏都要写出来
    sampled: bool = False
    truncated_fields: List[str] = Field(default_factory=list)
    redacted: bool = False
    received_at: datetime
    integrity: Optional[Integrity] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# 策略与判定
# --------------------------------------------------------------------------
class Rule(BaseModel):
    """一条审计规则。

    action 描述“命中后建议做什么”，与执行位置无关：
    离线只写报告，在线才可能真的拦。
    """

    id: str
    name: str
    dimension: Dimension
    severity: Severity
    enabled: bool = True
    action: Literal["record", "alert", "block"] = "record"
    match: Dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class Citation(BaseModel):
    """结论依据：知识库中的一段可核对文本。"""

    doc_id: str
    title: str
    section: str = ""
    snippet: str
    source: str = ""                        # 出处链接或标准编号
    score: float = 0.0


class RuleHit(BaseModel):
    rule_id: str
    rule_name: str
    dimension: Dimension
    severity: Severity
    span_ref: Optional[str] = None          # 必须能定位到具体 Span
    message: str
    evidence: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    source: Literal["rule", "judge", "human"] = "rule"
    needs_review: bool = False              # 模型判的结论默认需要人工复核
    produced_by: Optional[str] = None       # 产出该结论的审计 Agent 角色
    evidence_quote: Optional[str] = None    # 轨迹原文摘录，必须能在原文中逐字找到
    citations: List[Citation] = Field(default_factory=list)  # 知识库依据


class CostBreakdown(BaseModel):
    total_usd: float = 0.0
    by_model: Dict[str, float] = Field(default_factory=dict)
    by_span: List[Dict[str, Any]] = Field(default_factory=list)
    budget_usd: Optional[float] = None
    over_budget: bool = False


class AuditResult(BaseModel):
    trace_id: str
    verdict: VerdictLevel
    score: Optional[float] = None
    hits: List[RuleHit] = Field(default_factory=list)
    dimension_summary: Dict[str, Any] = Field(default_factory=dict)
    coverage: Dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime


# --------------------------------------------------------------------------
# 审计报告
# --------------------------------------------------------------------------
class AuditReport(BaseModel):
    schema_version: str = "1.0"
    report_id: str
    trace_id: str
    title: str
    agent_id: str
    agent_version: Optional[str] = None
    generated_at: datetime
    verdict: VerdictLevel
    summary: str                    # 给管理者看的一页结论
    cost: CostBreakdown
    safety: Dict[str, Any] = Field(default_factory=dict)
    quality: Dict[str, Any] = Field(default_factory=dict)
    compliance: Dict[str, Any] = Field(default_factory=dict)
    hits: List[RuleHit] = Field(default_factory=list)
    evidence_index: Dict[str, str] = Field(default_factory=dict)
    coverage: Dict[str, Any] = Field(default_factory=dict)
    disclaimer: str = ""
