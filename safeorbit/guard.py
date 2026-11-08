"""模型内容安全 Guard：提示注入、数据外泄、输出泄露与 RAG 风险。

只做高置信度模式识别，结论必须能定位到具体消息、文档或 span。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .models import Dimension, RuleHit, Severity
from .pii import find_pii, redact_text

logger = logging.getLogger("safeorbit.guard")


@dataclass
class GuardFinding:
    code: str
    severity: str
    message: str
    evidence: str
    location: str
    confidence: float = 0.9
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
            "location": self.location,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class _Pattern:
    code: str
    severity: str
    message: str
    regex: re.Pattern
    confidence: float = 0.9


_PATTERNS: List[_Pattern] = [
    _Pattern(
        "prompt_injection",
        "high",
        "疑似提示注入：试图覆盖系统指令",
        re.compile(
            r"(?i)(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above)\s+"
            r"(instructions|prompts?|rules?)|忽略(之前|以上|前面)的?(所有)?(指令|提示|规则)|"
            r"忽略(系统|开发者)(消息|指令)"
        ),
        0.95,
    ),
    _Pattern(
        "prompt_injection",
        "high",
        "疑似提示注入：试图获取系统提示或内部指令",
        re.compile(
            r"(?i)(reveal|show|print|repeat|leak)\s+(me\s+)?(your\s+)?"
            r"(system\s+prompt|instructions|developer\s+message|hidden\s+rules)|"
            r"(输出|泄露|打印|重复)(你的)?(系统提示|系统指令|开发者消息|隐藏规则)"
        ),
        0.95,
    ),
    _Pattern(
        "prompt_injection",
        "medium",
        "疑似越狱角色扮演",
        re.compile(
            r"(?i)\b(jailbreak|DAN\s+mode|do\s+anything\s+now|you\s+are\s+now\s+unrestricted|"
            r"act\s+as\s+an?\s+unrestricted)\b|越狱模式|无限制模式|DAN\s*模式|不受限制"
        ),
        0.8,
    ),
    _Pattern(
        "data_exfiltration",
        "high",
        "疑似数据外泄指令：要求把数据发送到外部地址",
        re.compile(
            r"(?i)(send|upload|post|exfiltrate|forward)\s+.{0,40}\b(to|at)\s+"
            r"(https?://|ftp://|[\w.-]+@)|把.{0,30}(发送|上传|转发|外发).{0,20}"
            r"(到|至).{0,20}(http|邮箱|地址)"
        ),
        0.85,
    ),
    _Pattern(
        "data_exfiltration",
        "medium",
        "疑似凭证窃取指令",
        re.compile(
            r"(?i)(print|show|reveal|dump|list)\s+.{0,30}"
            r"(api\s*keys?|access\s*tokens?|passwords?|credentials?|secrets?)|"
            r"(输出|打印|列出|泄露).{0,20}(密钥|口令|密码|凭据|token)"
        ),
        0.8,
    ),
    _Pattern(
        "data_exfiltration",
        "medium",
        "疑似批量导出个人敏感信息",
        re.compile(
            r"(?i)(list|export|dump|show)\s+.{0,30}(phone|id\s*card|personal\s+data|address)|"
            r"(列出|导出|输出|提供).{0,20}(手机号|身份证|个人信息|住址|家庭地址)"
        ),
        0.85,
    ),
    _Pattern(
        "secret_leak",
        "critical",
        "输出中出现疑似密钥或凭据",
        re.compile(
            r"(?i)(so_agent_[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9]{16,}|"
            r"AKIA[0-9A-Z]{16}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|"
            r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
        ),
        0.95,
    ),
    _Pattern(
        "prompt_leak",
        "high",
        "输出中疑似回显了系统提示或内部规则",
        re.compile(
            r"(?i)(my\s+system\s+prompt|system\s+prompt\s+is|"
            r"developer\s+message\s+is|我的系统提示是|系统指令是|内部规则是)"
        ),
        0.75,
    ),
]

_RAG_PATTERNS: List[_Pattern] = [
    _Pattern(
        "rag_injection",
        "high",
        "检索文档中包含疑似提示注入内容",
        re.compile(
            r"(?i)(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above)\s+"
            r"(instructions|prompts?|rules?)|忽略(之前|以上|前面)的?(所有)?(指令|提示)|"
            r"(system\s+prompt|developer\s+message)|系统提示|开发者消息"
        ),
        0.9,
    ),
    _Pattern(
        "rag_injection",
        "medium",
        "检索文档要求执行高风险动作",
        re.compile(
            r"(?i)(delete|drop|truncate|transfer|refund|send)\s+.{0,30}"
            r"(all|database|table|money|funds)|(删除|清空|转账|退款).{0,20}(所有|数据库|资金)"
        ),
        0.75,
    ),
]


def _mask(text: str, limit: int = 160) -> str:
    masked, _ = redact_text(text)
    return masked[:limit]


def scan_text(
    text: Any,
    location: str,
    *,
    patterns: Optional[Iterable[_Pattern]] = None,
    include_pii: bool = False,
) -> List[GuardFinding]:
    if isinstance(text, list):
        text = " ".join(str(item) for item in text)
    if not isinstance(text, str) or not text.strip():
        return []
    findings: List[GuardFinding] = []
    for pattern in patterns or _PATTERNS:
        match = pattern.regex.search(text)
        if not match:
            continue
        findings.append(
            GuardFinding(
                code=pattern.code,
                severity=pattern.severity,
                message=pattern.message,
                evidence=_mask(match.group(0)),
                location=location,
                confidence=pattern.confidence,
            )
        )
    if include_pii:
        for pii_type, sample in find_pii(text)[:5]:
            findings.append(
                GuardFinding(
                    code="pii_leak",
                    severity="high",
                    message="输出中出现敏感信息：%s" % pii_type,
                    evidence=sample,
                    location=location,
                    confidence=0.95,
                    metadata={"pii_type": pii_type},
                )
            )
    return findings


def check_messages(messages: Any) -> List[GuardFinding]:
    findings: List[GuardFinding] = []
    if not isinstance(messages, list):
        return findings
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "unknown")
        content = message.get("content")
        findings.extend(scan_text(content, "messages[%d].content（%s）" % (index, role)))
        if role == "system":
            findings.extend(
                scan_text(
                    content,
                    "messages[%d].content（system）" % index,
                    patterns=[p for p in _PATTERNS if p.code == "prompt_leak"],
                )
            )
    return findings


def check_output(text: Any) -> List[GuardFinding]:
    return scan_text(text, "completion", include_pii=True)


def check_retrieval(query: Any, documents: Any) -> List[GuardFinding]:
    findings = scan_text(query, "retrieval.query")
    if not isinstance(documents, list):
        return findings
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            continue
        content = document.get("content") or document.get("text") or ""
        findings.extend(
            scan_text(content, "retrieval.documents[%d]" % index, patterns=_RAG_PATTERNS)
        )
        findings.extend(
            scan_text(
                content,
                "retrieval.documents[%d]" % index,
                include_pii=True,
            )
        )
        classification = str(document.get("classification") or "").lower()
        if classification in ("secret", "confidential", "restricted"):
            findings.append(
                GuardFinding(
                    code="rag_sensitive_document",
                    severity="high",
                    message="检索到高密级文档（%s）" % classification,
                    evidence=str(document.get("doc_id") or document.get("source") or "")[:120],
                    location="retrieval.documents[%d]" % index,
                    confidence=1.0,
                    metadata={"classification": classification},
                )
            )
        elif classification == "internal":
            findings.append(
                GuardFinding(
                    code="rag_sensitive_document",
                    severity="medium",
                    message="检索到内部文档",
                    evidence=str(document.get("doc_id") or document.get("source") or "")[:120],
                    location="retrieval.documents[%d]" % index,
                    confidence=0.85,
                    metadata={"classification": classification},
                )
            )
    return findings


_SEVERITY = {
    "critical": Severity.CRITICAL,
    "high": Severity.HIGH,
    "medium": Severity.MEDIUM,
    "low": Severity.LOW,
    "info": Severity.INFO,
}


def to_rule_hits(
    findings: Iterable[GuardFinding],
    span_ref: Optional[str],
) -> List[RuleHit]:
    hits: List[RuleHit] = []
    for finding in findings:
        hits.append(
            RuleHit(
                rule_id="guard.%s" % finding.code,
                rule_name="内容安全：%s" % finding.code,
                dimension=Dimension.SAFETY,
                severity=_SEVERITY.get(finding.severity, Severity.MEDIUM),
                span_ref=span_ref,
                message=finding.message,
                evidence={
                    "location": finding.location,
                    "evidence": finding.evidence,
                    "confidence": finding.confidence,
                    **finding.metadata,
                },
                confidence=finding.confidence,
                source="rule",
                needs_review=False,
            )
        )
    if hits:
        logger.warning(
            "内容安全命中 span=%s count=%d codes=%s",
            span_ref or "-",
            len(hits),
            ",".join(sorted({hit.rule_id for hit in hits})),
        )
    return hits
