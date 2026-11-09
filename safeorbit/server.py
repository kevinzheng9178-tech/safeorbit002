"""P2/P3 接入服务：网关 + 实时审计 + 策略 + 处置 + 代码审计。

运行模式：
- shadow：只记录、只告警、只生成处置提案，不阻断业务；
- enforce：隔离会话、阻断工具、拒绝超预算调用。
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import weakref
import zipfile
from contextlib import asynccontextmanager, suppress
from uuid import uuid4

from .agents import AgentAuthError, AgentStore
from .adapters import integration_snippet
from .approval import ApprovalStore
from .security import SecurityMiddleware, RateLimiter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
import anyio
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

from .auth import AuthError, AuthManager, AuthStore, User, role_at_least
from .codeaudit import (
    CodeScanReport,
    load_code_report,
    load_code_reports,
    render_code_report_html,
    report_to_json as code_report_to_json,
    report_to_sarif,
    scan_path as code_scan_path,
)
from .dashboard import DASHBOARD_HTML
from .disposition import ACTIONS, DispositionStore
from . import eval as eval_mod
from .gateway import build_gateway_trajectory, mock_completion
from . import infrascan
from .judge import JudgeConfig, judge_trajectory
from . import judge as judge_mod
from .models import AuditReport, Span, SpanKind, SpanStatus, Trajectory
from .model_registry import ModelProfile, ModelRegistry
from .model_settings import ModelSettings, ModelSnapshot, SettingsError
from . import circuit as circuit_mod
from . import guard as content_guard, notify, outbound
from . import retention
from . import sca
from .normalize import normalize
from .ops import AuditLog, Metrics
from .otel import parse_otlp_json, parse_otlp_protobuf
from .pricing import load_pricing
from .policy import Policy, PolicyStore
from .report import build_report, render_html
from .rules import AuditContext, RuleEngine, default_rules
from .store import EventBus, TraceStore
from .eventstore import TraceEventStore
from .summary import SummaryReport, build_summary_report, render_summary_html
from .runtime_mode import RuntimeMode, RuntimeModeError
from .audit_params import AuditParams, AuditParamsError, apply_to_config
from .kb import default_kb
from .crew import run_crew
from .verdict import compose_verdict

from . import logging_config

logging_config.configure_logging()
logger = logging.getLogger("safeorbit.server")

MOCK_UPSTREAM = os.getenv("SAFEORBIT_UPSTREAM_MOCK", "0").lower() in ("1", "true", "yes")
DB_PATH = os.getenv("SAFEORBIT_DB", "data/safeorbit.db")
# 全局运行模式：显式启动参数优先，否则读网页保存的值
RUNTIME_MODE = RuntimeMode(DB_PATH, startup_mode=os.getenv("SAFEORBIT_MODE"))
# 审计参数：网页可配置，默认值来自代码与环境变量
AUDIT_PARAMS = AuditParams(DB_PATH)


def _mode() -> str:
    return RUNTIME_MODE.current()


def _judge_config(registry):
    """统一构造裁判配置，并叠加网页保存的审计参数。"""

    config = JudgeConfig.from_registry(registry)
    if config is not None:
        apply_to_config(AUDIT_PARAMS.current(), config)
    return config


REPORT_DIR = Path(os.getenv("SAFEORBIT_REPORT_DIR", "reports/live"))
CODE_REPORT_DIR = Path(os.getenv("SAFEORBIT_CODE_REPORT_DIR", "reports/codeaudit"))
SCA_REPORT_DIR = Path(os.getenv("SAFEORBIT_SCA_REPORT_DIR", "reports/sca"))
INFRA_REPORT_DIR = Path(os.getenv("SAFEORBIT_INFRA_REPORT_DIR", "reports/infra"))
EVIDENCE_DIR = Path(os.getenv("SAFEORBIT_EVIDENCE_DIR", "reports/evidence"))
SUMMARY_REPORT_DIR = Path(os.getenv("SAFEORBIT_SUMMARY_REPORT_DIR", "reports/summaries"))
# 审计小队分工详情：按 trace 缓存并落盘，服务重启后仍可回看
CREW_DIR = Path(os.getenv("SAFEORBIT_CREW_DIR", "reports/crew"))
_CREW_CACHE: Dict[str, Dict[str, Any]] = {}
_CREW_CACHE_LIMIT = 200
PUBLIC_GATEWAY_URL = os.getenv("SAFEORBIT_PUBLIC_URL", "http://127.0.0.1:8000/v1")
JUDGE_ENABLED = os.getenv("SAFEORBIT_JUDGE", "off").lower() in ("1", "true", "yes")
AUTH_ENABLED = os.getenv("SAFEORBIT_AUTH", "on").lower() not in ("0", "off", "false", "no")
COOKIE_SECURE = os.getenv("SAFEORBIT_COOKIE_SECURE", "1").lower() not in ("0", "false", "off")
CORS_ORIGINS = [item.strip() for item in os.getenv("SAFEORBIT_CORS_ORIGINS", "").split(",") if item.strip()]
STREAM_AUDIT_MAX_BYTES = int(os.getenv("SAFEORBIT_STREAM_AUDIT_MAX_BYTES", "1048576"))
BATCH_INGEST_LIMIT = int(os.getenv("SAFEORBIT_BATCH_INGEST_LIMIT", "50"))
# 代码检查支持拖拽上传：限制上传体积、文件数与解压后总体积，避免压缩炸弹。
CODE_UPLOAD_MAX_BYTES = int(os.getenv("SAFEORBIT_CODE_UPLOAD_MAX_BYTES", str(60 * 1024 * 1024)))
CODE_UPLOAD_MAX_FILES = int(os.getenv("SAFEORBIT_CODE_UPLOAD_MAX_FILES", "2000"))
CODE_UPLOAD_MAX_UNZIPPED = int(os.getenv("SAFEORBIT_CODE_UPLOAD_MAX_UNZIPPED", str(240 * 1024 * 1024)))
COOKIE_NAME = "safeorbit_session"
SESSION_TTL_SECONDS = 12 * 3600

PRICING = load_pricing()
STORE = TraceStore(DB_PATH)
EVENT_STORE = TraceEventStore(DB_PATH)
MODEL_SETTINGS = ModelSettings(DB_PATH)
MODEL_REGISTRY = MODEL_SETTINGS.snapshot().registry
JUDGE_ENABLED = MODEL_SETTINGS.snapshot().judge_enabled
_INITIAL_MODEL_REGISTRY = MODEL_REGISTRY
_INITIAL_JUDGE_ENABLED = JUDGE_ENABLED


def _model_snapshot() -> ModelSnapshot:
    snapshot = MODEL_SETTINGS.snapshot()
    # 保留嵌入调用和旧测试的模块级替换；正常服务只消费设置快照。
    if MODEL_REGISTRY is not _INITIAL_MODEL_REGISTRY or JUDGE_ENABLED != _INITIAL_JUDGE_ENABLED:
        registry = (MODEL_REGISTRY.frozen_snapshot() if MODEL_REGISTRY is not _INITIAL_MODEL_REGISTRY
                    else snapshot.registry)
        enabled = JUDGE_ENABLED if JUDGE_ENABLED != _INITIAL_JUDGE_ENABLED else snapshot.judge_enabled
        return ModelSnapshot(registry, snapshot.revision, enabled)
    return snapshot


BUS = EventBus()
RULES = default_rules()
AUTH_STORE = AuthStore(DB_PATH)
AUTH = AuthManager(AUTH_STORE)
AGENTS = AgentStore(DB_PATH)
POLICY = PolicyStore(DB_PATH)
POLICY.ensure_default()
DISPO = DispositionStore(DB_PATH)
APPROVALS = ApprovalStore(DB_PATH)
AUDIT_LOG = AuditLog(DB_PATH)
METRICS = Metrics()
STARTED_AT = time.monotonic()
CODE_REPORTS: Dict[str, CodeScanReport] = load_code_reports(CODE_REPORT_DIR)
LATEST_CODE_REPORT: Optional[str] = next(iter(CODE_REPORTS), None)
SCA_REPORTS: Dict[str, sca.ScaReport] = {}
LATEST_SCA_REPORT: Optional[str] = None
INFRA_REPORTS: Dict[str, infrascan.InfraReport] = {}
LATEST_INFRA_REPORT: Optional[str] = None
SUMMARY_REPORTS: Dict[str, SummaryReport] = {}
SUMMARY_REPORT_LIMIT = 20
LATEST_SUMMARY_REPORT: Optional[str] = None
_SUMMARY_ID_RE = re.compile(r"^sum-\d{8}-\d{6}-[0-9a-f]{8}$")

# 锁与事件循环绑定：按运行中的 loop 惰性创建，避免模块导入期创建 anyio/asyncio 锁。
_AUDIT_LOCK_SHARDS = 256
_LOOP_LOCKS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _loop_locks() -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    state = _LOOP_LOCKS.get(loop)
    if state is None:
        state = {
            "summary": asyncio.Lock(),
            "audit": [asyncio.Lock() for _ in range(_AUDIT_LOCK_SHARDS)],
        }
        _LOOP_LOCKS[loop] = state
    return state

_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
_DECISION_RANK = {"allow": 0, "review": 1, "deny": 2}
_AMOUNT_KEYS = {"amount", "total", "price", "value", "refund_amount", "quantity"}

async def require_secure_startup() -> None:
    if not AUTH_ENABLED:
        raise RuntimeError("安全试点不允许 SAFEORBIT_AUTH=off")
    if "*" in CORS_ORIGINS:
        raise RuntimeError("CORS 必须配置显式来源，不允许通配符")
    AUTH_STORE.require_ready()
    if _mode() == "enforce":
        default_policy = next(
            (item for item in POLICY.list() if item.agent_id == "*"), None
        )
        if default_policy is None or default_policy.mode != "enforce":
            logger.warning(
                "全局模式为 enforce，但默认策略（*）不是 enforce：未单独配置策略的智能体不会被阻断"
            )


async def _maintenance_loop(interval_seconds: float = 300.0) -> None:
    """周期清理过期审批票与隔离状态，避免统计口径被脏数据污染。"""

    while True:
        try:
            expired = APPROVALS.expire()
            released = DISPO.expire_isolations()
            if expired or released:
                logger.info("后台维护：过期审批 %d 条，解除隔离 %d 个", expired, released)
        except Exception as exc:  # 维护任务失败不能影响主服务
            logger.warning("后台维护任务失败：%s", type(exc).__name__)
        await asyncio.sleep(interval_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await require_secure_startup()
    task = asyncio.create_task(_maintenance_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="SafeOrbit", version="0.5.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-SafeOrbit-CSRF", "X-SafeOrbit-Agent-Id", "X-SafeOrbit-Trace-Id", "X-SafeOrbit-Session-Id"],
)


class RequestContextMiddleware:
    """注入 request_id，供访问日志与结构化日志关联。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {
            k.decode("latin1").lower(): v.decode("latin1")
            for k, v in scope.get("headers", [])
        }
        scope.setdefault("state", {})["request_id"] = logging_config.request_id_from_headers(headers)
        await self.app(scope, receive, send)


app.add_middleware(RequestContextMiddleware)
app.add_middleware(logging_config.AccessLogMiddleware)

app.add_middleware(
    SecurityMiddleware,
    server=sys.modules[__name__],
    max_bytes=int(os.getenv("SAFEORBIT_MAX_REQUEST_BYTES", "2097152")),
    max_spans=int(os.getenv("SAFEORBIT_MAX_SPANS", "1000")),
    requests_per_minute=int(os.getenv("SAFEORBIT_REQUESTS_PER_MINUTE", "120")),
    login_per_minute=int(os.getenv("SAFEORBIT_LOGIN_PER_MINUTE", "10")),
    upstream_concurrency=int(os.getenv("SAFEORBIT_UPSTREAM_CONCURRENCY", "8")),
    upload_max_bytes=CODE_UPLOAD_MAX_BYTES,
)


class ModelRequestMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        scope.setdefault("state", {})["model_snapshot"] = _model_snapshot()

        async def response_send(message):
            if message["type"] == "http.response.start" and scope["path"].startswith("/api/admin/models"):
                message["headers"] = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                message["headers"].append((b"cache-control", b"no-store"))
            await send(message)

        await self.app(scope, receive, response_send)


app.add_middleware(ModelRequestMiddleware)


def _claim(pairs: List[Tuple[str, str]]) -> None:
    try:
        STORE.claim_many(pairs)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# --------------------------------------------------------------------------
# 认证与角色
# --------------------------------------------------------------------------
def _cookie_user(request: Request) -> Optional[User]:
    return AUTH.authenticate_session(request.cookies.get(COOKIE_NAME))


_RENEWAL_CHECKED: Dict[str, float] = {}
_RENEWAL_CHECK_INTERVAL = 300.0


def _maybe_renew_session(request: Request, token: str) -> None:
    """滑动续期检查做节流：同一个会话 5 分钟内最多查一次库。"""

    now = time.monotonic()
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if now - _RENEWAL_CHECKED.get(digest, 0.0) < _RENEWAL_CHECK_INTERVAL:
        return
    _RENEWAL_CHECKED[digest] = now
    if len(_RENEWAL_CHECKED) > 2000:
        for key in list(_RENEWAL_CHECKED)[:1000]:
            _RENEWAL_CHECKED.pop(key, None)
    if AUTH_STORE.renew_session(token):
        request.state.renew_session_token = token


def _current_user(request: Request) -> User:
    if not AUTH_ENABLED:
        raise HTTPException(status_code=503, detail="不允许关闭管理认证")
    user = _cookie_user(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="未登录或会话已过期",
            headers={"WWW-Authenticate": "Cookie"},
        )
    token = request.cookies.get(COOKIE_NAME)
    if token:
        _maybe_renew_session(request, token)
    return user


def _require(request: Request, minimum: str = "viewer") -> User:
    user = _current_user(request)
    if not role_at_least(user.role, minimum):
        raise HTTPException(status_code=403, detail="权限不足：需要 %s 及以上角色" % minimum)
    return user


def _tenant_scope(user: User) -> Optional[str]:
    """管理员默认看全部；其他角色只看自己的租户。"""

    if user.role == "admin":
        return None
    return user.tenant_id or "default"


def _allowed_agent_ids(user: User) -> Optional[Set[str]]:
    tenant = _tenant_scope(user)
    if tenant is None:
        return None
    return set(AGENTS.agent_ids_for_tenant(tenant))


def _agent_in_scope(user: User, agent_id: str) -> bool:
    allowed = _allowed_agent_ids(user)
    if allowed is None:
        return True
    return agent_id in allowed


def _session_cookie_header(token: str) -> bytes:
    parts = [
        "%s=%s" % (COOKIE_NAME, token),
        "Path=/",
        "Max-Age=%d" % SESSION_TTL_SECONDS,
        "HttpOnly",
        "SameSite=strict",
    ]
    if COOKIE_SECURE:
        parts.append("Secure")
    return "; ".join(parts).encode("latin1")


class SessionRenewalMiddleware:
    """滑动续期：端点标记需要续期时，在响应头重设会话 Cookie。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                token = scope.setdefault("state", {}).get("renew_session_token")
                if token:
                    prefix = COOKIE_NAME.encode("latin1") + b"="
                    headers = [
                        (k, v) for k, v in message.get("headers", [])
                        if not (k.lower() == b"set-cookie" and v.startswith(prefix))
                    ]
                    headers.append((b"set-cookie", _session_cookie_header(token)))
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(SessionRenewalMiddleware)


def _login_response(user: User, token: str) -> JSONResponse:
    response = JSONResponse(
        {
            "authenticated": True,
            "user": user.to_public(),
            "providers": AUTH.provider_status(),
        }
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        path="/",
    )
    return response


@app.post("/api/auth/login")
async def login(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    username = str(payload.get("username") or "")
    password = str(payload.get("password") or "")
    provider = str(payload.get("provider") or "local")
    try:
        user = AUTH.login(username, password, provider=provider)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    token = AUTH.issue_session(user)
    logger.info("用户登录：%s（%s）", user.username, user.role)
    return _login_response(user, token)


@app.post("/api/auth/logout")
async def logout(request: Request) -> Dict[str, Any]:
    AUTH.logout(request.cookies.get(COOKIE_NAME))
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.get("/api/auth/me")
async def auth_me(request: Request) -> Dict[str, Any]:
    user = _cookie_user(request)
    token = request.cookies.get(COOKIE_NAME)
    if user is not None and token:
        _maybe_renew_session(request, token)
    return {
        "authenticated": user is not None,
        "auth_enabled": AUTH_ENABLED,
        "user": user.to_public() if user else None,
        "providers": AUTH.provider_status(),
    }


@app.get("/api/auth/users")
async def list_users(request: Request) -> Dict[str, Any]:
    _require(request, "admin")
    return {"users": AUTH_STORE.list_users()}


@app.post("/api/auth/users")
async def create_user(request: Request) -> Dict[str, Any]:
    _require(request, "admin")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    try:
        user = AUTH_STORE.create_user(
            str(payload.get("username") or ""),
            str(payload.get("password") or ""),
            role=str(payload.get("role") or "viewer"),
            display_name=str(payload.get("display_name") or ""),
        )
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"user": user.to_public()}


@app.post("/api/auth/sso")
async def sso_login(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    try:
        user = AUTH.sso_login(payload)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    token = AUTH.issue_session(user)
    logger.info("SSO 登录：%s（%s）", user.username, user.role)
    return _login_response(user, token)


# --------------------------------------------------------------------------
# Agent 接入管理
# --------------------------------------------------------------------------
@app.get("/api/agents")
async def list_agents(request: Request) -> Dict[str, Any]:
    user = _require(request, "viewer")
    tenant = _tenant_scope(user)
    agents = AGENTS.list_agents(tenant_id=tenant)
    trace_where = " WHERE tenant_id = ?" if tenant else ""
    trace_params: List[Any] = [tenant] if tenant else []
    with STORE._connect() as connection:
        last_seen = {row["agent_id"]: row["last_seen"] for row in connection.execute(
            "SELECT agent_id, MAX(last_seen) AS last_seen FROM agent_keys GROUP BY agent_id"
        ).fetchall()}
        reports = {row["agent_id"]: dict(row) for row in connection.execute(
            "SELECT agent_id, COUNT(*) AS report_count FROM traces"
            + trace_where
            + " GROUP BY agent_id",
            trace_params,
        ).fetchall()}
        received = {row["agent_id"]: row["received_count"] for row in connection.execute(
            "SELECT agent_id, COUNT(*) AS received_count FROM trace_owners GROUP BY agent_id"
        ).fetchall()}
    for agent in agents:
        agent["last_seen"] = last_seen.get(agent["agent_id"])
        agent["received_count"] = received.get(agent["agent_id"], 0)
        agent["report_count"] = reports.get(agent["agent_id"], {}).get("report_count", 0)
    return {"agents": agents, "note": "received_count 为归属占位轨迹数，包含调用前预留；不代表完整接收或完成审计", "received_count_kind": "trace_ownership_reservations"}


@app.post("/api/agents")
async def register_agent(request: Request) -> Dict[str, Any]:
    user = _require(request, "admin")
    payload = await request.json()
    tenant_id = str(payload.get("tenant_id") or user.tenant_id or "default").strip() or "default"
    try:
        agent = AGENTS.create_agent(
            str(payload.get("agent_id") or ""),
            str(payload.get("name") or ""),
            tenant_id=tenant_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    AUDIT_LOG.log(
        user.username, "agent.create", agent["agent_id"],
        {"tenant_id": tenant_id}, tenant_id=tenant_id,
    )
    return {"agent": agent}


@app.patch("/api/agents/{agent_id}")
async def enable_agent(agent_id: str, request: Request) -> Dict[str, Any]:
    _require(request, "admin")
    payload = await request.json()
    if not isinstance(payload.get("enabled"), bool):
        raise HTTPException(status_code=400, detail="enabled 必须是布尔值")
    try:
        result = AGENTS.set_enabled(agent_id, payload["enabled"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"agent": result}


@app.delete("/api/agents/{agent_id}")
async def delete_agent(agent_id: str, request: Request) -> Dict[str, Any]:
    """删除智能体与全部 Key；已产生的轨迹与报告保留。"""

    user = _require(request, "admin")
    try:
        result = AGENTS.delete_agent(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    AUDIT_LOG.log(
        user.username, "agent.delete", agent_id,
        {"keys_deleted": result["keys_deleted"]},
        tenant_id=user.tenant_id,
    )
    logger.info("智能体已删除：%s keys=%d by=%s", agent_id, result["keys_deleted"], user.username)
    return result


@app.get("/api/agents/{agent_id}/keys")
async def list_agent_keys(agent_id: str, request: Request) -> Dict[str, Any]:
    _require(request, "admin")
    return {"keys": AGENTS.list_keys(agent_id)}


@app.post("/api/agents/{agent_id}/keys")
async def issue_agent_key(agent_id: str, request: Request) -> Dict[str, Any]:
    user = _require(request, "admin")
    payload = await request.json()
    try:
        result = AGENTS.issue_key(agent_id, payload.get("scopes") or [], payload.get("expires_at"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    AUDIT_LOG.log(
        user.username, "agent.key.issue", agent_id,
        {"key_id": result["key_id"], "scopes": result["scopes"]},
        tenant_id=user.tenant_id,
    )
    return result


@app.delete("/api/agents/{agent_id}/keys/{key_id}")
async def revoke_agent_key(agent_id: str, key_id: str, request: Request) -> Dict[str, Any]:
    user = _require(request, "admin")
    try:
        AGENTS.revoke_key(agent_id, key_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    AUDIT_LOG.log(
        user.username, "agent.key.revoke", agent_id, {"key_id": key_id},
        tenant_id=user.tenant_id,
    )
    return {"revoked": key_id}


@app.get("/api/agents/{agent_id}/versions")
async def agent_versions(agent_id: str, request: Request) -> Dict[str, Any]:
    """按 Agent 版本对比轨迹量、风险率、成本与平均命中。"""

    user = _require(request, "viewer")
    return {
        "agent_id": agent_id,
        "versions": STORE.agent_versions(agent_id, tenant_id=_tenant_scope(user)),
    }


@app.get("/api/agents/{agent_id}/snippet")
async def agent_snippet(agent_id: str, request: Request, framework: str = "generic", model: Optional[str] = None) -> Dict[str, Any]:
    _require(request, "viewer")
    try:
        selected_model = "mock-model" if MOCK_UPSTREAM else request.state.model_snapshot.registry.resolve_gateway(model).id
        snippet = integration_snippet(framework, PUBLIC_GATEWAY_URL.removesuffix("/v1"), agent_id, model=selected_model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"snippet": snippet}


# --------------------------------------------------------------------------
# 策略 / 上下文 / 模式
# --------------------------------------------------------------------------
def _resolve_context(agent_id: str, request: Request) -> AuditContext:
    """只采用服务端策略，客户端不能覆盖。"""
    policy = POLICY.resolve(agent_id)
    return policy.to_context() if policy else AuditContext()


def _effective_mode(agent_id: str, request: Request) -> str:
    """全局模式是上限：只有服务端开启 enforce，单个 Agent 才能被真正阻断。"""

    if _mode() != "enforce":
        return "shadow"
    policy = POLICY.resolve(agent_id)
    requested = policy.mode if policy else "shadow"
    return "enforce" if requested == "enforce" else "shadow"


def _escalate(current: str, candidate: str) -> str:
    return candidate if _DECISION_RANK[candidate] > _DECISION_RANK[current] else current


def _runtime_block_reason(
    agent_id: str,
    session_id: Optional[str],
    mode: str,
    tenant_id: Optional[str] = None,
) -> Optional[str]:
    if mode != "enforce":
        return None
    isolation = DISPO.is_session_isolated(session_id, agent_id)
    if isolation:
        return "会话 %s 已被隔离：%s" % (session_id, isolation.get("reason") or "策略处置")
    policy = POLICY.resolve(agent_id)
    if policy and policy.budget_usd:
        spent = STORE.session_cost(session_id, agent_id, tenant_id=tenant_id)
        if spent >= policy.budget_usd:
            return "会话成本 $%.6f 已达到预算 $%.6f" % (spent, policy.budget_usd)
    return None


def _blocked_response(message: str, mode: str) -> JSONResponse:
    METRICS.inc("blocks")
    return JSONResponse(
        status_code=403,
        content={
            "error": {
                "message": "SafeOrbit 已阻断本次调用：%s" % message,
                "type": "safeorbit_blocked",
                "mode": mode,
            }
        },
    )


def _policy_from_payload(payload: Dict[str, Any], existing: Optional[Policy] = None) -> Dict[str, Any]:
    def numeric(name: str) -> Optional[float]:
        value = payload.get(name, getattr(existing, name, None) if existing else None)
        if value in (None, ""):
            return None
        return float(value)

    tools = payload.get("allowed_tools")
    if tools is None:
        tools = list(existing.allowed_tools) if existing else []
    if isinstance(tools, str):
        tools = [item.strip() for item in tools.split(",") if item.strip()]
    rules_enabled = payload.get("enabled_rules")
    if rules_enabled is None:
        rules_enabled = list(existing.enabled_rules) if existing else []
    if isinstance(rules_enabled, str):
        rules_enabled = [item.strip() for item in rules_enabled.split(",") if item.strip()]

    return {
        "agent_id": str(payload.get("agent_id", existing.agent_id if existing else "*")).strip() or "*",
        "name": str(payload.get("name", existing.name if existing else "") or ""),
        "mode": str(payload.get("mode", existing.mode if existing else "shadow")),
        "budget_usd": numeric("budget_usd"),
        "allowed_tools": [str(item) for item in tools],
        "write_amount_threshold": numeric("write_amount_threshold"),
        "loop_threshold": int(
            payload.get(
                "loop_threshold", existing.loop_threshold if existing else 3
            )
        ),
        "enabled_rules": [str(item) for item in rules_enabled],
        "require_confirmation_for_writes": bool(
            payload.get(
                "require_confirmation_for_writes",
                existing.require_confirmation_for_writes if existing else True,
            )
        ),
        "redact_content": bool(
            payload.get(
                "redact_content",
                existing.redact_content if existing else False,
            )
        ),
        "note": str(payload.get("note", existing.note if existing else "") or ""),
    }


def _filter_runtime(runtime: Dict[str, Any], allowed: Optional[Set[str]]) -> Dict[str, Any]:
    if allowed is None:
        return runtime
    return {
        "isolated_sessions": [
            item for item in runtime.get("isolated_sessions", [])
            if item.get("agent_id") in allowed
        ],
        "blocked_tools": [
            item for item in runtime.get("blocked_tools", [])
            if item.get("agent_id") in allowed
        ],
    }


@app.get("/api/rules")
async def list_rules(request: Request) -> Dict[str, Any]:
    _require(request, "viewer")
    return {
        "rules": [
            {
                "id": rule.id,
                "name": rule.name,
                "dimension": rule.dimension.value,
                "severity": rule.severity.value,
                "action": rule.action,
                "description": rule.description,
            }
            for rule in RULES
        ]
    }


@app.get("/api/judge/prompt")
async def judge_prompt(request: Request) -> Dict[str, Any]:
    """只读展示语义裁判的提示词与约束，便于评审核对提示词工程。"""

    _require(request, "viewer")
    return {
        "prompt": judge_mod.system_prompt(),
        "temperature": 0,
        "response_format": "json_object",
        "max_retries": int(os.getenv("SAFEORBIT_JUDGE_MAX_RETRIES", "1")),
        "guards": [
            "span 定位必须真实存在，模型编造的定位一律丢弃",
            "裁判结论一律标记 needs_review，不冒充硬规则",
            "超时、限流或结构无效时降级为规则审计，不拖垮报告",
            "连续失败触发熔断，熔断期间跳过语义复核",
        ],
    }


@app.get("/api/policies")
async def list_policies(request: Request) -> Dict[str, Any]:
    user = _require(request, "viewer")
    policies = POLICY.list()
    allowed = _allowed_agent_ids(user)
    if allowed is not None:
        policies = [
            policy for policy in policies
            if policy.agent_id == "*" or policy.agent_id in allowed
        ]
    return {
        "policies": [policy.model_dump(mode="json") for policy in policies],
        "mode": _mode(),
    }


@app.post("/api/policies/simulate")
async def simulate_policy(request: Request) -> Dict[str, Any]:
    """用草稿策略回放最近轨迹，预览切 enforce 后的影响面。"""

    user = _require(request, "auditor")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    try:
        limit = max(1, min(int(payload.get("limit") or 100), 200))
        values = _policy_from_payload(payload)
        if values["agent_id"] != "*" and not _agent_in_scope(user, values["agent_id"]):
            raise HTTPException(status_code=403, detail="无权为该智能体配置策略")
        preview = Policy(policy_id="sim-preview", created_by=user.username, **values)
    except HTTPException:
        raise
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    rows = STORE.list_traces(
        limit=limit,
        agent_id=values["agent_id"] if values["agent_id"] != "*" else None,
        tenant_id=_tenant_scope(user),
    )
    context = preview.to_context()
    rules = preview.select_rules(RULES)
    effective_mode = preview.mode if _mode() == "enforce" else "shadow"
    counts = {"pass": 0, "warn": 0, "fail": 0, "review": 0}
    would_block = 0
    would_review = 0
    samples: List[Dict[str, Any]] = []
    skipped = 0

    for row in rows:
        record = STORE.get(row["trace_id"])
        if not record or not record.get("payload"):
            skipped += 1
            continue
        try:
            trajectory = Trajectory.model_validate(record["payload"])
        except Exception:
            skipped += 1
            continue
        normalized = normalize(trajectory, PRICING)
        hits = RuleEngine(rules).run(normalized, context)
        verdict = compose_verdict(hits)
        counts[verdict.value] += 1
        high = [hit for hit in hits if hit.severity.value in ("critical", "high")]
        blocking = effective_mode == "enforce" and bool(high)
        if blocking:
            would_block += 1
        if hits:
            would_review += 1
        if hits and len(samples) < 20:
            top = max(
                hits,
                key=lambda hit: _SEVERITY_RANK.get(hit.severity.value, 0),
            )
            samples.append(
                {
                    "trace_id": row["trace_id"],
                    "agent_id": row["agent_id"],
                    "verdict": verdict.value,
                    "would_block": blocking,
                    "severity": top.severity.value,
                    "rule_id": top.rule_id,
                    "rule_name": top.rule_name,
                    "message": top.message,
                    "span_ref": top.span_ref,
                }
            )

    current = POLICY.find_by_agent(values["agent_id"])
    return {
        "policy": preview.model_dump(mode="json"),
        "global_mode": _mode(),
        "effective_mode": effective_mode,
        "current_policy": current.model_dump(mode="json") if current else None,
        "scanned": len(rows),
        "skipped": skipped,
        "counts": counts,
        "would_block": would_block,
        "would_review": would_review,
        "samples": samples,
        "limit": limit,
    }


@app.post("/api/policies")
async def create_policy(request: Request) -> Dict[str, Any]:
    user = _require(request, "auditor")
    try:
        payload = await request.json()
        values = _policy_from_payload(payload)
        if values["agent_id"] == "*" and user.role != "admin":
            raise HTTPException(status_code=403, detail="只有管理员可以修改默认策略")
        if values["agent_id"] != "*" and not _agent_in_scope(user, values["agent_id"]):
            raise HTTPException(status_code=403, detail="无权为该智能体配置策略")
        existing = POLICY.find_by_agent(values["agent_id"])
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail="Agent %s 已有策略 %s，请直接编辑该策略，避免多条策略静默覆盖"
                % (values["agent_id"], existing.policy_id),
            )
        policy = POLICY.upsert(created_by=user.username, **values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    AUDIT_LOG.log(
        user.username, "policy.create", policy.policy_id,
        {"agent_id": policy.agent_id, "mode": policy.mode}, tenant_id=user.tenant_id,
    )
    return {"policy": policy.model_dump(mode="json")}


@app.put("/api/policies/{policy_id}")
async def update_policy(policy_id: str, request: Request) -> Dict[str, Any]:
    user = _require(request, "auditor")
    existing = POLICY.get(policy_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="策略不存在")
    if existing.agent_id == "*" and user.role != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以修改默认策略")
    if existing.agent_id != "*" and not _agent_in_scope(user, existing.agent_id):
        raise HTTPException(status_code=403, detail="无权修改该策略")
    try:
        payload = await request.json()
        values = _policy_from_payload(payload, existing=existing)
        if values["agent_id"] == "*" and user.role != "admin":
            raise HTTPException(status_code=403, detail="只有管理员可以修改默认策略")
        if values["agent_id"] != "*" and not _agent_in_scope(user, values["agent_id"]):
            raise HTTPException(status_code=403, detail="无权为该智能体配置策略")
        policy = POLICY.upsert(created_by=existing.created_by, policy_id=policy_id, **values)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    AUDIT_LOG.log(
        user.username, "policy.update", policy_id,
        {"agent_id": policy.agent_id, "mode": policy.mode}, tenant_id=user.tenant_id,
    )
    return {"policy": policy.model_dump(mode="json"), "updated_by": user.username}


@app.delete("/api/policies/{policy_id}")
async def delete_policy(policy_id: str, request: Request) -> Dict[str, Any]:
    user = _require(request, "auditor")
    existing = POLICY.get(policy_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="策略不存在")
    if existing.agent_id == "*" and user.role != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以删除默认策略")
    if existing.agent_id != "*" and not _agent_in_scope(user, existing.agent_id):
        raise HTTPException(status_code=403, detail="无权删除该策略")
    if not POLICY.delete(policy_id):
        raise HTTPException(status_code=404, detail="策略不存在")
    AUDIT_LOG.log(user.username, "policy.delete", policy_id, tenant_id=user.tenant_id)
    return {"deleted": policy_id}


# --------------------------------------------------------------------------
# 处置
# --------------------------------------------------------------------------
@app.get("/api/dispositions")
async def list_dispositions(request: Request, status: Optional[str] = None) -> Dict[str, Any]:
    user = _require(request, "viewer")
    allowed = _allowed_agent_ids(user)
    items = DISPO.list(status=status)
    if allowed is not None:
        items = [item for item in items if item.agent_id in allowed]
    return {
        "dispositions": [item.to_public() for item in items],
        "runtime": _filter_runtime(DISPO.runtime_summary(), allowed),
    }


@app.post("/api/dispositions")
async def create_disposition(request: Request) -> Dict[str, Any]:
    """人工发起处置提案，覆盖自动提案未覆盖的场景。"""

    user = _require(request, "operator")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    agent_id = str(payload.get("agent_id") or "").strip()
    action = str(payload.get("action") or "require_approval")
    reason = str(payload.get("reason") or "").strip()
    if not agent_id or not reason:
        raise HTTPException(status_code=400, detail="必须提供 agent_id 与 reason")
    if not _agent_in_scope(user, agent_id):
        raise HTTPException(status_code=403, detail="无权处置该智能体")
    if action not in ACTIONS:
        raise HTTPException(status_code=400, detail="未知处置动作：%s" % action)
    item = DISPO.propose(
        agent_id=agent_id,
        action=action,
        reason=reason,
        trace_id=payload.get("trace_id"),
        session_id=payload.get("session_id"),
        target=payload.get("target"),
        severity=str(payload.get("severity") or "medium"),
        created_mode=_effective_mode(agent_id, request),
        requested_by=user.username,
        evidence={"manual": True},
    )
    await BUS.publish(
        {
            "event_type": "disposition",
            "trace_id": item.trace_id,
            "agent_id": item.agent_id,
            "verdict": "review",
            "severity": item.severity,
            "message": "人工提案：%s（%s）" % (item.reason, item.action),
            "cost_usd": 0.0,
            "created_at": datetime.now().astimezone().isoformat(),
        }
    )
    asyncio.create_task(notify.dispatch(item.to_public()))
    AUDIT_LOG.log(
        user.username, "disposition.create", item.disposition_id,
        {"agent_id": item.agent_id, "action": item.action}, tenant_id=user.tenant_id,
    )
    return {"disposition": item.to_public()}


@app.get("/api/dispositions/{disposition_id}/effect")
async def disposition_effect(disposition_id: str, request: Request) -> Dict[str, Any]:
    """处置执行后回检：统计同一 Agent/会话后续轨迹的风险情况。"""

    _require(request, "viewer")
    user = _current_user(request)
    item = DISPO.get(disposition_id)
    if item is None:
        raise HTTPException(status_code=404, detail="处置提案不存在")
    if not _agent_in_scope(user, item.agent_id):
        raise HTTPException(status_code=404, detail="处置提案不存在")
    if item.status != "executed" or item.executed_at is None:
        return {"disposition": item.to_public(), "effective": None, "note": "处置尚未执行"}
    rows = STORE.traces_after(
        item.agent_id,
        item.executed_at.isoformat(),
        session_id=item.session_id,
        limit=50,
    )
    risk = [row for row in rows if row.get("verdict") in ("fail", "review")]
    return {
        "disposition": item.to_public(),
        "observed": len(rows),
        "risk_traces": len(risk),
        "effective": (len(risk) == 0) if rows else None,
        "note": "暂无后续轨迹，待观察" if not rows else ("处置后未再出现风险轨迹" if not risk else "处置后仍有风险轨迹，建议升级处置"),
        "traces": rows,
    }


@app.post("/api/dispositions/{disposition_id}/decide")
async def decide_disposition(disposition_id: str, request: Request) -> Dict[str, Any]:
    user = _require(request, "auditor")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if "approved" not in payload:
        raise HTTPException(status_code=400, detail="缺少 approved 字段")
    item = DISPO.get(disposition_id)
    if item is None:
        raise HTTPException(status_code=404, detail="处置提案不存在")
    if not _agent_in_scope(user, item.agent_id):
        raise HTTPException(status_code=404, detail="处置提案不存在")
    try:
        item = DISPO.decide(
            disposition_id,
            approved=bool(payload.get("approved")),
            decided_by=user.username,
            note=str(payload.get("note") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await BUS.publish(
        {
            "event_type": "disposition",
            "trace_id": item.trace_id,
            "agent_id": item.agent_id,
            "verdict": "review",
            "severity": item.severity,
            "message": "处置%s：%s" % (item.status, item.reason),
            "cost_usd": 0.0,
            "created_at": datetime.now().astimezone().isoformat(),
        }
    )
    asyncio.create_task(notify.dispatch(item.to_public()))
    AUDIT_LOG.log(
        user.username, "disposition.decide", disposition_id,
        {"approved": bool(payload.get("approved"))}, tenant_id=user.tenant_id,
    )
    return {"disposition": item.to_public()}


@app.post("/api/dispositions/{disposition_id}/execute")
async def execute_disposition(disposition_id: str, request: Request) -> Dict[str, Any]:
    user = _require(request, "operator")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    item = DISPO.get(disposition_id)
    if item is None:
        raise HTTPException(status_code=404, detail="处置提案不存在")
    if not _agent_in_scope(user, item.agent_id):
        raise HTTPException(status_code=404, detail="处置提案不存在")
    mode = _effective_mode(item.agent_id, request)
    try:
        minutes = int(payload.get("isolation_minutes") or 30)
        item = DISPO.execute(
            disposition_id,
            mode=mode,
            executed_by=user.username,
            isolation_minutes=minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await BUS.publish(
        {
            "event_type": "disposition",
            "trace_id": item.trace_id,
            "agent_id": item.agent_id,
            "verdict": "review",
            "severity": item.severity,
            "message": item.execution_note,
            "cost_usd": 0.0,
            "created_at": datetime.now().astimezone().isoformat(),
        }
    )
    asyncio.create_task(notify.dispatch(item.to_public()))
    AUDIT_LOG.log(
        user.username, "disposition.execute", disposition_id,
        {"mode": mode, "action": item.action}, tenant_id=user.tenant_id,
    )
    return {"disposition": item.to_public(), "mode": mode}


@app.get("/api/runtime")
async def runtime_state(request: Request) -> Dict[str, Any]:
    user = _require(request, "viewer")
    return {
        "mode": _mode(),
        "runtime": _filter_runtime(DISPO.runtime_summary(), _allowed_agent_ids(user)),
    }


@app.post("/api/runtime/release")
async def release_runtime(request: Request) -> Dict[str, Any]:
    user = _require(request, "operator")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    released = []
    session_id = payload.get("session_id")
    agent_id = payload.get("agent_id")
    if not agent_id:
        raise HTTPException(status_code=400, detail="必须指定 agent_id")
    if not _agent_in_scope(user, str(agent_id)):
        raise HTTPException(status_code=403, detail="无权操作该智能体")
    if session_id:
        DISPO.release_isolation(str(session_id), str(agent_id))
        released.append("session:%s/%s" % (agent_id, session_id))
    tool_name = payload.get("tool_name")
    if agent_id and tool_name:
        DISPO.release_tool(str(agent_id), str(tool_name))
        released.append("tool:%s/%s" % (agent_id, tool_name))
    AUDIT_LOG.log(
        user.username, "runtime.release", agent_id,
        {"session_id": session_id, "tool_name": payload.get("tool_name")},
        tenant_id=user.tenant_id,
    )
    return {"released": released, "released_by": user.username}


# --------------------------------------------------------------------------
# 写操作审批
# --------------------------------------------------------------------------
@app.get("/api/approvals")
async def list_approvals(request: Request, status: Optional[str] = None) -> Dict[str, Any]:
    user = _require(request, "viewer")
    allowed = _allowed_agent_ids(user)
    items = APPROVALS.list(status=status)
    if allowed is not None:
        items = [item for item in items if item.agent_id in allowed]
    return {
        "approvals": [item.to_public() for item in items],
        "expired": APPROVALS.expire(),
    }


@app.post("/api/approvals")
async def create_approval(request: Request) -> Dict[str, Any]:
    """人工补录审批申请：用于没有 SDK 回调、需要线下确认的场景。"""

    user = _require(request, "operator")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    agent_id = str(payload.get("agent_id") or "").strip()
    tool_name = str(payload.get("tool_name") or "").strip()
    if not agent_id or not tool_name:
        raise HTTPException(status_code=400, detail="必须提供 agent_id 与 tool_name")
    if not _agent_in_scope(user, agent_id):
        raise HTTPException(status_code=403, detail="无权为该智能体创建审批")
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    item = APPROVALS.create(
        agent_id=agent_id,
        tool_name=tool_name,
        arguments=arguments,
        session_id=payload.get("session_id"),
        trace_id=payload.get("trace_id"),
        reason=str(payload.get("reason") or "人工补录的写操作审批"),
        requested_by=user.username,
    )
    AUDIT_LOG.log(
        user.username, "approval.create", item.approval_id,
        {"agent_id": item.agent_id, "tool_name": item.tool_name},
        tenant_id=user.tenant_id,
    )
    return {"approval": item.to_public()}


@app.post("/api/approvals/{approval_id}/decide")
async def decide_approval(approval_id: str, request: Request) -> Dict[str, Any]:
    user = _require(request, "operator")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if "approved" not in payload:
        raise HTTPException(status_code=400, detail="缺少 approved 字段")
    existing = APPROVALS.get(approval_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="审批单不存在")
    if not _agent_in_scope(user, existing.agent_id):
        raise HTTPException(status_code=404, detail="审批单不存在")
    try:
        item = APPROVALS.decide(
            approval_id,
            approved=bool(payload.get("approved")),
            decided_by=user.username,
            note=str(payload.get("note") or ""),
            ttl_minutes=int(payload.get("ttl_minutes") or 15),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await BUS.publish(
        {
            "event_type": "approval",
            "trace_id": item.trace_id,
            "agent_id": item.agent_id,
            "verdict": "review",
            "severity": "high" if item.status == "approved" else "medium",
            "message": "写操作审批%s：%s / %s" % (item.status, item.agent_id, item.tool_name),
            "cost_usd": 0.0,
            "created_at": datetime.now().astimezone().isoformat(),
        }
    )
    asyncio.create_task(notify.dispatch(item.to_public()))
    AUDIT_LOG.log(
        user.username, "approval.decide", approval_id,
        {"approved": bool(payload.get("approved")), "status": item.status},
        tenant_id=user.tenant_id,
    )
    return {"approval": item.to_public()}


# --------------------------------------------------------------------------
# 审计流水线
# --------------------------------------------------------------------------
def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:120]


def _remember_crew(trace_id: str, crew) -> None:
    """记录小队分工：内存缓存 + 落盘，避免服务重启后详情丢失。"""

    record = {
        "trace_id": trace_id,
        "status": crew.status,
        "note": crew.note,
        "agents": [
            {
                "role": agent.role,
                "name": agent.name,
                "status": agent.status,
                "findings": agent.findings,
                "note": agent.note,
                "citations": agent.citations,
            }
            for agent in crew.agents
        ],
        "created_at": datetime.now().astimezone().isoformat(),
    }
    _CREW_CACHE[trace_id] = record
    while len(_CREW_CACHE) > _CREW_CACHE_LIMIT:
        _CREW_CACHE.pop(next(iter(_CREW_CACHE)), None)
    try:
        CREW_DIR.mkdir(parents=True, exist_ok=True)
        (CREW_DIR / (_safe_name(trace_id) + ".json")).write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def _load_crew(trace_id: str) -> Optional[Dict[str, Any]]:
    cached = _CREW_CACHE.get(trace_id)
    if cached is not None:
        return cached
    path = CREW_DIR / (_safe_name(trace_id) + ".json")
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _audit_lock(trace_id: str):
    """同一条轨迹串行合并，不同轨迹可并行审计。锁按当前事件循环惰性创建。"""

    locks = _loop_locks()["audit"]
    if not trace_id:
        return locks[0]
    return locks[hashlib.sha256(trace_id.encode("utf-8")).digest()[0] % _AUDIT_LOCK_SHARDS]


def _parse_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _merge_trajectory(trajectory: Trajectory) -> Trajectory:
    record = STORE.get(trajectory.trace_id)
    if not record or not record.get("payload"):
        return trajectory
    try:
        previous = Trajectory.model_validate(record["payload"])
    except Exception:
        logger.warning("已有轨迹无法解析，按新轨迹处理：%s", trajectory.trace_id)
        return trajectory

    spans = {span.span_id: span for span in previous.spans}
    for span in trajectory.spans:
        spans[span.span_id] = span
    merged_spans = sorted(spans.values(), key=lambda span: span.start_time)

    merged = trajectory.model_copy(deep=True)
    merged.spans = merged_spans
    merged.started_at = min(previous.started_at, trajectory.started_at)
    ends = [value for value in (previous.ended_at, trajectory.ended_at) if value is not None]
    merged.ended_at = max(ends) if ends else None
    merged.received_at = min(previous.received_at, trajectory.received_at)
    metadata = dict(previous.metadata or {})
    metadata.update(trajectory.metadata or {})
    merged.metadata = metadata
    return merged


async def process_trace(
    trajectory: Trajectory,
    context: AuditContext,
    policy: Optional[Policy] = None,
    mode: str = "shadow",
    model_snapshot: Optional[ModelSnapshot] = None,
) -> None:
    """后台审计：不挡 Agent 的响应，慢活都在这里做。"""

    snapshot = model_snapshot if model_snapshot is not None else _model_snapshot()
    METRICS.inc("audits_started")
    try:
        async with _audit_lock(trajectory.trace_id):
            trajectory = _merge_trajectory(trajectory)
            baseline = STORE.agent_stats(
                trajectory.agent_id,
                exclude_trace_id=trajectory.trace_id,
                tenant_id=trajectory.tenant_id,
            )
            # 基线属于审计输入的一部分，必须在计算内容哈希之前写入。
            trajectory.metadata["baseline"] = baseline
            previous = STORE.last_integrity(trajectory.trace_id)
            active_policy = policy or POLICY.resolve(trajectory.agent_id)
            normalized = normalize(
                trajectory,
                PRICING,
                redact=bool(active_policy and active_policy.redact_content),
                prev_hash=previous[1] if previous else None,
            )
            rules = active_policy.select_rules(RULES) if active_policy else RULES
            rule_context = AuditContext(
                budget_usd=context.budget_usd,
                allowed_tools=context.allowed_tools,
                loop_threshold=context.loop_threshold,
                write_amount_threshold=context.write_amount_threshold,
                baseline=baseline,
            )
            hits = RuleEngine(rules).run(normalized, rule_context)

            judge_status = "skipped"
            judge_note = "语义裁判未启用"
            if snapshot.judge_enabled:
                selected = snapshot.registry.resolve_judge()
                config = _judge_config(snapshot.registry)
                if config is None:
                    judge_note = "裁判 %s 未配置密钥（%s），跳过语义裁判" % (
                        selected.id if selected else "默认",
                        selected.api_key_env if selected else "SAFEORBIT_JUDGE_API_KEY / DEEPSEEK_API_KEY",
                    )
                else:
                    crew = await run_crew(normalized, config)
                    hits.extend(crew.hits)
                    judge_status = crew.status
                    judge_note = crew.note
                    _remember_crew(normalized.trace_id, crew)

            report = build_report(
                normalized,
                hits,
                PRICING,
                judge_status=judge_status,
                judge_note=judge_note,
                budget_usd=context.budget_usd,
            )
            html = render_html(report)
            # SQLite 为同步驱动：放到线程池执行，避免 checkpoint 或锁等待阻塞事件循环
            await anyio.to_thread.run_sync(STORE.save, normalized, report, html)

            # 行为事件是审计的只读事实层；失败不能拖垮报告，但必须留下日志。
            policy_ref = None
            if active_policy is not None:
                policy_ref = "%s@%s" % (
                    active_policy.policy_id, active_policy.updated_at.isoformat()
                )
            try:
                await anyio.to_thread.run_sync(
                    lambda: EVENT_STORE.ingest(normalized, policy_ref=policy_ref, mode=mode)
                )
            except Exception:
                logger.exception("行为事件入库失败：%s", normalized.trace_id)

            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            (REPORT_DIR / (_safe_name(normalized.trace_id) + ".html")).write_text(
                html, encoding="utf-8"
            )

            disposition = None
            if report.verdict.value in ("fail", "review") and not DISPO.has_for_trace(normalized.trace_id):
                disposition = DISPO.auto_propose_from_report(
                    trace_id=normalized.trace_id,
                    agent_id=normalized.agent_id,
                    session_id=normalized.session_id,
                    verdict=report.verdict.value,
                    hits=[hit.model_dump(mode="json") for hit in report.hits],
                    mode=mode,
                )

            alert = [hit for hit in hits if _SEVERITY_RANK.get(hit.severity.value, 0) >= 2]
            if alert:
                top = max(alert, key=lambda hit: _SEVERITY_RANK.get(hit.severity.value, 0))
                event = {
                    "event_type": "alert",
                    "trace_id": normalized.trace_id,
                    "agent_id": normalized.agent_id,
                    "verdict": report.verdict.value,
                    "severity": top.severity.value,
                    "message": top.message,
                    "cost_usd": report.cost.total_usd,
                    "created_at": report.generated_at.isoformat(),
                    "disposition_id": disposition.disposition_id if disposition else None,
                }
                await BUS.publish(event)
                asyncio.create_task(notify.dispatch(event))
                METRICS.inc("alerts")
            METRICS.inc("audits_ok")
            logger.info(
                "audited trace=%s verdict=%s spans=%d hits=%d cost=%.6f mode=%s",
                normalized.trace_id,
                report.verdict.value,
                len(normalized.spans),
                len(report.hits),
                report.cost.total_usd,
                mode,
            )
    except Exception:
        METRICS.inc("audits_failed")
        logger.exception("审计轨迹失败：%s", trajectory.trace_id)


# --------------------------------------------------------------------------
# 页面与查询
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    # 页面文件禁止缓存，避免浏览器长期持有旧版页面；数据接口要求登录
    return HTMLResponse(DASHBOARD_HTML, headers={"Cache-Control": "no-store"})


_LOGO_PATH = Path(__file__).with_name("assets") / "logo.svg"


@app.get("/favicon.svg")
async def favicon() -> Response:
    """站点图标：与产品 Logo 同一份 SVG，浏览器标签页直接用。"""

    try:
        content = _LOGO_PATH.read_text(encoding="utf-8")
    except OSError:
        raise HTTPException(status_code=404, detail="logo 资源缺失")
    return Response(
        content=content,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/favicon.ico")
async def favicon_legacy() -> RedirectResponse:
    """老浏览器会请求 .ico，重定向到 SVG，避免无意义的 404。"""

    return RedirectResponse(url="/favicon.svg", status_code=307)


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "version": "0.5.0",
        "mode": _mode(),
        "mock_upstream": MOCK_UPSTREAM,
        "judge_enabled": _model_snapshot().judge_enabled,
        "auth_enabled": AUTH_ENABLED,
        "gateway_url": PUBLIC_GATEWAY_URL,
        "db": DB_PATH,
        "users": AUTH_STORE.count_users(),
    }


@app.get("/health/live")
async def health_live() -> Dict[str, Any]:
    """存活探针：进程在即返回 200，不依赖任何外部组件。"""

    return {"ok": True, "version": "0.5.0"}


def _db_healthy() -> bool:
    try:
        STORE.count_traces()
        return True
    except Exception:
        return False


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    """就绪探针：检查数据库等关键依赖，并在存在打开熔断器时如实上报。"""

    checks = {"db": _db_healthy()}
    open_circuits = {
        name: snap for name, snap in circuit_mod.REGISTRY.status().items() if snap["state"] == "open"
    }
    ready = all(checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "ok": ready,
            "version": "0.5.0",
            "checks": checks,
            "open_circuits": open_circuits,
        },
    )


@app.get("/api/system/status")
async def system_status(request: Request) -> Dict[str, Any]:
    """管理员可见的运行状态：依赖健康、熔断状态与进程内计数。"""

    _require(request, "admin")
    mode_state = RUNTIME_MODE.status()
    return {
        "version": "0.5.0",
        "mode": mode_state["mode"],
        "mode_source": mode_state["source"],
        "mode_locked": mode_state["locked"],
        "uptime_seconds": int(time.monotonic() - STARTED_AT),
        "db_ok": _db_healthy(),
        "judge_enabled": _model_snapshot().judge_enabled,
        "circuits": circuit_mod.REGISTRY.status(),
        "counters": dict(METRICS.counters),
        "events": EVENT_STORE.counts(tenant_id=_tenant_scope(_current_user(request))),
    }


@app.post("/api/system/mode")
async def set_system_mode(request: Request) -> Dict[str, Any]:
    """管理员切换全局运行模式；shadow 只记录，enforce 才允许阻断。"""

    user = _require(request, "admin")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    try:
        result = RUNTIME_MODE.set(str(payload.get("mode") or ""), updated_by=user.username)
    except RuntimeModeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    AUDIT_LOG.log(
        user.username, "system.mode", result["mode"],
        {"previous": result.get("previous"), "locked": result["locked"]},
        tenant_id=user.tenant_id,
    )
    logger.warning(
        "全局运行模式切换：%s -> %s（操作人 %s）",
        result.get("previous"), result["mode"], user.username,
    )
    return result


@app.get("/metrics")
async def metrics() -> Any:
    """Prometheus 文本指标；只含聚合计数，不含轨迹内容。"""

    gauges = {
        "traces": STORE.count_traces(),
        "approvals_pending": len(APPROVALS.list(status="proposed", limit=500)),
        "isolated_sessions": len(DISPO.runtime_summary().get("isolated_sessions") or []),
        "blocked_tools": len(DISPO.runtime_summary().get("blocked_tools") or []),
    }
    return StreamingResponse(
        iter([METRICS.render(gauges)]),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/api/audit-log")
async def list_audit_log(request: Request, limit: int = 100) -> Dict[str, Any]:
    user = _require(request, "admin")
    return {
        "events": AUDIT_LOG.list(limit=limit, tenant_id=_tenant_scope(user)),
        "limit": max(1, min(int(limit), 500)),
    }


@app.get("/api/traces")
async def list_traces(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    agent_id: Optional[str] = None,
    verdict: Optional[str] = None,
    search: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Dict[str, Any]:
    _require(request, "viewer")
    user = _current_user(request)
    tenant = _tenant_scope(user)
    traces = STORE.list_traces(
        limit=limit,
        offset=offset,
        agent_id=agent_id,
        verdict=verdict,
        search=search,
        start=start,
        end=end,
        tenant_id=tenant,
    )
    return {
        "traces": traces,
        "total": STORE.count_traces(
            agent_id=agent_id,
            verdict=verdict,
            search=search,
            start=start,
            end=end,
            tenant_id=tenant,
        ),
        "limit": max(1, min(int(limit), 500)),
        "offset": max(0, int(offset)),
        "tenant_id": tenant or "all",
        "mode": _mode(),
        "auth_enabled": AUTH_ENABLED,
        "gateway_url": PUBLIC_GATEWAY_URL,
        "mock_upstream": MOCK_UPSTREAM,
    }


@app.get("/api/traces/export.csv")
async def export_traces(
    request: Request,
    agent_id: Optional[str] = None,
    verdict: Optional[str] = None,
    search: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> StreamingResponse:
    """按当前筛选条件导出轨迹清单，便于线下复核与归档。"""

    _require(request, "viewer")
    user = _current_user(request)
    rows = STORE.list_traces(
        limit=500,
        offset=0,
        agent_id=agent_id,
        verdict=verdict,
        search=search,
        start=start,
        end=end,
        tenant_id=_tenant_scope(user),
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["trace_id", "agent_id", "agent_version", "verdict", "hits", "cost_usd",
         "report_version", "content_hash", "created_at"]
    )
    for row in rows:
        writer.writerow(
            [
                row.get("trace_id", ""),
                row.get("agent_id", ""),
                row.get("agent_version", ""),
                row.get("verdict", ""),
                row.get("hits_count", 0),
                row.get("cost_usd", 0.0),
                row.get("report_version", 1),
                row.get("content_hash", ""),
                row.get("created_at", ""),
            ]
        )
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="safeorbit-traces.csv"'},
    )


def _store_summary_report(report: SummaryReport) -> str:
    global LATEST_SUMMARY_REPORT
    SUMMARY_REPORTS[report.report_id] = report
    if len(SUMMARY_REPORTS) > SUMMARY_REPORT_LIMIT:
        for key in list(SUMMARY_REPORTS.keys())[:-SUMMARY_REPORT_LIMIT]:
            SUMMARY_REPORTS.pop(key, None)
    LATEST_SUMMARY_REPORT = report.report_id
    SUMMARY_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    html_text = render_summary_html(report)
    target = SUMMARY_REPORT_DIR / (_safe_name(report.report_id) + ".html")
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(html_text, encoding="utf-8")
    os.replace(str(temp), str(target))
    return html_text


def _summary_report_html(report_id: str) -> Optional[str]:
    if not _SUMMARY_ID_RE.match(report_id or ""):
        return None
    report = SUMMARY_REPORTS.get(report_id)
    if report is not None:
        return render_summary_html(report)
    path = SUMMARY_REPORT_DIR / (_safe_name(report_id) + ".html")
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _latest_summary_html() -> Optional[str]:
    global LATEST_SUMMARY_REPORT
    if LATEST_SUMMARY_REPORT:
        html_text = _summary_report_html(LATEST_SUMMARY_REPORT)
        if html_text is not None:
            return html_text
    if not SUMMARY_REPORT_DIR.is_dir():
        return None
    files = [path for path in SUMMARY_REPORT_DIR.glob("sum-*.html") if path.is_file()]
    if not files:
        return None
    newest = max(files, key=lambda path: path.stat().st_mtime)
    LATEST_SUMMARY_REPORT = newest.stem
    return newest.read_text(encoding="utf-8")


@app.get("/api/traces/overview")
async def traces_overview(request: Request) -> Dict[str, Any]:
    user = _require(request, "viewer")
    return await anyio.to_thread.run_sync(
        lambda: STORE.overview(tenant_id=_tenant_scope(user))
    )


@app.post("/api/traces/summary-report")
async def create_summary_report(request: Request) -> Dict[str, Any]:
    user = _require(request, "auditor")
    async with _loop_locks()["summary"]:
        overview = STORE.overview(tenant_id=_tenant_scope(user))
        if not overview["totals"]["traces"]:
            raise HTTPException(status_code=400, detail="暂无可汇总的轨迹")
        report = build_summary_report(overview)
        _store_summary_report(report)
    logger.info("汇总报告已生成：%s traces=%d by=%s",
                report.report_id, overview["totals"]["traces"], user.username)
    return {
        "report": report.model_dump(mode="json"),
        "report_id": report.report_id,
        "view_url": "/api/traces/summary-report/%s" % report.report_id,
        "download_url": "/api/traces/summary-report/%s/download" % report.report_id,
        "generated_by": user.username,
    }


@app.get("/api/traces/summary-report/latest", response_class=HTMLResponse)
async def summary_report_latest(request: Request) -> str:
    _require(request, "viewer")
    html_text = _latest_summary_html()
    if html_text is None:
        raise HTTPException(status_code=404, detail="还没有汇总报告")
    return html_text


@app.get("/api/traces/summary-report/{report_id}/download", response_class=HTMLResponse)
async def summary_report_download(report_id: str, request: Request) -> HTMLResponse:
    _require(request, "viewer")
    html_text = _summary_report_html(report_id)
    if html_text is None:
        raise HTTPException(status_code=404, detail="汇总报告不存在")
    return HTMLResponse(
        content=html_text,
        headers={
            "Content-Disposition": 'attachment; filename="%s.html"' % _safe_name(report_id)
        },
    )


@app.get("/api/traces/summary-report/{report_id}", response_class=HTMLResponse)
async def summary_report_view(report_id: str, request: Request) -> str:
    _require(request, "viewer")
    html_text = _summary_report_html(report_id)
    if html_text is None:
        raise HTTPException(status_code=404, detail="汇总报告不存在")
    return html_text


@app.get("/api/traces/{trace_id}")
async def get_trace(trace_id: str, request: Request) -> Dict[str, Any]:
    user = _require(request, "viewer")
    record = STORE.get(trace_id, tenant_id=_tenant_scope(user))
    if record is None:
        raise HTTPException(status_code=404, detail="轨迹不存在")
    return record["report_json"]


@app.get("/api/traces/{trace_id}/report", response_class=HTMLResponse)
async def get_report(trace_id: str, request: Request) -> str:
    user = _require(request, "viewer")
    record = STORE.get(trace_id, tenant_id=_tenant_scope(user))
    if record is None or not record.get("report_html"):
        raise HTTPException(status_code=404, detail="报告不存在")
    return record["report_html"]


@app.get("/api/traces/{trace_id}/integrity")
async def trace_integrity(trace_id: str, request: Request) -> Dict[str, Any]:
    """重算每个报告版本的内容哈希，并校验版本之间的链式引用。"""

    user = _require(request, "viewer")
    if STORE.get(trace_id, tenant_id=_tenant_scope(user)) is None:
        raise HTTPException(status_code=404, detail="轨迹不存在")
    return STORE.verify_chain(trace_id)


@app.get("/api/traces/{trace_id}/versions/{version}/report", response_class=HTMLResponse)
async def get_report_version(trace_id: str, version: int, request: Request) -> str:
    user = _require(request, "viewer")
    if STORE.get(trace_id, tenant_id=_tenant_scope(user)) is None:
        raise HTTPException(status_code=404, detail="轨迹不存在")
    record = STORE.get_report_version(trace_id, version)
    if record is None or not record.get("report_html"):
        raise HTTPException(status_code=404, detail="报告版本不存在")
    return record["report_html"]


@app.get("/api/traces/{trace_id}/spans")
async def get_trace_spans(trace_id: str, request: Request) -> Dict[str, Any]:
    """返回原始 span 明细与命中证据，供审计员逐条核对。"""

    user = _require(request, "viewer")
    record = STORE.get(trace_id, tenant_id=_tenant_scope(user))
    if record is None:
        raise HTTPException(status_code=404, detail="轨迹不存在")
    payload = record.get("payload") or {}
    report = record.get("report_json") or {}
    hits = report.get("hits") or []
    hits_by_span: Dict[str, List[Dict[str, Any]]] = {}
    for hit in hits:
        hits_by_span.setdefault(str(hit.get("span_ref") or ""), []).append(hit)

    spans = []
    for index, span in enumerate(payload.get("spans") or []):
        span_id = str(span.get("span_id") or "")
        spans.append(
            {
                "index": index,
                "span_id": span_id,
                "parent_span_id": span.get("parent_span_id"),
                "kind": span.get("kind"),
                "name": span.get("name"),
                "status": span.get("status"),
                "start_time": span.get("start_time"),
                "end_time": span.get("end_time"),
                "error": span.get("error"),
                "input": span.get("input"),
                "output": span.get("output"),
                "attributes": span.get("attributes") or {},
                "llm": span.get("llm"),
                "tool": span.get("tool"),
                "retrieval": span.get("retrieval"),
                "handoff": span.get("handoff"),
                "evidence_ref": span.get("evidence_ref"),
                "findings": hits_by_span.get(span_id, []),
            }
        )
    return {
        "trace_id": trace_id,
        "agent_id": record.get("agent_id"),
        "agent_version": record.get("agent_version"),
        "source": payload.get("source"),
        "framework": payload.get("framework"),
        "started_at": payload.get("started_at"),
        "ended_at": payload.get("ended_at"),
        "redacted": bool(payload.get("redacted")),
        "sampled": bool(payload.get("sampled")),
        "truncated_fields": payload.get("truncated_fields") or [],
        "integrity": payload.get("integrity"),
        "report_version": record.get("report_version"),
        "spans": spans,
    }


def _span_summary(span: Dict[str, Any]) -> str:
    kind = span.get("kind")
    if kind == "llm":
        llm = span.get("llm") or {}
        usage = llm.get("usage") or {}
        tokens = int(usage.get("prompt") or 0) + int(usage.get("completion") or 0)
        summary = "%s · %d tokens" % (llm.get("model") or "未知模型", tokens)
        if llm.get("cost_usd") is not None:
            summary += " · $%.6f" % float(llm["cost_usd"])
        return summary
    if kind == "tool":
        tool = span.get("tool") or {}
        name = tool.get("tool_name") or span.get("name") or "tool"
        side_effect = "写操作" if tool.get("side_effect") == "write" else "读取"
        summary = "%s · %s" % (name, side_effect)
        if tool.get("confirmed_by_human"):
            summary += " · 客户端自报人工确认（非可信审批）"
        return summary
    if kind == "retrieval":
        retrieval = span.get("retrieval") or {}
        return "检索 · %d 条" % len(retrieval.get("documents") or [])
    if kind == "handoff":
        handoff = span.get("handoff") or {}
        return "交接 → %s" % (handoff.get("to_agent") or "未知")
    return span.get("name") or kind or "节点"


_RESOURCE_KEYS = (
    ("url", "api"), ("endpoint", "api"), ("host", "api"),
    ("table", "database"), ("database", "database"), ("db", "database"),
    ("collection", "database"),
    ("path", "file"), ("file", "file"), ("filename", "file"),
    ("bucket", "file"), ("object", "file"),
    ("repo", "repository"), ("repository", "repository"),
    ("order_id", "record"), ("user_id", "record"), ("record_id", "record"),
    ("document_id", "record"), ("account_id", "record"), ("customer_id", "record"),
)
_RESOURCE_LABEL = {
    "api": "外部接口", "database": "数据表", "file": "文件",
    "repository": "代码仓库", "record": "业务对象", "tool": "工具",
}
_RESOURCE_RISK_ORDER = {"blocked": 0, "write": 1, "exec": 2, "read": 3}


def _tool_resource(tool: Dict[str, Any], fallback: str) -> Tuple[str, str]:
    """从工具参数推断被触碰的资源；推断不到时退回工具名。"""

    arguments = tool.get("arguments")
    if isinstance(arguments, dict):
        for key, kind in _RESOURCE_KEYS:
            value = arguments.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                return kind, str(value)[:160]
    return "tool", fallback


def _short_text(value: Any, limit: int = 240) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _timeline_payload(
    record: Dict[str, Any], trace_id: str
) -> Tuple[List[Dict[str, Any]], float]:
    payload = record.get("payload") or {}
    report = record.get("report_json") or {}
    raw_spans = payload.get("spans") or []
    origin = _parse_time(payload.get("started_at")) or _parse_time(payload.get("received_at"))
    hits = report.get("hits") or []

    items = []
    for span in raw_spans:
        start = _parse_time(span.get("start_time")) or origin
        end = _parse_time(span.get("end_time")) or start
        offset_ms = max((start - origin).total_seconds() * 1000, 0.0) if origin and start else 0.0
        duration_ms = max((end - start).total_seconds() * 1000, 0.0) if start and end else 0.0
        related = [
            {
                "rule_id": hit.get("rule_id"),
                "rule_name": hit.get("rule_name"),
                "severity": hit.get("severity"),
                "message": hit.get("message"),
            }
            for hit in hits
            if hit.get("span_ref") == span.get("span_id")
        ]
        items.append(
            {
                "span_id": span.get("span_id"),
                "kind": span.get("kind"),
                "name": span.get("name"),
                "status": span.get("status"),
                "offset_ms": round(offset_ms, 1),
                "duration_ms": round(duration_ms, 1),
                "summary": _span_summary(span),
                "findings": related,
            }
        )

    items.sort(key=lambda item: item["offset_ms"])
    base_offset = items[0]["offset_ms"] if items else 0.0
    for item in items:
        item["offset_ms"] = round(item["offset_ms"] - base_offset, 1)
    total_ms = max([item["offset_ms"] + item["duration_ms"] for item in items] + [1.0])
    for item in items:
        item["left_pct"] = round(item["offset_ms"] / total_ms * 100, 2)
        item["width_pct"] = round(max(item["duration_ms"] / total_ms * 100, 1.0), 2)
    return items, total_ms


def _audit_views(record: Dict[str, Any], trace_id: str) -> Dict[str, Any]:
    """把一条轨迹整理成五个审计视图：时间线、资源、权限、决策来源、持久变更。"""

    payload = record.get("payload") or {}
    report = record.get("report_json") or {}
    spans = payload.get("spans") or []
    hits = report.get("hits") or []
    hits_by_span: Dict[str, List[Dict[str, Any]]] = {}
    for hit in hits:
        hits_by_span.setdefault(str(hit.get("span_ref") or ""), []).append(hit)
    timeline, total_ms = _timeline_payload(record, trace_id)

    approvals = [
        item for item in APPROVALS.list(limit=500) if item.trace_id == trace_id
    ]
    approvals_by_tool: Dict[str, List[Any]] = {}
    for item in approvals:
        approvals_by_tool.setdefault(item.tool_name, []).append(item)

    resources: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    permissions: List[Dict[str, Any]] = []
    decisions: List[Dict[str, Any]] = []
    impact_items: List[Dict[str, Any]] = []

    last_llm: Optional[Dict[str, Any]] = None
    last_retrieval: Optional[Dict[str, Any]] = None
    blocked_count = 0
    write_count = 0

    for index, span in enumerate(spans):
        kind = span.get("kind")
        if kind == "llm":
            last_llm = span
            last_retrieval = None
            continue
        if kind == "retrieval":
            last_retrieval = span
            continue
        if kind != "tool":
            continue

        tool = span.get("tool") or {}
        tool_name = tool.get("tool_name") or span.get("name") or "tool"
        side_effect = tool.get("side_effect") or "unknown"
        blocked = bool(tool.get("blocked"))
        kind_key, target = _tool_resource(tool, tool_name)
        action = "write" if side_effect == "write" else ("read" if side_effect == "read" else "exec")
        risk = "blocked" if blocked else ("write" if action == "write" else ("exec" if action == "exec" else "read"))
        span_id = str(span.get("span_id") or "")
        findings = hits_by_span.get(span_id, [])

        bucket = resources.setdefault(
            (kind_key, target, action),
            {
                "kind": kind_key,
                "kind_label": _RESOURCE_LABEL.get(kind_key, "资源"),
                "resource": target,
                "action": action,
                "risk": risk,
                "count": 0,
                "blocked": False,
                "tools": [],
                "span_ids": [],
                "findings": [],
            },
        )
        bucket["count"] += 1
        bucket["blocked"] = bucket["blocked"] or blocked
        if tool_name not in bucket["tools"]:
            bucket["tools"].append(tool_name)
        bucket["span_ids"].append(span_id)
        bucket["findings"].extend(findings)
        if _RESOURCE_RISK_ORDER.get(risk, 9) < _RESOURCE_RISK_ORDER.get(bucket["risk"], 9):
            bucket["risk"] = risk

        if action == "write":
            write_count += 1
        if blocked:
            blocked_count += 1

        related_approvals = approvals_by_tool.get(tool_name) or []
        if related_approvals:
            source = "审批票据"
        elif tool.get("confirmed_by_human"):
            source = "客户端自报（不可信）"
        elif tool.get("permission"):
            source = "策略权限"
        else:
            source = "默认权限"
        permissions.append(
            {
                "at": span.get("start_time"),
                "span_id": span_id,
                "tool": tool_name,
                "permission": tool.get("permission") or "未声明",
                "side_effect": side_effect,
                "status": "已阻断" if blocked else ("已执行" if span.get("status") == "ok" else (span.get("status") or "未知")),
                "source": source,
                "approval_ids": [item.approval_id for item in related_approvals],
                "declared_by_client": bool(tool.get("confirmed_by_human")),
                "findings": findings,
            }
        )

        prompt = ""
        model = ""
        if last_llm:
            llm = last_llm.get("llm") or {}
            model = llm.get("model") or ""
            messages = llm.get("messages") or []
            for message in reversed(messages):
                if isinstance(message, dict) and message.get("role") == "user":
                    prompt = _short_text(message.get("content"))
                    break
        context_text = "无前置检索"
        if last_retrieval:
            retrieval = last_retrieval.get("retrieval") or {}
            documents = retrieval.get("documents") or []
            context_text = "检索「%s」命中 %d 条" % (
                _short_text(retrieval.get("query"), 80) or "未命名查询",
                len(documents),
            )
        decisions.append(
            {
                "span_id": span_id,
                "tool": tool_name,
                "at": span.get("start_time"),
                "prompt": prompt or "（未采集到前置提示）",
                "model": model,
                "context": context_text,
                "arguments": _short_text(tool.get("arguments")),
                "arguments_hash": hashlib.sha256(
                    json.dumps(tool.get("arguments") or {}, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
                ).hexdigest()[:16],
                "outcome": "已阻断" if blocked else _short_text(tool.get("result"), 160),
                "blocked": blocked,
                "findings": findings,
                "index": index,
            }
        )

        if action == "write":
            impact_items.append(
                {
                    "at": span.get("start_time"),
                    "span_id": span_id,
                    "tool": tool_name,
                    "target": target,
                    "target_kind": _RESOURCE_LABEL.get(kind_key, "资源"),
                    "blocked": blocked,
                    "result": _short_text(tool.get("result"), 200) or "（无返回内容）",
                }
            )

    resource_list = sorted(
        resources.values(),
        key=lambda item: (_RESOURCE_RISK_ORDER.get(item["risk"], 9), -item["count"], item["resource"]),
    )

    return {
        "trace_id": trace_id,
        "agent_id": record.get("agent_id"),
        "agent_version": record.get("agent_version"),
        "verdict": record.get("verdict"),
        "hits_count": record.get("hits_count"),
        "duration_ms": round(total_ms, 1),
        "report_url": "/api/traces/%s/report" % trace_id,
        "summary": {
            "steps": len(spans),
            "tools": sum(1 for span in spans if span.get("kind") == "tool"),
            "writes": write_count,
            "blocked": blocked_count,
            "resources": len(resource_list),
            "approvals": len(approvals),
        },
        "timeline": timeline,
        "resources": resource_list,
        "permissions": permissions,
        "decisions": decisions,
        "impact": {
            "items": impact_items,
            "reversible": False,
            "note": "安轨不持有业务系统的回滚能力；请在业务侧撤销变更，并在处置中心记录处置结果。",
        },
    }


@app.get("/api/traces/{trace_id}/timeline")
async def get_timeline(trace_id: str, request: Request) -> Dict[str, Any]:
    """轨迹回放数据：按时间顺序展开 span，并标注命中项。"""

    user = _require(request, "viewer")
    record = STORE.get(trace_id, tenant_id=_tenant_scope(user))
    if record is None:
        raise HTTPException(status_code=404, detail="轨迹不存在")
    items, total_ms = _timeline_payload(record, trace_id)
    return {
        "trace_id": trace_id,
        "agent_id": record.get("agent_id"),
        "verdict": record.get("verdict"),
        "hits_count": record.get("hits_count"),
        "duration_ms": round(total_ms, 1),
        "report_url": "/api/traces/%s/report" % trace_id,
        "spans": items,
    }


@app.get("/api/traces/{trace_id}/audit-views")
async def get_audit_views(trace_id: str, request: Request) -> Dict[str, Any]:
    """五视图审计数据：时间线、资源触碰、权限与授权、决策来源、持久变更。"""

    user = _require(request, "viewer")
    record = STORE.get(trace_id, tenant_id=_tenant_scope(user))
    if record is None:
        raise HTTPException(status_code=404, detail="轨迹不存在")
    return await anyio.to_thread.run_sync(_audit_views, record, trace_id)


@app.get("/api/traces/{trace_id}/crew")
async def trace_crew(trace_id: str, request: Request) -> Dict[str, Any]:
    """审计小队分工详情；未执行过小队时返回 unavailable，不伪造结论。"""

    user = _require(request, "viewer")
    if STORE.get(trace_id, tenant_id=_tenant_scope(user)) is None:
        raise HTTPException(status_code=404, detail="轨迹不存在")
    record = _load_crew(trace_id)
    if record is None:
        return {
            "trace_id": trace_id, "status": "unavailable",
            "agents": [], "note": "该轨迹未执行审计小队",
        }
    return record


_EVENT_OUTCOMES = frozenset(
    {"requested", "attempted", "succeeded", "failed", "blocked", "unknown"}
)
_EVENT_FAMILIES = frozenset(
    {"task.run", "model.invoke", "tool.invoke", "data.read",
     "message.send", "approval.decide", "system.event"}
)


def _event_filter_args(outcome: Optional[str], action_family: Optional[str]) -> None:
    if outcome and outcome not in _EVENT_OUTCOMES:
        raise HTTPException(status_code=400, detail="outcome 不在允许取值内")
    if action_family and action_family not in _EVENT_FAMILIES:
        raise HTTPException(status_code=400, detail="action_family 不在允许取值内")


@app.get("/api/traces/{trace_id}/events/tree")
async def trace_event_tree(trace_id: str, request: Request) -> Dict[str, Any]:
    """行为事件父子装配；父节点未观测到时显式列出缺口，不自动补造。"""

    user = _require(request, "viewer")
    tenant = _tenant_scope(user)
    if STORE.get(trace_id, tenant_id=tenant) is None:
        raise HTTPException(status_code=404, detail="轨迹不存在")
    return await anyio.to_thread.run_sync(
        lambda: EVENT_STORE.assemble_trace(trace_id, tenant_id=tenant)
    )


@app.get("/api/traces/{trace_id}/events")
async def trace_events(
    trace_id: str,
    request: Request,
    actor: Optional[str] = None,
    tool: Optional[str] = None,
    action_family: Optional[str] = None,
    resource: Optional[str] = None,
    outcome: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """按轨迹查询行为事实，并附带该轨迹当前覆盖状态。"""

    user = _require(request, "viewer")
    tenant = _tenant_scope(user)
    if STORE.get(trace_id, tenant_id=tenant) is None:
        raise HTTPException(status_code=404, detail="轨迹不存在")
    _event_filter_args(outcome, action_family)
    result = await anyio.to_thread.run_sync(
        lambda: EVENT_STORE.list_events(
            tenant_id=tenant, trace_id=trace_id, actor=actor, tool=tool,
            action_family=action_family, resource=resource, outcome=outcome,
            start=start, end=end, limit=limit, offset=offset,
        )
    )
    coverage = await anyio.to_thread.run_sync(
        lambda: EVENT_STORE.coverage(trace_id, tenant_id=tenant)
    )
    return {"trace_id": trace_id, "coverage": coverage, **result}


@app.get("/api/events")
async def list_events(
    request: Request,
    trace_id: Optional[str] = None,
    actor: Optional[str] = None,
    tool: Optional[str] = None,
    action_family: Optional[str] = None,
    resource: Optional[str] = None,
    outcome: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """跨轨迹查询行为事实；非管理员只在自己的租户与可见 Agent 内查询。"""

    user = _require(request, "viewer")
    tenant = _tenant_scope(user)
    allowed = _allowed_agent_ids(user)
    if actor and allowed is not None and actor not in allowed:
        raise HTTPException(status_code=403, detail="无权查询该智能体的事件")
    _event_filter_args(outcome, action_family)
    result = await anyio.to_thread.run_sync(
        lambda: EVENT_STORE.list_events(
            tenant_id=tenant, trace_id=trace_id, actor=actor, tool=tool,
            action_family=action_family, resource=resource, outcome=outcome,
            start=start, end=end, limit=limit, offset=offset,
        )
    )
    return {"tenant_id": tenant or "all", **result}


@app.get("/api/resources")
async def list_resources(request: Request, limit: int = 200) -> Dict[str, Any]:
    user = _require(request, "viewer")
    items = await anyio.to_thread.run_sync(
        lambda: EVENT_STORE.list_resources(tenant_id=_tenant_scope(user), limit=limit)
    )
    return {"resources": items}



@app.get("/api/audit/metrics")
async def audit_metrics(request: Request) -> Dict[str, Any]:
    """HOTL 监管指标：每个指标都对应一个可执行动作。"""

    user = _require(request, "viewer")
    tenant = _tenant_scope(user)
    overview = STORE.overview(detail_limit=1, tenant_id=tenant)
    allowed = _allowed_agent_ids(user)

    approvals = APPROVALS.list(limit=500)
    if allowed is not None:
        approvals = [item for item in approvals if item.agent_id in allowed]
    decided = [item for item in approvals if item.decided_at is not None]
    rejected = [item for item in decided if item.status == "rejected"]
    durations = [
        max((item.decided_at - item.created_at).total_seconds(), 0.0)
        for item in decided
        if item.decided_at is not None
    ]
    pending = sum(1 for item in approvals if item.status == "proposed")

    totals = overview.get("totals") or {}
    trace_total = int(totals.get("traces") or 0)
    risk_total = int(totals.get("risk_traces") or 0)
    drift_total = STORE.baseline_drift_count(tenant_id=tenant)

    daily = overview.get("daily") or []
    cost_7d = round(sum(float(point.get("cost_usd") or 0.0) for point in daily[-7:]), 6)
    cost_prev_7d = round(sum(float(point.get("cost_usd") or 0.0) for point in daily[-14:-7]), 6)

    def ratio(value: int, base: int) -> float:
        return round(value / base, 4) if base else 0.0

    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "traces": trace_total,
        "escalation_rate": ratio(risk_total, trace_total),
        "risk_traces": risk_total,
        "rejection_rate": ratio(len(rejected), len(decided)),
        "decided": len(decided),
        "pending": pending,
        "drift_rate": ratio(drift_total, trace_total),
        "drift_traces": drift_total,
        "avg_decision_seconds": round(sum(durations) / len(durations), 1) if durations else None,
        "cost_7d": cost_7d,
        "cost_prev_7d": cost_prev_7d,
        "cost_change": (
            round((cost_7d - cost_prev_7d) / cost_prev_7d, 4) if cost_prev_7d else None
        ),
    }


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    _require(request, "viewer")
    queue = BUS.subscribe()

    async def stream():
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                event = await queue.get()
                yield "event: alert\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        except asyncio.CancelledError:
            return
        finally:
            BUS.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------
# 代码审计
# --------------------------------------------------------------------------
def _store_code_report(report: CodeScanReport) -> Tuple[str, str]:
    global LATEST_CODE_REPORT
    CODE_REPORTS[report.report_id] = report
    if len(CODE_REPORTS) > 30:
        for key in list(CODE_REPORTS.keys())[:-30]:
            CODE_REPORTS.pop(key, None)
    LATEST_CODE_REPORT = report.report_id
    CODE_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_text = code_report_to_json(report)
    html_text = render_code_report_html(report)
    (CODE_REPORT_DIR / (_safe_name(report.report_id) + ".json")).write_text(json_text, encoding="utf-8")
    (CODE_REPORT_DIR / (_safe_name(report.report_id) + ".html")).write_text(html_text, encoding="utf-8")
    (CODE_REPORT_DIR / (_safe_name(report.report_id) + ".sarif")).write_text(
        report_to_sarif(report), encoding="utf-8"
    )
    return json_text, html_text


@app.post("/api/codeaudit/scan")
async def codeaudit_scan(request: Request) -> Dict[str, Any]:
    user = _require(request, "auditor")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    path = str(payload.get("path") or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="缺少 path")
    baseline = None
    baseline_path = str(payload.get("baseline") or "").strip()
    if baseline_path:
        try:
            baseline = load_code_report(baseline_path)
        except Exception:
            raise HTTPException(status_code=400, detail="基线报告无法读取")
    ignore_file = str(payload.get("ignore_file") or "").strip() or None
    try:
        report = code_scan_path(path, baseline=baseline, ignore_file=ignore_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _store_code_report(report)
    AUDIT_LOG.log(
        user.username, "codeaudit.scan", path,
        {"report_id": report.report_id, "findings": len(report.findings)},
        tenant_id=user.tenant_id,
    )
    logger.info("代码审计完成：%s files=%d findings=%d", path, report.files_scanned, len(report.findings))
    return {"report": report.model_dump(mode="json"), "scanned_by": user.username}


def _safe_upload_name(name: str) -> Optional[str]:
    """把上传文件名规范成安全的相对路径，拒绝绝对路径与 .. 穿越。"""

    raw = (name or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return None
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _extract_zip(archive: Path, dest: Path) -> int:
    """安全解压上传的 zip：限制条目数与解压体积，跳过目录穿越与系统垃圾文件。"""

    written = 0
    total = 0
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = [item for item in bundle.infolist() if not item.is_dir()]
            if len(members) > CODE_UPLOAD_MAX_FILES:
                raise HTTPException(
                    status_code=413,
                    detail="压缩包内文件过多（上限 %d 个）" % CODE_UPLOAD_MAX_FILES,
                )
            for info in members:
                relative = _safe_upload_name(info.filename)
                if not relative or relative.startswith("__MACOSX/"):
                    continue
                total += info.file_size
                if total > CODE_UPLOAD_MAX_UNZIPPED:
                    raise HTTPException(status_code=413, detail="压缩包解压后体积超过限制")
                target = dest / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, open(target, "wb") as sink:
                    shutil.copyfileobj(source, sink, 1024 * 1024)
                written += 1
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="压缩包无法解析，请确认是标准 zip 文件")
    return written


@app.post("/api/codeaudit/upload")
async def codeaudit_upload(request: Request) -> Dict[str, Any]:
    """接收拖拽上传的 zip / 文件 / 文件夹，落盘到临时目录后复用文件系统扫描。"""

    user = _require(request, "auditor")
    try:
        form = await request.form()
    except Exception:
        raise HTTPException(status_code=400, detail="请使用 multipart/form-data 上传文件")
    uploads = [value for key, value in form.multi_items()
               if key == "files" and hasattr(value, "read")]
    if not uploads:
        raise HTTPException(status_code=400, detail="没有收到文件")
    if len(uploads) > CODE_UPLOAD_MAX_FILES:
        raise HTTPException(
            status_code=413, detail="文件数量超过上限 %d 个" % CODE_UPLOAD_MAX_FILES
        )
    try:
        declared = json.loads(str(form.get("paths") or "[]"))
    except (ValueError, TypeError):
        declared = []
    if not isinstance(declared, list):
        declared = []

    workdir = Path(tempfile.mkdtemp(prefix="safeorbit-upload-"))
    root = workdir / "project"
    root.mkdir()
    total = 0
    try:
        if len(uploads) == 1 and str(uploads[0].filename or "").lower().endswith(".zip"):
            archive = workdir / "upload.zip"
            with open(archive, "wb") as sink:
                while True:
                    chunk = await uploads[0].read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > CODE_UPLOAD_MAX_BYTES:
                        raise HTTPException(status_code=413, detail="上传体积超过限制")
                    sink.write(chunk)
            written = _extract_zip(archive, root)
            label = str(uploads[0].filename or "upload.zip")
        else:
            written = 0
            for index, item in enumerate(uploads):
                declared_name = declared[index] if index < len(declared) else None
                relative = _safe_upload_name(
                    declared_name if isinstance(declared_name, str) else (item.filename or "")
                )
                if not relative:
                    continue
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, "wb") as sink:
                    while True:
                        chunk = await item.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > CODE_UPLOAD_MAX_BYTES:
                            raise HTTPException(status_code=413, detail="上传体积超过限制")
                        sink.write(chunk)
                written += 1
            label = "%d 个文件" % written
        if written == 0:
            raise HTTPException(status_code=400, detail="上传内容里没有可读取的文件")

        report = await anyio.to_thread.run_sync(code_scan_path, str(root))
        _store_code_report(report)
        AUDIT_LOG.log(
            user.username, "codeaudit.upload", label,
            {"report_id": report.report_id, "files": written, "findings": len(report.findings)},
            tenant_id=user.tenant_id,
        )
        logger.info(
            "代码审计（上传）完成：%s files=%d findings=%d",
            label, report.files_scanned, len(report.findings),
        )
        return {
            "report": report.model_dump(mode="json"),
            "scanned_by": user.username,
            "uploaded_files": written,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@app.get("/api/codeaudit/latest")
async def codeaudit_latest(request: Request) -> Dict[str, Any]:
    _require(request, "viewer")
    if not LATEST_CODE_REPORT or LATEST_CODE_REPORT not in CODE_REPORTS:
        raise HTTPException(status_code=404, detail="还没有代码审计报告")
    return {"report": CODE_REPORTS[LATEST_CODE_REPORT].model_dump(mode="json")}


def _code_report_html(report_id: str) -> Optional[str]:
    report = CODE_REPORTS.get(report_id)
    if report is not None:
        return render_code_report_html(report)
    path = CODE_REPORT_DIR / (_safe_name(report_id) + ".html")
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return None


@app.get("/api/codeaudit/latest/html", response_class=HTMLResponse)
async def codeaudit_latest_html(request: Request) -> str:
    _require(request, "viewer")
    if not LATEST_CODE_REPORT:
        raise HTTPException(status_code=404, detail="还没有代码审计报告")
    html_text = _code_report_html(LATEST_CODE_REPORT)
    if html_text is None:
        raise HTTPException(status_code=404, detail="代码审计报告不存在")
    return html_text


@app.get("/api/codeaudit/{report_id}/html", response_class=HTMLResponse)
async def codeaudit_html(report_id: str, request: Request) -> str:
    _require(request, "viewer")
    html_text = _code_report_html(report_id)
    if html_text is None:
        raise HTTPException(status_code=404, detail="代码审计报告不存在")
    return html_text


@app.get("/api/codeaudit/{report_id}/sarif")
async def codeaudit_sarif(report_id: str, request: Request) -> JSONResponse:
    _require(request, "viewer")
    report = CODE_REPORTS.get(report_id)
    if report is None:
        path = CODE_REPORT_DIR / (_safe_name(report_id) + ".sarif")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="代码审计 SARIF 不存在")
        return JSONResponse(
            content=json.loads(path.read_text(encoding="utf-8")),
            headers={"Content-Disposition": 'attachment; filename="%s.sarif"' % _safe_name(report_id)},
        )
    return JSONResponse(
        content=json.loads(report_to_sarif(report)),
        headers={"Content-Disposition": 'attachment; filename="%s.sarif"' % _safe_name(report_id)},
    )


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# 供应链 / 基础设施 / 评测 / 数据生命周期
# --------------------------------------------------------------------------
def _store_sca(report: sca.ScaReport) -> None:
    global LATEST_SCA_REPORT
    SCA_REPORTS[report.report_id] = report
    if len(SCA_REPORTS) > 20:
        for key in list(SCA_REPORTS.keys())[:-20]:
            SCA_REPORTS.pop(key, None)
    LATEST_SCA_REPORT = report.report_id
    SCA_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    base = _safe_name(report.report_id)
    (SCA_REPORT_DIR / (base + ".json")).write_text(sca.report_to_json(report), encoding="utf-8")
    (SCA_REPORT_DIR / (base + ".sarif")).write_text(sca.report_to_sarif(report), encoding="utf-8")


def _latest_sca() -> Optional[sca.ScaReport]:
    global LATEST_SCA_REPORT
    if LATEST_SCA_REPORT and LATEST_SCA_REPORT in SCA_REPORTS:
        return SCA_REPORTS[LATEST_SCA_REPORT]
    if not SCA_REPORT_DIR.is_dir():
        return None
    files = sorted(
        [path for path in SCA_REPORT_DIR.glob("sca-rpt-*.json") if path.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in files[:20]:
        try:
            report = sca.ScaReport.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        SCA_REPORTS[report.report_id] = report
        LATEST_SCA_REPORT = report.report_id
        return report
    return None


def _store_infra(report: infrascan.InfraReport) -> None:
    global LATEST_INFRA_REPORT
    INFRA_REPORTS[report.report_id] = report
    if len(INFRA_REPORTS) > 20:
        for key in list(INFRA_REPORTS.keys())[:-20]:
            INFRA_REPORTS.pop(key, None)
    LATEST_INFRA_REPORT = report.report_id
    INFRA_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    base = _safe_name(report.report_id)
    (INFRA_REPORT_DIR / (base + ".json")).write_text(
        infrascan.report_to_json(report), encoding="utf-8"
    )
    (INFRA_REPORT_DIR / (base + ".sarif")).write_text(
        infrascan.report_to_sarif(report), encoding="utf-8"
    )


def _latest_infra() -> Optional[infrascan.InfraReport]:
    global LATEST_INFRA_REPORT
    if LATEST_INFRA_REPORT and LATEST_INFRA_REPORT in INFRA_REPORTS:
        return INFRA_REPORTS[LATEST_INFRA_REPORT]
    if not INFRA_REPORT_DIR.is_dir():
        return None
    files = sorted(
        [path for path in INFRA_REPORT_DIR.glob("infra-rpt-*.json") if path.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in files[:20]:
        try:
            report = infrascan.InfraReport.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        INFRA_REPORTS[report.report_id] = report
        LATEST_INFRA_REPORT = report.report_id
        return report
    return None


@app.post("/api/sca/scan")
async def sca_scan(request: Request) -> Dict[str, Any]:
    user = _require(request, "auditor")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    path = str(payload.get("path") or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="缺少 path")
    try:
        report = sca.scan_dependencies(
            path, advisory_path=str(payload.get("advisory_path") or "").strip() or None
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _store_sca(report)
    AUDIT_LOG.log(
        user.username, "sca.scan", path,
        {"report_id": report.report_id, "findings": len(report.findings)},
        tenant_id=user.tenant_id,
    )
    return {
        "report": report.model_dump(mode="json"),
        "sarif_url": "/api/sca/%s/sarif" % report.report_id,
    }


@app.get("/api/sca/latest")
async def sca_latest(request: Request) -> Dict[str, Any]:
    _require(request, "viewer")
    report = _latest_sca()
    if report is None:
        raise HTTPException(status_code=404, detail="还没有依赖扫描报告")
    return {"report": report.model_dump(mode="json")}


@app.get("/api/sca/{report_id}/sarif")
async def sca_sarif(report_id: str, request: Request) -> JSONResponse:
    _require(request, "viewer")
    report = SCA_REPORTS.get(report_id)
    if report is None:
        path = SCA_REPORT_DIR / (_safe_name(report_id) + ".sarif")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="依赖扫描 SARIF 不存在")
        return JSONResponse(content=json.loads(path.read_text(encoding="utf-8")))
    return JSONResponse(content=json.loads(sca.report_to_sarif(report)))


@app.post("/api/infrascan/scan")
async def infra_scan(request: Request) -> Dict[str, Any]:
    user = _require(request, "auditor")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    path = str(payload.get("path") or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="缺少 path")
    try:
        report = infrascan.scan_infra(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _store_infra(report)
    AUDIT_LOG.log(
        user.username, "infra.scan", path,
        {"report_id": report.report_id, "findings": len(report.findings)},
        tenant_id=user.tenant_id,
    )
    return {
        "report": report.model_dump(mode="json"),
        "sarif_url": "/api/infrascan/%s/sarif" % report.report_id,
    }


@app.get("/api/infrascan/latest")
async def infra_latest(request: Request) -> Dict[str, Any]:
    _require(request, "viewer")
    report = _latest_infra()
    if report is None:
        raise HTTPException(status_code=404, detail="还没有基础设施扫描报告")
    return {"report": report.model_dump(mode="json")}


@app.get("/api/infrascan/{report_id}/sarif")
async def infra_sarif(report_id: str, request: Request) -> JSONResponse:
    _require(request, "viewer")
    report = INFRA_REPORTS.get(report_id)
    if report is None:
        path = INFRA_REPORT_DIR / (_safe_name(report_id) + ".sarif")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="基础设施扫描 SARIF 不存在")
        return JSONResponse(content=json.loads(path.read_text(encoding="utf-8")))
    return JSONResponse(content=json.loads(infrascan.report_to_sarif(report)))


@app.post("/api/eval/redteam")
async def redteam(request: Request) -> Dict[str, Any]:
    user = _require(request, "auditor")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    prompts = payload.get("prompts") if isinstance(payload.get("prompts"), list) else None
    generated: List[str] = []
    if payload.get("generate") and prompts is None:
        config = _judge_config(_model_snapshot().registry)
        if config is not None:
            generated = await eval_mod.generate_prompts(config, count=6)
        prompts = list(eval_mod.REDTEAM_PROMPTS) + generated
    report = eval_mod.run_redteam(prompts)
    AUDIT_LOG.log(
        user.username, "eval.redteam", report.report_id,
        {"total": report.total, "detected": report.detected, "generated": len(generated)},
        tenant_id=user.tenant_id,
    )
    return {
        "report": report.model_dump(mode="json"),
        "generated": generated,
        "generated_count": len(generated),
    }


def _retention_policy(payload: Optional[Dict[str, Any]] = None) -> retention.RetentionPolicy:
    data = payload or {}
    return retention.RetentionPolicy(
        trace_days=int(data.get("trace_days") or os.getenv("SAFEORBIT_TRACE_RETENTION_DAYS", "90")),
        report_versions=int(
            data.get("report_versions") or os.getenv("SAFEORBIT_REPORT_VERSION_KEEP", "10")
        ),
        audit_days=int(data.get("audit_days") or os.getenv("SAFEORBIT_AUDIT_RETENTION_DAYS", "365")),
    )


@app.get("/api/retention")
async def retention_status(request: Request) -> Dict[str, Any]:
    _require(request, "admin")
    policy = _retention_policy()
    return {"policy": policy.to_public(), "preview": retention.preview(DB_PATH, policy)}


@app.post("/api/retention/purge")
async def retention_purge(request: Request) -> Dict[str, Any]:
    user = _require(request, "admin")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    policy = _retention_policy(payload)
    apply = bool(payload.get("apply"))
    result = retention.purge(DB_PATH, policy, dry_run=not apply)
    AUDIT_LOG.log(
        user.username, "retention.purge", "database",
        {"apply": apply, "deleted": result.get("deleted")}, tenant_id=user.tenant_id,
    )
    return result


@app.post("/api/evidence/export")
async def evidence_export(request: Request) -> Dict[str, Any]:
    user = _require(request, "auditor")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    trace_ids = payload.get("trace_ids")
    if isinstance(trace_ids, list):
        trace_ids = [str(item) for item in trace_ids]
    else:
        trace_ids = None
    allowed = _allowed_agent_ids(user)
    if allowed is not None:
        scoped = set(STORE.trace_ids(tenant_id=_tenant_scope(user)))
        trace_ids = [
            item for item in (trace_ids if trace_ids is not None else scoped)
            if item in scoped
        ]
    name = "evidence-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip"
    result = retention.export_evidence(
        DB_PATH, str(EVIDENCE_DIR / name), trace_ids=trace_ids
    )
    AUDIT_LOG.log(
        user.username, "evidence.export", name,
        {"traces": result.get("trace_count")}, tenant_id=user.tenant_id,
    )
    return {
        **result,
        "download_url": "/api/evidence/" + name,
    }


@app.get("/api/evidence/{filename}")
async def evidence_download(filename: str, request: Request) -> FileResponse:
    _require(request, "auditor")
    if not re.fullmatch(r"evidence-\d{8}-\d{6}\.zip", filename):
        raise HTTPException(status_code=404, detail="证据包不存在")
    path = EVIDENCE_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="证据包不存在")
    return FileResponse(path, media_type="application/zip", filename=filename)


# 模型网关
# --------------------------------------------------------------------------
@app.get("/v1/models")
async def models(request: Request) -> Dict[str, Any]:
    snapshot = request.state.model_snapshot
    if MOCK_UPSTREAM:
        return {"object": "list", "data": [{"id": "mock-model", "object": "model"}]}
    return {"object": "list", "data": [
        {"id": p.id, "object": "model", "owned_by": p.provider}
        for p in snapshot.registry.profiles if p.gateway and p.api_key
    ]}


@app.get("/api/models")
async def model_catalog(request: Request) -> Dict[str, Any]:
    _require(request, "viewer")
    snapshot = request.state.model_snapshot
    selected = snapshot.registry.resolve_judge()
    return {
        "models": [p.public() for p in snapshot.registry.profiles],
        "judge_profile": selected.id if selected else None,
        "judge_enabled": snapshot.judge_enabled,
        "revision": snapshot.revision,
        "mock": MOCK_UPSTREAM,
    }


_MODEL_TEST_RATE = RateLimiter(5)
_MODEL_TEST_BUSY = False


def _model_admin(request: Request) -> User:
    if request.headers.get("authorization"):
        raise HTTPException(status_code=403, detail="模型管理仅允许管理员 Cookie 会话")
    return _require(request, "admin")


@app.get("/api/admin/models")
async def admin_models(request: Request) -> Dict[str, Any]:
    _model_admin(request)
    return MODEL_SETTINGS.public_admin()


@app.put("/api/admin/models")
async def save_models(request: Request) -> Dict[str, Any]:
    user = _model_admin(request)
    try:
        result = MODEL_SETTINGS.save(await request.json(), actor=user.username)
    except SettingsError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    AUDIT_LOG.log(
        user.username, "models.save", "model-settings",
        {"revision": result.get("revision")}, tenant_id=user.tenant_id,
    )
    return result


@app.post("/api/admin/models/test")
async def test_model(request: Request) -> Dict[str, Any]:
    global _MODEL_TEST_BUSY
    user = _model_admin(request)
    payload = await request.json()
    if (set(payload) != {"id", "expected_revision"} or not isinstance(payload["id"], str)
            or type(payload["expected_revision"]) is not int):
        raise HTTPException(status_code=400, detail="仅接受已保存连接 id 与 expected_revision")
    snapshot = request.state.model_snapshot
    if payload["expected_revision"] != snapshot.revision:
        raise HTTPException(status_code=409, detail="模型设置已变更，请重新加载")
    profile = next((p for p in snapshot.registry.profiles if p.id == payload["id"]), None)
    if profile is None:
        raise HTTPException(status_code=404, detail="模型连接不存在")
    if _MODEL_TEST_BUSY:
        raise HTTPException(status_code=429, detail="连接测试并发已达到限制")
    if not _MODEL_TEST_RATE.allow(user.username):
        raise HTTPException(status_code=429, detail="每位管理员每分钟最多测试 5 次")
    _MODEL_TEST_BUSY = True
    started = time.monotonic()
    category = "missing_key"
    try:
        if profile.api_key:
            category = "invalid_response"
            with anyio.fail_after(15):
                async with outbound.async_client(timeout=15) as client:
                    async with client.stream(
                        "POST", profile.base_url.rstrip("/") + "/chat/completions",
                        headers=_upstream_headers(profile),
                        json={"model": profile.model, "messages": [{"role": "user", "content": "Reply OK"}],
                              "max_tokens": 8, "stream": False},
                    ) as upstream:
                        if upstream.status_code >= 300:
                            category = "upstream_error"
                        else:
                            data = bytearray()
                            async for chunk in upstream.aiter_bytes():
                                if len(data) + len(chunk) > 65536:
                                    category = "response_too_large"
                                    break
                                data.extend(chunk)
                            else:
                                body = json.loads(data)
                                content = body["choices"][0]["message"]["content"]
                                if isinstance(content, str):
                                    category = "ok"
    except (TimeoutError, httpx.TimeoutException):
        category = "timeout"
    except httpx.RequestError:
        category = "network_error"
    except Exception:
        category = "invalid_response"
    finally:
        _MODEL_TEST_BUSY = False
    return {"success": category == "ok", "category": category,
            "elapsed_ms": round((time.monotonic() - started) * 1000), "revision": snapshot.revision}


@app.get("/api/admin/audit-params")
async def get_audit_params(request: Request) -> Dict[str, Any]:
    """裁判参数：超时、重试、输出上限、轨迹输入上限与知识库召回条数。"""

    _model_admin(request)
    return AUDIT_PARAMS.public()


@app.put("/api/admin/audit-params")
async def save_audit_params(request: Request) -> Dict[str, Any]:
    user = _model_admin(request)
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    values = payload.get("params", payload) if isinstance(payload, dict) else payload
    try:
        result = AUDIT_PARAMS.save(values, updated_by=user.username)
    except AuditParamsError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    AUDIT_LOG.log(
        user.username, "settings.audit_params", "audit-params",
        {"params": result["params"]}, tenant_id=user.tenant_id,
    )
    logger.warning("审计参数已更新（操作人 %s）：%s", user.username, result["params"])
    return result


@app.get("/api/admin/kb")
async def kb_status(request: Request) -> Dict[str, Any]:
    _model_admin(request)
    return dict(default_kb().stats())


@app.post("/api/admin/kb/rebuild")
async def kb_rebuild(request: Request) -> Dict[str, Any]:
    user = _model_admin(request)
    kb = default_kb()
    chunks = await anyio.to_thread.run_sync(kb.rebuild)
    AUDIT_LOG.log(
        user.username, "settings.kb_rebuild", "knowledge-base",
        {"chunks": int(chunks)}, tenant_id=user.tenant_id,
    )
    result = dict(kb.stats())
    result["rebuilt"] = int(chunks)
    return result


@app.get("/api/admin/data-stats")
async def data_stats(request: Request) -> Dict[str, Any]:
    """数据管理页使用的实体计数与数据库体积。"""

    _model_admin(request)
    size = 0
    for suffix in ("", "-wal", "-shm"):
        path = Path(DB_PATH + suffix)
        try:
            size += path.stat().st_size
        except OSError:
            continue
    return {
        "db_ok": _db_healthy(),
        "db_path": DB_PATH,
        "db_size_bytes": size,
        "traces": STORE.count_traces(),
        "agents": len(AGENTS.list_agents()),
        "approvals_pending": len(APPROVALS.list(status="proposed", limit=500)),
        "policies": len(POLICY.list()),
        "audit_events": AUDIT_LOG.count(),
        "events": EVENT_STORE.counts(),
    }


@app.post("/v1/tool/check")
async def tool_check(request: Request) -> Dict[str, Any]:
    """SDK / MCP 代理在执行工具前调用：allow / review / deny。"""

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    agent_id = request.state.agent_id
    session_id = body.get("session_id") or request.headers.get("x-safeorbit-session-id")
    trace_id = body.get("trace_id") or request.headers.get("x-safeorbit-trace-id")
    if trace_id:
        _claim([(str(trace_id), agent_id)])
    tool_name = str(body.get("tool_name") or "").strip()
    if not tool_name:
        raise HTTPException(status_code=400, detail="缺少 tool_name")
    arguments = body.get("arguments") if isinstance(body.get("arguments"), dict) else {}
    side_effect = str(body.get("side_effect") or "unknown")
    required_scope = str(body.get("required_scope") or "").strip()
    confirmed = bool(body.get("confirmed_by_human"))
    approval_id = str(body.get("approval_id") or "").strip()
    estimated_cost = body.get("estimated_cost_usd")
    try:
        estimated_cost = float(estimated_cost) if estimated_cost not in (None, "") else None
    except (TypeError, ValueError):
        estimated_cost = None

    policy = POLICY.resolve(agent_id)
    mode = _effective_mode(agent_id, request)
    decision = "allow"
    reasons: List[str] = []
    approval_required = False

    isolation = DISPO.is_session_isolated(session_id, agent_id)
    if isolation:
        decision = "deny"
        reasons.append("会话已被隔离：%s" % (isolation.get("reason") or "策略处置"))
    blocked_tool = DISPO.is_tool_blocked(agent_id, tool_name)
    if blocked_tool:
        decision = "deny"
        reasons.append("工具已被策略阻断：%s" % (blocked_tool.get("reason") or "人工处置"))

    if policy is not None:
        if required_scope:
            agent_scopes = set(getattr(request.state, "agent_scopes", None) or [])
            if required_scope not in agent_scopes:
                decision = _escalate(decision, "deny" if mode == "enforce" else "review")
                reasons.append("缺少工具所需权限：%s" % required_scope)
        if policy.allowed_tools and tool_name not in set(policy.allowed_tools):
            decision = _escalate(decision, "deny" if mode == "enforce" else "review")
            reasons.append("工具 %s 不在白名单" % tool_name)
        if side_effect == "write" and policy.require_confirmation_for_writes:
            # 客户端自报的 confirmed_by_human 仅作提示，不能替代可信审批。
            if confirmed:
                reasons.append("已忽略客户端自报的人工确认，不作为审批依据")
            if approval_id:
                approved, note = APPROVALS.consume(
                    approval_id,
                    agent_id=agent_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    trace_id=str(trace_id) if trace_id else None,
                )
                reasons.append(note)
                if not approved:
                    decision = _escalate(decision, "deny" if mode == "enforce" else "review")
            else:
                request_item = APPROVALS.create(
                    agent_id=agent_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    session_id=str(session_id) if session_id else None,
                    trace_id=str(trace_id) if trace_id else None,
                    reason="写操作需要人工审批",
                    requested_by="runtime",
                )
                approval_id = request_item.approval_id
                approval_required = True
                decision = _escalate(decision, "deny" if mode == "enforce" else "review")
                reasons.append("写操作需要人工审批（审批单 %s）" % approval_id)
        threshold = policy.write_amount_threshold
        if threshold is not None:
            for key, value in arguments.items():
                if str(key).lower() in _AMOUNT_KEYS and isinstance(value, (int, float)) and value > threshold:
                    decision = _escalate(decision, "deny" if mode == "enforce" else "review")
                    reasons.append("写操作金额 %s=%s 超过阈值 %s" % (key, value, threshold))
        if policy.budget_usd is not None and estimated_cost is not None and estimated_cost > policy.budget_usd:
            decision = _escalate(decision, "deny" if mode == "enforce" else "review")
            reasons.append("预估成本 $%.6f 超过预算 $%.6f" % (estimated_cost, policy.budget_usd))
    else:
        reasons.append("未配置策略，按放行处理")

    if decision == "deny" and mode == "enforce" and not approval_required:
        METRICS.inc("blocks")
        DISPO.propose(
            agent_id=agent_id,
            action="block_tool",
            target=tool_name,
            reason="；".join(reasons) or "运行时鉴权拒绝",
            trace_id=str(trace_id) if trace_id else None,
            session_id=str(session_id) if session_id else None,
            severity="high",
            created_mode=mode,
            requested_by="runtime",
            evidence={"arguments": arguments, "side_effect": side_effect},
        )

    return {
        "decision": decision,
        "mode": mode,
        "reasons": reasons,
        "tool_name": tool_name,
        "agent_id": agent_id,
        "session_id": session_id,
        "trace_id": trace_id,
        "approval_id": approval_id or None,
        "approval_required": approval_required,
        "policy_id": policy.policy_id if policy else None,
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, background: BackgroundTasks) -> Any:
    snapshot = request.state.model_snapshot
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    if not isinstance(body, dict) or not body.get("messages"):
        raise HTTPException(status_code=400, detail="缺少 messages")

    profile = None
    if not MOCK_UPSTREAM:
        try:
            profile = snapshot.registry.resolve_gateway(body.get("model"))
        except ValueError:
            raise HTTPException(status_code=400, detail="未知模型别名或模型未开放网关用途")
        if not profile.api_key:
            raise HTTPException(status_code=503, detail="所选模型未配置服务端 Key")
        if body.get("stream") and not profile.supports_stream:
            raise HTTPException(status_code=400, detail="所选模型不支持流式调用")
        body = dict(body, model=profile.model)
    else:
        body = dict(body, model="mock-model")

    started_at = datetime.now().astimezone()
    trace_id = request.headers.get("x-safeorbit-trace-id") or "gw-" + uuid4().hex
    session_id = request.headers.get("x-safeorbit-session-id") or trace_id
    agent_id = request.state.agent_id
    _claim([(trace_id, agent_id)])
    context = _resolve_context(agent_id, request)
    policy = POLICY.resolve(agent_id)
    mode = _effective_mode(agent_id, request)

    guard_findings = content_guard.check_messages(body.get("messages"))
    blocking_guard = [
        item for item in guard_findings if item.severity in ("critical", "high")
    ]
    if mode == "enforce" and blocking_guard:
        now = datetime.now().astimezone()
        guard_span = Span(
            span_id="guard-" + uuid4().hex[:10],
            kind=SpanKind.SYSTEM,
            name="guard.input",
            start_time=now,
            end_time=now,
            status=SpanStatus.BLOCKED,
            input={"message_count": len(body.get("messages") or [])},
            attributes={
                "guard_findings": [item.to_dict() for item in blocking_guard],
            },
        )
        trajectory = Trajectory(
            trace_id=trace_id,
            session_id=session_id,
            agent_id=agent_id,
            tenant_id=request.state.tenant_id,
            started_at=now,
            ended_at=now,
            status=SpanStatus.BLOCKED,
            spans=[guard_span],
            source="gateway",
            received_at=now,
        )
        background.add_task(process_trace, trajectory, context, policy, mode, snapshot)
        return _blocked_response(
            "输入命中内容安全规则：%s" % blocking_guard[0].message, mode
        )

    blocked = _runtime_block_reason(
        agent_id, session_id, mode, tenant_id=request.state.tenant_id
    )
    if blocked:
        return _blocked_response(blocked, mode)

    if body.get("stream"):
        return await _stream_completion(
            request,
            body,
            background,
            agent_id=agent_id,
            session_id=session_id,
            trace_id=trace_id,
            started_at=started_at,
            context=context,
            policy=policy,
            mode=mode,
            profile=profile,
            model_snapshot=snapshot,
        )

    if MOCK_UPSTREAM:
        response_body = mock_completion(body)
        status_code = 200
        status = SpanStatus.OK
        error = None
    else:
        headers = _upstream_headers(profile)
        status_code = 200
        status = SpanStatus.OK
        error = None
        breaker = circuit_mod.circuit(
            "upstream:%s" % profile.id,
            circuit_mod.CircuitConfig(
                failure_threshold=int(os.getenv("SAFEORBIT_UPSTREAM_FAILURE_THRESHOLD", "5")),
                recovery_timeout=float(os.getenv("SAFEORBIT_UPSTREAM_RECOVERY_SECONDS", "30")),
                timeout=float(os.getenv("SAFEORBIT_UPSTREAM_TIMEOUT", "120")) + 5,
            ),
        )
        try:
            upstream = await breaker.call(
                lambda: _post_upstream(profile, headers, body)
            )
            status_code = upstream.status_code
            if upstream.status_code >= 300:
                status = SpanStatus.ERROR
                if upstream.status_code < 400:
                    status_code = 502
                error = "模型上游请求失败"
                response_body = {"error": "模型上游请求失败"}
            else:
                response_body = upstream.json()
        except _UpstreamUnavailable as exc:
            # 5xx/429 已计入熔断，仍按原始状态码如实回传
            upstream = exc.response
            status_code = upstream.status_code
            status = SpanStatus.ERROR
            error = "模型上游请求失败"
            response_body = {"error": "模型上游请求失败"}
        except circuit_mod.CircuitOpenError:
            response_body = {"error": "模型上游暂时不可用（熔断中）"}
            status_code = 503
            status = SpanStatus.ERROR
            error = "模型上游熔断"
        except Exception:
            response_body = {"error": "模型上游调用失败"}
            status_code = 502
            status = SpanStatus.ERROR
            error = "模型上游调用失败"

    ended_at = datetime.now().astimezone()
    trajectory = build_gateway_trajectory(
        body,
        response_body,
        agent_id=agent_id,
        session_id=session_id,
        trace_id=trace_id,
        started_at=started_at,
        ended_at=ended_at,
        status=status,
        error=error,
        profile_id=profile.id if profile else None,
        provider=profile.provider if profile else "mock",
        mock=MOCK_UPSTREAM,
    )
    trajectory.tenant_id = request.state.tenant_id
    background.add_task(process_trace, trajectory, context, policy, mode, snapshot)
    return JSONResponse(content=response_body, status_code=status_code)


def _upstream_headers(profile: ModelProfile) -> Dict[str, str]:
    if not profile.api_key:
        raise HTTPException(status_code=503, detail="所选模型未配置服务端 Key")
    return {"Content-Type": "application/json", "Authorization": "Bearer " + profile.api_key}


class _UpstreamUnavailable(Exception):
    """5xx/429 视为上游不可用，计入熔断；仍保留原始响应以便如实回传状态码。"""

    def __init__(self, response):
        super().__init__("upstream unavailable")
        self.response = response


async def _post_upstream(profile: ModelProfile, headers: Dict[str, str], body: Dict[str, Any]):
    async with outbound.async_client(timeout=float(os.getenv("SAFEORBIT_UPSTREAM_TIMEOUT", "120"))) as client:
        response = await client.post(
            profile.base_url.rstrip("/") + "/chat/completions",
            headers=headers,
            json=body,
        )
    if response.status_code >= 500 or response.status_code == 429:
        raise _UpstreamUnavailable(response)
    return response


def _mock_stream_chunks(response_body: Dict[str, Any]) -> List[str]:
    content = ((response_body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    pieces = [content[index:index + 18] for index in range(0, len(content), 18)] or [""]
    model = response_body.get("model") or "mock-model"
    chunks = [
        json.dumps(
            {
                "id": response_body.get("id"),
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": piece}, "finish_reason": None}],
            },
            ensure_ascii=False,
        )
        for piece in pieces
    ]
    chunks.append(
        json.dumps(
            {
                "id": response_body.get("id"),
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": response_body.get("usage") or {},
            },
            ensure_ascii=False,
        )
    )
    return chunks


async def _stream_completion(
    request: Request,
    body: Dict[str, Any],
    background: BackgroundTasks,
    *,
    agent_id: str,
    session_id: Optional[str],
    trace_id: Optional[str],
    started_at: datetime,
    context: AuditContext,
    policy: Optional[Policy],
    mode: str,
    profile: Optional[ModelProfile] = None,
    model_snapshot: Optional[ModelSnapshot] = None,
) -> Any:
    """流式透传：原样转发 SSE，同时累积内容与 usage 以生成审计轨迹。"""

    async def audit(
        content: str,
        usage: Dict[str, Any],
        model: str,
        status: SpanStatus,
        error: Optional[str],
    ) -> None:
        response_body = {
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop" if status == SpanStatus.OK else "error",
                }
            ],
            "usage": usage,
        }
        trajectory = build_gateway_trajectory(
            body,
            response_body,
            agent_id=agent_id,
            session_id=session_id,
            trace_id=trace_id,
            started_at=started_at,
            ended_at=datetime.now().astimezone(),
            status=status,
            error=error,
            profile_id=profile.id if profile else None,
            provider=profile.provider if profile else "mock",
            mock=MOCK_UPSTREAM,
        )
        trajectory.spans[0].attributes["stream"] = True
        trajectory.spans[0].attributes["audit_complete"] = status == SpanStatus.OK
        trajectory.tenant_id = request.state.tenant_id
        with anyio.CancelScope(shield=True):
            await process_trace(trajectory, context, policy, mode, model_snapshot)

    if MOCK_UPSTREAM:
        response_body = mock_completion(body)
        mock_content = ((response_body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        mock_truncated = len(mock_content.encode("utf-8")) > STREAM_AUDIT_MAX_BYTES
        if mock_truncated:
            response_body["choices"][0]["message"]["content"] = mock_content.encode("utf-8")[:STREAM_AUDIT_MAX_BYTES].decode("utf-8", errors="ignore")
        chunks = _mock_stream_chunks(response_body)

        async def mock_generator():
            complete = False
            try:
                for chunk in chunks:
                    yield "data: " + chunk + "\n\n"
                if mock_truncated:
                    yield 'event: error\ndata: {"error":"audit_buffer_limit","audit_complete":false}\n\n'
                else:
                    yield "data: [DONE]\n\n"
                    complete = True
            finally:
                await audit(
                    ((response_body.get("choices") or [{}])[0].get("message") or {}).get("content") or "",
                    response_body.get("usage") or {},
                    str(response_body.get("model") or body.get("model") or "mock-model"),
                    SpanStatus.OK if complete else SpanStatus.ERROR,
                    None if complete else "流断连，审计不完整",
                )

        return StreamingResponse(
            mock_generator(),
            background=background,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    headers = _upstream_headers(profile)
    client = outbound.async_client(timeout=120)
    try:
        upstream_request = client.build_request(
            "POST", profile.base_url.rstrip("/") + "/chat/completions", headers=headers, json=body
        )
        upstream = await client.send(upstream_request, stream=True)
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            await client.aclose()
            await audit("", {}, str(body.get("model") or "unknown"), SpanStatus.ERROR, "流建立期间断连，审计不完整")
        raise
    except Exception:
        await client.aclose()
        await audit("", {}, str(body.get("model") or "unknown"), SpanStatus.ERROR, "模型上游调用失败")
        return JSONResponse(
            content={"error": "模型上游调用失败"},
            status_code=502,
        )

    if upstream.status_code >= 300:
        with anyio.CancelScope(shield=True):
            await upstream.aclose()
            await client.aclose()
            await audit("", {}, str(body.get("model") or "unknown"), SpanStatus.ERROR, "模型上游请求失败")
        return JSONResponse(content={"error": "模型上游请求失败"}, status_code=upstream.status_code if upstream.status_code >= 400 else 502)

    content_parts: List[str] = []
    usage: Dict[str, Any] = {}
    model = str(body.get("model") or "unknown")
    status = SpanStatus.OK
    error: Optional[str] = None

    async def upstream_generator():
        nonlocal usage, model, status, error
        cached_bytes = 0
        complete = False
        try:
            async for line in upstream.aiter_lines():
                if not line:
                    yield "\n"
                    continue
                if line.startswith("data:"):
                    data = line[5:].strip()
                    cached_bytes += len(line.encode("utf-8"))
                    if cached_bytes > STREAM_AUDIT_MAX_BYTES:
                        status = SpanStatus.ERROR
                        error = "流式审计缓存达到上限，流已终止，审计不完整"
                        yield 'event: error\ndata: {"error":"audit_buffer_limit","audit_complete":false}\n\n'
                        return
                    if data == "[DONE]":
                        complete = True
                    if data and data != "[DONE]": 
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            chunk = None
                        if isinstance(chunk, dict):
                            if chunk.get("usage"):
                                usage = chunk["usage"]
                            if chunk.get("model"):
                                model = str(chunk["model"])
                            for choice in chunk.get("choices") or []:
                                delta = choice.get("delta") or {}
                                piece = delta.get("content")
                                if isinstance(piece, str) and piece:
                                    content_parts.append(piece)
                yield line + "\n"
        except Exception:
            status = SpanStatus.ERROR
            error = "模型上游流异常，审计不完整"
            yield 'event: error\ndata: {"error":"upstream_stream_failed","audit_complete":false}\n\n'
        finally:
            if not complete or upstream.status_code >= 400:
                status = SpanStatus.ERROR
                error = error or "流未完整结束，审计不完整"
            with anyio.CancelScope(shield=True):
                try:
                    await upstream.aclose()
                finally:
                    await client.aclose()
                    await audit("".join(content_parts), usage, model, status, error)

    return StreamingResponse(
        upstream_generator(),
        background=background,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "text/event-stream"),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/v1/traces")
async def otlp_traces(request: Request, background: BackgroundTasks) -> Dict[str, Any]:
    """接收 OTLP/HTTP JSON 或 protobuf。"""

    snapshot = request.state.model_snapshot
    agent_key_id = request.state.agent_id
    content_type = request.headers.get("content-type", "").lower()
    try:
        if "protobuf" in content_type:
            body = await request.body()
            trajectories = parse_otlp_protobuf(body)
            for trajectory in trajectories:
                if trajectory.agent_id not in ("otel-agent", agent_key_id):
                    raise HTTPException(status_code=403, detail="OTLP resource 身份与 Agent Key 不匹配")
                trajectory.agent_id = agent_key_id
        else:
            payload = await request.json()
            trajectories = parse_otlp_json(payload)
    except HTTPException:
        raise
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail="OTLP 轨迹格式无效") from exc
    _claim([(t.trace_id, request.state.agent_id) for t in trajectories])
    for trajectory in trajectories:
        if trajectory.tenant_id and trajectory.tenant_id != request.state.tenant_id:
            raise HTTPException(status_code=403, detail="轨迹租户与 Agent Key 不匹配")
        trajectory.tenant_id = request.state.tenant_id
        context = _resolve_context(trajectory.agent_id, request)
        background.add_task(
            process_trace,
            trajectory,
            context,
            POLICY.resolve(trajectory.agent_id),
            _effective_mode(trajectory.agent_id, request),
            snapshot,
        )
    return {"accepted": len(trajectories), "trace_ids": [t.trace_id for t in trajectories]}


@app.post("/v1/ingest/batch")
async def ingest_batch(request: Request, background: BackgroundTasks) -> Dict[str, Any]:
    """批量投递轨迹：逐条校验与失败隔离；重复投递由事件层幂等吸收。"""

    snapshot = request.state.model_snapshot
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    items = payload.get("traces") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="traces 必须是至少包含一条轨迹的数组")
    if len(items) > BATCH_INGEST_LIMIT:
        raise HTTPException(
            status_code=413,
            detail="单批最多 %d 条轨迹" % BATCH_INGEST_LIMIT,
        )
    accepted: List[str] = []
    rejected: List[Dict[str, Any]] = []
    for index, raw in enumerate(items):
        try:
            if isinstance(raw, dict):
                raw = dict(raw)
                raw.setdefault("source", "sdk")
                raw.setdefault("received_at", datetime.now().astimezone().isoformat())
            trajectory = Trajectory.model_validate(raw)
        except Exception:
            # 不回显校验原文，避免把上游字段值写回响应或日志
            rejected.append({"index": index, "reason": "轨迹校验失败"})
            continue
        if trajectory.tenant_id and trajectory.tenant_id != request.state.tenant_id:
            rejected.append(
                {"index": index, "trace_id": trajectory.trace_id, "reason": "轨迹租户与 Agent Key 不匹配"}
            )
            continue
        trajectory.tenant_id = request.state.tenant_id
        try:
            _claim([(trajectory.trace_id, request.state.agent_id)])
        except ValueError as exc:
            rejected.append(
                {"index": index, "trace_id": trajectory.trace_id, "reason": str(exc)}
            )
            continue
        context = _resolve_context(trajectory.agent_id, request)
        background.add_task(
            process_trace,
            trajectory,
            context,
            POLICY.resolve(trajectory.agent_id),
            _effective_mode(trajectory.agent_id, request),
            snapshot,
        )
        accepted.append(trajectory.trace_id)
    return {
        "accepted": len(accepted),
        "rejected": len(rejected),
        "trace_ids": accepted,
        "errors": rejected,
    }


@app.post("/v1/ingest")
async def ingest_trajectory(request: Request, background: BackgroundTasks) -> Dict[str, Any]:
    """直接投递一条规范轨迹（用于 SDK/适配器）。"""

    snapshot = request.state.model_snapshot
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    if isinstance(payload, dict):
        payload.setdefault("source", "sdk")
        payload.setdefault("received_at", datetime.now().astimezone().isoformat())
    try:
        trajectory = Trajectory.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="轨迹校验失败：%s" % exc)
    if trajectory.tenant_id and trajectory.tenant_id != request.state.tenant_id:
        raise HTTPException(status_code=403, detail="轨迹租户与 Agent Key 不匹配")
    trajectory.tenant_id = request.state.tenant_id
    _claim([(trajectory.trace_id, request.state.agent_id)])
    context = _resolve_context(trajectory.agent_id, request)
    background.add_task(
        process_trace,
        trajectory,
        context,
        POLICY.resolve(trajectory.agent_id),
        _effective_mode(trajectory.agent_id, request),
        snapshot,
    )
    return {"accepted": 1, "trace_id": trajectory.trace_id}
