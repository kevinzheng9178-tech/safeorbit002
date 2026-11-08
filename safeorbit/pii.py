"""敏感信息识别与脱敏。

只收录高置信度模式：宁可漏报，也不要把订单号当身份证。
"""

from __future__ import annotations

import re
from typing import Any, List, Pattern, Tuple

PII_PATTERNS: List[Tuple[str, Pattern[str]]] = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("id_card", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("api_key", re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9]{16,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]


def _mask(text: str) -> str:
    if len(text) <= 4:
        return "***"
    return text[:2] + "***" + text[-2:]


def find_pii(text: str) -> List[Tuple[str, str]]:
    """返回 [(类型, 命中片段)]，片段已做掩码。"""

    hits: List[Tuple[str, str]] = []
    for label, pattern in PII_PATTERNS:
        for match in pattern.findall(text):
            hits.append((label, _mask(match)))
    return hits


def redact_text(text: str) -> Tuple[str, int]:
    count = 0
    for _, pattern in PII_PATTERNS:
        text, replaced = pattern.subn(lambda m: _mask(m.group(0)), text)
        count += replaced
    return text, count


def redact_value(value: Any) -> Tuple[Any, int]:
    """递归脱敏任意 JSON 值。"""

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        total = 0
        items = []
        for item in value:
            cleaned, count = redact_value(item)
            total += count
            items.append(cleaned)
        return items, total
    if isinstance(value, dict):
        total = 0
        result = {}
        for key, item in value.items():
            cleaned, count = redact_value(item)
            total += count
            result[key] = cleaned
        return result, total
    return value, 0
