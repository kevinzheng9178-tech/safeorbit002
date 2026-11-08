"""生产级硬化：熔断、结构化日志、健康探针、备份恢复。"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import sqlite3
import sys

import httpx
import pytest

from safeorbit import backup as backup_mod
from safeorbit import circuit, cli, logging_config, notify


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def fast_circuit():
    return circuit.CircuitConfig(
        failure_threshold=2, recovery_timeout=0.05, success_threshold=1, timeout=5
    )


# --------------------------- 熔断器 ---------------------------
def test_breaker_opens_after_threshold_and_reports_state(fast_circuit):
    breaker = circuit.CircuitBreaker("demo", fast_circuit)

    async def boom():
        raise RuntimeError("upstream down")

    async def scenario():
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await breaker.call(boom)
        with pytest.raises(circuit.CircuitOpenError):
            await breaker.call(boom)

    run(scenario())
    assert breaker._snapshot()["state"] == "open"


def test_breaker_half_open_recovers(fast_circuit):
    breaker = circuit.CircuitBreaker("recover", fast_circuit)

    async def boom():
        raise RuntimeError("down")

    async def ok():
        return "ok"

    async def scenario():
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await breaker.call(boom)
        await asyncio.sleep(0.06)
        assert await breaker.call(ok) == "ok"
        assert await breaker.call(ok) == "ok"

    run(scenario())
    assert breaker._snapshot()["state"] == "closed"
    assert breaker._snapshot()["failures"] == 0


def test_breaker_timeout_counts_as_failure():
    config = circuit.CircuitConfig(failure_threshold=1, recovery_timeout=60, timeout=0.01)
    breaker = circuit.CircuitBreaker("slow", config)

    async def slow():
        await asyncio.sleep(0.2)

    with pytest.raises(asyncio.TimeoutError):
        run(breaker.call(slow))
    assert breaker._snapshot()["state"] == "open"


def test_registry_reset_clears_state(fast_circuit):
    circuit.REGISTRY.get("x", fast_circuit)
    assert "x" in circuit.REGISTRY.status()
    circuit.REGISTRY.reset()
    assert circuit.REGISTRY.status() == {}


def test_notify_stops_sending_when_circuit_opens(monkeypatch):
    monkeypatch.setenv("SAFEORBIT_WEBHOOK_URL", "https://hooks.example.test/audit")

    def factory(**kwargs):
        def handler(request):
            raise httpx.ConnectError("down", request=request)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(notify.outbound, "async_client", factory)
    results = [run(notify.dispatch({"event_type": "alert"})) for _ in range(6)]
    assert results[:5] == [False] * 5
    assert results[5] is False
    assert circuit.REGISTRY.status()["notify"]["state"] == "open"


def test_notify_success_returns_true(monkeypatch):
    monkeypatch.setenv("SAFEORBIT_WEBHOOK_URL", "https://hooks.example.test/audit")
    monkeypatch.setenv("SAFEORBIT_WEBHOOK_TOKEN", "token-1")

    def factory(**kwargs):
        def handler(request):
            assert request.headers["Authorization"] == "Bearer token-1"
            return httpx.Response(200, json={"ok": True})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(notify.outbound, "async_client", factory)
    assert run(notify.dispatch({"event_type": "alert"})) is True


# --------------------------- 日志与请求 ID ---------------------------
def test_json_formatter_includes_request_id():
    record = logging.LogRecord("safeorbit.test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.request_id = "abc123"
    payload = json.loads(logging_config.JsonFormatter().format(record))
    assert payload["msg"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "abc123"


def test_request_id_from_headers_passthrough_and_generated():
    assert logging_config.request_id_from_headers({"x-request-id": "given"}) == "given"
    generated = logging_config.request_id_from_headers({})
    assert generated and generated != "given"


# --------------------------- 健康探针 ---------------------------
@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFEORBIT_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("SAFEORBIT_REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("SAFEORBIT_CODE_REPORT_DIR", str(tmp_path / "code"))
    monkeypatch.setenv("SAFEORBIT_SUMMARY_REPORT_DIR", str(tmp_path / "summaries"))
    monkeypatch.setenv("SAFEORBIT_UPSTREAM_MOCK", "1")
    monkeypatch.setenv("SAFEORBIT_COOKIE_SECURE", "0")
    monkeypatch.setenv("SAFEORBIT_AUTH", "on")
    monkeypatch.setenv("SAFEORBIT_MODELS_CONFIG", "")
    monkeypatch.delenv("SAFEORBIT_JUDGE_PROFILE", raising=False)
    sys.modules.pop("safeorbit.server", None)
    module = importlib.import_module("safeorbit.server")
    module.AUTH_STORE.create_user("admin", "Test-password-strong1!", role="admin")
    yield module
    sys.modules.pop("safeorbit.server", None)


def http_get(server, path, headers=None):
    async def invoke():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://test"
        ) as client:
            return await client.get(path, headers=headers)
    return run(invoke())


def test_live_probe_does_not_require_auth(server):
    response = http_get(server, "/health/live")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_ready_probe_reports_db_check(server):
    response = http_get(server, "/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["checks"] == {"db": True}
    assert body["open_circuits"] == {}


def test_ready_probe_returns_503_when_db_unhealthy(server, monkeypatch):
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("db locked")

    monkeypatch.setattr(server.STORE, "count_traces", broken)
    response = http_get(server, "/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"db": False}


def test_request_id_header_is_echoed_and_generated(server):
    passed = http_get(server, "/health/live", headers={"X-Request-Id": "trace-42"})
    assert passed.headers["x-request-id"] == "trace-42"
    generated = http_get(server, "/health/live")
    assert generated.headers["x-request-id"]


def test_logo_favicon_is_served(server):
    response = http_get(server, "/favicon.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.text.lstrip().startswith("<svg")
    assert "<title>SafeOrbit" in response.text
    assert http_get(server, "/favicon.ico").status_code == 307


def test_judge_prompt_is_readonly_and_authenticated(server):
    assert http_get(server, "/api/judge/prompt").status_code == 401

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
            body = (await client.get("/api/judge/prompt")).json()
            assert "SafeOrbit" in body["prompt"]
            assert body["temperature"] == 0
            assert body["response_format"] == "json_object"
            assert body["guards"]

    run(scenario())


def test_system_status_requires_login(server):
    assert http_get(server, "/api/system/status").status_code == 401


def test_system_status_reports_runtime_facts(server):
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
            response = await client.get("/api/system/status")
            assert response.status_code == 200
            body = response.json()
            assert body["db_ok"] is True
            assert body["version"] == "0.5.0"
            assert body["mode"] in ("shadow", "enforce")
            assert isinstance(body["circuits"], dict)
            assert isinstance(body["counters"], dict)

    run(scenario())


# --------------------------- 代码检查拖拽上传 ---------------------------
def _code_upload_client(server):
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url="http://test",
        headers={"X-SafeOrbit-CSRF": "1"},
    )
    return client


def test_code_upload_requires_login(server):
    async def scenario():
        async with _code_upload_client(server) as client:
            return await client.post(
                "/api/codeaudit/upload",
                files={"files": ("app.py", b"print(1)\n", "text/x-python")},
            )

    assert run(scenario()).status_code == 401


def test_code_upload_scans_files_and_returns_findings(server):
    async def scenario():
        async with _code_upload_client(server) as client:
            login = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "Test-password-strong1!"},
            )
            assert login.status_code == 200
            return await client.post(
                "/api/codeaudit/upload",
                files={"files": ("app.py", b'password = "Sup3rSecretValue123"\n', "text/x-python")},
                data={"paths": json.dumps(["src/app.py"])},
            )

    response = run(scenario())
    assert response.status_code == 200
    body = response.json()
    assert body["uploaded_files"] == 1
    assert body["report"]["files_scanned"] == 1
    assert body["report"]["findings"][0]["rule_id"] == "hardcoded_secret"
    assert body["report"]["findings"][0]["file"] == "src/app.py"


def test_code_upload_zip_skips_path_traversal_entries(server):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("app/main.py", 'password = "Sup3rSecretValue123"\n')
        bundle.writestr("../escape.py", 'password = "Sup3rSecretValue123"\n')
    buffer.seek(0)

    async def scenario():
        async with _code_upload_client(server) as client:
            login = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "Test-password-strong1!"},
            )
            assert login.status_code == 200
            return await client.post(
                "/api/codeaudit/upload",
                files={"files": ("project.zip", buffer.getvalue(), "application/zip")},
            )

    response = run(scenario())
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["files_scanned"] == 1
    assert [item["file"] for item in report["findings"]] == ["app/main.py"]


def test_code_upload_rejects_traversal_paths(server):
    async def scenario():
        async with _code_upload_client(server) as client:
            login = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "Test-password-strong1!"},
            )
            assert login.status_code == 200
            return await client.post(
                "/api/codeaudit/upload",
                files={"files": ("evil.py", b"print(1)\n", "text/x-python")},
                data={"paths": json.dumps(["../evil.py"])},
            )

    response = run(scenario())
    assert response.status_code == 400
    assert "没有可读取的文件" in response.json()["detail"]


# --------------------------- 备份与恢复 ---------------------------
def _make_db(path):
    connection = sqlite3.connect(str(path))
    connection.execute("CREATE TABLE t (v TEXT)")
    connection.execute("INSERT INTO t VALUES ('before')")
    connection.commit()
    connection.close()


def test_backup_and_restore_roundtrip(tmp_path):
    db_path = tmp_path / "app.db"
    _make_db(db_path)
    target = backup_mod.backup(str(db_path), str(tmp_path / "snap.db"))
    assert target.endswith("snap.db")

    connection = sqlite3.connect(str(db_path))
    connection.execute("INSERT INTO t VALUES ('after')")
    connection.commit()
    connection.close()

    backup_mod.restore(str(db_path), target, confirm=True)
    connection = sqlite3.connect(str(db_path))
    rows = [row[0] for row in connection.execute("SELECT v FROM t")]
    connection.close()
    assert rows == ["before"]


def test_restore_requires_confirm(tmp_path):
    db_path = tmp_path / "app.db"
    _make_db(db_path)
    target = backup_mod.backup(str(db_path), str(tmp_path / "snap.db"))
    with pytest.raises(ValueError):
        backup_mod.restore(str(db_path), target)


def test_list_backups_sorted_desc(tmp_path):
    db_path = tmp_path / "app.db"
    _make_db(db_path)
    first = backup_mod.backup(str(db_path), str(tmp_path / "app.db.backup-20260101-000000"))
    second = backup_mod.backup(str(db_path), str(tmp_path / "app.db.backup-20260102-000000"))
    assert backup_mod.list_backups(str(db_path)) == [second, first]


def test_cli_backup_and_restore(tmp_path, capsys):
    db_path = tmp_path / "app.db"
    _make_db(db_path)
    out = tmp_path / "cli-backup.db"

    assert cli.main(["backup", "--db", str(db_path), "--out", str(out)]) == 0
    assert out.exists()
    assert str(out) in capsys.readouterr().out

    # 默认命名（app.db.backup-<时间戳>）才会被 --list 识别
    assert cli.main(["backup", "--db", str(db_path)]) == 0
    capsys.readouterr()
    assert cli.main(["backup", "--db", str(db_path), "--list"]) == 0
    assert "app.db.backup-" in capsys.readouterr().out

    assert cli.main(["restore", str(out), "--db", str(db_path)]) == 2
    assert cli.main(["restore", str(out), "--db", str(db_path), "--confirm"]) == 0
