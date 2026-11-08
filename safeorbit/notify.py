"""通知分发：SSE 之外的可选 Webhook 通道。

默认不发送；设置 SAFEORBIT_WEBHOOK_URL 后才启用。
Webhook 地址必须满足出站安全策略（公网 HTTPS 443），避免把审计数据发到内网或明文端点。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

from . import circuit as circuit_mod
from . import outbound

logger = logging.getLogger("safeorbit.notify")


async def dispatch(event: Dict[str, Any]) -> bool:
    url = os.getenv("SAFEORBIT_WEBHOOK_URL", "").strip()
    if not url:
        return False
    token = os.getenv("SAFEORBIT_WEBHOOK_TOKEN", "").strip()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token

    async def _send():
        async with outbound.async_client(timeout=10) as client:
            return await client.post(url, json=event, headers=headers)

    try:
        breaker = circuit_mod.circuit("notify")
        response = await breaker.call(_send)
        if response.status_code >= 300:
            logger.warning("通知 Webhook 返回 %s", response.status_code)
            return False
        return True
    except circuit_mod.CircuitOpenError:
        logger.warning("通知 Webhook 熔断器已打开，跳过发送")
        return False
    except Exception as exc:  # 通知失败不能影响审计主流程
        logger.warning("通知 Webhook 发送失败：%s", type(exc).__name__)
        return False
