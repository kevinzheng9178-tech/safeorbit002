"""模型价格表与成本估算。

成本在归一化阶段一次性算完，写进 span.llm.cost_usd；
报告阶段只做汇总，避免不同环节各有各的口径。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import yaml
from pydantic import BaseModel, Field

from .models import TokenUsage

DEFAULT_PRICING_PATH = Path(__file__).with_name("pricing.yaml")


class ModelPrice(BaseModel):
    input_per_1m: float
    cached_input_per_1m: Optional[float] = None
    output_per_1m: float
    reasoning_per_1m: Optional[float] = None


class PricingTable(BaseModel):
    currency: str = "USD"
    effective_date: Optional[str] = None
    models: Dict[str, ModelPrice] = Field(default_factory=dict)


def load_pricing(path: Optional[str] = None) -> PricingTable:
    """读取价格表；文件不存在时返回空表（成本记为未知，而不是编一个数）。"""

    pricing_path = Path(path) if path else DEFAULT_PRICING_PATH
    if not pricing_path.exists():
        return PricingTable()

    with pricing_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return PricingTable.model_validate(raw)


def estimate_cost(
    usage: TokenUsage,
    model: str,
    table: PricingTable,
) -> Optional[float]:
    """按价格表估算单次模型调用成本。

    返回 None 表示“价格表里没有这个模型”——宁可标注未知，也不要猜。
    """

    price = table.models.get(model)
    if price is None:
        return None

    # 缓存命中的输入单独计价；未命中的按正常输入价
    cached = min(max(usage.cached, 0), max(usage.prompt, 0))
    uncached_prompt = max(usage.prompt - cached, 0)

    cost = (
        uncached_prompt * price.input_per_1m
        + cached * (price.cached_input_per_1m if price.cached_input_per_1m is not None else price.input_per_1m)
        + max(usage.completion, 0) * price.output_per_1m
    ) / 1_000_000.0

    # reasoning 若单独计价且未包含在 completion 内，才额外累加
    if price.reasoning_per_1m is not None and usage.reasoning > 0:
        cost += usage.reasoning * price.reasoning_per_1m / 1_000_000.0

    return round(cost, 6)
