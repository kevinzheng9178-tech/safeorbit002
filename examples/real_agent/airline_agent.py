"""真实开源 Agent 演示：OpenAI Agents SDK + DeepSeek + SafeOrbit。

Agent 源码来自 GitHub: openai/openai-agents-python（MIT），examples/customer_service。
本文件做了三处改造：
1. 模型换成 DeepSeek（OpenAI 兼容接口），真实推理、真实工具调用；
2. update_seat 改为真实写 SQLite 座位表，不再是只改内存；
3. 每次工具调用前先问 SafeOrbit /v1/tool/check，结束后把轨迹上报 /v1/ingest。

运行前设置：
    $env:DEEPSEEK_API_KEY="sk-..."
    $env:SAFEORBIT_AGENT_KEY="so_agent_..."
    $env:SAFEORBIT_AGENT_ID="airline-agent"

用法：
    examples/real_agent/.venv/Scripts/python.exe examples/real_agent/airline_agent.py "把确认号 ABC123 的座位改成 12A"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from agents import (
    Agent,
    ItemHelpers,
    MessageOutputItem,
    RunContextWrapper,
    Runner,
    ToolCallItem,
    ToolCallOutputItem,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_disabled,
)
from agents.decorators import tool

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "airline.db"


def _load_env() -> None:
    """读取同目录 .env（已 gitignore），方便本地放 Key。"""

    path = BASE_DIR / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()

SAFEORBIT_URL = os.getenv("SAFEORBIT_URL", "http://127.0.0.1:8000").rstrip("/")
AGENT_KEY = os.getenv("SAFEORBIT_AGENT_KEY", "")
AGENT_ID = os.getenv("SAFEORBIT_AGENT_ID", "airline-agent")
APPROVAL_ID = os.getenv("SAFEORBIT_APPROVAL_ID", "")

MODEL = os.getenv("AGENT_MODEL", "deepseek-flash")
BASE_URL = os.getenv("AGENT_BASE_URL", "https://api.deepseek.com/v1")
API_KEY = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY", "")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE IF NOT EXISTS bookings ("
        "confirmation_number TEXT PRIMARY KEY, passenger TEXT, seat TEXT, updated_at TEXT)"
    )
    return connection


def seed() -> None:
    with _connect() as connection:
        connection.executemany(
            "INSERT OR IGNORE INTO bookings VALUES (?, ?, ?, ?)",
            [
                ("ABC123", "张三", "8C", _now()),
                ("XYZ789", "李四", "3A", _now()),
                ("QWE456", "王五", "22F", _now()),
            ],
        )


class AirlineContext(BaseModel):
    confirmation_number: Optional[str] = None
    seat_number: Optional[str] = None
    trace_id: str = ""
    session_id: str = ""
    blocked: List[str] = Field(default_factory=list)
    spans: List[Dict[str, Any]] = Field(default_factory=list)


class SafeOrbitBlocked(RuntimeError):
    pass


async def check_tool(tool_name: str, arguments: Dict[str, Any], side_effect: str = "write") -> Dict[str, Any]:
    """执行前向 SafeOrbit 申请授权；服务不可用时按拒绝处理（fail-closed）。"""

    if not AGENT_KEY:
        return {"decision": "allow", "reasons": ["未配置 SAFEORBIT_AGENT_KEY，跳过运行时检查"], "approval_id": None}
    headers = {
        "Authorization": "Bearer %s" % AGENT_KEY,
        "X-SafeOrbit-Agent-Id": AGENT_ID,
        "Content-Type": "application/json",
    }
    payload = {
        "tool_name": tool_name,
        "arguments": arguments,
        "side_effect": side_effect,
        "trace_id": CURRENT.get("trace_id"),
        "session_id": CURRENT.get("session_id"),
        "approval_id": APPROVAL_ID or None,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(SAFEORBIT_URL + "/v1/tool/check", json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
    except Exception as exc:  # 审计不可用时拒绝写操作，避免绕过
        return {
            "decision": "deny",
            "reasons": ["SafeOrbit 不可用，写操作按拒绝处理：%s" % type(exc).__name__],
            "approval_id": None,
        }


CURRENT: Dict[str, str] = {}


def _tool_span(
    span_id: str, name: str, arguments: Dict[str, Any], result: Any, status: str, blocked: bool = False
) -> Dict[str, Any]:
    return {
        "span_id": span_id,
        "parent_span_id": "root",
        "kind": "tool",
        "name": name,
        "start_time": _now(),
        "end_time": _now(),
        "status": status,
        "tool": {
            "tool_name": name,
            "arguments": arguments,
            "result": result,
            "side_effect": "write",
            "confirmed_by_human": bool(APPROVAL_ID),
            "blocked": blocked,
        },
    }


@tool
async def lookup_booking(confirmation_number: str) -> str:
    """按确认号查询订单和当前座位。"""

    with _connect() as connection:
        row = connection.execute(
            "SELECT confirmation_number, passenger, seat FROM bookings WHERE confirmation_number = ?",
            (confirmation_number.strip().upper(),),
        ).fetchone()
    if row is None:
        return "没有找到确认号 %s 的订单。" % confirmation_number
    return json.dumps(dict(row), ensure_ascii=False)


@tool
async def update_seat(
    context: RunContextWrapper[AirlineContext],
    confirmation_number: str,
    new_seat: str,
) -> str:
    """修改指定确认号的座位；这是真实写操作，会先经过 SafeOrbit 审计。"""

    arguments = {"confirmation_number": confirmation_number, "new_seat": new_seat}
    span_id = "tool-" + uuid.uuid4().hex[:8]
    if "update_seat" in context.context.blocked:
        return "该操作已被拦截，正在等待人工审批，请勿重复调用。"
    verdict = await check_tool("update_seat", arguments, "write")
    decision = str(verdict.get("decision") or "deny")
    reasons = "；".join(verdict.get("reasons") or [])
    if decision != "allow":
        context.context.blocked.append("update_seat")
        message = "操作被 SafeOrbit 拦截（%s）：%s" % (decision, reasons or "策略拒绝")
        if verdict.get("approval_id"):
            message += (
                "。审批单：%s；在控制台批准后，用同一 trace 重试："
                "设置 SAFEORBIT_TRACE_ID=%s 和 SAFEORBIT_APPROVAL_ID=%s。"
                % (verdict["approval_id"], context.context.trace_id, verdict["approval_id"])
            )
        context.context.spans.append(_tool_span(span_id, "update_seat", arguments, message, "error", blocked=True))
        return message

    with _connect() as connection:
        row = connection.execute(
            "SELECT passenger, seat FROM bookings WHERE confirmation_number = ?",
            (confirmation_number.strip().upper(),),
        ).fetchone()
        if row is None:
            message = "没有找到确认号 %s 的订单。" % confirmation_number
            context.context.spans.append(_tool_span(span_id, "update_seat", arguments, message, "error"))
            return message
        connection.execute(
            "UPDATE bookings SET seat = ?, updated_at = ? WHERE confirmation_number = ?",
            (new_seat.strip().upper(), _now(), confirmation_number.strip().upper()),
        )

    context.context.confirmation_number = confirmation_number
    context.context.seat_number = new_seat
    message = "已把确认号 %s 的座位从 %s 改为 %s。" % (
        confirmation_number.strip().upper(),
        row["seat"],
        new_seat.strip().upper(),
    )
    context.context.spans.append(_tool_span(span_id, "update_seat", arguments, message, "ok"))
    return message


def build_agents() -> Agent[AirlineContext]:
    seat_agent = Agent[AirlineContext](
        name="Seat Agent",
        model=MODEL,
        handoff_description="处理座位查询与改签。",
        instructions=(
            "你是航空公司座位客服。先查订单，确认用户要改的确认号和目标座位，"
            "再调用 update_seat。工具返回被拦截时，如实告知用户原因，不要重复尝试绕过。"
        ),
        tools=[lookup_booking, update_seat],
    )
    triage = Agent[AirlineContext](
        name="Triage Agent",
        model=MODEL,
        handoff_description="分流用户请求。",
        instructions=(
            "你是分流客服。涉及座位查询或修改时，把对话交给 Seat Agent；"
            "其他问题礼貌说明暂不支持。"
        ),
        handoffs=[seat_agent],
    )
    return triage


async def upload_trace(context: AirlineContext, user_message: str, answer: str) -> None:
    if not AGENT_KEY:
        return
    spans = [
        {
            "span_id": "root",
            "kind": "agent",
            "name": "airline_customer_service",
            "start_time": _now(),
            "end_time": _now(),
            "status": "ok",
        },
        {
            "span_id": "llm-1",
            "parent_span_id": "root",
            "kind": "llm",
            "name": "plan",
            "start_time": _now(),
            "end_time": _now(),
            "status": "ok",
            "llm": {
                "provider": "deepseek",
                "model": MODEL,
                "messages": [{"role": "user", "content": user_message}],
                "completion": answer,
                "usage": {},
                "temperature": 0.2,
                "finish_reason": "stop",
            },
        },
    ]
    for span in context.spans:
        span["parent_span_id"] = "root"
        spans.append(span)
    payload = {
        "schema_version": "1.0",
        "trace_id": context.trace_id,
        "session_id": context.session_id,
        "agent_id": AGENT_ID,
        "agent_version": "airline-1.0",
        "framework": "openai-agents",
        "started_at": _now(),
        "ended_at": _now(),
        "status": "ok",
        "source": "sdk",
        "metadata": {"scene": "真实 Agent：座位修改"},
        "spans": spans,
    }
    headers = {"Authorization": "Bearer %s" % AGENT_KEY, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(SAFEORBIT_URL + "/v1/ingest", json=payload, headers=headers)
            data = response.json()
        print("\n[SafeOrbit] 轨迹已上报：%s" % data.get("trace_id", context.trace_id))
    except Exception as exc:
        print("\n[SafeOrbit] 轨迹上报失败：%s" % exc)


async def run_once(message: str) -> str:
    context = AirlineContext(
        trace_id=os.getenv("SAFEORBIT_TRACE_ID") or "live-airline-" + uuid.uuid4().hex[:8],
        session_id=os.getenv("SAFEORBIT_SESSION_ID") or "sess-" + uuid.uuid4().hex[:6],
    )
    CURRENT.update({"trace_id": context.trace_id, "session_id": context.session_id})
    result = await Runner.run(build_agents(), [{"role": "user", "content": message}], context=context)
    for item in result.new_items:
        if isinstance(item, ToolCallItem):
            print("[工具] 模型决定调用工具")
        elif isinstance(item, ToolCallOutputItem):
            print("[工具结果] %s" % item.output)
    messages = [
        ItemHelpers.text_message_output(item)
        for item in result.new_items
        if isinstance(item, MessageOutputItem)
    ]
    answer = messages[-1] if messages else ""
    await upload_trace(context, message, answer)
    return answer


async def main() -> int:
    parser = argparse.ArgumentParser(description="OpenAI Agents SDK + DeepSeek + SafeOrbit 真实演示")
    parser.add_argument("message", nargs="*", help="要发给 Agent 的话；省略则进入交互模式")
    parser.add_argument("--seed", action="store_true", help="初始化示例订单")
    args = parser.parse_args()

    if args.seed:
        seed()
        print("已初始化订单：ABC123 / XYZ789 / QWE456")

    if not API_KEY:
        print("缺少 DEEPSEEK_API_KEY，无法调用真实模型。")
        return 2
    set_default_openai_client(AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY))
    set_default_openai_api("chat_completions")
    set_tracing_disabled(True)

    message = " ".join(args.message).strip()
    if message:
        print(await run_once(message))
        return 0

    print("输入问题，空行退出。示例：把 ABC123 的座位改成 12A")
    while True:
        try:
            text = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            break
        print("客服：%s" % await run_once(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
