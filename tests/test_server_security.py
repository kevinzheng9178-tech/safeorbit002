"""所有 server 导入及 ASGI 请求使用临时库，不访问本机业务数据。"""
import asyncio
import importlib
import json
import sys
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from safeorbit.model_registry import ModelProfile, ModelRegistry
from safeorbit.models import Dimension, RuleHit, Severity, Trajectory, VerdictLevel
from safeorbit.pricing import load_pricing
from safeorbit.report import build_report
from safeorbit.security import RateLimiter, SecurityMiddleware


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFEORBIT_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("SAFEORBIT_REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("SAFEORBIT_CODE_REPORT_DIR", str(tmp_path / "code"))
    monkeypatch.setenv("SAFEORBIT_SUMMARY_REPORT_DIR", str(tmp_path / "summaries"))
    monkeypatch.setenv("SAFEORBIT_UPSTREAM_MOCK", "1")
    monkeypatch.setenv("SAFEORBIT_COOKIE_SECURE", "0")
    monkeypatch.setenv("SAFEORBIT_AUTH", "on")
    monkeypatch.setenv("SAFEORBIT_MAX_REQUEST_BYTES", "4096")
    monkeypatch.setenv("SAFEORBIT_MAX_SPANS", "2")
    monkeypatch.setenv("SAFEORBIT_REQUESTS_PER_MINUTE", "120")
    monkeypatch.setenv("SAFEORBIT_LOGIN_PER_MINUTE", "10")
    monkeypatch.setenv("SAFEORBIT_UPSTREAM_CONCURRENCY", "8")
    monkeypatch.setenv("SAFEORBIT_JUDGE", "off")
    monkeypatch.setenv("SAFEORBIT_MODELS_CONFIG", "")
    monkeypatch.delenv("SAFEORBIT_JUDGE_PROFILE", raising=False)
    sys.modules.pop("safeorbit.server", None)
    module = importlib.import_module("safeorbit.server")
    module.AUTH_STORE.create_user("admin", "Test-password-strong1!", role="admin")
    module.AGENTS.create_agent("agent-a")
    module.AGENTS.create_agent("agent-b")
    yield module
    sys.modules.pop("safeorbit.server", None)


def run(coroutine):
    return asyncio.run(coroutine)


def key(server, agent="agent-a", scopes=None, expires_at=None):
    return server.AGENTS.issue_key(agent, scopes or ["models:read", "gateway:invoke", "tools:check", "traces:write"], expires_at)


def request(server, path, token=None, body=None, headers=None, method=None):
    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
            values = dict(headers or {})
            if token:
                values["Authorization"] = "Bearer " + token
            return await client.request(method or ("GET" if path == "/v1/models" else "POST"), path, json=body, headers=values)
    return run(invoke())


@pytest.mark.parametrize("path", ["/v1/models", "/v1/chat/completions", "/v1/tool/check", "/v1/traces", "/v1/ingest"])
def test_all_entry_auth(server, path, monkeypatch):
    calls = []
    async def audit(*args):
        calls.append(args)
    monkeypatch.setattr(server, "process_trace", audit)
    def upstream(req):
        calls.append(req)
        return httpx.Response(200, json={})
    mock_upstream(server, monkeypatch, upstream)
    assert request(server, path, body={}).status_code == 401
    assert request(server, path, "invalid", {}).status_code == 401
    issued = key(server)
    server.AGENTS.revoke_key("agent-a", issued["key_id"])
    assert request(server, path, issued["key"], {}).status_code == 401
    expired = key(server)
    with server.AGENTS._connect() as connection:
        connection.execute("UPDATE agent_keys SET expires_at = ? WHERE key_id = ?",
                           ("2000-01-01T00:00:00+00:00", expired["key_id"]))
    assert request(server, path, expired["key"], {}).status_code == 401
    issued = key(server)
    server.AGENTS.set_enabled("agent-a", False)
    assert request(server, path, issued["key"], {}).status_code in (401, 403)
    assert not calls


@pytest.mark.parametrize("path", ["/v1/models", "/v1/chat/completions", "/v1/tool/check", "/v1/traces", "/v1/ingest"])
def test_scopes(server, path):
    scope = "traces:write" if path == "/v1/models" else "models:read"
    assert request(server, path, key(server, scopes=[scope])["key"], {}).status_code == 403


def test_identity_policy_and_atomic_ownership(server):
    token = key(server)["key"]
    assert request(server, "/v1/tool/check", token, {"agent_id": "agent-b"}).status_code == 403
    assert request(server, "/v1/models", token, headers={"x-safeorbit-agent-id": "agent-b"}).status_code == 403
    for header in ("x-safeorbit-mode", "x-safeorbit-budget", "x-safeorbit-allow-tools"):
        assert request(server, "/v1/models", token, headers={header: "shadow"}).status_code == 400
    payload = {"resourceSpans": [
        {"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": agent}}]},
         "scopeSpans": [{"spans": [{"traceId": "same", "spanId": agent}]}]}
        for agent in ("agent-a", "agent-b")
    ]}
    assert request(server, "/v1/traces", token, payload).status_code == 403
    server.STORE.claim("occupied", "agent-b")
    payload = {"resourceSpans": [{"scopeSpans": [{"spans": [
        {"traceId": "free", "spanId": "one"}, {"traceId": "occupied", "spanId": "two"}
    ]}]}]}
    assert request(server, "/v1/traces", token, payload).status_code == 409
    server.STORE.claim("free", "agent-b")


def test_mock_gateway_and_stream_audited(server):
    token = key(server)["key"]
    for stream in (False, True):
        response = request(server, "/v1/chat/completions", token,
                           {"messages": [{"role": "user", "content": "hello"}], "stream": stream})
        assert response.status_code == 200
    assert len(server.STORE.list_traces()) == 2
    records = [server.STORE.get(item["trace_id"]) for item in server.STORE.list_traces()]
    assert any(record["payload"]["spans"][0]["attributes"].get("audit_complete") for record in records)


def test_limits_and_upstream_credentials(server, monkeypatch):
    token = key(server)["key"]
    assert request(server, "/v1/ingest", token, {"spans": [{}, {}, {}]}).status_code == 413
    assert request(server, "/v1/ingest", token, {"huge": "a" * 5000}).status_code == 413
    monkeypatch.setattr(server, "MOCK_UPSTREAM", False)
    profile = ModelProfile(id="test-model", provider="custom", model="test-model",
                           base_url="https://upstream.invalid/v1", api_key_env="SAFEORBIT_TEST_MODEL_KEY")
    monkeypatch.delenv(profile.api_key_env, raising=False)
    monkeypatch.setattr(server, "MODEL_REGISTRY", ModelRegistry(profiles=[profile], default_gateway=profile.id))
    response = request(server, "/v1/models", token)
    assert response.status_code == 200
    assert response.json() == {"object": "list", "data": []}
    monkeypatch.setenv(profile.api_key_env, "server-only-key")
    assert server._upstream_headers(profile)["Authorization"] == "Bearer server-only-key"
    limiter = RateLimiter(1)
    assert limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")


def test_startup_and_csrf(server, monkeypatch):
    run(server.require_secure_startup())
    monkeypatch.setattr(server, "AUTH_ENABLED", False)
    with pytest.raises(RuntimeError):
        run(server.require_secure_startup())
    monkeypatch.setattr(server, "AUTH_ENABLED", True)
    assert request(server, "/api/auth/login", body={}).status_code == 403
    headers = {"X-SafeOrbit-CSRF": "1", "Origin": "https://evil.invalid"}
    assert request(server, "/api/auth/login", body={}, headers=headers).status_code == 403
    headers["Origin"] = "http://test"
    assert request(server, "/api/auth/login", body={"username": "admin", "password": "Test-password-strong1!"}, headers=headers).status_code == 200


def test_logout_without_body_invalidates_session(server):
    """退出登录是空体 POST：不能被管理面 JSON 校验拦成 400。"""

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://test",
            headers={"X-SafeOrbit-CSRF": "1"},
        ) as client:
            login = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "Test-password-strong1!"},
            )
            assert login.status_code == 200
            assert (await client.get("/api/auth/me")).json()["authenticated"] is True
            assert (await client.post("/api/auth/logout")).status_code == 200
            assert (await client.get("/api/auth/me")).json()["authenticated"] is False

    run(scenario())


def test_management_write_still_rejects_invalid_json(server):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://test",
            headers={"X-SafeOrbit-CSRF": "1"},
        ) as client:
            await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "Test-password-strong1!"},
            )
            response = await client.post("/api/agents", content=b"not-json")
            assert response.status_code == 400

    run(scenario())


def test_session_slides_when_near_expiry(server):
    """会话距过期不足 1 小时时应重设 Cookie，而不是强制登出。"""

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://test",
            headers={"X-SafeOrbit-CSRF": "1"},
        ) as client:
            login = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "Test-password-strong1!"},
            )
            assert login.status_code == 200
            assert "safeorbit_session=" in login.headers.get("set-cookie", "")
            soon = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
            with server.AUTH_STORE._connect() as connection:
                connection.execute("UPDATE sessions SET expires_at = ?", (soon,))
            server._RENEWAL_CHECKED.clear()
            response = await client.get("/api/auth/me")
            assert response.status_code == 200
            assert "safeorbit_session=" in response.headers.get("set-cookie", "")

    run(scenario())


def test_management_roles_and_objects(server):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test", headers={"X-SafeOrbit-CSRF": "1"}) as client:
            result = await client.post("/api/auth/login", json={"username": "admin", "password": "Test-password-strong1!"})
            assert result.status_code == 200
            assert "bootstrap_note" not in result.json()
            assert (await client.post("/api/agents", json=[])).status_code == 400
            assert (await client.post("/api/agents", json={"agent_id": "managed"})).status_code == 200
            issued = await client.post("/api/agents/managed/keys", json={"scopes": ["models:read"]})
            assert issued.status_code == 200
            agent_key = issued.json()["key"]
            authenticated = await client.get("/v1/models", headers={"Authorization": "Bearer " + agent_key})
            assert authenticated.status_code == 200
            result = await client.get("/api/agents")
            assert result.status_code == 200
            assert all("report_count" in a and "received_count" in a for a in result.json()["agents"])
            managed = next(a for a in result.json()["agents"] if a["agent_id"] == "managed")
            assert managed["last_seen"]
            assert agent_key not in result.text
            assert "key_hash" not in result.text
            assert (await client.get("/api/agents/managed/snippet")).status_code == 200
            server.AUTH_STORE.create_user("reader", "Test-reader-password1!", role="viewer")
            await client.post("/api/auth/login", json={"username": "reader", "password": "Test-reader-password1!"})
            assert (await client.get("/api/agents")).status_code == 200
            assert (await client.get("/api/agents/managed/keys")).status_code == 403
            assert (await client.patch("/api/agents/managed", json={"enabled": False})).status_code == 403
    run(scenario())


def _seed_summary_trace(server, trace_id="summary-trace-1"):
    now = datetime.now().astimezone()
    trajectory = Trajectory(
        trace_id=trace_id, agent_id="agent-a", session_id="summary-session",
        started_at=now, received_at=now,
    )
    report = build_report(trajectory, [], load_pricing())
    report.verdict = VerdictLevel.FAIL
    report.cost.total_usd = 0.25
    report.hits = [
        RuleHit(
            rule_id="pii_leak", rule_name="敏感信息外泄", dimension=Dimension.SAFETY,
            severity=Severity.HIGH,
            message="调用携带 sk-upstreamsecret1234567890 与 user@example.com",
        )
    ]
    server.STORE.save(trajectory, report, "<html></html>")


def test_summary_report_roles_generation_and_files(server):
    async def scenario():
        server.AUTH_STORE.create_user("reader", "Test-reader-password1!", role="viewer")
        server.AUTH_STORE.create_user("writer", "Test-writer-password1!", role="auditor")
        agent_key = server.AGENTS.issue_key("agent-a", ["models:read"])["key"]
        csrf = {"X-SafeOrbit-CSRF": "1"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://test"
        ) as client:
            login = await client.post(
                "/api/auth/login",
                json={"username": "reader", "password": "Test-reader-password1!"},
                headers=csrf,
            )
            assert login.status_code == 200
            assert (await client.get("/api/traces/overview")).status_code == 200
            assert (await client.post("/api/traces/summary-report", json={}, headers=csrf)).status_code == 403
            assert (await client.get("/api/traces/summary-report/latest")).status_code == 404

            login = await client.post(
                "/api/auth/login",
                json={"username": "writer", "password": "Test-writer-password1!"},
                headers=csrf,
            )
            assert login.status_code == 200
            assert (await client.post("/api/traces/summary-report", json={})).status_code == 403
            empty = await client.post("/api/traces/summary-report", json={}, headers=csrf)
            assert empty.status_code == 400

            _seed_summary_trace(server)
            created = await client.post("/api/traces/summary-report", json={}, headers=csrf)
            assert created.status_code == 200
            body = created.json()
            report_id = body["report_id"]
            assert report_id.startswith("sum-")
            files = list(server.SUMMARY_REPORT_DIR.glob("*.html"))
            assert len(files) == 1
            assert files[0].stem == report_id

            assert (await client.get(body["view_url"])).status_code == 200
            download = await client.get(body["download_url"])
            assert download.status_code == 200
            assert "attachment" in download.headers["content-disposition"]
            latest = await client.get("/api/traces/summary-report/latest")
            assert latest.status_code == 200
            assert "轨迹审计汇总报告" in latest.text

            overview = await client.get("/api/traces/overview")
            data = overview.json()
            assert data["totals"]["traces"] == 1
            assert data["verdicts"] == {"pass": 0, "warn": 0, "fail": 1, "review": 0}
            assert data["risks"][0]["trace_id"] == "summary-trace-1"
            assert agent_key not in overview.text

            for path in files:
                text = path.read_text(encoding="utf-8")
                assert "Content-Security-Policy" in text
                assert agent_key not in text
                assert "sk-upstreamsecret1234567890" not in text
                assert "user@example.com" not in text
    run(scenario())


def test_startup_uninitialized(server, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "AUTH_STORE", server.AuthStore(str(tmp_path / "empty.db")))
    with pytest.raises(server.AuthError):
        run(server.require_secure_startup())


def test_concurrency_disconnect_releases():
    class Agents:
        def authenticate(self, token, scope):
            return {"agent_id": "a"}
    class Server:
        AGENTS = Agents()
        MOCK_UPSTREAM = True
        UPSTREAM_KEY = None
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        async def downstream(scope, receive, send):
            entered.set()
            await release.wait()
        middleware = SecurityMiddleware(downstream, Server(), 100, 2, 100, 10, 1)
        scope = {"type": "http", "path": "/v1/models", "method": "GET", "headers": [(b"authorization", b"Bearer key")]}
        async def receive():
            return {"type": "http.request", "body": b""}
        sent = []
        async def send(message):
            sent.append(message)
        task = asyncio.create_task(middleware(dict(scope), receive, send))
        await entered.wait()
        await middleware(dict(scope), receive, send)
        assert sent[0]["status"] == 429
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert middleware.upstream._value == 1
    run(scenario())


def test_actual_chunked_bytes():
    calls = []
    async def downstream(scope, receive, send):
        calls.append(True)
    messages = iter([{"type": "http.request", "body": b"ab", "more_body": True},
                     {"type": "http.request", "body": b"cd", "more_body": False}])
    sent = []
    async def receive():
        return next(messages)
    async def send(message):
        sent.append(message)
    async def scenario():
        middleware = SecurityMiddleware(downstream, None, 3, 2, 10, 10, 1)
        await middleware({"type": "http", "path": "/health", "method": "POST", "headers": []}, receive, send)
    run(scenario())
    assert sent[0]["status"] == 413
    assert not calls


def mock_upstream(server, monkeypatch, handler):
    original = server.outbound.async_client
    clients = []
    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        if "transport" not in kwargs:
            kwargs["transport"] = transport
            client = original(*args, **kwargs)
            assert client.trust_env is False
            assert client.follow_redirects is False
            clients.append(client)
            return client
        return original(*args, **kwargs)

    monkeypatch.setattr(server, "MOCK_UPSTREAM", False)
    monkeypatch.setenv("SAFEORBIT_TEST_MODEL_KEY", "test-server-secret")
    profile = ModelProfile(id="test-model", provider="custom", model="test-model",
                           base_url="https://upstream.invalid/v1", api_key_env="SAFEORBIT_TEST_MODEL_KEY")
    monkeypatch.setattr(server, "MODEL_REGISTRY", ModelRegistry(profiles=[profile], default_gateway=profile.id))
    monkeypatch.setattr(server.outbound, "async_client", factory)
    return clients


def multi_model_registry(monkeypatch):
    monkeypatch.setenv("SAFEORBIT_TEST_DEEPSEEK_KEY", "deepseek-test-secret")
    monkeypatch.setenv("SAFEORBIT_TEST_OPENAI_KEY", "openai-test-secret")
    monkeypatch.delenv("SAFEORBIT_TEST_MISSING_KEY", raising=False)
    return ModelRegistry(profiles=[
        ModelProfile(id="fast", provider="deepseek", model="deepseek-chat",
                     api_key_env="SAFEORBIT_TEST_DEEPSEEK_KEY"),
        ModelProfile(id="reason", provider="deepseek", model="deepseek-reasoner",
                     api_key_env="SAFEORBIT_TEST_DEEPSEEK_KEY"),
        ModelProfile(id="other", provider="openai", model="gpt-test",
                     api_key_env="SAFEORBIT_TEST_OPENAI_KEY"),
        ModelProfile(id="no-stream", provider="openai", model="gpt-no-stream",
                     api_key_env="SAFEORBIT_TEST_OPENAI_KEY", supports_stream=False),
        ModelProfile(id="missing", provider="openai", model="gpt-missing",
                     api_key_env="SAFEORBIT_TEST_MISSING_KEY"),
        ModelProfile(id="judge-only", provider="openai", model="gpt-judge",
                     api_key_env="SAFEORBIT_TEST_OPENAI_KEY", gateway=False, judge=True),
    ], default_gateway="fast", judge_profile="judge-only")


@pytest.mark.parametrize("stream", [False, True])
def test_multi_model_routing(server, monkeypatch, stream):
    token = key(server)["key"]
    calls = []

    def upstream(req):
        calls.append(req)
        model = json.loads(req.content)["model"]
        if stream:
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text='data: ' + json.dumps({"model": model, "choices": [{"delta": {"content": "hello"}}]}) + '\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={"model": model, "choices": [{"message": {"content": "hello"}}]})

    clients = mock_upstream(server, monkeypatch, upstream)
    registry = multi_model_registry(monkeypatch)
    monkeypatch.setattr(server, "MODEL_REGISTRY", registry)
    for alias in (None, "fast", "reason", "other"):
        body = {"messages": [{"role": "user", "content": "hello"}], "stream": stream}
        if alias is not None:
            body["model"] = alias
        result = request(server, "/v1/chat/completions", token, body,
                         headers={"Cookie": "safeorbit_session=human-secret"})
        assert result.status_code == 200
        profile = registry.resolve_gateway(alias)
        sent = calls[-1]
        assert str(sent.url) == profile.base_url + "/chat/completions"
        assert json.loads(sent.content) == dict(body, model=profile.model, agent_id="agent-a")
        assert sent.headers["authorization"] == "Bearer " + profile.api_key
        assert token not in str(sent.headers)
        assert "cookie" not in sent.headers
        if stream:
            assert "[DONE]" in result.text
        else:
            assert result.json()["model"] == profile.model
    assert len(calls) == 4
    assert all(client.is_closed for client in clients)
    records = [server.STORE.get(row["trace_id"]) for row in server.STORE.list_traces()]
    assert len(records) == 4
    for secret in (token, "deepseek-test-secret", "openai-test-secret", "human-secret"):
        assert secret not in json.dumps(records)


@pytest.mark.parametrize("alias,stream,status", [
    ("missing", False, 503), ("missing", True, 503),
    ("unknown", False, 400), ("unknown", True, 400),
    ("judge-only", False, 400), ("no-stream", True, 400),
])
def test_multi_model_rejections_do_not_call_upstream(server, monkeypatch, alias, stream, status):
    calls = []
    mock_upstream(server, monkeypatch, lambda req: calls.append(req))
    monkeypatch.setattr(server, "MODEL_REGISTRY", multi_model_registry(monkeypatch))
    result = request(server, "/v1/chat/completions", key(server)["key"],
                     {"model": alias, "messages": [{"role": "user", "content": "hello"}], "stream": stream})
    assert result.status_code == status
    assert not calls
    assert not server.STORE.list_traces()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_upstream_redirect_is_not_followed(server, monkeypatch, stream, status):
    calls = []

    def upstream(req):
        calls.append(req)
        return httpx.Response(status, headers={"location": "https://evil.invalid/collect"},
                              text="test-server-secret")

    clients = mock_upstream(server, monkeypatch, upstream)
    result = request(server, "/v1/chat/completions", key(server)["key"],
                     {"messages": [{"role": "user", "content": "hello"}], "stream": stream})
    assert result.status_code == 502
    assert len(calls) == 1
    assert calls[0].url.host == "upstream.invalid"
    assert "test-server-secret" not in result.text
    assert "evil.invalid" not in result.text
    assert all(client.is_closed for client in clients)
    assert security_middleware(server).upstream._value == 8


def test_model_catalog_authentication_and_safe_fields(server, monkeypatch):
    calls = []
    mock_upstream(server, monkeypatch, lambda req: calls.append(req))
    registry = multi_model_registry(monkeypatch)
    monkeypatch.setattr(server, "MODEL_REGISTRY", registry)
    token = key(server)["key"]
    assert request(server, "/api/models", method="GET").status_code == 401
    assert request(server, "/api/models", token, method="GET").status_code == 401
    result = request(server, "/v1/models", token)
    assert result.status_code == 200
    assert result.json() == {"object": "list", "data": [
        {"id": p.id, "object": "model", "owned_by": p.provider}
        for p in registry.profiles if p.gateway and p.api_key
    ]}

    async def scenario():
        server.AUTH_STORE.create_user("reader", "Test-reader-password1!", role="viewer")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
            login = await client.post("/api/auth/login", json={"username": "reader", "password": "Test-reader-password1!"},
                                      headers={"X-SafeOrbit-CSRF": "1"})
            assert login.status_code == 200
            catalog = await client.get("/api/models")
            assert catalog.status_code == 200
            data = catalog.json()
            assert data["judge_profile"] == "judge-only"
            assert data["judge_enabled"] is False
            assert data["mock"] is False
            assert data["models"] == [p.public() for p in registry.profiles]
            fields = {"id", "display_name", "provider", "model", "gateway", "judge", "supports_stream",
                      "supports_json_object", "key_configured"}
            assert all(set(p) == fields for p in data["models"])
            assert next(p for p in data["models"] if p["id"] == "missing")["key_configured"] is False
            for secret in ("deepseek-test-secret", "openai-test-secret", "api_key_env", "base_url",
                           "SAFEORBIT_TEST_", "api.openai.com", "api.deepseek.com"):
                assert secret not in catalog.text
            assert (await client.get("/v1/models")).status_code == 401
    run(scenario())
    assert not calls


def security_middleware(server):
    middleware = server.app.middleware_stack
    while not isinstance(middleware, SecurityMiddleware):
        middleware = middleware.app
    return middleware


def test_real_upstream_credentials_and_reports(server, monkeypatch, caplog):
    token = key(server)["key"]
    calls = []

    def upstream(req):
        calls.append(req)
        assert req.headers["authorization"] == "Bearer test-server-secret"
        assert token not in str(req.headers)
        assert "cookie" not in req.headers
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "test-model"}]})
        if json.loads(req.content).get("stream"):
            return httpx.Response(200, text='data: {"model":"test-model","choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={"model": "test-model", "choices": [{"message": {"content": "hello"}}]})

    clients = mock_upstream(server, monkeypatch, upstream)
    assert request(server, "/v1/models", token).status_code == 200
    for stream in (False, True):
        assert request(server, "/v1/chat/completions", token,
                       {"messages": [{"role": "user", "content": "hello"}], "stream": stream},
                       headers={"Cookie": "safeorbit_session=human-secret"}).status_code == 200
    assert len(calls) == 2
    assert all(client.is_closed for client in clients)
    records = [server.STORE.get(row["trace_id"]) for row in server.STORE.list_traces()]
    assert len(records) == 2
    for secret in (token, "test-server-secret", "human-secret"):
        assert secret not in json.dumps(records)
        assert secret not in caplog.text
        assert all(secret not in path.read_text(encoding="utf-8") for path in server.REPORT_DIR.glob("*.html"))


def test_authentication_planes_are_separate(server):
    token = key(server)["key"]
    assert request(server, "/api/agents", token, method="GET").status_code == 401
    assert request(server, "/api/agents", token, {"agent_id": "forbidden"},
                   headers={"X-SafeOrbit-CSRF": "1"}).status_code == 401

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
            result = await client.post("/api/auth/login", json={"username": "admin", "password": "Test-password-strong1!"},
                                       headers={"X-SafeOrbit-CSRF": "1"})
            assert result.status_code == 200
            for path in ("/v1/models", "/v1/chat/completions", "/v1/tool/check", "/v1/traces", "/v1/ingest"):
                result = await client.request("GET" if path == "/v1/models" else "POST", path, json={})
                assert result.status_code == 401
    run(scenario())


def test_route_request_and_login_rate_limits(server):
    token = key(server)["key"]
    assert request(server, "/v1/models", token).status_code == 200
    middleware = security_middleware(server)
    middleware.agent_rate = RateLimiter(1)
    assert request(server, "/v1/models", token).status_code == 200
    result = request(server, "/v1/models", token)
    assert result.status_code == 429
    assert result.headers["retry-after"] == "60"
    assert request(server, "/v1/models", key(server, "agent-b")["key"]).status_code == 200
    middleware.login_rate = RateLimiter(1)
    headers = {"X-SafeOrbit-CSRF": "1"}
    assert request(server, "/api/auth/login", body={"username": "admin", "password": "wrong"}, headers=headers).status_code == 401
    result = request(server, "/api/auth/login", body={"username": "admin", "password": "Test-password-strong1!"}, headers=headers)
    assert result.status_code == 429
    assert result.headers["retry-after"] == "60"


@pytest.mark.parametrize("failure", ["buffer", "error", "disconnect", "http", "connect", "connect_cancel"])
def test_real_stream_failures_release_resources(server, monkeypatch, failure, caplog):
    token = key(server)["key"]
    monkeypatch.setattr(server, "STREAM_AUDIT_MAX_BYTES", 100 if failure == "buffer" else 4096)

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            content = "x" * (200 if failure == "buffer" else 1)
            yield ('data: ' + json.dumps({"choices": [{"delta": {"content": content}}]}) + '\n\n').encode()
            if failure == "disconnect":
                await asyncio.Event().wait()
            if failure == "error":
                raise RuntimeError("test-server-secret " + token)
            yield b'data: [DONE]\n\n'

        async def aclose(self):
            self.closed = True

    stream = Stream()

    def upstream(req):
        assert req.headers["authorization"] == "Bearer test-server-secret"
        if failure == "connect_cancel":
            raise asyncio.CancelledError()
        if failure == "connect":
            raise httpx.ConnectError("test-server-secret " + token, request=req)
        return httpx.Response(503 if failure == "http" else 200,
                              headers={"content-type": "text/event-stream"}, stream=stream)

    clients = mock_upstream(server, monkeypatch, upstream)
    body = {"messages": [{"role": "user", "content": "hello"}], "stream": True}
    if failure == "disconnect":
        async def scenario():
            delivered = False
            first_chunk = asyncio.Event()
            messages = []

            async def receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
                await first_chunk.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                messages.append(message)
                if message["type"] == "http.response.body" and message.get("body"):
                    first_chunk.set()

            scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"},
                     "http_version": "1.1", "method": "POST", "scheme": "http",
                     "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions", "query_string": b"",
                     "headers": [(b"host", b"test"), (b"authorization", ("Bearer " + token).encode()),
                                 (b"content-type", b"application/json")],
                     "client": ("127.0.0.1", 123), "server": ("test", 80)}
            await asyncio.wait_for(server.app(scope, receive, send), timeout=5)
            assert first_chunk.is_set()
            assert messages[0]["status"] == 200
        run(scenario())
    elif failure == "connect_cancel":
        with pytest.raises(asyncio.CancelledError):
            request(server, "/v1/chat/completions", token, body)
    else:
        result = request(server, "/v1/chat/completions", token, body)
        assert result.status_code == (503 if failure == "http" else 502 if failure == "connect" else 200)
        if failure in ("buffer", "error"):
            assert '"audit_complete":false' in result.text
            assert "[DONE]" not in result.text
    assert all(client.is_closed for client in clients)
    if failure not in ("connect", "connect_cancel"):
        assert stream.closed
    middleware = security_middleware(server)
    assert middleware.upstream._value == 8
    records = [server.STORE.get(row["trace_id"]) for row in server.STORE.list_traces()]
    assert len(records) == 1
    assert records[0]["payload"]["spans"][0]["attributes"]["audit_complete"] is False
    for secret in (token, "test-server-secret"):
        assert secret not in json.dumps(records)
        assert secret not in caplog.text
