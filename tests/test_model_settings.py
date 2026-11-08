import base64
import json
import os
import sqlite3

import pytest

from safeorbit.model_registry import ModelProfile, ModelRegistry
from safeorbit.model_settings import DPAPIBackend, ModelSettings, SettingsError, load_model_snapshot


class FakeBackend:
    def encrypt(self, value):
        return base64.b64encode(value.encode())

    def decrypt(self, value):
        return base64.b64decode(value).decode()


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ("SAFEORBIT_MODELS_CONFIG", "SAFEORBIT_JUDGE_PROFILE", "SAFEORBIT_JUDGE",
                 "SAFEORBIT_UPSTREAM_API_KEY", "SAFEORBIT_UPSTREAM_MODEL", "SAFEORBIT_UPSTREAM_BASE_URL",
                 "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def payload(revision, action="replace", key="test-secret-unique"):
    profile = {"id": "chat", "display_name": "测试模型", "provider": "custom",
               "base_url": "https://api.example.com/v1", "model": "m", "judge": True,
               "key_action": action}
    if key is not None:
        profile["api_key"] = key
    return {"expected_revision": revision, "profiles": [profile], "default_gateway": "chat",
            "judge_profile": "chat", "judge_enabled": True}


def settings(tmp_path, **kwargs):
    return ModelSettings(tmp_path / "settings.db", backend=FakeBackend(), **kwargs)


def test_empty_initialization_and_no_reimport(tmp_path, monkeypatch):
    manager = settings(tmp_path)
    assert manager.snapshot().registry.profiles == ()
    assert manager.snapshot().revision == 1
    monkeypatch.setenv("DEEPSEEK_API_KEY", "new-env-key")
    assert settings(tmp_path).snapshot().registry.profiles == ()


def test_keep_replace_clear_and_restart(tmp_path):
    manager = settings(tmp_path)
    saved = manager.save(payload(1))
    assert saved["revision"] == 2
    assert saved["profiles"][0]["key_state"] == "configured"
    assert settings(tmp_path).snapshot().registry.resolve_gateway().api_key == "test-secret-unique"
    manager.save(payload(2, "keep", None))
    assert manager.snapshot().registry.resolve_gateway().api_key == "test-secret-unique"
    manager.save(payload(3, "replace", "rotated-secret"))
    assert manager.snapshot().registry.resolve_gateway().api_key == "rotated-secret"
    manager.save(payload(4, "clear", None))
    assert settings(tmp_path).snapshot().registry.resolve_gateway().api_key == ""


def test_snapshot_is_frozen_independent_and_secret_free(tmp_path, monkeypatch):
    manager = settings(tmp_path)
    manager.save(payload(1))
    old = manager.snapshot()
    manager.save(payload(2, "replace", "new-secret"))
    assert old.registry.resolve_gateway().api_key == "test-secret-unique"
    assert old.registry.profiles is not manager.snapshot().registry.profiles
    with pytest.raises(ValueError):
        old.registry.default_gateway = None
    with pytest.raises(ValueError):
        old.registry.profiles[0].model = "changed"
    assert isinstance(old.registry.profiles, tuple)
    assert "test-secret-unique" not in repr(old)
    assert "test-secret-unique" not in json.dumps(old.registry.model_dump())
    assert "test-secret-unique" not in json.dumps(manager.public_admin())
    assert "new-secret" not in json.dumps(manager.public_admin())
    for path in tmp_path.iterdir():
        assert b"test-secret-unique" not in path.read_bytes()
        assert b"new-secret" not in path.read_bytes()


def test_saved_empty_directory_and_disabled_judge(tmp_path, monkeypatch):
    manager = settings(tmp_path)
    data = payload(1)
    data["judge_enabled"] = False
    manager.save(data)
    assert manager.snapshot().registry.resolve_judge() is None
    assert manager.public_admin()["judge_profile"] == "chat"
    manager.save({"expected_revision": 2, "profiles": [], "default_gateway": None,
                  "judge_profile": None, "judge_enabled": False})
    monkeypatch.setenv("DEEPSEEK_API_KEY", "later-test-key")
    assert settings(tmp_path).snapshot().registry.profiles == ()


def test_decryption_failure_never_falls_back(tmp_path):
    manager = settings(tmp_path)
    manager.save(payload(1))

    class Broken(FakeBackend):
        def decrypt(self, value):
            raise RuntimeError("test-secret-unique")

    with pytest.raises(SettingsError) as error:
        ModelSettings(manager.db_path, backend=Broken())
    assert error.value.status_code == 503
    assert "test-secret-unique" not in str(error.value)


def test_origin_change_requires_explicit_key_action(tmp_path):
    manager = settings(tmp_path)
    manager.save(payload(1))
    data = payload(2, "keep", None)
    data["profiles"][0]["base_url"] = "https://different.example.com/v1"
    with pytest.raises(SettingsError):
        manager.save(data)
    assert manager.snapshot().revision == 2
    data["profiles"][0]["key_action"] = "clear"
    manager.save(data)
    assert manager.snapshot().registry.resolve_gateway().api_key == ""


def test_revision_double_check(tmp_path):
    manager = settings(tmp_path)
    stale = settings(tmp_path)
    manager.save(payload(1))
    with pytest.raises(SettingsError) as error:
        stale.save(payload(1))
    assert error.value.status_code == 409
    assert stale.snapshot().revision == 1
    with pytest.raises(SettingsError) as error:
        manager.save(payload(1))
    assert error.value.status_code == 409


def test_encrypt_and_transaction_failure_leave_old_snapshot(tmp_path):
    manager = settings(tmp_path)
    old = manager.snapshot()

    class Broken(FakeBackend):
        def encrypt(self, value):
            raise RuntimeError(value)

    manager.backend = Broken()
    with pytest.raises(SettingsError) as error:
        manager.save(payload(1))
    assert "test-secret-unique" not in str(error.value)
    assert manager.snapshot() is old
    manager.backend = FakeBackend()
    with sqlite3.connect(manager.db_path) as conn:
        conn.execute("CREATE TRIGGER fail BEFORE UPDATE ON model_settings BEGIN SELECT RAISE(ABORT, 'failure'); END")
    with pytest.raises(SettingsError):
        manager.save(payload(1))
    assert manager.snapshot() is old
    assert settings(tmp_path).snapshot().revision == 1


def test_yaml_readonly_snapshot_captures_environment(tmp_path, monkeypatch):
    config = tmp_path / "models.yaml"
    config.write_text(json.dumps({"version": 1, "profiles": [{"id": "a", "provider": "openai", "model": "gpt"}],
                                 "default_gateway": "a"}), encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "first-env-secret")
    manager = settings(tmp_path, models_config=config)
    monkeypatch.setenv("OPENAI_API_KEY", "second-env-secret")
    assert manager.snapshot().registry.resolve_gateway().api_key == "first-env-secret"
    assert ModelRegistry.load(config).resolve_gateway().api_key == "second-env-secret"
    assert manager.public_admin()["read_only"]
    assert not (tmp_path / "settings.db").exists()
    with pytest.raises(SettingsError) as error:
        manager.save(payload(0))
    assert error.value.status_code == 403
    with pytest.raises(SettingsError):
        settings(tmp_path, models_config=tmp_path / "missing.yaml")


def test_legacy_import_once_and_readonly_helper(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFEORBIT_UPSTREAM_API_KEY", "legacy-test-secret")
    db = tmp_path / "settings.db"
    snapshot = load_model_snapshot(db, backend=FakeBackend())
    assert not db.exists()
    assert snapshot.registry.resolve_gateway().api_key == "legacy-test-secret"
    manager = settings(tmp_path)
    monkeypatch.setenv("SAFEORBIT_UPSTREAM_API_KEY", "rotated-env-secret")
    assert settings(tmp_path).snapshot().registry.resolve_gateway().api_key == "legacy-test-secret"
    assert manager.snapshot().registry.resolve_gateway().api_key == "legacy-test-secret"
    assert b"legacy-test-secret" not in db.read_bytes()


def test_judge_overrides_locked_and_captured(tmp_path, monkeypatch):
    manager = settings(tmp_path)
    manager.save(payload(1))
    monkeypatch.setenv("SAFEORBIT_JUDGE", "0")
    monkeypatch.setenv("SAFEORBIT_JUDGE_PROFILE", "chat")
    overridden = settings(tmp_path)
    assert not overridden.snapshot().judge_enabled
    assert overridden.snapshot().registry.resolve_judge() is None
    assert overridden.public_admin()["judge_enabled_locked"]
    monkeypatch.setenv("SAFEORBIT_JUDGE_PROFILE", "missing")
    assert overridden.snapshot().registry.resolve_judge() is None
    data = payload(2, "keep", None)
    data["profiles"] = []
    data["default_gateway"] = None
    data["judge_profile"] = None
    data["judge_enabled"] = False
    with pytest.raises(SettingsError):
        overridden.save(data)
    with pytest.raises(SettingsError):
        settings(tmp_path)


@pytest.mark.parametrize("url", ["http://localhost:9000/v1", "https://127.0.0.1", "https://10.1.1.1",
                                "https://100.64.0.1", "https://example.com:8443", "https://[::1]",
                                "https://[::ffff:8.8.8.8]", "https://[2002:0808:0808::1]"])
def test_endpoint_restrictions(url):
    with pytest.raises(ValueError):
        ModelProfile(id="a", provider="custom", model="m", base_url=url)


@pytest.mark.skipif(os.name != "nt", reason="Windows CurrentUser DPAPI only")
def test_dpapi_temporary_string():
    backend = DPAPIBackend()
    value = "safeorbit-temporary-integration-string"
    ciphertext = backend.encrypt(value)
    assert value.encode() not in ciphertext
    assert backend.decrypt(ciphertext) == value


def test_cli_override_precedes_environment(tmp_path, monkeypatch):
    config = tmp_path / "models.yaml"
    config.write_text(json.dumps({"version": 1, "profiles": [
        {"id": "review", "provider": "openai", "model": "m", "judge": True}]}), encoding="utf-8")
    monkeypatch.setenv("SAFEORBIT_JUDGE_PROFILE", "missing")
    manager = settings(tmp_path, models_config=config, judge_profile="review", judge_enabled=True)
    assert manager.snapshot().registry.resolve_judge().id == "review"


def test_invalid_legacy_no_bypass(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFEORBIT_UPSTREAM_BASE_URL", "http://legacy.internal/v1")
    with pytest.raises(SettingsError):
        settings(tmp_path)


@pytest.mark.parametrize("action,key", [("replace", "******"), ("keep", "secret"), ("clear", "secret"), ("replace", "")])
def test_invalid_key_operations_safe(tmp_path, action, key):
    manager = settings(tmp_path)
    with pytest.raises(SettingsError) as error:
        manager.save(payload(1, action, key))
    assert "input_value" not in str(error.value)
    assert "secret" not in str(error.value)
