"""本机受信模型目录；凭据仅在使用时从环境变量读取。"""
from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from typing import List, Literal, Optional, Tuple, Union
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator


class _Credential:
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return "<credential hidden>"


_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openai": "https://api.openai.com/v1",
}
_KEY_ENVS = {"deepseek": "DEEPSEEK_API_KEY", "dashscope": "DASHSCOPE_API_KEY", "openai": "OPENAI_API_KEY"}


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    display_name: str = ""
    _credential: Optional[_Credential] = PrivateAttr(default=None)

    provider: Literal["deepseek", "dashscope", "openai", "custom"]
    base_url: str = Field(default="", repr=False)
    model: str = Field(min_length=1)
    api_key_env: str = Field(default="", pattern=r"^(?:[A-Za-z_][A-Za-z0-9_]*)?$")
    gateway: bool = True
    judge: bool = False
    supports_stream: bool = True
    supports_json_object: bool = True

    @model_validator(mode="after")
    def validate_endpoint(self):
        if not self.base_url:
            self.base_url = _BASE_URLS.get(self.provider, "")
        if not self.api_key_env:
            self.api_key_env = _KEY_ENVS.get(self.provider, "")
        if not self.base_url or not self.model.strip():
            raise ValueError("模型地址、模型名称和密钥环境变量名称不能为空")
        try:
            url = urlsplit(self.base_url)
            port = url.port
            if (not url.hostname or url.username is not None or url.password is not None
                    or url.query or url.fragment or "?" in self.base_url or "#" in self.base_url
                    or "\\" in self.base_url or any(c.isspace() for c in self.base_url)):
                raise ValueError()
            if self.provider in _BASE_URLS:
                expected = urlsplit(_BASE_URLS[self.provider])
                if url.scheme != "https" or url.hostname != expected.hostname or port not in (None, 443):
                    raise ValueError()
            if url.scheme != "https" or port not in (None, 443):
                raise ValueError()
            host = url.hostname.lower().rstrip(".")
            if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host and ":" not in host:
                raise ValueError()
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None:
                if (not address.is_global or address.is_multicast or address.is_reserved
                        or address.is_unspecified or address.is_loopback or address.is_link_local) or (address.version == 6 and (
                        address.ipv4_mapped is not None or address.sixtofour is not None
                        or address.teredo is not None or address in ipaddress.ip_network("64:ff9b::/96")
                        or address in ipaddress.ip_network("64:ff9b:1::/48"))):
                    raise ValueError()
        except ValueError:
            raise ValueError("模型地址不符合安全限制") from None
        self.base_url = self.base_url.rstrip("/")
        return self

    @property
    def api_key(self) -> str:
        return self._credential.value if self._credential is not None else os.getenv(self.api_key_env, "")

    def public(self) -> dict:
        return {
            "id": self.id, "display_name": self.display_name, "provider": self.provider, "model": self.model,
            "gateway": self.gateway, "judge": self.judge,
            "supports_stream": self.supports_stream,
            "supports_json_object": self.supports_json_object,
            "key_configured": bool(self.api_key),
        }


class ModelRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1] = 1
    legacy: bool = False
    profiles: Union[List[ModelProfile], Tuple[ModelProfile, ...]] = Field(default_factory=list)
    _snapshot: bool = PrivateAttr(default=False)
    default_gateway: Optional[str] = None
    judge_profile: Optional[str] = None

    @model_validator(mode="after")
    def validate_selections(self):
        ids = [p.id for p in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("模型别名重复")
        if self.default_gateway is not None:
            self.resolve_gateway(self.default_gateway)
        if self.judge_profile is not None:
            self._resolve(self.judge_profile, "judge")
        return self

    @classmethod
    def load(cls, path=None, *, apply_judge_override=True) -> "ModelRegistry":
        selected = path if path is not None else os.getenv("SAFEORBIT_MODELS_CONFIG")
        if selected is None or selected == "":
            key_env = ("SAFEORBIT_UPSTREAM_API_KEY" if os.getenv("SAFEORBIT_UPSTREAM_API_KEY")
                       else "DEEPSEEK_API_KEY")
            model = os.getenv("SAFEORBIT_UPSTREAM_MODEL") or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
            try:
                profile = ModelProfile(
                    id=model if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", model) else "legacy-upstream",
                    provider="custom", model=model,
                    base_url=(os.getenv("SAFEORBIT_UPSTREAM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL")
                              or _BASE_URLS["deepseek"]).rstrip("/"),
                    api_key_env=key_env,
                )
            except ValueError:
                raise ValueError("旧模型配置无效（配置内容已隐藏）") from None
            registry = cls(legacy=True, profiles=[profile], default_gateway=profile.id)
        else:
            try:
                data = yaml.safe_load(Path(selected).read_text(encoding="utf-8"))
                if not isinstance(data, dict) or "legacy" in data or "version" not in data:
                    raise ValueError()
                registry = cls.model_validate(data)
            except (OSError, UnicodeError, yaml.YAMLError, ValueError, TypeError):
                raise ValueError("模型目录配置无效，请检查文件、版本、别名、用途及地址（配置内容已隐藏）") from None
        if apply_judge_override and os.getenv("SAFEORBIT_JUDGE_PROFILE"):
            registry.resolve_judge()
        return registry

    def _resolve(self, alias: str, purpose: str) -> ModelProfile:
        profile = next((p for p in self.profiles if p.id == alias), None)
        if profile is None or not getattr(profile, purpose):
            raise ValueError("模型别名不存在或未允许 %s 用途" % purpose)
        return profile

    def resolve_gateway(self, alias: Optional[str] = None) -> ModelProfile:
        selected = alias if alias is not None else self.default_gateway
        if selected is None:
            raise ValueError("未配置默认网关模型")
        return self._resolve(selected, "gateway")

    def resolve_judge(self, alias: Optional[str] = None) -> Optional[ModelProfile]:
        selected = alias if alias is not None else (
            self.judge_profile if self._snapshot else os.getenv("SAFEORBIT_JUDGE_PROFILE") or self.judge_profile)
        return None if selected is None else self._resolve(selected, "judge")

    def frozen_snapshot(self, keys=None, judge_profile=None, override_judge=False) -> "ModelRegistry":
        profiles = []
        for profile in self.profiles:
            frozen = _FrozenProfile.model_construct(**profile.model_dump())
            frozen._credential = _Credential(profile.api_key if keys is None else keys.get(profile.id, ""))
            profiles.append(frozen)
        data = self.model_dump(exclude={"profiles"})
        if override_judge:
            data["judge_profile"] = judge_profile
        result = _FrozenRegistry.model_construct(**data, profiles=tuple(profiles))
        result._snapshot = True
        return result


class _FrozenProfile(ModelProfile):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _FrozenRegistry(ModelRegistry):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
