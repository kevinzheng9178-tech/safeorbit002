"""审计参数：默认值、范围校验、持久化与裁判配置叠加。"""

import pytest

from safeorbit.audit_params import AuditParams, AuditParamsError, apply_to_config
from safeorbit.judge import JudgeConfig


@pytest.fixture
def store(tmp_path):
    return AuditParams(str(tmp_path / "params.db"))


def test_defaults_are_stable(store):
    assert store.current() == {
        "timeout_s": 30.0,
        "max_retries": 2,
        "max_tokens": 3200,
        "max_input_chars": 20000,
        "kb_limit": 4,
    }
    public = store.public()
    assert public["params"] == store.current()
    assert public["limits"]["kb_limit"] == {"min": 1, "max": 8, "kind": "int"}


def test_save_persists_for_next_start(store, tmp_path):
    result = store.save({"timeout_s": 60, "max_tokens": 4096}, updated_by="admin")
    assert result["params"]["timeout_s"] == 60.0
    assert result["params"]["max_tokens"] == 4096
    again = AuditParams(str(tmp_path / "params.db"))
    assert again.current()["timeout_s"] == 60.0
    assert again.current()["max_tokens"] == 4096


@pytest.mark.parametrize("values", [
    {"timeout_s": 1},
    {"timeout_s": 999},
    {"max_retries": -1},
    {"max_retries": 6},
    {"max_tokens": 100},
    {"kb_limit": 9},
    {"kb_limit": "abc"},
    {"max_retries": True},
    {"unknown": 1},
])
def test_invalid_values_rejected(store, values):
    with pytest.raises(AuditParamsError):
        store.save(values)


def test_save_merges_partial_payload(store):
    store.save({"timeout_s": 45})
    values = store.current()
    assert values["timeout_s"] == 45.0
    assert values["max_tokens"] == 3200


def test_apply_to_config_overrides_registry_defaults():
    config = JudgeConfig(api_key="test-key")
    apply_to_config(
        {"timeout_s": 45, "max_retries": 1, "max_tokens": 2048,
         "max_input_chars": 8000, "kb_limit": 6},
        config,
    )
    assert config.timeout_s == 45.0
    assert config.max_retries == 1
    assert config.max_tokens == 2048
    assert config.max_input_chars == 8000
    assert config.kb_limit == 6
