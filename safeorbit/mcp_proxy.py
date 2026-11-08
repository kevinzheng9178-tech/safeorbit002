"""P2 MCP stdio 代理：包裹任意 MCP Server，捕获 tools/call 轨迹。

用法：
    py -m safeorbit mcp-proxy --url http://127.0.0.1:8000 -- npx -y @xxx/server

默认 CLI/SDK 为采集模式：tools/call 返回后上报 /v1/ingest，不做强制控制。
显式 --enforce 启用执行前检查：检查失败、认证失败、超时均不转发下游工具。
策略由服务端决定；进程内代理不是不可绕过的工具控制。凭据读取 SAFEORBIT_AGENT_KEY。
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from .adapters import SafeOrbitClient, ToolBlocked
from .models import Span, SpanKind, SpanStatus, ToolAttrs

WRITE_HINTS = (
    "refund", "create", "delete", "update", "write", "send", "transfer", "pay", "remove", "insert",
)


def _now() -> datetime:
    return datetime.now().astimezone()


def guess_side_effect(tool_name: str) -> str:
    lowered = (tool_name or "").lower()
    return "write" if any(hint in lowered for hint in WRITE_HINTS) else "unknown"


class McpProxy:
    """stdio 双向代理。核心逻辑与进程管理分离，方便单测。"""

    def __init__(
        self,
        server_cmd: List[str],
        *,
        base_url: str = "http://127.0.0.1:8000",
        agent_id: str = "mcp-agent",
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        enforce: bool = False,
        approval_id: Optional[str] = None,
        client: Optional[SafeOrbitClient] = None,
        out=None,
        err=None,
        api_key: Optional[str] = None,
    ):
        self.server_cmd = server_cmd
        self.client = client or SafeOrbitClient(
            base_url, agent_id=agent_id, trace_id=trace_id, session_id=session_id, api_key=api_key
        )
        self.enforce = enforce
        self.approval_id = approval_id
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        self._write_lock = threading.Lock()
        self._pending: Dict[Any, Dict[str, Any]] = {}
        self._process: Optional[Any] = None

    # ---------- 消息处理 ----------
    def handle_client_message(self, message: Dict[str, Any]) -> bool:
        """返回 True 表示应转发给 MCP Server。"""

        if message.get("method") != "tools/call":
            return True
        params = message.get("params") or {}
        tool_name = str(params.get("name") or "unknown-tool")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        side_effect = guess_side_effect(tool_name)

        if self.enforce:
            try:
                check_kwargs = {
                    "side_effect": side_effect,
                    "confirmed_by_human": False,
                }
                if self.approval_id:
                    check_kwargs["approval_id"] = self.approval_id
                decision = self.client.check_tool(tool_name, arguments, **check_kwargs)
                if not isinstance(decision, dict) or decision.get("decision") not in ("allow", "review", "deny"):
                    raise ValueError("无效检查结果")
                if decision.get("decision") == "deny":
                    raise ToolBlocked(
                        "工具 %s 被 SafeOrbit 拦截：%s"
                        % (tool_name, "；".join(decision.get("reasons") or [])),
                        decision,
                    )
            except ToolBlocked:
                self._record_tool(
                    tool_name, arguments, side_effect=side_effect, status=SpanStatus.BLOCKED,
                    error="SafeOrbit 策略拒绝执行",
                )
                self._reply(
                    {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": {
                            "code": -32001,
                            "message": "SafeOrbit 拦截：策略拒绝执行",
                        },
                    }
                )
                return False
            except Exception:
                self.err.write("SafeOrbit 执行前检查失败（含认证失败/超时），已拒绝下游工具执行。\n")
                self.err.flush()
                self._reply(
                    {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": {
                            "code": -32002,
                            "message": "SafeOrbit 执行前检查失败，拒绝执行",
                        },
                    }
                )
                return False

        self._pending[message.get("id")] = {
            "tool_name": tool_name,
            "arguments": arguments,
            "side_effect": side_effect,
            "started_at": _now(),
        }
        return True

    def handle_server_message(self, message: Dict[str, Any]) -> None:
        message_id = message.get("id")
        meta = self._pending.pop(message_id, None)
        if meta is None:
            return
        error = message.get("error")
        result = message.get("result")
        tool_error = isinstance(result, dict) and result.get("isError") is True
        self._record_tool(
            meta["tool_name"],
            meta["arguments"],
            side_effect=meta["side_effect"],
            result=result,
            status=SpanStatus.ERROR if error or tool_error else SpanStatus.OK,
            error=json.dumps(error, ensure_ascii=False) if error else ("MCP 工具返回 isError" if tool_error else None),
            started_at=meta["started_at"],
        )

    def _record_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        side_effect: str,
        result: Any = None,
        status: SpanStatus = SpanStatus.OK,
        error: Optional[str] = None,
        started_at: Optional[datetime] = None,
    ) -> None:
        span = Span(
            span_id="mcp-" + __import__("uuid").uuid4().hex[:10],
            kind=SpanKind.TOOL,
            name=tool_name,
            start_time=started_at or _now(),
            end_time=_now(),
            status=status,
            error=error,
            tool=ToolAttrs(
                tool_name=tool_name,
                arguments=arguments or {},
                result=result,
                side_effect=side_effect,  # type: ignore[arg-type]
                blocked=status == SpanStatus.BLOCKED,
            ),
            attributes={"adapter": "mcp", "protocol": "stdio"},
        )
        try:
            self.client.emit(span)
        except Exception:
            self.err.write("SafeOrbit 工具轨迹上报失败；本次调用未确认接收，请检查凭据、权限或服务状态。\n")
            self.err.flush()

    def _reply(self, payload: Dict[str, Any]) -> None:
        with self._write_lock:
            self.out.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.out.flush()

    # ---------- 进程泵 ----------
    def _pump_client(self) -> None:
        assert self._process is not None
        stdin = self._process.stdin
        for line in sys.stdin:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                message = json.loads(stripped)
            except json.JSONDecodeError:
                stdin.write(line)
                stdin.flush()
                continue
            forward = self.handle_client_message(message)
            if forward:
                stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                stdin.flush()
        try:
            stdin.close()
        except Exception:
            pass

    def _pump_server(self) -> None:
        assert self._process is not None
        for line in self._process.stdout:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                message = json.loads(stripped)
            except json.JSONDecodeError:
                message = None
            if isinstance(message, dict):
                self.handle_server_message(message)
            with self._write_lock:
                self.out.write(line)
                self.out.flush()

    def run(self) -> int:
        import subprocess

        self.err.write("SafeOrbit MCP 模式：%s。\n" % (
            "执行前检查；检查失败拒绝执行下游工具" if self.enforce else "仅采集；执行后上报，不提供强制控制"
        ))
        self.err.flush()
        self._process = subprocess.Popen(
            self.server_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        server_thread = threading.Thread(target=self._pump_server, daemon=True)
        server_thread.start()
        try:
            self._pump_client()
        except KeyboardInterrupt:
            pass
        finally:
            try:
                if self._process.stdin and not self._process.stdin.closed:
                    self._process.stdin.close()
            except Exception:
                pass
            try:
                self._process.wait(timeout=5)
            except Exception:
                self._process.terminate()
            server_thread.join(timeout=2)
        return 0


def run_proxy(
    server_cmd: List[str],
    *,
    base_url: str = "http://127.0.0.1:8000",
    agent_id: str = "mcp-agent",
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    enforce: bool = False,
    approval_id: Optional[str] = None,
) -> int:
    if not server_cmd:
        raise ValueError("缺少 MCP Server 启动命令，例如：-- npx -y @modelcontextprotocol/server-filesystem .")
    return McpProxy(
        server_cmd,
        base_url=base_url,
        agent_id=agent_id,
        trace_id=trace_id,
        session_id=session_id,
        enforce=enforce,
        approval_id=approval_id,
    ).run()
