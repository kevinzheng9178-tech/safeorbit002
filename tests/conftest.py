"""全局测试夹具：隔离跨用例的熔断器状态。"""

from __future__ import annotations

import pytest

from safeorbit import circuit


@pytest.fixture(autouse=True)
def reset_circuit_breakers():
    """熔断器是进程级共享状态，用例之间必须重置，否则失败计数会互相污染。"""

    circuit.REGISTRY.reset()
    yield
    circuit.REGISTRY.reset()
