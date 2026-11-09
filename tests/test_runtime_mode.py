"""全局运行模式：默认值、网页持久化、启动参数锁定与非法输入。"""

import pytest

from safeorbit.runtime_mode import RuntimeMode, RuntimeModeError


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("SAFEORBIT_MODE", raising=False)


@pytest.fixture
def store(tmp_path):
    return RuntimeMode(str(tmp_path / "mode.db"))


def test_default_is_shadow_and_not_locked(store):
    assert store.current() == "shadow"
    assert store.status() == {"mode": "shadow", "source": "default", "locked": False}


def test_switch_persists_for_next_start(store, tmp_path):
    result = store.set("enforce", updated_by="admin")
    assert result["mode"] == "enforce"
    assert result["previous"] == "shadow"
    assert result["source"] == "admin"
    again = RuntimeMode(str(tmp_path / "mode.db"))
    assert again.current() == "enforce"
    assert again.status()["source"] == "admin"


def test_startup_argument_locks_web_switch(tmp_path):
    locked = RuntimeMode(str(tmp_path / "mode.db"), startup_mode="enforce")
    assert locked.current() == "enforce"
    assert locked.status() == {"mode": "enforce", "source": "startup", "locked": True}
    with pytest.raises(RuntimeModeError) as excinfo:
        locked.set("shadow", updated_by="admin")
    assert excinfo.value.status_code == 409
    # 启动参数只影响本次进程，不写库，也不覆盖网页保存值
    assert RuntimeMode(str(tmp_path / "mode.db")).current() == "shadow"


def test_env_mode_locks_when_present(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFEORBIT_MODE", "enforce")
    mode = RuntimeMode(str(tmp_path / "mode.db"))
    assert mode.locked() is True
    assert mode.current() == "enforce"


def test_invalid_env_value_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFEORBIT_MODE", "sometimes")
    mode = RuntimeMode(str(tmp_path / "mode.db"))
    assert mode.locked() is False
    assert mode.current() == "shadow"


@pytest.mark.parametrize("value", ["", "block", None, 1])
def test_invalid_mode_rejected(store, value):
    with pytest.raises(RuntimeModeError) as excinfo:
        store.set(value)
    assert excinfo.value.status_code == 400


def test_explicit_web_value_wins_over_stale_default(store, tmp_path):
    store.set("enforce", updated_by="admin")
    store.set("shadow", updated_by="operator")
    assert RuntimeMode(str(tmp_path / "mode.db")).current() == "shadow"
