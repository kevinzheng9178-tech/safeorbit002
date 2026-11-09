"""审计知识库：把法规与安全标准切片入库，供审计 Agent 检索判定依据。

设计原则：
- 本地 SQLite + FTS5，零外部依赖、可离线运行；
- 检索结果只作为「引用」，不参与裁决，不影响确定性规则；
- 每条结果都能回到原文段落，便于人工核对。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .models import Citation

DOCS_DIR = Path(__file__).with_name("kb_docs")
DEFAULT_DB_PATH = Path("data") / "kb.db"

_CHUNK_CHARS = 520
_CHUNK_OVERLAP = 80


@dataclass
class _Chunk:
    doc_id: str
    title: str
    section: str
    text: str
    source: str


def _split_sections(text: str) -> List[tuple[str, str]]:
    """按 Markdown 二级标题切分，返回 (章节名, 正文)。"""

    sections: List[tuple[str, str]] = []
    title = ""
    buffer: List[str] = []
    for raw in text.splitlines():
        if raw.startswith("## "):
            if buffer and "".join(buffer).strip():
                sections.append((title, "\n".join(buffer).strip()))
            title = raw[3:].strip()
            buffer = []
        elif raw.startswith("# "):
            continue
        else:
            buffer.append(raw)
    if buffer and "".join(buffer).strip():
        sections.append((title, "\n".join(buffer).strip()))
    return sections


def _window(text: str) -> List[str]:
    """把长正文切成带重叠的窗口，保证引用片段不至于跨度过大。"""

    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= _CHUNK_CHARS:
        return [text] if text else []
    pieces: List[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + _CHUNK_CHARS)
        window = text[start:end]
        # 尽量在段落或句号处收尾，避免把一句话切断
        cut = max(window.rfind("\n\n"), window.rfind("。"), window.rfind("；"))
        if cut > _CHUNK_CHARS // 2:
            window = window[: cut + 1]
            end = start + cut + 1
        pieces.append(window.strip())
        if end >= len(text):
            break
        start = max(end - _CHUNK_OVERLAP, start + 1)
    return [piece for piece in pieces if piece]


def _parse_doc(path: Path) -> List[_Chunk]:
    """解析单个文档：一级标题作为文档标题，文件名主干作为 doc_id。"""

    raw = path.read_text(encoding="utf-8")
    doc_title = path.stem
    for line in raw.splitlines():
        if line.startswith("# "):
            doc_title = line[2:].strip()
            break

    source = ""
    source_match = re.search(r"^>.*?来源[:：]\s*(.+)$", raw, re.M)
    if source_match:
        source = source_match.group(1).strip()

    chunks: List[_Chunk] = []
    for section, body in _split_sections(raw):
        for piece in _window(body):
            chunks.append(
                _Chunk(
                    doc_id=path.stem,
                    title=doc_title,
                    section=section,
                    text=piece,
                    source=source,
                )
            )
    return chunks


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    return connection


def _tokenizer(connection: sqlite3.Connection) -> str:
    """优先用 trigram（对中文友好），失败则退回 unicode61。"""

    for candidate in ("trigram", "unicode61"):
        try:
            connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS __kb_probe USING fts5(x, tokenize='%s')" % candidate
            )
            connection.execute("DROP TABLE IF EXISTS __kb_probe")
            return candidate
        except sqlite3.OperationalError:
            continue
    return "unicode61"


class KnowledgeBase:
    """审计知识库：负责建库、检索与统计。"""

    def __init__(self, db_path: Optional[Path] = None, docs_dir: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.docs_dir = Path(docs_dir) if docs_dir else DOCS_DIR
        self._tokenizer_name = ""

    # -- 建库 ---------------------------------------------------------------
    def rebuild(self) -> int:
        """从文档目录重建索引，返回切片总数。"""

        connection = _connect(self.db_path)
        try:
            connection.execute("DROP TABLE IF EXISTS kb_fts")
            self._tokenizer_name = _tokenizer(connection)
            connection.execute(
                "CREATE VIRTUAL TABLE kb_fts USING fts5("
                "doc_id, title, section, source, text, tokenize='%s')" % self._tokenizer_name
            )
            rows = []
            if self.docs_dir.is_dir():
                for path in sorted(self.docs_dir.glob("*.md")):
                    for chunk in _parse_doc(path):
                        rows.append(
                            (chunk.doc_id, chunk.title, chunk.section, chunk.source, chunk.text)
                        )
            if rows:
                connection.executemany(
                    "INSERT INTO kb_fts (doc_id, title, section, source, text) VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
            connection.commit()
            return len(rows)
        finally:
            connection.close()

    # -- 检索 ---------------------------------------------------------------
    def _ensure_ready(self) -> bool:
        if not self.db_path.exists():
            return False
        connection = _connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='kb_fts'"
            ).fetchone()
            return row is not None
        finally:
            connection.close()

    def search(self, query: str, limit: int = 4) -> List[Citation]:
        """检索依据片段；查询为空或索引缺失时返回空列表，绝不抛错。"""

        query = (query or "").strip()
        if not query or not self._ensure_ready():
            return []

        connection = _connect(self.db_path)
        try:
            rows = self._match(connection, query, limit)
            if not rows:
                rows = self._like(connection, query, limit)
            return [
                Citation(
                    doc_id=row["doc_id"],
                    title=row["title"],
                    section=row["section"] or "",
                    snippet=row["text"],
                    source=row["source"] or "",
                    score=round(float(row["rank"]), 4) if "rank" in row.keys() else 0.0,
                )
                for row in rows
            ]
        except sqlite3.Error:
            return []
        finally:
            connection.close()

    def _match(self, connection: sqlite3.Connection, query: str, limit: int):
        # FTS5 语法里的双引号与运算符会破坏查询，这里只保留文字与空格
        safe = re.sub(r'["\*\^\(\):]', " ", query)
        terms = [term for term in safe.split() if len(term) >= 2]
        if not terms:
            return []
        expression = " OR ".join('"%s"' % term for term in terms[:8])
        try:
            return connection.execute(
                "SELECT doc_id, title, section, source, text, bm25(kb_fts) AS rank "
                "FROM kb_fts WHERE kb_fts MATCH ? ORDER BY rank LIMIT ?",
                (expression, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []

    def _like(self, connection: sqlite3.Connection, query: str, limit: int):
        terms = [term for term in re.split(r"\s+", query) if len(term) >= 2][:4]
        if not terms:
            return []
        where = " OR ".join("text LIKE ?" for _ in terms)
        params = ["%" + term + "%" for term in terms] + [limit]
        return connection.execute(
            "SELECT doc_id, title, section, source, text FROM kb_fts WHERE %s LIMIT ?" % where,
            params,
        ).fetchall()

    # -- 统计 ---------------------------------------------------------------
    def stats(self) -> dict:
        if not self._ensure_ready():
            return {"ready": False, "chunks": 0, "documents": 0, "tokenizer": self._tokenizer_name}
        connection = _connect(self.db_path)
        try:
            chunks = connection.execute("SELECT COUNT(*) AS c FROM kb_fts").fetchone()["c"]
            documents = connection.execute(
                "SELECT COUNT(DISTINCT doc_id) AS c FROM kb_fts"
            ).fetchone()["c"]
            return {
                "ready": True,
                "chunks": int(chunks),
                "documents": int(documents),
                "tokenizer": self._tokenizer_name,
                "path": str(self.db_path),
            }
        finally:
            connection.close()


_default: Optional[KnowledgeBase] = None


def default_kb() -> KnowledgeBase:
    """进程内共享的知识库实例；首次调用时若索引缺失则自动建库。"""

    global _default
    if _default is None:
        _default = KnowledgeBase()
        if not _default._ensure_ready():
            try:
                _default.rebuild()
            except sqlite3.Error:
                pass
    return _default
