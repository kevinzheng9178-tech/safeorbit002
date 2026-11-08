"""P3 测试：通用 SDK 适配与 MCP stdio 代理。"""

from __future__ import annotations

import io
import json
from datetime import datetime

import pytest

from safeorbit.adapters import (
    SafeOrbitClient,
    ToolBlocked,
    integration_snippet,
    record_openai_response,
    supported_frameworks,
    traced_tool,
)
from safeorbit.mcp_proxy import McpProxy, guess_side_effect
from safeorbit.models import SpanKind, SpanStatus


def _client_with_payloads() -> tuple:
    client = SafeOrbitClient("http://127.0.0.1:8000", agent_id="agent-a", trace_id="trace-1", session_id="sess-1", api_key="test-agent-key")
    payloads = []

    def fake_post(path, payload):
        payloads.append((path, payload))
        if path == "/tool/check":
            return {"decision": "allow", "reasons": []}
        return {"accepted": 1}

    client._post = fake_post  # type: ignore[assignment]
    return client, payloads


def test_sdk_tool_span_is_auditable_payload():
    client, payloads = _client_with_payloads()
    span = client.tool_span("refund", {"amount": 100}, side_effect="write")
    assert span.kind == SpanKind.TOOL

    path, payload = payloads[-1]
    assert path == "/ingest"
    assert payload["source"] == "sdk"
    assert payload["trace_id"] == "trace-1"
    assert payload["agent_id"] == "agent-a"
    assert payload["spans"][0]["kind"] == "tool"
    assert payload["spans"][0]["tool"]["arguments"]["amount"] == 100


def test_sdk_llm_span_carries_usage():
    client, payloads = _client_with_payloads()
    client.llm_span(
        "deepseek-chat",
        [{"role": "user", "content": "hi"}],
        completion="hello",
        usage={"prompt_tokens": 10, "completion_tokens": 4},
    )
    payload = payloads[-1][1]
    llm = payload["spans"][0]["llm"]
    assert llm["model"] == "deepseek-chat"
    assert llm["usage"]["prompt"] == 10
    assert llm["usage"]["completion"] == 4


def test_check_tool_denies_when_server_says_deny():
    client, payloads = _client_with_payloads()

    def deny_post(path, payload):
        payloads.append((path, payload))
        return {"decision": "deny", "reasons": ["写操作未经人工确认"]}

    client._post = deny_post  # type: ignore[assignment]
    with pytest.raises(ToolBlocked) as exc_info:
        client.check_tool("refund", {"amount": 100}, side_effect="write")
    assert "写操作未经人工确认" in str(exc_info.value)
    assert payloads[-1][1]["tool_name"] == "refund"


def test_traced_tool_reports_after_execution():
    client, payloads = _client_with_payloads()
    with traced_tool(client, "get_order", {"order_id": "O-1"}):
        pass
    assert payloads[-1][0] == "/ingest"
    assert payloads[-1][1]["spans"][0]["tool"]["tool_name"] == "get_order"


def test_record_openai_response():
    client, payloads = _client_with_payloads()
    record_openai_response(
        client,
        "deepseek-chat",
        [{"role": "user", "content": "hi"}],
        {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        },
    )
    span = payloads[-1][1]["spans"][0]
    assert span["llm"]["completion"] == "ok"
    assert span["llm"]["usage"]["prompt"] == 3


def test_integration_snippets():
    assert "generic" in supported_frameworks()
    assert "base_url=" in integration_snippet("openai", "http://127.0.0.1:8000")
    assert "SafeOrbitClient" in integration_snippet("generic")
    with pytest.raises(ValueError):
        integration_snippet("unknown-framework")


# --------------------------------------------------------------------------
# MCP 代理
# --------------------------------------------------------------------------
class _FakeMcpClient:
    def __init__(self, decision: str = "allow"):
        self.decision = decision
        self.spans = []

    def check_tool(self, tool_name, arguments, *, side_effect, confirmed_by_human):
        return {"decision": self.decision, "reasons": ["策略拒绝"] if self.decision == "deny" else []}

    def emit(self, span):
        self.spans.append(span)


def test_mcp_side_effect_heuristic():
    assert guess_side_effect("refund_order") == "write"
    assert guess_side_effect("search_policy") == "unknown"


def test_mcp_captures_tools_call_result():
    client = _FakeMcpClient()
    proxy = McpProxy(["fake-server"], client=client, out=io.StringIO())
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "refund", "arguments": {"amount": 100}},
    }
    assert proxy.handle_client_message(message) is True
    assert 1 in proxy._pending

    proxy.handle_server_message({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    span = client.spans[-1]
    assert span.kind == SpanKind.TOOL
    assert span.tool.tool_name == "refund"
    assert span.tool.side_effect == "write"
    assert span.status == SpanStatus.OK


def test_mcp_denies_tools_call_in_enforce_mode():
    client = _FakeMcpClient(decision="deny")
    out = io.StringIO()
    proxy = McpProxy(["fake-server"], client=client, enforce=True, out=out)
    message = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "refund", "arguments": {"amount": 100}},
    }
    assert proxy.handle_client_message(message) is False
    reply = json.loads(out.getvalue().strip().splitlines()[-1])
    assert reply["id"] == 2
    assert reply["error"]["code"] == -32001
    assert client.spans[-1].status == SpanStatus.BLOCKED
    assert client.spans[-1].tool.blocked is True


def test_mcp_passthrough_for_non_tool_methods():
    client = _FakeMcpClient()
    proxy = McpProxy(["fake-server"], client=client, out=io.StringIO())
    assert proxy.handle_client_message({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}) is True
    assert proxy._pending == {}


@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_sdk_missing_key_never_sends_request(monkeypatch, api_key):
    monkeypatch.delenv("SAFEORBIT_AGENT_KEY", raising=False)
    sent = []
    monkeypatch.setattr("safeorbit.adapters.httpx.post", lambda *a, **kw: sent.append(kw))
    client = SafeOrbitClient(api_key=api_key)
    with pytest.raises(ValueError, match="SAFEORBIT_AGENT_KEY"):
        client.check_tool("refund")
    assert sent == []


@pytest.mark.parametrize("explicit, expected", [(None, "env-key"), (" explicit-key ", "explicit-key")])
def test_sdk_all_requests_use_bearer(monkeypatch, explicit, expected):
    import httpx

    monkeypatch.setenv("SAFEORBIT_AGENT_KEY", " env-key ")
    sent = []

    def post(url, **kwargs):
        sent.append((url, kwargs))
        return httpx.Response(200, json={"decision": "allow"}, request=httpx.Request("POST", url))

    monkeypatch.setattr("safeorbit.adapters.httpx.post", post)
    client = SafeOrbitClient("http://localhost:8000/v1", api_key=explicit)
    client.check_tool("search")
    client.tool_span("search")
    assert [url for url, _ in sent] == ["http://localhost:8000/v1/tool/check", "http://localhost:8000/v1/ingest"]
    assert all(kwargs["headers"]["Authorization"] == "Bearer " + expected for _, kwargs in sent)
    assert all(not any("budget" in key or "mode" in key for key in kwargs["headers"]) for _, kwargs in sent)


@pytest.mark.parametrize("response", [{}, {"decision": "oops"}, None])
def test_sdk_invalid_check_result_fails_closed(response):
    client, _ = _client_with_payloads()
    client._post = lambda *args: response
    with pytest.raises(ValueError, match="拒绝执行"):
        with traced_tool(client, "refund"):
            pytest.fail("invalid check must never enter tool body")


def test_snippets_use_environment_and_correct_protocol(monkeypatch):
    monkeypatch.setenv("SAFEORBIT_AGENT_KEY", "secret-not-in-snippet")
    for framework in supported_frameworks():
        snippet = integration_snippet(framework, "http://localhost:8000/v1")
        assert "SAFEORBIT_AGENT_KEY" in snippet
        assert "secret-not-in-snippet" not in snippet
        assert "/v1/v1" not in snippet
    assert 'api_key=os.environ["SAFEORBIT_AGENT_KEY"]' in integration_snippet("openai")
    assert "json=otlp_json" in integration_snippet("otel")
    assert "/v1/traces" in integration_snippet("otel")
    assert "仅采集" in integration_snippet("langchain")


@pytest.mark.parametrize("failure", ["auth", "timeout", "malformed"])
def test_mcp_check_failure_never_forwards_or_leaks_key(failure):
    import httpx

    class BrokenClient(_FakeMcpClient):
        def check_tool(self, *args, **kwargs):
            if failure == "malformed":
                return {"decision": "invalid"}
            if failure == "timeout":
                raise httpx.ReadTimeout("secret-agent-key")
            raise RuntimeError("401 secret-agent-key")

    out, err = io.StringIO(), io.StringIO()
    client = BrokenClient()
    proxy = McpProxy(["fake"], client=client, enforce=True, out=out, err=err)
    assert proxy.handle_client_message({"id": 1, "method": "tools/call", "params": {"name": "refund"}}) is False
    assert proxy._pending == {}
    assert json.loads(out.getvalue())["error"]["code"] == -32002
    assert "拒绝" in err.getvalue()
    assert "secret-agent-key" not in out.getvalue() + err.getvalue()


def test_mcp_collection_failure_is_visible_but_not_precheck():
    class BrokenClient(_FakeMcpClient):
        def check_tool(self, *args, **kwargs):
            pytest.fail("collection mode must not precheck")

        def emit(self, span):
            raise RuntimeError("secret-agent-key")

    err = io.StringIO()
    proxy = McpProxy(["fake"], client=BrokenClient(), out=io.StringIO(), err=err)
    assert proxy.handle_client_message({"id": 1, "method": "tools/call", "params": {"name": "search"}})
    proxy.handle_server_message({"id": 1, "result": {"ok": True}})
    assert "上报失败" in err.getvalue()
    assert "secret-agent-key" not in err.getvalue()


def test_mcp_is_error_result_is_recorded_as_error():
    client = _FakeMcpClient()
    proxy = McpProxy(["fake"], client=client, out=io.StringIO())
    proxy.handle_client_message({"id": 1, "method": "tools/call", "params": {"name": "search"}})
    proxy.handle_server_message({"id": 1, "result": {"isError": True}})
    assert client.spans[-1].status == SpanStatus.ERROR


def test_dashboard_dom_references_and_agent_panel():
    import re
    from html.parser import HTMLParser
    from safeorbit.dashboard import DASHBOARD_HTML

    class IdParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = set()

        def handle_starttag(self, tag, attrs):
            self.ids.update(value for key, value in attrs if key == "id")

    parser = IdParser()
    parser.feed(DASHBOARD_HTML)
    references = set(re.findall(r'getElementById\(["\']([^"\']+)["\']\)', DASHBOARD_HTML))
    assert references <= parser.ids, references - parser.ids
    tabs = re.findall(r'data-tab="([^"]+)"', DASHBOARD_HTML)
    assert all("panel-" + tab in parser.ids for tab in tabs)
    assert "async function loadAgents()" in DASHBOARD_HTML
    assert 'config.headers.set("X-SafeOrbit-CSRF", "1")' in DASHBOARD_HTML
    assert "accepted 不等于审计完成" in DASHBOARD_HTML


def test_demo_uses_key_without_client_policy_override(monkeypatch):
    from examples.demo_agent import _headers

    monkeypatch.delenv("SAFEORBIT_AGENT_KEY", raising=False)
    with pytest.raises(ValueError):
        _headers("trace", "session", "agent")
    monkeypatch.setenv("SAFEORBIT_AGENT_KEY", "demo-key")
    headers = _headers("trace", "session", "agent")
    assert headers["Authorization"] == "Bearer demo-key"
    assert not any("budget" in key or "mode" in key for key in headers)
