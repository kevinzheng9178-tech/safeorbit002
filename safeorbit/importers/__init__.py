"""轨迹导入器。P0 只支持 SafeOrbit 规范 JSON。"""

from .canonical import load_trajectory

__all__ = ["load_trajectory"]
