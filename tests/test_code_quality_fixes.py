"""代码质量报告整改项的回归测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from safeorbit import infrascan
from safeorbit.auth import AuthStore
from safeorbit.security import BoundaryError, check_csrf
from safeorbit.store import TraceStore


def test_csrf_rejects_missing_host_header():
    scope = {"scheme": "http"}
    headers = {"x-safeorbit-csrf": "1", "origin": "http://"}
    with pytest.raises(BoundaryError) as excinfo:
        check_csrf(scope, headers)
    assert excinfo.value.status == 403


def test_csrf_accepts_same_origin_with_host():
    scope = {"scheme": "http"}
    headers = {"x-safeorbit-csrf": "1", "origin": "http://127.0.0.1:8000", "host": "127.0.0.1:8000"}
    check_csrf(scope, headers)


def test_schema_helpers_reject_unknown_table(tmp_path):
    store = TraceStore(str(tmp_path / "quality.db"))
    with store._connect() as connection:
        with pytest.raises(ValueError):
            TraceStore._ensure_columns(connection, "users", (("extra", "TEXT"),))


def test_infra_scan_ignores_commented_kind(tmp_path):
    (tmp_path / "notes.yaml").write_text(
        '# kind: Deployment\nnote: "privileged: true"\n', encoding="utf-8"
    )
    report = infrascan.scan_infra(str(tmp_path))
    assert report.findings == []


def test_infra_scan_still_detects_real_manifest(tmp_path):
    (tmp_path / "deploy.yaml").write_text(
        "apiVersion: v1\nkind: Pod\nmetadata:\n  name: demo\nspec:\n"
        "  containers:\n    - name: c\n      image: demo\n      securityContext:\n"
        "        privileged: true\n",
        encoding="utf-8",
    )
    report = infrascan.scan_infra(str(tmp_path))
    assert any(item.rule_id == "k8s_privileged" for item in report.findings)


def test_session_renewal_extends_near_expiry(tmp_path):
    store = AuthStore(str(tmp_path / "auth.db"))
    store.bootstrap_admin("admin", "Test-password-strong1!")
    token = store.create_session("admin")
    soon = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    with store._connect() as connection:
        connection.execute("UPDATE sessions SET expires_at = ?", (soon,))
    assert store.renew_session(token) is True
    # 已续期后不会重复写入
    assert store.renew_session(token) is False


def test_demo_command_requires_agent_key(monkeypatch):
    from safeorbit import cli

    monkeypatch.setenv("SAFEORBIT_AGENT_KEY", "")
    assert cli.main(["demo", "--dir", "fixtures"]) == 2
    args = cli._build_parser().parse_args(["demo", "--url", "http://x", "--agent-key", "k"])
    assert args.command == "demo"
    assert args.agent_key == "k"


def test_demo_command_skips_other_agent_traces(tmp_path, monkeypatch, capsys):
    import httpx

    from safeorbit import cli

    (tmp_path / "demo_a.json").write_text('{"agent_id": "agent-a", "trace_id": "t-a"}', encoding="utf-8")
    (tmp_path / "demo_b.json").write_text('{"agent_id": "agent-b", "trace_id": "t-b"}', encoding="utf-8")

    class FakeResponse:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self._body = body

        def json(self):
            return self._body

        @property
        def text(self):
            return str(self._body)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, json=None, headers=None):
            if json.get("agent_id") == "agent-a":
                return FakeResponse(200, {"trace_id": "t-a"})
            return FakeResponse(403, {"detail": "请求身份与 Agent Key 不匹配"})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    assert cli.main(["demo", "--dir", str(tmp_path), "--agent-key", "k"]) == 0
    out = capsys.readouterr().out
    assert "1/2 条进入审计" in out
    assert "1 条因 Agent 不匹配跳过" in out


def test_session_renewal_respects_absolute_cap(tmp_path):
    store = AuthStore(str(tmp_path / "auth2.db"))
    store.bootstrap_admin("admin", "Test-password-strong1!")
    token = store.create_session("admin")
    long_ago = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    soon = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    with store._connect() as connection:
        connection.execute(
            "UPDATE sessions SET created_at = ?, expires_at = ?", (long_ago, soon)
        )
    assert store.renew_session(token) is False
