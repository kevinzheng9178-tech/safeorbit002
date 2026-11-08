"""把一次 OpenAI 兼容调用变成一条 SafeOrbit 轨迹。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from .models import (
    LLMAttrs,
    Span,
    SpanKind,
    SpanStatus,
    TokenUsage,
    Trajectory,
)


def _usage(response: Dict[str, Any]) -> TokenUsage:
    raw = response.get("usage") or {}
    details = raw.get("completion_tokens_details") or {}
    return TokenUsage(
        prompt=int(raw.get("prompt_tokens") or 0),
        completion=int(raw.get("completion_tokens") or 0),
        cached=int(raw.get("prompt_cache_hit_tokens") or 0),
        reasoning=int(details.get("reasoning_tokens") or raw.get("reasoning_tokens") or 0),
    )


def _completion(response: Dict[str, Any]) -> Optional[str]:
    choices = response.get("choices") or []
    if not choices:
        return None
    message = choices[0].get("message") or {}
    return message.get("content") or message.get("reasoning_content")


def build_gateway_trajectory(
    request_body: Dict[str, Any],
    response_body: Dict[str, Any],
    *,
    agent_id: str,
    session_id: Optional[str],
    trace_id: Optional[str],
    started_at: datetime,
    ended_at: datetime,
    status: SpanStatus = SpanStatus.OK,
    error: Optional[str] = None,
    profile_id: Optional[str] = None,
    provider: str = "openai-compatible",
    mock: bool = False,
) -> Trajectory:
    messages = [item for item in request_body.get("messages", []) if isinstance(item, dict)]
    span = Span(
        span_id="llm-" + uuid4().hex[:12],
        kind=SpanKind.LLM,
        name="chat.completions",
        start_time=started_at,
        end_time=ended_at,
        status=status,
        error=error,
        input={"message_count": len(messages), "model": request_body.get("model")},
        output=_completion(response_body),
        llm=LLMAttrs(
            provider="mock" if mock else provider,
            model=str(request_body.get("model") or response_body.get("model") or "unknown"),
            messages=messages,
            completion=_completion(response_body),
            usage=_usage(response_body),
            temperature=request_body.get("temperature"),
        ),
        attributes={
            "gateway": True,
            "profile_id": profile_id,
            "provider": "mock" if mock else provider,
            "upstream_model": request_body.get("model"),
            "mock": mock,
            "stream": bool(request_body.get("stream")),
        },
    )
    return Trajectory(
        trace_id=trace_id or "gw-" + uuid4().hex[:12],
        session_id=session_id or trace_id,
        agent_id=agent_id,
        started_at=started_at,
        ended_at=ended_at,
        status=status,
        spans=[span],
        source="gateway",
        received_at=datetime.now().astimezone(),
        metadata={"endpoint": "/v1/chat/completions"},
    )


def mock_completion(request_body: Dict[str, Any]) -> Dict[str, Any]:
    """离线演示用的假上游：不联网也能演示“真实 Agent 接入”。"""

    messages = request_body.get("messages") or []
    prompt_chars = sum(len(str(item.get("content", ""))) for item in messages if isinstance(item, dict))
    return {
        "id": "mock-" + uuid4().hex[:8],
        "object": "chat.completion",
        "model": request_body.get("model", "mock-model"),
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "已收到您的请求。为避免信息泄露，敏感信息请勿直接提供。",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": max(prompt_chars // 2, 20),
            "completion_tokens": 24,
            "total_tokens": max(prompt_chars // 2, 20) + 24,
        },
    }
