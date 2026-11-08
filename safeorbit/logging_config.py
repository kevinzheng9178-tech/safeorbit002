"""结构化日志与请求追踪。

提供：
- 请求 ID：每个 HTTP 请求生成/透传 request_id，写入日志与响应头；
- JSON 结构化日志（生产）或人类可读日志（开发）；
- 访问日志中间件，记录方法、路径、状态码、耗时与请求 ID。
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, Optional

_REQUEST_ID_HEADER = "x-request-id"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: Optional[str] = None) -> None:
    """按环境变量 SAFEORBIT_LOG_FORMAT 配置根日志（json 或 text）。"""

    level = (level or os.getenv("SAFEORBIT_LOG_LEVEL", "INFO")).upper()
    fmt = os.getenv("SAFEORBIT_LOG_FORMAT", "text").lower()
    root = logging.getLogger()
    root.setLevel(level)
    # 清理已有 handler，避免重复输出
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    root.addHandler(handler)


def request_id_from_headers(headers: Dict[str, str]) -> str:
    incoming = headers.get(_REQUEST_ID_HEADER) or headers.get("x-request-id")
    return incoming if incoming else uuid.uuid4().hex[:16]


class AccessLogMiddleware:
    """记录每个 HTTP 请求的访问日志与耗时。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = time.monotonic()
        status_holder = {"status": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message.get("status", 0)
                headers = message.get("headers", [])
                headers = [(k, v) for k, v in headers if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", scope.get("state", {}).get("request_id", "").encode("latin1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.monotonic() - started) * 1000
            logging.getLogger("safeorbit.access").info(
                "%s %s -> %d in %.1fms",
                scope.get("method", "?"),
                scope.get("path", "?"),
                status_holder["status"],
                elapsed_ms,
            )
