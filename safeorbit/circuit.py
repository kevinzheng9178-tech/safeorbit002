"""熔断器与重试：为出站依赖（裁判、通知、模型上游）提供统一故障隔离。"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, TypeVar

logger = logging.getLogger("safeorbit.circuit")

T = TypeVar("T")


class CircuitOpenError(Exception):
    """熔断器打开时拒绝执行。"""


@dataclass
class CircuitConfig:
    failure_threshold: int = 5      # 连续失败多少次打开熔断
    recovery_timeout: float = 30.0  # 打开后多久进入半开
    success_threshold: int = 2      # 半开时需连续成功多少次才闭合
    timeout: float = 30.0           # 单次调用超时


class CircuitBreaker:
    """线程安全的简单熔断器：closed -> open -> half_open -> closed。"""

    def __init__(self, name: str, config: Optional[CircuitConfig] = None):
        self.name = name
        self.config = config or CircuitConfig()
        self._state = "closed"
        self._failures = 0
        self._half_open_successes = 0
        self._opened_at = 0.0
        # 临界区没有 await，用线程锁避免 Python 3.9 下跨事件循环绑定问题。
        self._lock = threading.Lock()

    def _snapshot(self) -> dict:
        return {
            "name": self.name,
            "state": self._state,
            "failures": self._failures,
        }

    async def call(
        self,
        fn: Callable[[], Awaitable[T]],
        *,
        on_open: Optional[Callable[[str], None]] = None,
    ) -> T:
        """执行一次受熔断器保护的调用。打开时直接抛 CircuitOpenError。"""

        with self._lock:
            if self._state == "open":
                if time.monotonic() - self._opened_at < self.config.recovery_timeout:
                    raise CircuitOpenError("熔断器 %s 已打开" % self.name)
                self._state = "half_open"
                self._half_open_successes = 0

        try:
            result = await asyncio.wait_for(fn(), timeout=self.config.timeout)
        except asyncio.TimeoutError:
            await self._on_failure()
            raise
        except CircuitOpenError:
            raise
        except Exception:
            await self._on_failure()
            raise

        await self._on_success()
        return result

    async def _on_failure(self) -> None:
        with self._lock:
            if self._state == "half_open":
                self._open()
                return
            self._failures += 1
            if self._failures >= self.config.failure_threshold:
                self._open()

    async def _on_success(self) -> None:
        with self._lock:
            if self._state == "half_open":
                self._half_open_successes += 1
                if self._half_open_successes >= self.config.success_threshold:
                    self._state = "closed"
                    self._failures = 0
            elif self._state == "closed":
                self._failures = 0

    def _open(self) -> None:
        self._state = "open"
        self._opened_at = time.monotonic()
        self._half_open_successes = 0
        logger.warning("熔断器 %s 打开（连续失败 %d 次）", self.name, self.config.failure_threshold)


class CircuitRegistry:
    """按名称复用熔断器实例，避免每次调用重建状态。"""

    def __init__(self):
        self._breakers: dict = {}

    def get(self, name: str, config: Optional[CircuitConfig] = None) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name, config)
        return self._breakers[name]

    def status(self) -> dict:
        return {name: breaker._snapshot() for name, breaker in self._breakers.items()}

    def reset(self) -> None:
        """清空所有熔断状态（测试与运维手动恢复用）。"""

        self._breakers.clear()


REGISTRY = CircuitRegistry()


def circuit(name: str, config: Optional[CircuitConfig] = None) -> CircuitBreaker:
    return REGISTRY.get(name, config)
