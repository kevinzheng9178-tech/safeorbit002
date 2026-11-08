"""MCP over HTTP 代理：转发 JSON-RPC，并在 tools/call 前后采集/鉴权。

说明：
- 面向 Streamable HTTP / JSON-RPC POST 形态的 MCP Server；
- GET/SSE 透传；tools/call 的执行前检查与执行后上报与 stdio 代理一致；
- 仍属于进程内代理，不是不可绕过的强制控制。
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import httpx

from .adapters import SafeOrbitClient, ToolBlocked
from .mcp_proxy import guess_side_effect


def _now() -> datetime:
    return datetime.now().astimezone()


def _jsonrpc_error(request_id: Any, code: int, message: str) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        ensure_ascii=False,
    ).encode("utf-8")


def run_http_proxy(
    listen_host: str,
    listen_port: int,
    upstream_url: str,
    *,
    base_url: str = "http://127.0.0.1:8000",
    agent_id: str = "mcp-http-agent",
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    enforce: bool = False,
    approval_id: Optional[str] = None,
) -> int:
    parsed = urlsplit(upstream_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("upstream 必须是 http(s) URL")
    client = SafeOrbitClient(
        base_url, agent_id=agent_id, trace_id=trace_id, session_id=session_id
    )

    class Handler(BaseHTTPRequestHandler):
        server_version = "SafeOrbitMCP/0.3"

        def log_message(self, format, *args):  # noqa: A002 - 保持标准库签名
            sys.stderr.write("SafeOrbit MCP HTTP: " + (format % args) + "\n")

        def _forward_headers(self) -> Dict[str, str]:
            headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
            accept = self.headers.get("Accept")
            if accept:
                headers["Accept"] = accept
            return headers

        def _send_bytes(self, status: int, payload: bytes, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802 - 标准库命名
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            try:
                message = json.loads(body)
            except (TypeError, ValueError):
                message = None

            if not isinstance(message, dict):
                self._send_bytes(400, _jsonrpc_error(None, -32700, "请求体不是合法 JSON-RPC"))
                return

            request_id = message.get("id")
            pending = None
            if message.get("method") == "tools/call":
                params = message.get("params") or {}
                tool_name = str(params.get("name") or "unknown-tool")
                arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
                side_effect = guess_side_effect(tool_name)
                if enforce:
                    try:
                        kwargs = {
                            "side_effect": side_effect,
                            "confirmed_by_human": False,
                        }
                        if approval_id:
                            kwargs["approval_id"] = approval_id
                        decision = client.check_tool(tool_name, arguments, **kwargs)
                        if decision.get("decision") not in ("allow", "review", "deny"):
                            raise ValueError("无效检查结果")
                        if decision.get("decision") == "deny":
                            raise ToolBlocked(
                                "；".join(decision.get("reasons") or []), decision
                            )
                    except ToolBlocked as exc:
                        client.tool_span(
                            tool_name,
                            arguments,
                            side_effect=side_effect,
                            blocked=True,
                            status="blocked",
                            error=str(exc),
                        )
                        self._send_bytes(200, _jsonrpc_error(request_id, -32001, "SafeOrbit 拦截：策略拒绝执行"))
                        return
                    except Exception:
                        self._send_bytes(200, _jsonrpc_error(request_id, -32002, "SafeOrbit 执行前检查失败，拒绝执行"))
                        return
                pending = {
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "side_effect": side_effect,
                    "started_at": _now(),
                }

            try:
                response = httpx.post(
                    upstream_url,
                    content=body,
                    headers=self._forward_headers(),
                    timeout=60,
                    trust_env=False,
                )
            except Exception:
                if pending:
                    client.tool_span(
                        pending["tool_name"],
                        pending["arguments"],
                        side_effect=pending["side_effect"],
                        status="error",
                        error="MCP HTTP 上游调用失败",
                        started_at=pending["started_at"],
                    )
                self._send_bytes(502, _jsonrpc_error(request_id, -32000, "MCP HTTP 上游调用失败"))
                return

            if pending:
                result = None
                error = None
                try:
                    payload = response.json()
                    if isinstance(payload, dict):
                        result = payload.get("result")
                        error = payload.get("error")
                except Exception:
                    payload = None
                tool_error = isinstance(result, dict) and result.get("isError") is True
                client.tool_span(
                    pending["tool_name"],
                    pending["arguments"],
                    side_effect=pending["side_effect"],
                    result=result,
                    status="error" if error or tool_error or response.status_code >= 400 else "ok",
                    error=json.dumps(error, ensure_ascii=False) if error else None,
                    started_at=pending["started_at"],
                )

            self._send_bytes(
                response.status_code,
                response.content,
                response.headers.get("content-type", "application/json"),
            )

        def do_GET(self) -> None:  # noqa: N802 - 标准库命名
            try:
                with httpx.stream(
                    "GET",
                    upstream_url,
                    headers=self._forward_headers(),
                    timeout=60,
                    trust_env=False,
                ) as response:
                    self.send_response(response.status_code)
                    content_type = response.headers.get("content-type", "text/event-stream")
                    self.send_header("Content-Type", content_type)
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    for chunk in response.iter_bytes():
                        self.wfile.write(chunk)
                        self.wfile.flush()
            except Exception:
                self._send_bytes(502, b'{"error":"MCP HTTP upstream SSE unavailable"}')

    server = ThreadingHTTPServer((listen_host, listen_port), Handler)
    sys.stderr.write(
        "SafeOrbit MCP HTTP 代理：%s:%d -> %s（%s）\n"
        % (listen_host, listen_port, upstream_url, "执行前检查" if enforce else "仅采集")
    )
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
