"""单实例数据面与管理面边界；不提供分布式配额保证。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict, deque
from typing import Any
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

logger = logging.getLogger("safeorbit.security")


SCOPES = {
    "/v1/models": "models:read",
    "/v1/chat/completions": "gateway:invoke",
    "/v1/tool/check": "tools:check",
    "/v1/traces": "traces:write",
    "/v1/ingest": "traces:write",
    "/v1/ingest/batch": "traces:write",
}
OVERRIDE_HEADERS = {
    "x-safeorbit-mode", "x-safeorbit-budget", "x-safeorbit-write-threshold",
    "x-safeorbit-loop-threshold", "x-safeorbit-allow-tools",
}


class RateLimiter:
    def __init__(self, limit: int, window: float = 60, max_identities: int = 10000):
        self.limit = limit
        self.window = window
        self.max_identities = max_identities
        self.entries = OrderedDict()

    def allow(self, identity: str) -> bool:
        now = time.monotonic()
        events = self.entries.pop(identity, deque())
        while events and events[0] <= now - self.window:
            events.popleft()
        self.entries[identity] = events
        if len(self.entries) > self.max_identities:
            self.entries.popitem(last=False)
        if len(events) >= self.limit:
            return False
        events.append(now)
        return True


class BoundaryError(Exception):
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail


def bind_identity(payload: dict, agent_id: str) -> None:
    for key in ("agent_id", "safeorbit.agent_id", "service.name"):
        if key in payload and payload[key] not in (None, "", agent_id):
            raise BoundaryError(403, "请求身份与 Agent Key 不匹配")
    payload["agent_id"] = agent_id


def prepare_payload(path: str, payload: Any, agent_id: str, max_spans: int) -> None:
    if not isinstance(payload, dict):
        raise BoundaryError(400, "请求体必须是 JSON 对象")
    bind_identity(payload, agent_id)
    count = len(payload.get("spans") or [])
    if path == "/v1/traces":
        count = 0
        for resource in payload.get("resourceSpans", []) or []:
            attrs = resource.setdefault("resource", {}).setdefault("attributes", [])
            for attr in attrs:
                if attr.get("key") in ("service.name", "safeorbit.agent_id"):
                    if (attr.get("value") or {}).get("stringValue") != agent_id:
                        raise BoundaryError(403, "OTLP resource 身份与 Agent Key 不匹配")
            for key in ("service.name", "safeorbit.agent_id"):
                if not any(attr.get("key") == key for attr in attrs):
                    attrs.append({"key": key, "value": {"stringValue": agent_id}})
            for scope in resource.get("scopeSpans", []) or []:
                count += len(scope.get("spans", []) or [])
    if count > max_spans:
        raise BoundaryError(413, "批次 span 数量超过限制")


def check_csrf(scope: dict, headers: dict) -> None:
    if headers.get("x-safeorbit-csrf") != "1":
        raise BoundaryError(403, "管理写请求需要 X-SafeOrbit-CSRF: 1")
    if headers.get("sec-fetch-site", "none") not in ("same-origin", "none"):
        raise BoundaryError(403, "拒绝非同源管理写请求")
    origin = headers.get("origin")
    if origin:
        host = headers.get("host", "").strip()
        if not host:
            # HTTP/1.1 必须带 Host；缺失时不能把空 netloc 当作可比较的期望来源
            raise BoundaryError(403, "缺少 Host 头，拒绝管理写请求")
        parsed = urlsplit(origin)
        expected = urlsplit(scope.get("scheme", "http") + "://" + host)
        if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc) or parsed.path or parsed.query or parsed.fragment:
            raise BoundaryError(403, "拒绝非同源 Origin")


# 文件上传走 multipart，不能按 JSON 校验，且需要独立体积上限。
UPLOAD_PATHS = frozenset({"/api/codeaudit/upload"})


class SecurityMiddleware:
    def __init__(self, app, server, max_bytes: int, max_spans: int,
                 requests_per_minute: int, login_per_minute: int, upstream_concurrency: int,
                 upload_max_bytes: int = 0):
        self.app = app
        self.server = server
        self.max_bytes = max_bytes
        self.upload_max_bytes = upload_max_bytes or max_bytes
        self.max_spans = max_spans
        self.agent_rate = RateLimiter(requests_per_minute)
        self.login_rate = RateLimiter(login_per_minute)
        self.upstream = asyncio.Semaphore(upstream_concurrency)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        acquired = False
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope["headers"]}
        path = scope["path"]
        try:
            if path.startswith("/api/") and scope["method"] not in ("GET", "HEAD", "OPTIONS"):
                check_csrf(scope, headers)
            if path in ("/api/auth/login", "/api/auth/sso"):
                client = scope.get("client") or ("unknown", 0)
                if not self.login_rate.allow(str(client[0])):
                    raise BoundaryError(429, "登录请求过于频繁")
            agent = None
            if path in SCOPES and scope["method"] != "OPTIONS":
                authorization = headers.get("authorization", "").split()
                if len(authorization) != 2 or authorization[0].lower() != "bearer":
                    raise BoundaryError(401, "需要 Agent Bearer Key")
                try:
                    identity = self.server.AGENTS.authenticate(authorization[1], SCOPES[path])
                except self.server.AgentAuthError as exc:
                    raise BoundaryError(exc.status_code, str(exc))
                agent = identity["agent_id"]
                scope.setdefault("state", {})["agent_id"] = agent
                tenant_lookup = getattr(self.server.AGENTS, "tenant_of", None)
                scope.setdefault("state", {})["tenant_id"] = (
                    tenant_lookup(agent) if tenant_lookup else "default"
                )
                scope_lookup = getattr(self.server.AGENTS, "scopes_for_key", None)
                scope.setdefault("state", {})["agent_scopes"] = (
                    scope_lookup(identity.get("key_id", "")) if scope_lookup else []
                )
                if headers.get("x-safeorbit-agent-id") not in (None, "", agent):
                    raise BoundaryError(403, "请求头身份与 Agent Key 不匹配")
                if OVERRIDE_HEADERS.intersection(headers):
                    raise BoundaryError(400, "策略和模式仅由服务端配置，禁止覆盖请求头")
                if not self.agent_rate.allow(agent):
                    raise BoundaryError(429, "Agent 请求频率超过限制")
            chunks = []
            size = 0
            limit = self.upload_max_bytes if path in UPLOAD_PATHS else self.max_bytes
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > limit:
                    raise BoundaryError(413, "请求实际字节超过限制")
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            body = b"".join(chunks)
            content_type = headers.get("content-type", "").lower()
            protobuf_otlp = path == "/v1/traces" and "protobuf" in content_type
            if (path.startswith("/api/") and scope["method"] in ("POST", "PUT", "PATCH")
                    and body and path not in UPLOAD_PATHS):
                try:
                    management_payload = json.loads(body)
                except (ValueError, TypeError):
                    raise BoundaryError(400, "请求体必须是 JSON 对象")
                if not isinstance(management_payload, dict):
                    raise BoundaryError(400, "请求体必须是 JSON 对象")
            if agent and scope["method"] == "POST" and not protobuf_otlp:
                try:
                    payload = json.loads(body)
                    prepare_payload(path, payload, agent, self.max_spans)
                except BoundaryError:
                    raise
                except (ValueError, TypeError, AttributeError):
                    raise BoundaryError(400, "请求 JSON 格式无效")
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if agent and path in ("/v1/models", "/v1/chat/completions"):
                if self.upstream.locked():
                    raise BoundaryError(429, "上游并发已达到限制")
                await self.upstream.acquire()
                acquired = True
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            await self.app(scope, replay, send)
        except BoundaryError as exc:
            logger.log(
                logging.INFO if exc.status == 401 else logging.WARNING,
                "边界拦截 %s %s -> %d %s",
                scope.get("method", "?"), path, exc.status, exc.detail,
            )
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status,
                                    headers={"Retry-After": "60"} if exc.status == 429 else None)
            await response(scope, receive, send)
        finally:
            if acquired:
                self.upstream.release()
