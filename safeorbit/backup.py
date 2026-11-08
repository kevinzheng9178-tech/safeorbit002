"""SQLite 在线备份与恢复。

使用 sqlite3 的在线备份 API，可在服务运行期间安全备份。
备份文件带时间戳，恢复前要求显式确认，避免误覆盖。
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional


def backup(db_path: str, output_path: Optional[str] = None) -> str:
    """在线备份 SQLite 到指定文件；未指定时生成带时间戳的备份。"""

    source = Path(db_path)
    if not source.is_file():
        raise FileNotFoundError("数据库不存在：%s" % db_path)
    target = Path(output_path) if output_path else source.with_name(
        "%s.backup-%s" % (source.name, datetime.now().strftime("%Y%m%d-%H%M%S"))
    )
    target.parent.mkdir(parents=True, exist_ok=True)

    destination = sqlite3.connect(str(target))
    try:
        with sqlite3.connect(str(source)) as connection:
            connection.backup(destination)
    finally:
        destination.close()
    return str(target)


def restore(db_path: str, backup_path: str, *, confirm: bool = False) -> str:
    """从备份恢复；confirm 为 True 时才执行，避免误覆盖生产库。"""

    source = Path(backup_path)
    if not source.is_file():
        raise FileNotFoundError("备份文件不存在：%s" % backup_path)
    if not confirm:
        raise ValueError("恢复为破坏性操作，必须显式 --confirm")

    target = Path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # 先对当前库做一次紧急备份，防止恢复失败丢失数据
    safety = target.with_name("%s.pre-restore-%s" % (target.name, datetime.now().strftime("%Y%m%d-%H%M%S")))
    if target.is_file():
        shutil.copy2(str(target), str(safety))

    destination = sqlite3.connect(str(target))
    try:
        with sqlite3.connect(str(source)) as connection:
            connection.backup(destination)
    finally:
        destination.close()
    return str(target)


def list_backups(db_path: str) -> list:
    source = Path(db_path)
    pattern = "%s.backup-*" % source.name
    return sorted(
        [str(path) for path in source.parent.glob(pattern)],
        reverse=True,
    )
