"""规范 JSON 导入器。

P0 的边界很明确：只吃 SafeOrbit 规范格式。
LangSmith / Langfuse / OTel JSON 的转换适配器属于 P1，这里不提前造。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from ..models import Trajectory


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        try:
            raw = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError("文件不是合法 JSON：%s（第 %s 行）" % (path, exc.lineno)) from exc

    if not isinstance(raw, dict):
        raise ValueError("轨迹文件顶层必须是对象（JSON object），实际是 %s" % type(raw).__name__)
    return raw


def load_trajectory(path: str) -> Trajectory:
    """读取轨迹文件并做基础校验。

    只做“结构与必填字段”的校验，不做业务判断；
    父节点缺失等问题交给归一化阶段标记为警告，而不是直接拒绝。
    """

    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError("轨迹文件不存在：%s" % path)
    if file_path.suffix.lower() != ".json":
        raise ValueError("P0 仅支持 .json 轨迹文件，当前为 %s" % file_path.suffix)

    raw = _read_json(file_path)

    schema_version = str(raw.get("schema_version", "1.0"))
    if not schema_version.startswith("1."):
        raise ValueError("不支持的 schema_version：%s（当前仅支持 1.x）" % schema_version)

    raw.setdefault("source", "file")
    if not raw.get("received_at"):
        raw["received_at"] = datetime.now().astimezone().isoformat()

    try:
        trajectory = Trajectory.model_validate(raw)
    except Exception as exc:  # pydantic 的校验信息已足够定位问题
        raise ValueError("轨迹字段校验失败：%s" % exc) from exc

    _ensure_unique_span_ids(trajectory)
    return trajectory


def _ensure_unique_span_ids(trajectory: Trajectory) -> None:
    seen = set()
    duplicates = set()
    for span in trajectory.spans:
        if span.span_id in seen:
            duplicates.add(span.span_id)
        seen.add(span.span_id)

    if duplicates:
        raise ValueError("span_id 重复：%s" % ", ".join(sorted(duplicates)))
