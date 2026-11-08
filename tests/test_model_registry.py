import json
from pathlib import Path

import pytest
import yaml

from safeorbit.model_registry import ModelProfile, ModelRegistry


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ("SAFEORBIT_MODELS_CONFIG", "SAFEORBIT_JUDGE_PROFILE", "SAFEORBIT_UPSTREAM_API_KEY",
                 "SAFEORBIT_UPSTREAM_BASE_URL", "SAFEORBIT_UPSTREAM_MODEL", "DEEPSEEK_API_KEY",
                 "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
        monkeypatch.delenv(name, raising=False)


def write_config(tmp_path, **updates):
    data = {"version": 1, "default_gateway": "chat", "judge_profile": "review",
            "profiles": [
                {"id": "chat", "provider": "openai", "model": "gpt-4o-mini"},
                {"id": "review", "provider": "deepseek", "model": "deepseek-chat", "judge": True},
            ]}
    data.update(updates)
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_example_and_provider_defaults(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "models.example.yaml"
    registry = ModelRegistry.load(path)
    assert not registry.legacy
    assert isinstance(registry.profiles, list)
    assert [p.provider for p in registry.profiles] == ["deepseek", "dashscope", "openai", "custom"]
    assert registry.resolve_gateway().base_url == "https://api.deepseek.com"
    assert registry.profiles[1].base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert registry.profiles[2].base_url == "https://api.openai.com/v1"
    assert registry.resolve_judge().id == "deepseek-chat"
    with pytest.raises(ValueError):
        registry.resolve_gateway("custom-example")
    with pytest.raises(ValueError):
        registry.resolve_judge("qwen-plus")


def test_runtime_key_public_and_repr(tmp_path, monkeypatch):
    registry = ModelRegistry.load(write_config(tmp_path))
    profile = registry.resolve_gateway()
    assert profile.api_key == ""
    monkeypatch.setenv("OPENAI_API_KEY", "fake-top-secret")
    assert profile.api_key == "fake-top-secret"
    assert profile.public()["key_configured"] is True
    assert "fake-top-secret" not in repr(registry)
    assert "fake-top-secret" not in json.dumps(profile.public())
    assert "base_url" not in profile.public()
    assert "api_key_env" not in profile.public()
    monkeypatch.setenv("OPENAI_API_KEY", "rotated")
    assert profile.api_key == "rotated"


def test_environment_and_selections(tmp_path, monkeypatch):
    path = write_config(tmp_path)
    monkeypatch.setenv("SAFEORBIT_MODELS_CONFIG", str(path))
    registry = ModelRegistry.load()
    assert registry.resolve_gateway().id == "chat"
    assert registry.resolve_judge().id == "review"
    for resolve in (registry.resolve_gateway, registry.resolve_judge):
        with pytest.raises(ValueError):
            resolve("unknown-secret-alias")
    monkeypatch.setenv("SAFEORBIT_JUDGE_PROFILE", "chat")
    with pytest.raises(ValueError):
        ModelRegistry.load()


@pytest.mark.parametrize("updates", [
    {"version": 2}, {"profiles": []},
    {"default_gateway": "unknown"}, {"judge_profile": "chat"},
    {"profiles": [{"id": "chat", "provider": "openai", "model": "a"}] * 2},
    {"profiles": [{"id": "chat", "provider": "unsupported", "model": "a"}]},
    {"profiles": [{"id": "chat", "provider": "openai", "model": "a", "api_key_env": "bad-name"}]},
    {"profiles": [{"id": "chat", "provider": "openai", "model": "a", "api_key": "fake-secret"}]},
    {"legacy": True},
])
def test_invalid_configuration_is_sanitized(tmp_path, updates):
    with pytest.raises(ValueError) as exc:
        ModelRegistry.load(write_config(tmp_path, **updates))
    assert "fake-secret" not in str(exc.value)
    assert "input_value" not in str(exc.value)


@pytest.mark.parametrize("url", [
    "http://remote.example/v1", "https://user:fake-secret@example.com", "https://example.com/?key=fake-secret",
    "https://example.com/#fake-secret", "ftp://example.com", "https://example.com:bad", "https://example.com\\evil",
    "http://127.0.0.1:9000/v1", "http://[::1]:9000/v1", "http://localhost:9000/v1",
])
def test_custom_url_restrictions(tmp_path, url):
    path = write_config(tmp_path, judge_profile=None, profiles=[
        {"id": "chat", "provider": "custom", "model": "a", "base_url": url, "api_key_env": "CUSTOM_KEY"}])
    with pytest.raises(ValueError) as exc:
        ModelRegistry.load(path)
    assert "fake-secret" not in str(exc.value)


@pytest.mark.parametrize("url", ["https://remote.example/v1", "https://remote.example:443/v1"])
def test_custom_allowed_urls(url):
    assert ModelProfile(id="custom", provider="custom", model="local", base_url=url, api_key_env="LOCAL_KEY").base_url == url


@pytest.mark.parametrize("url", ["http://api.openai.com/v1", "https://evil.example/v1", "https://api.openai.com:8443/v1"])
def test_builtin_url_restrictions(url):
    with pytest.raises(ValueError):
        ModelProfile(id="chat", provider="openai", model="gpt", base_url=url)


def test_missing_default_and_same_provider_aliases(tmp_path):
    registry = ModelRegistry.load(write_config(tmp_path, default_gateway=None, judge_profile=None, profiles=[
        {"id": "one", "provider": "openai", "model": "gpt"},
        {"id": "two", "provider": "openai", "model": "gpt"},
    ]))
    with pytest.raises(ValueError):
        registry.resolve_gateway()
    assert registry.resolve_gateway("two").model == "gpt"
    assert registry.resolve_judge() is None


def test_explicit_missing_and_bad_yaml_never_fallback(tmp_path):
    with pytest.raises(ValueError):
        ModelRegistry.load(tmp_path / "missing.yaml")
    path = tmp_path / "bad.yaml"
    path.write_text("profiles: [fake-secret", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        ModelRegistry.load(path)
    assert "fake-secret" not in str(exc.value)


def test_legacy_preserves_upstream(monkeypatch):
    monkeypatch.setenv("SAFEORBIT_UPSTREAM_BASE_URL", "https://legacy.example/v1/")
    monkeypatch.setenv("SAFEORBIT_UPSTREAM_API_KEY", "legacy-key")
    monkeypatch.setenv("SAFEORBIT_UPSTREAM_MODEL", "legacy-model")
    registry = ModelRegistry.load()
    assert registry.legacy
    profile = registry.resolve_gateway()
    assert profile.base_url == "https://legacy.example/v1"
    assert profile.api_key == "legacy-key"
    assert profile.model == "legacy-model"
    assert registry.resolve_judge() is None
