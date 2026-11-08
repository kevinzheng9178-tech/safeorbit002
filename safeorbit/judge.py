"""LLM 裁判（异步）：只做语义类判断，永远不进同步链路。

设计约束：
- 温度 0、强制 JSON 输出、失败即降级；
- 裁判结论一律 needs_review=True，不冒充硬规则；
- span 引用必须真实存在，否则丢弃该条结论（防止模型编造定位）。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field, PrivateAttr

from . import circuit as circuit_mod
from . import outbound
from .model_registry import ModelRegistry
from .models import Dimension, RuleHit, Severity, SpanKind, Trajectory

_DEFAULT_BASE_URL = "https://api.deepseek.com"
_DEFAULT_MODEL = "deepseek-chat"


class JudgeConfig(BaseModel):
    _api_key: str = PrivateAttr(default="")

    def __init__(self, *, api_key: str = "", **data):
        super().__init__(**data)
        self._api_key = api_key

    @property
    def api_key(self) -> str:
        return self._api_key

    base_url: str = Field(default=_DEFAULT_BASE_URL, repr=False)
    model: str = _DEFAULT_MODEL
    profile_id: Optional[str] = None
    api_key_env: Optional[str] = None
    supports_json_object: bool = True
    timeout_s: float = 30.0
    max_input_chars: int = 20000
    max_retries: int = Field(
        default_factory=lambda: max(0, int(os.getenv("SAFEORBIT_JUDGE_MAX_RETRIES", "1")))
    )

    @classmethod
    def from_registry(cls, registry: ModelRegistry, profile: Optional[str] = None) -> Optional["JudgeConfig"]:
        selected = registry.resolve_judge(profile)
        if selected is None:
            return cls.from_env() if registry.legacy else None
        return cls(
            api_key=selected.api_key, base_url=selected.base_url, model=selected.model,
            profile_id=selected.id, api_key_env=selected.api_key_env,
            supports_json_object=selected.supports_json_object,
        )

    @classmethod
    def from_env(cls, model: Optional[str] = None) -> Optional["JudgeConfig"]:
        api_key = os.getenv("SAFEORBIT_JUDGE_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            return None
        return cls(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", _DEFAULT_BASE_URL),
            model=model or os.getenv("DEEPSEEK_MODEL", _DEFAULT_MODEL),
        )


class JudgeOutcome(BaseModel):
    status: str = "ok"                # ok / skipped / error
    hits: List[RuleHit] = Field(default_factory=list)
    note: str = ""


_SYSTEM_PROMPT = """你是 SafeOrbit 的 Agent 轨迹审计专家。
只依据用户提供的轨迹做判断，不要臆测轨迹之外的信息。
重点检查语义类问题：幻觉、答非所问、任务未完成、工具误用、流程缺陷。
输出严格 JSON：
{"findings":[{"type":"hallucination|off_topic|incomplete|tool_misuse|workflow","span_id":"轨迹中的span_id","severity":"low|medium|high","detail":"一句话说明","confidence":0.0-1.0}]}
没有发现则输出 {"findings":[]}。"""


def system_prompt() -> str:
    """对外展示用的裁判提示词（只读，便于核对提示词工程）。"""

    return _SYSTEM_PROMPT


def _truncate(value: Any, limit: int = 2000) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "...[truncated]"
    return value


def _condensed_trajectory(trajectory: Trajectory, max_chars: int) -> str:
    spans: List[Dict[str, Any]] = []
    for span in trajectory.spans:
        item: Dict[str, Any] = {
            "span_id": span.span_id,
            "parent_span_id": span.parent_span_id,
            "kind": span.kind.value,
            "name": span.name,
            "status": span.status.value,
        }
        if span.llm is not None:
            item["llm"] = {
                "model": span.llm.model,
                "messages": _truncate(span.llm.messages),
                "completion": _truncate(span.llm.completion),
            }
        if span.tool is not None:
            item["tool"] = {
                "tool_name": span.tool.tool_name,
                "arguments": _truncate(span.tool.arguments),
                "result": _truncate(span.tool.result),
            }
        if span.retrieval is not None:
            item["retrieval"] = {
                "query": span.retrieval.query,
                "documents": _truncate(span.retrieval.documents),
            }
        if span.handoff is not None:
            item["handoff"] = span.handoff.model_dump()
        spans.append(item)

    payload = {
        "trace_id": trajectory.trace_id,
        "agent_id": trajectory.agent_id,
        "agent_version": trajectory.agent_version,
        "framework": trajectory.framework,
        "spans": spans,
    }
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return text[:max_chars]


def _parse_hits(content: str, trajectory: Trajectory) -> List[RuleHit]:
    data = json.loads(content)
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise ValueError("裁判输出结构无效")

    valid_ids = {span.span_id for span in trajectory.spans}
    severity_map = {
        "critical": Severity.CRITICAL,
        "high": Severity.HIGH,
        "medium": Severity.MEDIUM,
        "low": Severity.LOW,
    }

    hits: List[RuleHit] = []
    for finding in data["findings"]:
        if not isinstance(finding, dict):
            raise ValueError("裁判发现项结构无效")
        span_id = finding.get("span_id")
        if not isinstance(span_id, str) or span_id not in valid_ids:
            continue  # 模型编造的定位，一律丢弃
        confidence = finding.get("confidence", 0.6)
        if (finding.get("type") not in {"hallucination", "off_topic", "incomplete", "tool_misuse", "workflow"}
                or finding.get("severity") not in severity_map
                or not isinstance(finding.get("detail"), str)
                or not finding["detail"].strip()
                or isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not 0 <= confidence <= 1):
            raise ValueError("裁判发现项字段无效")
        hits.append(
            RuleHit(
                rule_id="judge.%s" % finding.get("type", "quality"),
                rule_name="语义裁判：%s" % finding.get("type", "quality"),
                dimension=Dimension.QUALITY,
                severity=severity_map.get(str(finding.get("severity", "")).lower(), Severity.MEDIUM),
                span_ref=span_id,
                message=str(finding.get("detail", "")).strip() or "裁判发现问题",
                evidence={"type": finding.get("type"), "raw": finding},
                confidence=float(finding.get("confidence", 0.6)),
                source="judge",
                needs_review=True,
            )
        )
    return hits


async def judge_trajectory(
    trajectory: Trajectory,
    config: Optional[JudgeConfig] = None,
) -> JudgeOutcome:
    if config is None:
        config = JudgeConfig.from_env()
    if config is None:
        return JudgeOutcome(status="skipped", note="未配置 SAFEORBIT_JUDGE_API_KEY / DEEPSEEK_API_KEY，跳过语义裁判")
    identity = "profile=%s model=%s" % (config.profile_id or "legacy", config.model)
    if not config.api_key:
        return JudgeOutcome(
            status="skipped",
            note="%s：未配置 %s，跳过语义裁判" % (identity, config.api_key_env or "裁判密钥"),
        )

    payload = {
        "model": config.model,
        "temperature": 0,
        "max_tokens": 800,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "请审计以下轨迹：\n" + _condensed_trajectory(trajectory, config.max_input_chars),
            },
        ],
    }

    if config.supports_json_object:
        payload["response_format"] = {"type": "json_object"}

    async def _request_completion() -> dict:
        async with outbound.async_client(timeout=config.timeout_s) as client:
            response = await client.post(
                config.base_url.rstrip("/") + "/chat/completions",
                headers={
                    "Authorization": "Bearer " + config.api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    breaker = circuit_mod.circuit(
        "judge:%s" % (config.profile_id or config.model),
        circuit_mod.CircuitConfig(
            failure_threshold=int(os.getenv("SAFEORBIT_JUDGE_FAILURE_THRESHOLD", "5")),
            timeout=config.timeout_s + 5,
        ),
    )

    last_error = "响应结构无效"
    attempts = 1 + max(0, int(config.max_retries))
    for _ in range(attempts):  # 重试次数可配置，避免把失败拖成超长等待
        try:
            body = await breaker.call(_request_completion)
            content = body["choices"][0]["message"]["content"]
            hits = _parse_hits(content, trajectory)
            note = "%s：裁判完成，产生 %d 条建议复核结论" % (identity, len(hits))
            if hits:
                note += "；模型结论需人工复核"
            return JudgeOutcome(status="ok", hits=hits, note=note)
        except circuit_mod.CircuitOpenError:
            return JudgeOutcome(status="error", note="%s：裁判熔断器已打开，临时跳过语义裁判" % identity)
        except httpx.HTTPStatusError as exc:
            last_error = "HTTP %d" % exc.response.status_code
        except httpx.TimeoutException:
            last_error = "请求超时"
        except httpx.RequestError:
            last_error = "网络错误"
        except Exception:  # 裁判失败绝不能拖垮报告，也不能回显上游正文或异常细节
            last_error = "响应结构或 JSON 无效"

    return JudgeOutcome(status="error", note="%s：裁判调用失败：%s" % (identity, last_error))


def collect_tool_stats(trajectory: Trajectory) -> Dict[str, Any]:
    """给报告用的工具侧统计，避免统计逻辑散落在多个地方。"""

    total = 0
    writes = 0
    blocked = 0
    for span in trajectory.spans:
        if span.kind != SpanKind.TOOL or span.tool is None:
            continue
        total += 1
        if span.tool.side_effect == "write":
            writes += 1
        if span.tool.blocked:
            blocked += 1
    return {"tool_calls": total, "write_calls": writes, "blocked_calls": blocked}
