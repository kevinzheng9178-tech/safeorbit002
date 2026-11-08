import asyncio
import importlib
import json
import sys

import httpx
import pytest

from safeorbit.judge import JudgeConfig
from safeorbit.model_settings import ModelSettings


SECRET = "api-settings-test-secret"


class Backend:
    def encrypt(self, value):
        return value.encode()[::-1]

    def decrypt(self, value):
        return value[::-1].decode()


@pytest.fixture
def server(tmp_path, monkeypatch):
    for name in ("SAFEORBIT_MODELS_CONFIG", "SAFEORBIT_JUDGE_PROFILE", "SAFEORBIT_JUDGE",
                 "SAFEORBIT_UPSTREAM_API_KEY", "SAFEORBIT_UPSTREAM_BASE_URL", "SAFEORBIT_UPSTREAM_MODEL",
                 "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SAFEORBIT_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("SAFEORBIT_REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("SAFEORBIT_UPSTREAM_MOCK", "1")
    monkeypatch.setenv("SAFEORBIT_COOKIE_SECURE", "0")
    sys.modules.pop("safeorbit.server", None)
    module = importlib.import_module("safeorbit.server")
    module.MODEL_SETTINGS = ModelSettings(tmp_path / "api.db", backend=Backend())
    admin = module.AUTH_STORE.create_user("admin", "Test-password-strong1!", role="admin")
    viewer = module.AUTH_STORE.create_user("viewer", "Test-password-strong1!", role="viewer")
    module.test_cookies = {"admin": module.AUTH.issue_session(admin), "viewer": module.AUTH.issue_session(viewer)}
    yield module
    sys.modules.pop("safeorbit.server", None)


def request(server, method="GET", path="/api/admin/models", payload=None, role="admin", csrf=True, headers=None):
    async def invoke():
        values = dict(headers or {})
        if csrf:
            values["X-SafeOrbit-CSRF"] = "1"
        cookies = {server.COOKIE_NAME: server.test_cookies[role]} if role else {}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test", cookies=cookies) as client:
            return await client.request(method, path, json=payload, headers=values)
    return asyncio.run(invoke())


def config(revision, key=SECRET):
    return {"expected_revision": revision, "profiles": [
        {"id": "chat", "provider": "openai", "model": "test-chat", "judge": True,
         "key_action": "replace", "api_key": key}], "default_gateway": "chat",
        "judge_profile": "chat", "judge_enabled": True}


def saved(server):
    initial = request(server).json()
    result = request(server, "PUT", payload=config(initial["revision"]))
    assert result.status_code == 200, result.text
    return result.json()["revision"]


def install(server, monkeypatch, handler):
    original = server.outbound.async_client
    calls = []

    def dispatch(req):
        calls.append(req)
        return handler(req)

    monkeypatch.setattr(server.outbound, "async_client", lambda **kw: original(transport=httpx.MockTransport(dispatch), **kw))
    return calls


def test_private_key_serialization():
    value = JudgeConfig(api_key=SECRET)
    assert value.api_key == SECRET
    assert SECRET not in repr(value)
    assert "api_key" not in value.model_dump()
    assert SECRET not in value.model_dump_json()
    assert "api_key" not in JudgeConfig.model_json_schema()["properties"]


def test_admin_permissions_csrf_and_no_store(server):
    for role, status in [(None, 401), ("viewer", 403)]:
        result = request(server, role=role)
        assert result.status_code == status
        assert result.headers["cache-control"] == "no-store"
    assert request(server, headers={"Authorization": "Bearer agent-token"}).status_code == 403
    assert request(server, "PUT", payload={}, csrf=False).status_code == 403
    assert request(server, "PUT", payload={}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_save_conflict_safe_response_and_readonly(server):
    revision = saved(server)
    public = request(server)
    assert public.headers["cache-control"] == "no-store"
    assert SECRET not in public.text
    assert public.json()["profiles"][0]["key_configured"]
    assert request(server, "PUT", payload=config(revision - 1)).status_code == 409
    server.MODEL_SETTINGS.read_only = True
    assert request(server, "PUT", payload=config(revision)).status_code == 403


def test_real_test_even_mock_mode_limits_and_no_traces(server, monkeypatch):
    revision = saved(server)
    calls = install(server, monkeypatch, lambda req: httpx.Response(200, json={"choices": [{"message": {"content": SECRET}}]}))
    payload = {"id": "chat", "expected_revision": revision}
    for _ in range(5):
        response = request(server, "POST", "/api/admin/models/test", payload)
        assert response.json()["success"]
        assert SECRET not in response.text
    assert len(calls) == 5
    assert request(server, "POST", "/api/admin/models/test", payload).status_code == 429
    assert json.loads(calls[0].content)["max_tokens"] == 8
    assert calls[0].headers["authorization"] == "Bearer " + SECRET
    assert server.STORE.list_traces() == []


@pytest.mark.parametrize("failure,category", [(401, "upstream_error"), (302, "upstream_error"),
                                                ("huge", "response_too_large"), ("timeout", "timeout"),
                                                ("network", "network_error"), ("invalid", "invalid_response")])
def test_test_failure_sanitization(server, monkeypatch, failure, category):
    revision = saved(server)

    def handler(req):
        if failure == "timeout":
            raise httpx.ReadTimeout(SECRET)
        if failure == "network":
            raise httpx.ConnectError(SECRET)
        if failure == "huge":
            return httpx.Response(200, content=b"x" * 65537)
        if failure == "invalid":
            return httpx.Response(200, text=SECRET)
        return httpx.Response(failure, text=SECRET)

    install(server, monkeypatch, handler)
    response = request(server, "POST", "/api/admin/models/test", {"id": "chat", "expected_revision": revision})
    assert response.json()["category"] == category
    assert SECRET not in response.text
    assert server.MODEL_SETTINGS.snapshot().revision == revision
    assert not server._MODEL_TEST_BUSY


def test_test_only_saved_revision_and_concurrency(server):
    revision = saved(server)
    path = "/api/admin/models/test"
    assert request(server, "POST", path, {"id": "chat", "expected_revision": revision - 1}).status_code == 409
    assert request(server, "POST", path, {"id": "chat", "expected_revision": revision, "url": "https://evil.example"}).status_code == 400
    server._MODEL_TEST_BUSY = True
    assert request(server, "POST", path, {"id": "chat", "expected_revision": revision}).status_code == 429


@pytest.mark.parametrize("stream", [False, True])
def test_gateway_rotation_keeps_entry_snapshot(server, monkeypatch, stream):
    revision = saved(server)
    monkeypatch.setattr(server, "MOCK_UPSTREAM", False)
    server.AGENTS.create_agent("test-agent")
    token = server.AGENTS.issue_key("test-agent", ["gateway:invoke"])["key"]
    snapshots = []

    async def audit(*args):
        snapshots.append(args[-1])

    def handler(req):
        if server.MODEL_SETTINGS.snapshot().revision == revision:
            server.MODEL_SETTINGS.save(config(revision, "rotated-secret"))
            assert req.headers["authorization"] == "Bearer " + SECRET
        else:
            assert req.headers["authorization"] == "Bearer rotated-secret"
        if stream:
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n', headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    monkeypatch.setattr(server, "process_trace", audit)
    install(server, monkeypatch, handler)
    result = request(server, "POST", "/v1/chat/completions", {"stream": stream, "messages": [{"role": "user", "content": "Hi"}]}, role=None, headers={"Authorization": "Bearer " + token})
    assert result.status_code == 200
    assert snapshots[0].revision == revision
    assert snapshots[0].registry.resolve_gateway().api_key == SECRET
    assert server.MODEL_SETTINGS.snapshot().registry.resolve_gateway().api_key == "rotated-secret"
    result = request(server, "POST", "/v1/chat/completions", {"stream": stream, "messages": [{"role": "user", "content": "Hi"}]}, role=None, headers={"Authorization": "Bearer " + token})
    assert result.status_code == 200
    assert snapshots[1].revision == revision + 1
    assert snapshots[1].registry.resolve_gateway().api_key == "rotated-secret"


def test_delayed_judge_uses_captured_key(server, monkeypatch):
    from safeorbit.models import Trajectory
    from safeorbit.rules import AuditContext

    revision = saved(server)
    snapshot = server.MODEL_SETTINGS.snapshot()
    server.MODEL_SETTINGS.save(config(revision, "rotated-secret"))
    seen = install(server, monkeypatch, lambda req: httpx.Response(200, json={
        "choices": [{"message": {"content": '{"findings":[]}'}}]}))
    trajectory = Trajectory.model_validate({"trace_id": "delayed", "agent_id": "test-agent",
        "started_at": "2026-11-03T10:00:00+08:00", "received_at": "2026-11-03T10:00:00+08:00", "spans": []})
    asyncio.run(server.process_trace(trajectory, AuditContext(), model_snapshot=snapshot))
    assert seen[0].headers["authorization"] == "Bearer " + SECRET
    assert server.STORE.get("delayed") is not None
    assert SECRET not in json.dumps(server.STORE.get("delayed"), default=str)


def test_test_inflight_concurrency_and_cancel_release(server, monkeypatch):
    revision = saved(server)
    entered = None
    release = None
    original = server.outbound.async_client
    clients = []

    async def handler(req):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    def factory(**kwargs):
        client = original(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(server.outbound, "async_client", factory)

    async def scenario():
        nonlocal entered, release
        entered = asyncio.Event()
        release = asyncio.Event()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test",
                                     cookies={server.COOKIE_NAME: server.test_cookies["admin"]},
                                     headers={"X-SafeOrbit-CSRF": "1"}) as client:
            payload = {"id": "chat", "expected_revision": revision}
            first = asyncio.create_task(client.post("/api/admin/models/test", json=payload))
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                blocked = await client.post("/api/admin/models/test", json=payload)
                assert blocked.status_code == 429
                assert len(clients) == 1
            finally:
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
            assert not server._MODEL_TEST_BUSY
            assert clients[0].is_closed
            release.set()
            recovered = await client.post("/api/admin/models/test", json=payload)
            assert recovered.json()["success"]
            assert server.MODEL_SETTINGS.snapshot().revision == revision
            assert not server.STORE.list_traces()
            assert not server.REPORT_DIR.exists()

    asyncio.run(scenario())
    assert all(client.is_closed for client in clients)


def test_cli_serve_parameters_applied_before_validation(tmp_path, monkeypatch):
    from safeorbit import cli

    seen = []
    monkeypatch.delenv("SAFEORBIT_JUDGE", raising=False)
    for name in ("SAFEORBIT_MODELS_CONFIG", "SAFEORBIT_DB", "SAFEORBIT_PUBLIC_URL"):
        monkeypatch.setenv(name, "")
    monkeypatch.setattr(cli, "load_model_snapshot", lambda path: seen.append((path, __import__("os").environ.get("SAFEORBIT_MODELS_CONFIG"), __import__("os").environ.get("SAFEORBIT_JUDGE"))))
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)
    db = str(tmp_path / "cli.db")
    assert cli.main(["serve", "--db", db, "--models-config", "explicit.yaml"]) == 0
    assert seen == [(db, "explicit.yaml", None)]
    assert cli._build_parser().parse_args(["serve"]).judge is None
