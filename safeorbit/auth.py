"""P2 本地账号与会话认证。

设计取舍：
1. 只用标准库：PBKDF2-HMAC-SHA256 加盐，会话 token 只把哈希落库；
2. 角色最小化：viewer < operator < auditor < admin；
3. 身份提供方抽象成 IdentityProvider，本地密码是默认实现，SSO 预留接口。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .storage import connect as connect_db

logger = logging.getLogger("safeorbit.auth")

ROLE_RANK = {"viewer": 1, "operator": 2, "auditor": 3, "admin": 4}
ROLES = list(ROLE_RANK.keys())
PBKDF2_ITERATIONS = 200_000
SESSION_TTL_HOURS = 12
# 默认口令检测只需对同一份哈希做一次 PBKDF2，避免每个请求都重复计算。
_DEFAULT_PASSWORD_CHECKS: Dict[str, bool] = {}


class AuthError(Exception):
    """认证失败 / 权限不足。"""


@dataclass
class User:
    username: str
    role: str
    display_name: str = ""
    disabled: bool = False
    created_at: str = ""
    tenant_id: str = "default"

    def to_public(self) -> Dict[str, Any]:
        return {
            "username": self.username,
            "role": self.role,
            "display_name": self.display_name or self.username,
            "disabled": self.disabled,
            "created_at": self.created_at,
            "tenant_id": self.tenant_id,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def role_at_least(role: str, minimum: str) -> bool:
    return ROLE_RANK.get(role, 0) >= ROLE_RANK.get(minimum, 99)


def _hash_password(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return base64.b64encode(digest).decode("ascii")


def validate_password(password: str) -> None:
    if (len(password or "") < 12 or not any(c.islower() for c in password)
            or not any(c.isupper() for c in password)
            or not any(c.isdigit() for c in password)
            or not any(not c.isalnum() and not c.isspace() for c in password)):
        raise AuthError("密码至少 12 个字符，且包含大写字母、小写字母、数字和符号")


def _encode_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _hash_password(password, salt)
    return "pbkdf2_sha256$%d$%s$%s" % (
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        digest,
    )


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = _hash_password(password, salt, int(iterations))
        return hmac.compare_digest(expected, digest_b64)
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------
# 身份提供方抽象：本地密码 / SSO 预留
# --------------------------------------------------------------------------
class IdentityProvider:
    """身份提供方接口。接入企业 SSO 时实现 authenticate 即可。"""

    name = "base"

    def authenticate(self, username: str, password: str) -> Optional[User]:
        raise NotImplementedError

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "enabled": True}

    def login(self, payload: Dict[str, Any]) -> Optional[User]:
        return self.authenticate(str(payload.get("username", "")), str(payload.get("password", "")))


class LocalPasswordProvider(IdentityProvider):
    name = "local"

    def __init__(self, store: "AuthStore"):
        self.store = store

    def authenticate(self, username: str, password: str) -> Optional[User]:
        return self.store.verify_credentials(username, password)


class SSOProvider(IdentityProvider):
    """SSO 占位：接口与方法已对齐，接 IdP 时替换 authenticate 实现。

    当前落地方式为“可信反向代理断言”：
    IdP 由 oauth2-proxy / 网关完成认证，再把 username、groups、timestamp 与签名传给安轨。
    """

    name = "sso"

    def authenticate(self, username: str, password: str) -> Optional[User]:
        raise AuthError("SSO 仅接受带签名的断言，请通过 /api/auth/sso 登录")

    def login(self, payload: Dict[str, Any]) -> Optional[User]:
        return None

    def describe(self) -> Dict[str, Any]:
        enabled = os.getenv("SAFEORBIT_SSO_ENABLED", "0").lower() in ("1", "true", "yes", "on")
        return {
            "name": self.name,
            "enabled": enabled,
            "mode": "trusted-proxy-assertion",
            "note": "由可信反向代理提供签名断言；未启用时仅本地密码登录",
        }


def _sso_role(groups: List[str]) -> str:
    group_set = {str(item).strip().lower() for item in groups}

    def configured(name: str, default: str) -> str:
        return os.getenv(name, default).strip().lower()

    if configured("SAFEORBIT_SSO_ADMIN_GROUP", "safeorbit-admin") in group_set:
        return "admin"
    if configured("SAFEORBIT_SSO_AUDITOR_GROUP", "safeorbit-auditor") in group_set:
        return "auditor"
    if configured("SAFEORBIT_SSO_OPERATOR_GROUP", "safeorbit-operator") in group_set:
        return "operator"
    return configured("SAFEORBIT_SSO_DEFAULT_ROLE", "viewer")


def verify_sso_assertion(payload: Dict[str, Any], *, max_age_seconds: int = 300) -> Dict[str, Any]:
    """校验反向代理签名断言，返回标准化身份。

    签名串：username + "\n" + 逗号分隔的 groups + "\n" + timestamp
    """

    secret = os.getenv("SAFEORBIT_SSO_SECRET", "")
    if not secret:
        raise AuthError("SSO 未配置签名密钥")
    username = str(payload.get("username") or "").strip()
    if len(username) < 3:
        raise AuthError("SSO 用户名无效")
    groups_raw = payload.get("groups") or []
    if isinstance(groups_raw, str):
        groups = [item.strip() for item in groups_raw.split(",") if item.strip()]
    elif isinstance(groups_raw, (list, tuple)):
        groups = [str(item).strip() for item in groups_raw if str(item).strip()]
    else:
        raise AuthError("SSO groups 格式无效")
    timestamp = payload.get("timestamp")
    try:
        issued_at = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise AuthError("SSO 时间戳无效") from None
    if issued_at.tzinfo is None:
        raise AuthError("SSO 时间戳必须带时区")
    now = _now()
    if abs((now - issued_at.astimezone(timezone.utc)).total_seconds()) > max_age_seconds:
        raise AuthError("SSO 断言已过期")
    canonical = "%s\n%s\n%s" % (username, ",".join(groups), payload.get("timestamp"))
    expected = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    signature = str(payload.get("signature") or "")
    if not hmac.compare_digest(expected, signature):
        raise AuthError("SSO 签名校验失败")
    return {"username": username, "groups": groups, "role": _sso_role(groups)}


# --------------------------------------------------------------------------
# 存储：账号 + 会话
# --------------------------------------------------------------------------
class AuthStore:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        return connect_db(str(self.db_path))

    def _init(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    display_name TEXT,
                    disabled INTEGER DEFAULT 0,
                    created_at TEXT,
                    tenant_id TEXT DEFAULT 'default'
                )
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(users)").fetchall()
            }
            if "tenant_id" not in columns:
                connection.execute(
                    "ALTER TABLE users ADD COLUMN tenant_id TEXT DEFAULT 'default'"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    created_at TEXT,
                    expires_at TEXT
                )
                """
            )

            connection.execute(
                "CREATE TABLE IF NOT EXISTS auth_schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO auth_schema_migrations (version, applied_at) VALUES (1, ?)",
                (_now().isoformat(),),
            )

    # ---------- 账号 ----------
    def count_users(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"] if row else 0)

    def create_user(
        self,
        username: str,
        password: str,
        role: str = "viewer",
        display_name: str = "",
        tenant_id: str = "default",
    ) -> User:
        username = (username or "").strip()
        if len(username) < 3:
            raise AuthError("用户名至少 3 个字符")
        validate_password(password)
        if role not in ROLE_RANK:
            raise AuthError("未知角色：%s" % role)
        tenant_id = (tenant_id or "default").strip() or "default"
        now = _now().isoformat()
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM users WHERE username = ?", (username,)
            ).fetchone()
            if exists:
                raise AuthError("用户已存在：%s" % username)
            connection.execute(
                """
                INSERT INTO users
                (username, password_hash, role, display_name, disabled, created_at, tenant_id)
                VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (username, _encode_password(password), role, display_name or username, now, tenant_id),
            )
        return User(
            username=username,
            role=role,
            display_name=display_name or username,
            created_at=now,
            tenant_id=tenant_id,
        )

    def require_ready(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT username, password_hash FROM users WHERE role = 'admin' AND disabled = 0"
            ).fetchall()
        if not rows:
            raise AuthError("无可用管理员；请在本机运行 py -m safeorbit user init --username <用户名> --db <数据库路径>；已有管理员请运行 user reset-password 后用 user enable 启用")
        if len(_DEFAULT_PASSWORD_CHECKS) > 64:
            _DEFAULT_PASSWORD_CHECKS.clear()
        for row in rows:
            if row["username"] != "admin":
                continue
            digest = row["password_hash"]
            if digest not in _DEFAULT_PASSWORD_CHECKS:
                _DEFAULT_PASSWORD_CHECKS[digest] = _verify_password("safeorbit", digest)
            if _DEFAULT_PASSWORD_CHECKS[digest]:
                raise AuthError("检测到旧默认管理员凭据；请在本机运行 user reset-password --username admin --db <数据库路径>")

    def bootstrap_admin(self, username: str, password: str) -> Optional[User]:
        """本机显式初始化；已有管理员则跳过。"""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM users WHERE role = 'admin'").fetchone():
                return None
            username = (username or "").strip()
            if len(username) < 3:
                raise AuthError("用户名至少 3 个字符")
            validate_password(password)
            if connection.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                raise AuthError("用户已存在，请选用新的管理员用户名")
            now = _now().isoformat()
            connection.execute(
                "INSERT INTO users (username, password_hash, role, display_name, disabled, created_at) VALUES (?, ?, 'admin', ?, 0, ?)",
                (username, _encode_password(password), "初始管理员", now),
            )
        return self.get_user(username)

    def reset_password(self, username: str, password: str) -> None:
        validate_password(password)
        encoded = _encode_password(password)
        with self._connect() as connection:
            cursor = connection.execute("UPDATE users SET password_hash = ? WHERE username = ?", (encoded, username))
            if not cursor.rowcount:
                raise AuthError("用户不存在")
            connection.execute("DELETE FROM sessions WHERE username = ?", (username,))

    def verify_credentials(self, username: str, password: str) -> Optional[User]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ?", ((username or "").strip(),)
            ).fetchone()
        if row is None:
            return None
        if int(row["disabled"] or 0):
            return None
        if not _verify_password(password or "", row["password_hash"]):
            return None
        return User(
            username=row["username"],
            role=row["role"],
            display_name=row["display_name"] or row["username"],
            disabled=False,
            created_at=row["created_at"] or "",
            tenant_id=row["tenant_id"] or "default",
        )

    def get_user(self, username: str) -> Optional[User]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
        if row is None:
            return None
        return User(
            username=row["username"],
            role=row["role"],
            display_name=row["display_name"] or row["username"],
            disabled=bool(row["disabled"]),
            created_at=row["created_at"] or "",
            tenant_id=row["tenant_id"] or "default",
        )

    def ensure_sso_user(
        self,
        username: str,
        role: str,
        display_name: str = "",
        tenant_id: str = "default",
    ) -> User:
        """SSO 用户自动落地；口令列写为不可用值，禁止本地密码登录。"""

        username = (username or "").strip()
        if len(username) < 3 or len(username) > 128:
            raise AuthError("SSO 用户名长度必须在 3-128 之间")
        if role not in ROLE_RANK:
            raise AuthError("未知角色：%s" % role)
        tenant_id = (tenant_id or "default").strip() or "default"
        now = _now().isoformat()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO users (username, password_hash, role, display_name, disabled, created_at, tenant_id) "
                    "VALUES (?, 'sso$disabled', ?, ?, 0, ?, ?)",
                    (username, role, display_name or username, now, tenant_id),
                )
            else:
                connection.execute(
                    "UPDATE users SET role = ?, display_name = ?, disabled = 0, tenant_id = ? WHERE username = ?",
                    (role, display_name or row["display_name"] or username, tenant_id, username),
                )
        return User(
            username=username,
            role=role,
            display_name=display_name or username,
            disabled=False,
            created_at=now,
            tenant_id=tenant_id,
        )

    def list_users(self) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT username, role, display_name, disabled, created_at, tenant_id "
                "FROM users ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def set_disabled(self, username: str, disabled: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET disabled = ? WHERE username = ?",
                (1 if disabled else 0, username),
            )

    # ---------- 会话 ----------
    def create_session(self, username: str, ttl_hours: int = SESSION_TTL_HOURS) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = _now()
        expires = now + timedelta(hours=ttl_hours)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO sessions (token_hash, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token_hash, username, now.isoformat(), expires.isoformat()),
            )
        return token

    def resolve_session(self, token: Optional[str]) -> Optional[User]:
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)
            ).fetchone()
        if row is None:
            return None
        try:
            expires_at = datetime.fromisoformat(row["expires_at"])
        except (TypeError, ValueError):
            return None
        if expires_at <= _now():
            self.delete_session(token)
            return None
        user = self.get_user(row["username"])
        if user is None or user.disabled:
            return None
        return user

    def renew_session(
        self,
        token: Optional[str],
        *,
        ttl_hours: int = SESSION_TTL_HOURS,
        absolute_hours: int = 24 * 7,
        renew_within_hours: float = 1.0,
    ) -> bool:
        """滑动续期：距过期不足阈值时延长会话，但不超过绝对上限。

        返回 True 表示需要重新下发 Cookie。续期只延长服务端记录，
        Cookie 由调用方按同样属性重设，避免凭据被无限续命。
        """

        if not token:
            return False
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT created_at, expires_at FROM sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        if row is None:
            return False
        now = _now()
        try:
            created_at = datetime.fromisoformat(row["created_at"])
            expires_at = datetime.fromisoformat(row["expires_at"])
        except (TypeError, ValueError):
            return False
        if expires_at <= now:
            return False
        if now - created_at >= timedelta(hours=absolute_hours):
            return False
        if expires_at - now > timedelta(hours=renew_within_hours):
            return False
        new_expires = min(now + timedelta(hours=ttl_hours), created_at + timedelta(hours=absolute_hours))
        with self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                (new_expires.isoformat(), token_hash),
            )
        return True

    def delete_session(self, token: Optional[str]) -> None:
        if not token:
            return
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def purge_expired(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM sessions WHERE expires_at <= ?", (_now().isoformat(),)
            )
        return cursor.rowcount


class AuthManager:
    """认证门面：本地密码 + SSO 预留，统一给 API 层使用。"""

    def __init__(self, store: AuthStore):
        self.store = store
        self.providers: Dict[str, IdentityProvider] = {
            "local": LocalPasswordProvider(store),
            "sso": SSOProvider(),
        }
        self.bootstrap_note = ""

    def login(self, username: str, password: str, provider: str = "local") -> User:
        self.store.require_ready()
        impl = self.providers.get(provider)
        if impl is None:
            raise AuthError("未知身份提供方：%s" % provider)
        user = impl.authenticate(username, password)
        if user is None:
            logger.warning("登录失败 provider=%s username=%s", provider, username)
            raise AuthError("用户名或密码错误")
        logger.info("登录成功 provider=%s username=%s role=%s", provider, username, user.role)
        return user

    def sso_login(self, payload: Dict[str, Any]) -> User:
        if os.getenv("SAFEORBIT_SSO_ENABLED", "0").lower() not in ("1", "true", "yes", "on"):
            raise AuthError("SSO 未启用；请设置 SAFEORBIT_SSO_ENABLED=1 与 SAFEORBIT_SSO_SECRET")
        identity = verify_sso_assertion(payload)
        return self.store.ensure_sso_user(
            identity["username"],
            identity["role"],
            display_name=identity["username"],
        )

    def issue_session(self, user: User, ttl_hours: int = SESSION_TTL_HOURS) -> str:
        return self.store.create_session(user.username, ttl_hours=ttl_hours)

    def authenticate_session(self, token: Optional[str]) -> Optional[User]:
        try:
            self.store.require_ready()
        except AuthError as exc:
            logger.warning("会话校验被拒绝：%s", exc)
            return None
        user = self.store.resolve_session(token)
        if user is None and token:
            logger.info("会话无效或已过期")
        return user

    def logout(self, token: Optional[str]) -> None:
        self.store.delete_session(token)

    def provider_status(self) -> List[Dict[str, Any]]:
        return [provider.describe() for provider in self.providers.values()]

    def summary(self) -> Dict[str, Any]:
        return {
            "users": self.store.count_users(),
            "providers": self.provider_status(),
            "bootstrap_note": self.bootstrap_note,
        }
