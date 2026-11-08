"""管理员初始化、隐藏输入与独立 Agent 凭据回归测试。"""

import getpass
import sqlite3
import warnings
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from safeorbit.agents import AgentAuthError, AgentStore, SCOPES
from safeorbit.auth import AuthError, AuthManager, AuthStore, _encode_password
from safeorbit.cli import main

PASSWORD = "SafeOrbit-test-123!"
NEW_PASSWORD = "SafeOrbit-reset-456!"


def test_new_database_requires_explicit_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFEORBIT_ADMIN_PASSWORD", PASSWORD)
    store = AuthStore(str(tmp_path / "auth.db"))
    manager = AuthManager(store)
    assert store.count_users() == 0
    assert manager.bootstrap_note == ""
    with pytest.raises(AuthError, match="user init"):
        store.require_ready()
    with pytest.raises(AuthError):
        manager.login("admin", PASSWORD)
    assert manager.authenticate_session("unknown") is None
    store.bootstrap_admin("root", PASSWORD)
    store.require_ready()
    assert manager.login("root", PASSWORD).role == "admin"


def test_legacy_default_requires_local_reset_and_revokes_sessions(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "auth.db")
    store = AuthStore(path)
    store.bootstrap_admin("admin", PASSWORD)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE users SET password_hash = ? WHERE username = 'admin'", (_encode_password("safeorbit"),))
    token = store.create_session("admin")
    manager = AuthManager(store)
    with pytest.raises(AuthError, match="reset-password"):
        store.require_ready()
    assert manager.authenticate_session(token) is None
    with pytest.raises(AuthError):
        manager.login("admin", "safeorbit")
    monkeypatch.setattr(getpass, "getpass", Mock(side_effect=[NEW_PASSWORD, NEW_PASSWORD]))
    assert main(["user", "reset-password", "--username", "admin", "--db", path]) == 0
    store.require_ready()
    assert store.verify_credentials("admin", "safeorbit") is None
    assert manager.login("admin", NEW_PASSWORD).role == "admin"
    assert store.resolve_session(token) is None
    assert NEW_PASSWORD not in capsys.readouterr().out


def test_existing_password_and_schema_are_preserved(tmp_path, monkeypatch):
    path = str(tmp_path / "shared.db")
    store = AuthStore(path)
    store.bootstrap_admin("admin", PASSWORD)
    token = store.create_session("admin")
    monkeypatch.setenv("SAFEORBIT_ADMIN_PASSWORD", NEW_PASSWORD)
    reopened = AuthStore(path)
    assert reopened.bootstrap_admin("other", NEW_PASSWORD) is None
    assert AuthManager(reopened).login("admin", PASSWORD).username == "admin"
    assert reopened.resolve_session(token).username == "admin"
    assert reopened.verify_credentials("admin", NEW_PASSWORD) is None
    AgentStore(path)
    AgentStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM auth_schema_migrations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM agent_schema_migrations").fetchone()[0] == 1


def test_reset_normal_password_invalidates_old_session(tmp_path):
    store = AuthStore(str(tmp_path / "auth.db"))
    store.bootstrap_admin("admin", PASSWORD)
    token = store.create_session("admin")
    store.reset_password("admin", NEW_PASSWORD)
    assert store.resolve_session(token) is None
    assert store.verify_credentials("admin", PASSWORD) is None
    assert store.verify_credentials("admin", NEW_PASSWORD) is not None


@pytest.mark.parametrize("password", ["short", "alllowercase123!", "ALLUPPERCASE123!", "NoNumbersHere!", "NoSymbolsHere123"])
def test_strong_password_required(tmp_path, password):
    store = AuthStore(str(tmp_path / "auth.db"))
    with pytest.raises(AuthError):
        store.bootstrap_admin("admin", password)
    assert store.count_users() == 0


def test_cli_init_hidden_confirmation_and_no_secret_output(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "auth.db")
    prompt = Mock(side_effect=[PASSWORD, PASSWORD])
    monkeypatch.setattr(getpass, "getpass", prompt)
    assert main(["user", "init", "--username", "admin", "--db", path]) == 0
    assert prompt.call_count == 2
    assert AuthStore(path).verify_credentials("admin", PASSWORD) is not None
    captured = capsys.readouterr()
    assert PASSWORD not in captured.out + captured.err
    with pytest.raises(SystemExit):
        main(["user", "init", "--username", "admin", "--password", PASSWORD, "--db", path])


def test_cli_rejects_unhidden_input_and_mismatch(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "auth.db")
    monkeypatch.setattr(getpass, "getpass", Mock(side_effect=[PASSWORD, NEW_PASSWORD]))
    assert main(["user", "init", "--username", "admin", "--db", path]) == 2
    assert AuthStore(path).count_users() == 0

    def unsafe_prompt(prompt):
        warnings.warn("cannot hide", getpass.GetPassWarning)
        return PASSWORD

    monkeypatch.setattr(getpass, "getpass", unsafe_prompt)
    assert main(["user", "init", "--username", "admin", "--db", path]) == 2
    assert AuthStore(path).count_users() == 0
    captured = capsys.readouterr()
    assert PASSWORD not in captured.out + captured.err


@pytest.fixture
def agents(tmp_path):
    store = AgentStore(str(tmp_path / "agents.db"))
    store.create_agent("agent-a", "Agent A")
    return store


def test_key_one_time_display_no_plaintext_and_last_seen(agents):
    issued = agents.issue_key("agent-a", sorted(SCOPES))
    public = agents.list_keys("agent-a")[0]
    assert "key" not in public and "key_hash" not in public
    assert public["prefix"] != issued["key"]
    assert public["last_seen"] is None
    with sqlite3.connect(str(agents.db_path)) as connection:
        stored = connection.execute("SELECT * FROM agent_keys").fetchone()
        assert issued["key"] not in stored
    reopened = AgentStore(str(agents.db_path))
    identity = reopened.authenticate(issued["key"], "models:read")
    assert identity == {"agent_id": "agent-a", "key_id": issued["key_id"]}
    assert agents.list_keys("agent-a")[0]["last_seen"] is not None
    assert agents.list_agents()[0]["name"] == "Agent A"


@pytest.mark.parametrize("token", [None, "", "wrong", "a" * 300])
def test_reject_missing_and_wrong_key(agents, token):
    with pytest.raises(AgentAuthError) as error:
        agents.authenticate(token, "models:read")
    assert error.value.status_code == 401


def test_immediate_revoke_scope_disable_and_rotation(agents):
    first = agents.issue_key("agent-a", ["models:read"])
    second = agents.issue_key("agent-a", ["models:read"])
    other_process = AgentStore(str(agents.db_path))
    assert agents.authenticate(first["key"], "models:read")
    with pytest.raises(AgentAuthError) as error:
        agents.authenticate(first["key"], "gateway:invoke")
    assert error.value.status_code == 403
    other_process.revoke_key("agent-a", first["key_id"])
    with pytest.raises(AgentAuthError) as error:
        agents.authenticate(first["key"], "models:read")
    assert error.value.status_code == 401
    assert agents.authenticate(second["key"], "models:read")
    assert other_process.set_enabled("agent-a", False)["enabled"] is False
    with pytest.raises(AgentAuthError) as error:
        agents.authenticate(second["key"], "models:read")
    assert error.value.status_code == 403
    other_process.set_enabled("agent-a", True)
    assert agents.authenticate(second["key"], "models:read")
    assert any(key["revoked"] for key in agents.list_keys("agent-a"))


def test_expiration_and_scope_validation(agents):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    key = agents.issue_key("agent-a", ["traces:write"], future)
    assert agents.authenticate(key["key"], "traces:write")
    with sqlite3.connect(str(agents.db_path)) as connection:
        connection.execute("UPDATE agent_keys SET expires_at = ? WHERE key_id = ?", ("2000-01-01T00:00:00+00:00", key["key_id"]))
    with pytest.raises(AgentAuthError) as error:
        agents.authenticate(key["key"], "traces:write")
    assert error.value.status_code == 401
    for scopes in ([], ["admin"], ["models:read", "*"], "models:read", [None], None):
        with pytest.raises(ValueError):
            agents.issue_key("agent-a", scopes)
    for expiry in ("2000-01-01T00:00:00Z", "2100-01-01", "bad", "", 123):
        with pytest.raises(ValueError):
            agents.issue_key("agent-a", ["models:read"], expiry)


@pytest.mark.parametrize("agent_id", ["../etc", "a/b", "a\\b", ".", "", "a.b", "%2F", "a?x", "a" * 129])
def test_agent_ids_are_path_safe(agents, agent_id):
    with pytest.raises(ValueError):
        agents.create_agent(agent_id)
    with pytest.raises(ValueError):
        agents.issue_key(agent_id, ["models:read"])


def test_missing_duplicate_and_cross_agent_revoke(agents):
    with pytest.raises(ValueError):
        agents.create_agent("agent-a")
    with pytest.raises(ValueError):
        agents.set_enabled("missing", False)
    with pytest.raises(ValueError):
        agents.issue_key("missing", ["models:read"])
    agents.create_agent("agent-b")
    key = agents.issue_key("agent-a", ["models:read"])
    with pytest.raises(ValueError):
        agents.revoke_key("agent-b", key["key_id"])
    assert agents.authenticate(key["key"], "models:read")


def test_human_and_agent_credentials_are_not_interchangeable(tmp_path):
    path = str(tmp_path / "shared.db")
    humans = AuthStore(path)
    humans.bootstrap_admin("admin", PASSWORD)
    human_token = humans.create_session("admin")
    agents = AgentStore(path)
    agents.create_agent("agent-a")
    agent_key = agents.issue_key("agent-a", ["models:read"])["key"]
    with pytest.raises(AgentAuthError):
        agents.authenticate(human_token, "models:read")
    assert AuthManager(humans).authenticate_session(agent_key) is None
