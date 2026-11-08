"""P3 多框架适配：通用 SDK、OpenTelemetry、LangChain 回调与接入片段。

原则：适配器只负责采集，不改变被审计 Agent 的业务逻辑；
所有上报都走 /v1/ingest，服务端按 trace_id 合并。
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Protocol, Tuple
from uuid import uuid4

import httpx

from .models import (
    LLMAttrs,
    Span,
    SpanKind,
    SpanStatus,
    ToolAttrs,
    TokenUsage,
    Trajectory,
)


class SpanRecorder(Protocol):
    def emit(self, span: Span) -> None:
        ...


def _now() -> datetime:
    return datetime.now().astimezone()


class ToolBlocked(Exception):
    """运行时鉴权拒绝执行工具。"""

    def __init__(self, message: str, decision: Dict[str, Any]):
        super().__init__(message)
        self.decision = decision


class SafeOrbitClient:
    """最小 SDK：一行配置，之后每次模型 / 工具调用上报一个 span。"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        agent_id: str = "sdk-agent",
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        timeout: float = 10.0,
        api_key: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.trace_id = trace_id or "sdk-" + uuid4().hex[:10]
        self.session_id = session_id or "sess-" + uuid4().hex[:8]
        self.timeout = timeout
        self.api_key = (api_key if api_key is not None else os.environ.get("SAFEORBIT_AGENT_KEY", "")).strip()

    def _url(self, path: str) -> str:
        if self.base_url.endswith("/v1"):
            return self.base_url + path
        return self.base_url + "/v1" + path

    def headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise ValueError("缺少 Agent 凭据：请设置 SAFEORBIT_AGENT_KEY 或显式传入 api_key")
        return {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
            "x-safeorbit-trace-id": self.trace_id,
            "x-safeorbit-session-id": self.session_id,
            "x-safeorbit-agent-id": self.agent_id,
        }

    def _post(self, path: str, payload: Dict[str, Any]) -> Any:
        # 本机服务必须绕过系统代理，否则会被代理拦成 502
        response = httpx.post(
            self._url(path),
            json=payload,
            timeout=self.timeout,
            trust_env=False,
            headers=self.headers(),
        )
        response.raise_for_status()
        return response.json()

    def emit(self, span: Span) -> None:
        trajectory = Trajectory(
            trace_id=self.trace_id,
            session_id=self.session_id,
            agent_id=self.agent_id,
            framework="safeorbit-sdk",
            started_at=span.start_time,
            ended_at=span.end_time or _now(),
            spans=[span],
            source="sdk",
            received_at=_now(),
            metadata={"adapter": "generic"},
        )
        self._post("/ingest", trajectory.model_dump(mode="json"))

    def tool_span(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        *,
        result: Any = None,
        side_effect: str = "unknown",
        confirmed_by_human: bool = False,
        blocked: bool = False,
        status: SpanStatus = SpanStatus.OK,
        error: Optional[str] = None,
        started_at: Optional[datetime] = None,
        span_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
    ) -> Span:
        span = Span(
            span_id=span_id or "tool-" + uuid4().hex[:10],
            parent_span_id=parent_span_id,
            kind=SpanKind.TOOL,
            name=tool_name,
            start_time=started_at or _now(),
            end_time=_now(),
            status=status,
            error=error,
            tool=ToolAttrs(
                tool_name=tool_name,
                arguments=arguments or {},
                result=result,
                side_effect=side_effect,  # type: ignore[arg-type]
                confirmed_by_human=confirmed_by_human,
                blocked=blocked,
            ),
            attributes={"adapter": "sdk"},
        )
        self.emit(span)
        return span

    def llm_span(
        self,
        model: str,
        messages: Optional[List[Dict[str, Any]]] = None,
        *,
        completion: Optional[str] = None,
        usage: Optional[Dict[str, int]] = None,
        provider: str = "openai-compatible",
        started_at: Optional[datetime] = None,
        span_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Span:
        usage_data = usage or {}
        span = Span(
            span_id=span_id or "llm-" + uuid4().hex[:10],
            parent_span_id=parent_span_id,
            kind=SpanKind.LLM,
            name="chat.completions",
            start_time=started_at or _now(),
            end_time=_now(),
            status=SpanStatus.ERROR if error else SpanStatus.OK,
            error=error,
            llm=LLMAttrs(
                provider=provider,
                model=model,
                messages=messages or [],
                completion=completion,
                usage=TokenUsage(
                    prompt=int(usage_data.get("prompt_tokens") or usage_data.get("prompt") or 0),
                    completion=int(
                        usage_data.get("completion_tokens") or usage_data.get("completion") or 0
                    ),
                    cached=int(usage_data.get("cached") or 0),
                    reasoning=int(usage_data.get("reasoning") or 0),
                ),
            ),
            attributes={"adapter": "sdk"},
        )
        self.emit(span)
        return span

    def check_tool(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        *,
        side_effect: str = "unknown",
        confirmed_by_human: bool = False,
        estimated_cost_usd: Optional[float] = None,
        required_scope: Optional[str] = None,
        approval_id: Optional[str] = None,
        strict: bool = False,
    ) -> Dict[str, Any]:
        """执行工具前询问服务端：allow / review / deny。"""

        payload = {
            "agent_id": self.agent_id,
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "tool_name": tool_name,
            "arguments": arguments or {},
            "side_effect": side_effect,
            "confirmed_by_human": confirmed_by_human,
            "estimated_cost_usd": estimated_cost_usd,
        }
        if required_scope:
            payload["required_scope"] = required_scope
        if approval_id:
            payload["approval_id"] = approval_id
        decision = self._post("/tool/check", payload)
        if not isinstance(decision, dict) or decision.get("decision") not in ("allow", "review", "deny"):
            raise ValueError("SafeOrbit 工具检查返回无效决定，拒绝执行")
        blocked = decision.get("decision") == "deny" or (strict and decision.get("decision") == "review")
        if blocked:
            detail = "；".join(decision.get("reasons") or [])
            if decision.get("approval_id"):
                detail += "（审批单 %s）" % decision["approval_id"]
            raise ToolBlocked(
                "工具 %s 被 SafeOrbit 拦截：%s" % (tool_name, detail),
                decision,
            )
        return decision


@contextmanager
def traced_tool(
    client: SafeOrbitClient,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
    *,
    side_effect: str = "unknown",
    confirmed_by_human: bool = False,
    required_scope: Optional[str] = None,
    approval_id: Optional[str] = None,
    check: bool = True,
) -> Iterator[SafeOrbitClient]:
    """工具调用上下文：执行前鉴权，执行后自动上报结果。"""

    if check:
        client.check_tool(
            tool_name,
            arguments,
            side_effect=side_effect,
            confirmed_by_human=confirmed_by_human,
            required_scope=required_scope,
            approval_id=approval_id,
        )
    started_at = _now()
    try:
        yield client
    except Exception as exc:
        client.tool_span(
            tool_name,
            arguments,
            side_effect=side_effect,
            confirmed_by_human=confirmed_by_human,
            status=SpanStatus.ERROR,
            error=str(exc),
            started_at=started_at,
        )
        raise
    else:
        client.tool_span(
            tool_name,
            arguments,
            side_effect=side_effect,
            confirmed_by_human=confirmed_by_human,
            started_at=started_at,
        )


def record_openai_response(
    client: SafeOrbitClient,
    model: str,
    messages: List[Dict[str, Any]],
    response: Dict[str, Any],
    *,
    started_at: Optional[datetime] = None,
) -> Span:
    """把一次 OpenAI 兼容响应转成 LLM span（供不使用网关的采集方式）。"""

    completion = None
    choices = response.get("choices") or []
    if choices:
        completion = (choices[0].get("message") or {}).get("content")
    return client.llm_span(
        model,
        messages,
        completion=completion,
        usage=response.get("usage") or {},
        provider=str(response.get("provider") or "openai-compatible"),
        started_at=started_at,
    )


def langchain_callback_handler(client: SafeOrbitClient) -> Optional[Any]:
    """可选接入 LangChain；未安装 langchain-core 时返回 None。"""

    try:
        from langchain_core.callbacks import BaseCallbackHandler  # type: ignore
    except Exception:
        return None

    class SafeOrbitCallbackHandler(BaseCallbackHandler):  # type: ignore[misc]
        def __init__(self) -> None:
            self._llm_started: Dict[str, datetime] = {}
            self._tool_started: Dict[str, Tuple[datetime, Dict[str, Any]]] = {}

        def on_llm_start(self, serialized, prompts, **kwargs) -> None:
            run_id = str(kwargs.get("run_id") or uuid4().hex)
            self._llm_started[run_id] = _now()

        def on_llm_end(self, response, **kwargs) -> None:
            run_id = str(kwargs.get("run_id") or "")
            started = self._llm_started.pop(run_id, _now())
            text = None
            try:
                text = response.generations[0][0].text
            except Exception:
                text = None
            llm_output = getattr(response, "llm_output", None) or {}
            client.llm_span(
                model=str(llm_output.get("model_name") or "langchain-llm"),
                messages=[],
                completion=text,
                usage=llm_output.get("token_usage") or {},
                started_at=started,
            )

        def on_tool_start(self, serialized, input_str, **kwargs) -> None:
            run_id = str(kwargs.get("run_id") or uuid4().hex)
            arguments: Dict[str, Any] = {}
            if isinstance(input_str, dict):
                arguments = input_str
            else:
                try:
                    arguments = json.loads(input_str)
                except Exception:
                    arguments = {"input": str(input_str)}
            self._tool_started[run_id] = (_now(), arguments)

        def on_tool_end(self, output, **kwargs) -> None:
            run_id = str(kwargs.get("run_id") or "")
            started, arguments = self._tool_started.pop(run_id, (_now(), {}))
            name = str(kwargs.get("name") or "langchain-tool")
            client.tool_span(name, arguments, result=str(output)[:2000], started_at=started)

        def on_tool_error(self, error, **kwargs) -> None:
            run_id = str(kwargs.get("run_id") or "")
            started, arguments = self._tool_started.pop(run_id, (_now(), {}))
            name = str(kwargs.get("name") or "langchain-tool")
            client.tool_span(
                name,
                arguments,
                status=SpanStatus.ERROR,
                error=str(error),
                started_at=started,
            )

    return SafeOrbitCallbackHandler()


def llamaindex_callback_handler(client: SafeOrbitClient) -> Optional[Any]:
    """可选接入 LlamaIndex；未安装 llama-index-core 时返回 None。"""

    try:
        from llama_index.core.callbacks import BaseCallbackHandler, CBEventType  # type: ignore
    except Exception:
        try:
            from llama_index.callbacks import BaseCallbackHandler, CBEventType  # type: ignore
        except Exception:
            return None

    class SafeOrbitLlamaIndexHandler(BaseCallbackHandler):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__(event_starts_to_ignore=[], event_ends_to_ignore=[])
            self._starts: Dict[str, Tuple[datetime, Any, Dict[str, Any]]] = {}

        def on_event_start(self, event_type, payload=None, event_id="", parent_id="", **kwargs) -> None:
            if event_type in (CBEventType.LLM, CBEventType.FUNCTION_CALL, CBEventType.TOOL):
                self._starts[str(event_id)] = (_now(), event_type, payload or {})

        def on_event_end(self, event_type, payload=None, event_id="", **kwargs) -> None:
            started, kind, start_payload = self._starts.pop(
                str(event_id), (_now(), event_type, {})
            )
            end_payload = payload or {}
            if kind == CBEventType.LLM:
                response = end_payload.get("response")
                client.llm_span(
                    model=str(start_payload.get("model") or "llamaindex-llm"),
                    messages=[],
                    completion=str(response) if response is not None else None,
                    started_at=started,
                )
                return
            tool_name = str(
                start_payload.get("tool_name")
                or start_payload.get("name")
                or "llamaindex-tool"
            )
            arguments = start_payload.get("tool_kwargs") or start_payload.get("kwargs") or {}
            client.tool_span(
                tool_name,
                arguments if isinstance(arguments, dict) else {},
                result=end_payload.get("tool_output", end_payload.get("output")),
                started_at=started,
            )

    return SafeOrbitLlamaIndexHandler()


_SNIPPETS: Dict[str, str] = {
    "openai": """import os
from openai import OpenAI

# SAFEORBIT_AGENT_KEY 为安轨 Agent Key；模型上游 Key 仅配置在服务端
client = OpenAI(base_url="%(gateway)s", api_key=os.environ["SAFEORBIT_AGENT_KEY"])
client.chat.completions.create(
    model="%(model)s",
    messages=[{"role": "user", "content": "你好"}],
)""",
    "generic": """from safeorbit.adapters import SafeOrbitClient, traced_tool

# 先在本机环境中设置 SAFEORBIT_AGENT_KEY；不在代码中保存 Key。
client = SafeOrbitClient("%(base)s", agent_id="%(agent)s")
client.llm_span("%(model)s", [{"role": "user", "content": "你好"}], completion="回复")
with traced_tool(client, "refund", {"order_id": "O-1", "amount": 100}, side_effect="write"):
    ...  # 真正的工具调用写在这里""",
    "langchain": """from safeorbit.adapters import SafeOrbitClient, langchain_callback_handler

# 读取 SAFEORBIT_AGENT_KEY；回调仅采集，不执行工具前检查。
client = SafeOrbitClient("%(base)s", agent_id="%(agent)s")
handler = langchain_callback_handler(client)
agent.invoke({"input": "帮我退款"}, config={"callbacks": [handler]})""",
    "otel": """import os
import httpx

# 支持 OTLP HTTP JSON 与 protobuf：POST /v1/traces。
# JSON 使用 json=otlp_json；protobuf 使用 content=otlp_bytes 且 Content-Type: application/x-protobuf。
# resourceSpans / resource 中的 Agent ID 必须匹配凭据。
response = httpx.post("%(base)s/v1/traces", json=otlp_json,
    headers={"Authorization": "Bearer " + os.environ["SAFEORBIT_AGENT_KEY"]},
    timeout=10, trust_env=False)
response.raise_for_status()""",
    "mcp": """# 先在当前进程环境中设置 SAFEORBIT_AGENT_KEY（不要写入此模板）。
# 默认是采集模式：执行后上报，不做执行前检查，不提供强制控制。
py -m safeorbit mcp-proxy --url %(base)s --agent-id %(agent)s -- npx -y @modelcontextprotocol/server-filesystem .
# 如需执行前检查，在 -- 前添加 --enforce；检查失败/认证失败/超时不执行下游工具。
# enforce 下的写操作需要审批中心批准后，把审批单 ID 传给 --approval-id。
# 检查结果由服务端策略决定；不是不可绕过的工具控制。""",
    "mcp-http": """# 包裹 MCP over HTTP Server；GET/SSE 透传，POST tools/call 采集与鉴权。
# 先在当前环境设置 SAFEORBIT_AGENT_KEY。
py -m safeorbit mcp-http-proxy --upstream http://127.0.0.1:3000/mcp --url %(base)s --agent-id %(agent)s
# 需要执行前检查时添加 --enforce；写操作审批单通过 --approval-id 传入。""",
    "llamaindex": """from safeorbit.adapters import SafeOrbitClient, llamaindex_callback_handler

# 读取 SAFEORBIT_AGENT_KEY；未安装 llama-index-core 时 handler 返回 None。
client = SafeOrbitClient("%(base)s", agent_id="%(agent)s")
handler = llamaindex_callback_handler(client)
# 把 handler 注册到全局 callback manager 或查询引擎。
index.as_query_engine().query("帮我退款", callback_manager=handler)""",
    "langgraph": """from safeorbit.adapters import SafeOrbitClient, langchain_callback_handler

# 读取 SAFEORBIT_AGENT_KEY；回调仅采集。
client = SafeOrbitClient("%(base)s", agent_id="%(agent)s")
handler = langchain_callback_handler(client)
graph.invoke({"input": "帮我退款"}, config={"callbacks": [handler]})""",
    "autogen": """from safeorbit.adapters import SafeOrbitClient, traced_tool

# 读取 SAFEORBIT_AGENT_KEY；在工具函数内部执行真实调用。
client = SafeOrbitClient("%(base)s", agent_id="%(agent)s")
with traced_tool(client, "refund", {"order_id": "O-1", "amount": 100}, side_effect="write"):
    ...  # 在 AutoGen 工具函数内部执行真实调用""",
    "crewai": """from safeorbit.adapters import SafeOrbitClient, traced_tool

# 读取 SAFEORBIT_AGENT_KEY；在 CrewAI Tool 的 _run 中执行真实调用。
client = SafeOrbitClient("%(base)s", agent_id="%(agent)s")
with traced_tool(client, "refund", {"order_id": "O-1", "amount": 100}, side_effect="write"):
    ...  # 在 CrewAI Tool 的 _run 中执行真实调用""",
}


def integration_snippet(
    framework: str,
    base_url: str = "http://127.0.0.1:8000",
    agent_id: str = "demo-agent",
    model: str = "deepseek-chat",
) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    gateway = base + "/v1"
    key = framework.strip().lower()
    if key not in _SNIPPETS:
        raise ValueError("不支持的框架：%s（可选：%s）" % (framework, ", ".join(_SNIPPETS)))
    return _SNIPPETS[key] % {
        "base": base,
        "gateway": gateway,
        "agent": agent_id,
        "model": model,
    }


def supported_frameworks() -> List[str]:
    return sorted(_SNIPPETS.keys())
