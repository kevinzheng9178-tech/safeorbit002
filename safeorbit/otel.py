"""OTLP/JSON → 统一轨迹模型。

P1 只接收 OTLP JSON（protobuf 稍后再说），并按 OpenTelemetry GenAI
语义约定做最小映射：模型、token、工具名、工具参数、错误状态。
"""

from __future__ import annotations

import base64
import json
import struct
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .models import (
    LLMAttrs,
    RetrievalAttrs,
    Span,
    SpanKind,
    SpanStatus,
    TokenUsage,
    ToolAttrs,
    Trajectory,
)


def _value_of(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in value:
            raw = value[key]
            if key == "intValue":
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    return raw
            if key == "doubleValue":
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    return raw
            return raw
    if "arrayValue" in value:
        return [_value_of(item) for item in value["arrayValue"].get("values", [])]
    return None


def _attributes(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {item.get("key"): _value_of(item.get("value")) for item in items or []}


def _timestamp(nano: Optional[str]) -> datetime:
    if not nano:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(int(nano) / 1_000_000_000, tz=timezone.utc)


def _json_or_text(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _span_kind(attributes: Dict[str, Any]) -> SpanKind:
    operation = str(attributes.get("gen_ai.operation.name", "")).lower()
    if operation in ("chat", "text_completion", "embeddings"):
        return SpanKind.LLM
    if operation == "execute_tool":
        return SpanKind.TOOL
    if operation in ("invoke_agent", "create_agent"):
        return SpanKind.AGENT
    if operation == "handoff":
        return SpanKind.HANDOFF
    if attributes.get("gen_ai.tool.name"):
        return SpanKind.TOOL
    if attributes.get("gen_ai.request.model"):
        return SpanKind.LLM
    return SpanKind.SYSTEM


def _to_span(raw: Dict[str, Any]) -> Span:
    attributes = _attributes(raw.get("attributes"))
    kind = _span_kind(attributes)
    status_code = (raw.get("status") or {}).get("code")
    status = SpanStatus.ERROR if status_code == 2 else SpanStatus.OK

    span = Span(
        span_id=raw.get("spanId") or "span-unknown",
        parent_span_id=raw.get("parentSpanId") or None,
        kind=kind,
        name=raw.get("name") or kind.value,
        start_time=_timestamp(raw.get("startTimeUnixNano")),
        end_time=_timestamp(raw.get("endTimeUnixNano")) if raw.get("endTimeUnixNano") else None,
        status=status,
        error=(raw.get("status") or {}).get("message"),
        input=_json_or_text(attributes.get("gen_ai.prompt")),
        output=_json_or_text(attributes.get("gen_ai.completion")),
        attributes=attributes,
    )

    if kind == SpanKind.LLM:
        span.llm = LLMAttrs(
            provider=str(attributes.get("gen_ai.system", "unknown")),
            model=str(attributes.get("gen_ai.request.model") or attributes.get("gen_ai.response.model") or "unknown"),
            messages=_messages(attributes),
            completion=attributes.get("gen_ai.completion") or attributes.get("gen_ai.output"),
            usage=TokenUsage(
                prompt=int(attributes.get("gen_ai.usage.input_tokens") or 0),
                completion=int(attributes.get("gen_ai.usage.output_tokens") or 0),
                cached=int(attributes.get("gen_ai.usage.cached_tokens") or 0),
                reasoning=int(attributes.get("gen_ai.usage.reasoning_tokens") or 0),
            ),
        )

    if kind == SpanKind.TOOL:
        span.tool = ToolAttrs(
            tool_name=str(attributes.get("gen_ai.tool.name") or span.name),
            arguments=_json_or_text(attributes.get("gen_ai.tool.call.arguments")) or {},
            result=_json_or_text(attributes.get("gen_ai.tool.call.result")),
            side_effect=str(attributes.get("safeorbit.tool.side_effect", "unknown")),
        )

    if kind == SpanKind.RETRIEVAL:
        span.retrieval = RetrievalAttrs(query=str(attributes.get("gen_ai.retrieval.query", "")))

    return span


def _messages(attributes: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("gen_ai.input.messages", "gen_ai.prompt"):
        raw = _json_or_text(attributes.get(key))
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, str) and raw:
            return [{"role": "user", "content": raw}]
    return []


def parse_otlp_json(payload: Dict[str, Any]) -> List[Trajectory]:
    """把一份 OTLP JSON 拆成若干条轨迹（按 traceId 分组）。"""

    grouped: Dict[str, List[Span]] = {}
    services: Dict[str, str] = {}

    for resource_span in payload.get("resourceSpans", []) or []:
        resource_attributes = _attributes((resource_span.get("resource") or {}).get("attributes"))
        service_name = str(resource_attributes.get("service.name", "otel-agent"))

        for scope_span in resource_span.get("scopeSpans", []) or []:
            for raw in scope_span.get("spans", []) or []:
                trace_id = raw.get("traceId") or "otel-unknown"
                grouped.setdefault(trace_id, []).append(_to_span(raw))
                services.setdefault(trace_id, service_name)

    trajectories: List[Trajectory] = []
    for trace_id, spans in grouped.items():
        starts = [span.start_time for span in spans]
        ends = [span.end_time for span in spans if span.end_time is not None]
        trajectories.append(
            Trajectory(
                trace_id=trace_id,
                agent_id=services.get(trace_id, "otel-agent"),
                started_at=min(starts),
                ended_at=max(ends) if ends else None,
                status=SpanStatus.ERROR if any(s.status == SpanStatus.ERROR for s in spans) else SpanStatus.OK,
                spans=spans,
                source="otel",
                received_at=datetime.now(timezone.utc),
            )
        )
    return trajectories


# --------------------------------------------------------------------------
# OTLP protobuf 最小解码器
# --------------------------------------------------------------------------
def _varint(data: bytes, position: int):
    result = 0
    shift = 0
    while position < len(data):
        byte = data[position]
        position += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, position
        shift += 7
        if shift > 70:
            break
    raise ValueError("protobuf varint 不完整")


def _wire_fields(data: bytes) -> Dict[int, List[Any]]:
    fields: Dict[int, List[Any]] = {}
    position = 0
    while position < len(data):
        key, position = _varint(data, position)
        number = key >> 3
        wire = key & 0x07
        if number == 0:
            raise ValueError("protobuf 字段编号无效")
        if wire == 0:
            value, position = _varint(data, position)
        elif wire == 1:
            value = data[position:position + 8]
            position += 8
        elif wire == 2:
            length, position = _varint(data, position)
            value = data[position:position + length]
            position += length
        elif wire == 5:
            value = data[position:position + 4]
            position += 4
        else:
            raise ValueError("不支持的 protobuf wire type")
        fields.setdefault(number, []).append((wire, value))
    return fields


def _last(fields: Dict[int, List[Any]], number: int) -> Any:
    values = fields.get(number)
    return values[-1][1] if values else None


def _all(fields: Dict[int, List[Any]], number: int) -> List[Any]:
    return [value for _, value in fields.get(number, [])]


def _text(value: Any) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else ""


def _signed_int64(value: int) -> int:
    return value - (1 << 64) if value >= (1 << 63) else value


def _fixed64(value: Any) -> int:
    if isinstance(value, bytes) and len(value) == 8:
        return struct.unpack("<Q", value)[0]
    return int(value or 0)


def _decode_any(value: bytes) -> Dict[str, Any]:
    fields = _wire_fields(value)
    if 1 in fields:
        return {"stringValue": _text(_last(fields, 1))}
    if 2 in fields:
        return {"boolValue": bool(_last(fields, 2))}
    if 3 in fields:
        return {"intValue": str(_signed_int64(int(_last(fields, 3) or 0)))}
    if 4 in fields:
        raw = _last(fields, 4)
        return {"doubleValue": struct.unpack("<d", raw)[0] if len(raw) == 8 else 0.0}
    if 5 in fields:
        array = _wire_fields(_last(fields, 5))
        return {"arrayValue": {"values": [_decode_any(item) for item in _all(array, 1)]}}
    if 6 in fields:
        pairs = _wire_fields(_last(fields, 6))
        return {"kvlistValue": {"values": [_decode_key_value(item) for item in _all(pairs, 1)]}}
    if 7 in fields:
        return {"bytesValue": base64.b64encode(_last(fields, 7)).decode("ascii")}
    return {}


def _decode_key_value(value: bytes) -> Dict[str, Any]:
    fields = _wire_fields(value)
    return {
        "key": _text(_last(fields, 1)) if 1 in fields else "",
        "value": _decode_any(_last(fields, 2)) if 2 in fields else {},
    }


def _decode_resource(value: bytes) -> Dict[str, Any]:
    fields = _wire_fields(value)
    return {"attributes": [_decode_key_value(item) for item in _all(fields, 1)]}


def _decode_status(value: bytes) -> Dict[str, Any]:
    fields = _wire_fields(value)
    return {
        "code": int(_last(fields, 3) or 0),
        "message": _text(_last(fields, 2)) if 2 in fields else "",
    }


def _decode_span(value: bytes) -> Dict[str, Any]:
    fields = _wire_fields(value)
    span: Dict[str, Any] = {
        "traceId": (_last(fields, 1) or b"").hex(),
        "spanId": (_last(fields, 2) or b"").hex(),
        "name": _text(_last(fields, 5)) if 5 in fields else "",
        "attributes": [_decode_key_value(item) for item in _all(fields, 9)],
    }
    parent = _last(fields, 4) if 4 in fields else None
    if parent:
        span["parentSpanId"] = parent.hex()
    if 7 in fields:
        span["startTimeUnixNano"] = str(_fixed64(_last(fields, 7)))
    if 8 in fields:
        span["endTimeUnixNano"] = str(_fixed64(_last(fields, 8)))
    if 15 in fields:
        span["status"] = _decode_status(_last(fields, 15))
    return span


def parse_otlp_protobuf(payload: bytes) -> List[Trajectory]:
    """解码 OTLP/HTTP protobuf，转换为与 JSON 相同的中间结构后复用映射。"""

    fields = _wire_fields(payload)
    resource_spans = []
    for raw in _all(fields, 1):
        resource_fields = _wire_fields(raw)
        resource = (
            _decode_resource(_last(resource_fields, 1))
            if 1 in resource_fields
            else {"attributes": []}
        )
        scopes = []
        for scope_raw in _all(resource_fields, 2):
            scope_fields = _wire_fields(scope_raw)
            scopes.append({"spans": [_decode_span(item) for item in _all(scope_fields, 2)]})
        resource_spans.append({"resource": resource, "scopeSpans": scopes})
    return parse_otlp_json({"resourceSpans": resource_spans})
