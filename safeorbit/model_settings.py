"""单进程模型设置、固定凭据快照与 Windows CurrentUser 密钥保护。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import threading
from urllib.parse import urlsplit

from .model_registry import ModelRegistry
from .storage import connect as connect_db


class SettingsError(ValueError):
    def __init__(self, message="模型设置无效（配置内容已隐藏）", status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class DPAPIBackend:
    """无 UI、无明文降级；密文绑定当前 Windows 账户。"""

    def _crypt(self, value, decrypt=False):
        if os.name != "nt":
            raise SettingsError("本机密钥保护不可用", 503)

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        buffer = ctypes.create_string_buffer(value)
        source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        output = Blob()
        function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        function.restype = wintypes.BOOL
        try:
            if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
                raise SettingsError("本机密钥保护失败，请重新配置密钥", 503)
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            if output.pbData:
                kernel.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))

    def encrypt(self, value: str) -> bytes:
        return self._crypt(value.encode("utf-8"))

    def decrypt(self, value: bytes) -> str:
        return self._crypt(value, decrypt=True).decode("utf-8")


@dataclass(frozen=True)
class ModelSnapshot:
    registry: ModelRegistry
    revision: int
    judge_enabled: bool


def _origin(url):
    parsed = urlsplit(url)
    return parsed.scheme.lower(), parsed.hostname.lower(), parsed.port or 443


class ModelSettings:
    """save 接收完整设置；profiles 每项附 key_action 与仅 replace 时的 api_key。"""

    def __init__(self, db_path, *, models_config=None, backend=None, judge_profile=None,
                 judge_enabled=None, readonly=False):
        self.db_path = str(db_path)
        self.backend = backend if backend is not None else DPAPIBackend()
        self._lock = threading.RLock()
        self._yaml = models_config if models_config is not None else os.getenv("SAFEORBIT_MODELS_CONFIG")
        self.source = "yaml" if self._yaml else "sqlite"
        self.read_only = bool(self._yaml) or readonly
        self._judge_profile_override = judge_profile if judge_profile is not None else os.getenv("SAFEORBIT_JUDGE_PROFILE") or None
        self._judge_enabled_override = judge_enabled
        if judge_enabled is None and "SAFEORBIT_JUDGE" in os.environ:
            value = os.environ["SAFEORBIT_JUDGE"].strip().lower()
            if value not in ("1", "0", "true", "false", "yes", "no", "on", "off"):
                raise SettingsError("裁判启动开关无效")
            self._judge_enabled_override = value in ("1", "true", "yes", "on")
        if self._judge_enabled_override is not None and type(self._judge_enabled_override) is not bool:
            raise SettingsError("裁判启动开关无效")
        self._ciphertexts = {}
        try:
            if self._yaml:
                registry = ModelRegistry.load(self._yaml, apply_judge_override=False)
                self._config = dict(registry.model_dump(), judge_enabled=False)
                self._current = self._build(registry, 0, False)
                return
            row = None
            if readonly:
                if Path(self.db_path).exists():
                    with self._connect(readonly=True) as conn:
                        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_settings'").fetchone()
                        if exists:
                            row = conn.execute("SELECT revision, config_json FROM model_settings WHERE id=1").fetchone()
                            if row:
                                self._ciphertexts = dict(conn.execute("SELECT profile_id, ciphertext FROM model_credentials"))
            else:
                with self._connect() as conn:
                    conn.execute("CREATE TABLE IF NOT EXISTS model_settings (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, config_json TEXT NOT NULL)")
                    conn.execute("CREATE TABLE IF NOT EXISTS model_credentials (profile_id TEXT PRIMARY KEY, ciphertext BLOB NOT NULL)")
                    row = conn.execute("SELECT revision, config_json FROM model_settings WHERE id=1").fetchone()
                    if row:
                        self._ciphertexts = dict(conn.execute("SELECT profile_id, ciphertext FROM model_credentials"))
            if row:
                self._config = json.loads(row[1])
                registry, enabled = self._parse_config(self._config)
                self._current = self._build(registry, row[0], enabled, self._decrypt(self._ciphertexts))
            else:
                registry = self._legacy()
                keys = {p.id: p.api_key for p in registry.profiles}
                self._config = dict(registry.model_dump(), judge_enabled=False)
                candidate = self._build(registry, 0 if readonly else 1, False, keys)
                if not readonly:
                    ciphertexts = {alias: self._encrypt(key) for alias, key in keys.items() if key}
                    with self._connect() as conn:
                        conn.execute("BEGIN IMMEDIATE")
                        if conn.execute("SELECT 1 FROM model_settings WHERE id=1").fetchone():
                            raise SettingsError("模型设置已变更，请重新加载", 409)
                        conn.execute("INSERT INTO model_settings VALUES (1, ?, ?)", (1, json.dumps(self._config)))
                        conn.executemany("INSERT INTO model_credentials VALUES (?, ?)", ciphertexts.items())
                    self._ciphertexts = ciphertexts
                self._current = candidate
        except SettingsError:
            raise
        except Exception:
            raise SettingsError("模型设置加载失败（配置内容已隐藏）", 503) from None

    @contextmanager
    def _connect(self, readonly=False):
        conn = (sqlite3.connect(Path(self.db_path).resolve().as_uri() + "?mode=ro", uri=True)
                if readonly else connect_db(self.db_path))
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _legacy(self):
        names = ("SAFEORBIT_UPSTREAM_API_KEY", "DEEPSEEK_API_KEY", "SAFEORBIT_UPSTREAM_MODEL",
                 "DEEPSEEK_MODEL", "SAFEORBIT_UPSTREAM_BASE_URL", "DEEPSEEK_BASE_URL")
        if not any(os.getenv(name) for name in names):
            return ModelRegistry()
        return ModelRegistry.load(apply_judge_override=False)

    def _encrypt(self, key):
        try:
            value = self.backend.encrypt(key)
            if not isinstance(value, bytes) or not value:
                raise ValueError()
            return value
        except Exception:
            raise SettingsError("本机密钥加密失败，设置未保存", 503) from None

    def _decrypt(self, ciphertexts):
        try:
            keys = {alias: self.backend.decrypt(value) for alias, value in ciphertexts.items()}
            if any(not isinstance(value, str) for value in keys.values()):
                raise ValueError()
            return keys
        except Exception:
            raise SettingsError("本机密钥解密失败，请使用原服务账户或重新配置", 503) from None

    def _parse_config(self, config):
        data = dict(config)
        enabled = data.pop("judge_enabled", False)
        if type(enabled) is not bool:
            raise SettingsError()
        registry = ModelRegistry.model_validate(data)
        return registry, enabled

    def _build(self, registry, revision, enabled, keys=None):
        selected = self._judge_profile_override or registry.judge_profile
        if selected is not None:
            registry._resolve(selected, "judge")
        effective_enabled = enabled if self._judge_enabled_override is None else self._judge_enabled_override
        if effective_enabled and selected is None:
            raise SettingsError("启用裁判前请选择裁判模型")
        frozen = registry.frozen_snapshot(keys, judge_profile=selected if effective_enabled else None, override_judge=True)
        return ModelSnapshot(frozen, revision, effective_enabled)

    def snapshot(self):
        return self._current

    def public_admin(self):
        with self._lock:
            snapshot = self._current
            profiles = []
            for profile in snapshot.registry.profiles:
                item = profile.model_dump()
                item.pop("api_key_env", None)
                item.update(key_configured=bool(profile.api_key),
                            key_state="configured" if profile.api_key else "missing")
                profiles.append(item)
            return {"version": 1, "profiles": profiles,
                    "default_gateway": self._config["default_gateway"],
                    "judge_profile": self._config["judge_profile"],
                    "judge_enabled": self._config["judge_enabled"],
                    "effective_judge_profile": snapshot.registry.judge_profile,
                    "effective_judge_enabled": snapshot.judge_enabled,
                    "revision": snapshot.revision, "source": self.source,
                    "read_only": self.read_only,
                    "read_only_reason": "显式 YAML 配置仅可读取，修改后重启" if self._yaml else ("CLI 只读加载" if self.read_only else ""),
                    "overrides": {"judge_profile": self._judge_profile_override,
                                  "judge_enabled": self._judge_enabled_override},
                    "override_reason": "启动参数或环境变量覆盖裁判设置" if (
                        self._judge_profile_override is not None or self._judge_enabled_override is not None) else "",
                    "judge_profile_locked": self._judge_profile_override is not None,
                    "judge_enabled_locked": self._judge_enabled_override is not None}

    def save(self, payload: dict, actor: str = "") -> dict:
        del actor  # 不将身份或任何表单输入写入凭据表或日志。
        with self._lock:
            if self.read_only:
                raise SettingsError("当前模型设置为只读", 403)
            try:
                if not isinstance(payload, dict) or set(payload) - {
                        "expected_revision", "version", "profiles", "default_gateway", "judge_profile", "judge_enabled"}:
                    raise SettingsError()
                revision = payload.get("expected_revision")
                if type(revision) is not int:
                    raise SettingsError("保存必须携带 expected_revision")
                if revision != self._current.revision:
                    raise SettingsError("模型设置已变更，请重新加载", 409)
                if not {"profiles", "default_gateway", "judge_profile", "judge_enabled"} <= payload.keys():
                    raise SettingsError("请提交完整模型设置")
                data = {k: v for k, v in payload.items() if k != "expected_revision"}
                raw_profiles = data["profiles"]
                if not isinstance(raw_profiles, list):
                    raise SettingsError()
                profiles, operations = [], {}
                for raw in raw_profiles:
                    item = dict(raw)
                    action = item.pop("key_action", "keep")
                    key = item.pop("api_key", "")
                    if action not in ("keep", "replace", "clear") or not isinstance(key, str):
                        raise SettingsError("密钥操作无效")
                    if (action == "replace" and (not key.strip() or key.strip(" *•●") == "")) or (action != "replace" and key):
                        raise SettingsError("密钥操作无效，请勿提交掩码")
                    if "api_key_env" in item:
                        raise SettingsError("本机设置不接受密钥环境变量")
                    operations[item["id"]] = action, key
                    profiles.append(item)
                data["profiles"] = profiles
                registry, enabled = self._parse_config(data)
                if self._judge_profile_override is not None:
                    registry._resolve(self._judge_profile_override, "judge")
                    if data["judge_profile"] != self._config["judge_profile"]:
                        raise SettingsError("裁判模型已被启动参数锁定")
                if self._judge_enabled_override is not None and enabled != self._config["judge_enabled"]:
                    raise SettingsError("裁判开关已被启动参数锁定")
                old = {p.id: p for p in self._current.registry.profiles}
                ciphertexts, keys = {}, {}
                for profile in registry.profiles:
                    action, key = operations[profile.id]
                    previous = old.get(profile.id)
                    if action == "keep":
                        if previous and _origin(profile.base_url) != _origin(previous.base_url):
                            raise SettingsError("修改连接 origin 后必须重新填写或明确清除密钥")
                        key = previous.api_key if previous else ""
                        if profile.id in self._ciphertexts:
                            ciphertexts[profile.id] = self._ciphertexts[profile.id]
                    elif action == "replace":
                        ciphertexts[profile.id] = self._encrypt(key)
                    else:
                        key = ""
                    keys[profile.id] = key
                config = dict(registry.model_dump(), judge_enabled=enabled)
                candidate = self._build(registry, revision + 1, enabled, keys)
                encoded = json.dumps(config, ensure_ascii=False)
                with self._connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute("SELECT revision FROM model_settings WHERE id=1").fetchone()
                    if row is None or row[0] != revision:
                        raise SettingsError("模型设置已变更，请重新加载", 409)
                    conn.execute("UPDATE model_settings SET revision=?, config_json=? WHERE id=1", (revision + 1, encoded))
                    conn.execute("DELETE FROM model_credentials")
                    conn.executemany("INSERT INTO model_credentials VALUES (?, ?)", ciphertexts.items())
                self._config = config
                self._ciphertexts = ciphertexts
                self._current = candidate
                return self.public_admin()
            except SettingsError:
                raise
            except Exception:
                raise SettingsError("模型设置保存失败（配置内容已隐藏）") from None


def load_model_snapshot(db_path, **kwargs) -> ModelSnapshot:
    """CLI/audit 只读加载；未初始化时仅在内存读取 legacy，不创建或写入数据库。"""
    return ModelSettings(db_path, readonly=True, **kwargs).snapshot()
