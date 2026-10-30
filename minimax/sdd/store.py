from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SYMBOL_PATTERN = re.compile(r"^(?:class|def)\s+([A-Za-z_]\w*)", re.MULTILINE)
_HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Document:
    doc_id: str
    kind: str
    path: str
    title: str
    content: str
    metadata: dict[str, Any]
    version: str
    updated_at: str

    @property
    def symbols(self) -> list[str]:
        values = self.metadata.get("symbols", [])
        return [str(value) for value in values]


def source_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def extract_symbols(path: str, content: str) -> list[str]:
    if path.endswith(".py"):
        return list(dict.fromkeys(_SYMBOL_PATTERN.findall(content)))
    if path.endswith(".md"):
        return [heading.strip() for heading in _HEADING_PATTERN.findall(content)]
    return []


def document_from_file(
    path: Path,
    *,
    kind: str,
    minimax_dir: Path,
    repo_root: Path,
    imported_from: str | None = None,
) -> Document:
    content = path.read_text(encoding="utf-8")
    relative = path.relative_to(minimax_dir).as_posix()
    doc_id = f"{kind}:{relative}"
    if path.suffix == ".md":
        match = _HEADING_PATTERN.search(content)
        title = match.group(1).strip() if match else relative
    else:
        title = relative
    metadata: dict[str, Any] = {
        "symbols": extract_symbols(relative, content),
        "lines": len(content.splitlines()),
        "repo_path": (repo_root / "minimax" / relative).as_posix(),
    }
    if imported_from:
        metadata["imported_from"] = imported_from
    return Document(
        doc_id=doc_id,
        kind=kind,
        path=f"minimax/{relative}",
        title=title,
        content=content,
        metadata=metadata,
        version=source_hash(content),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def _sql_literal(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "''").replace("\x00", "\\0")
    return f"'{escaped}'"


class EngineeringStore:
    """Embedded seekdb document store used to build the task context."""

    def __init__(self, db_dir: Path, *, log_path: Path | None = None) -> None:
        import pylibseekdb

        db_dir.mkdir(parents=True, exist_ok=True)
        self.db_dir = db_dir
        self.log_path = log_path
        self.queries: list[dict[str, Any]] = []
        self._pylibseekdb = pylibseekdb
        self._instance = pylibseekdb.open(str(db_dir))
        self._connection = self._instance.connect(database="test")
        self._cursor = self._connection.cursor()
        self._closed = False

    def initialize(self) -> None:
        self._cursor.execute(
            "CREATE TABLE IF NOT EXISTS engineering_docs ("
            "doc_id VARCHAR(256) PRIMARY KEY, "
            "kind VARCHAR(32) NOT NULL, "
            "path VARCHAR(512) NOT NULL, "
            "title VARCHAR(512) NOT NULL, "
            "content TEXT NOT NULL, "
            "metadata TEXT NOT NULL, "
            "version VARCHAR(64) NOT NULL, "
            "updated_at VARCHAR(64) NOT NULL"
            ")"
        )
        self._connection.commit()

    def reset(self) -> None:
        self._cursor.execute("DROP TABLE IF EXISTS engineering_docs")
        self._connection.commit()
        self.initialize()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._cursor.close()
        finally:
            try:
                self._connection.close()
            finally:
                self._instance.close()

    def upsert(self, document: Document) -> None:
        metadata = json.dumps(document.metadata, ensure_ascii=False, sort_keys=True)
        sql = (
            "REPLACE INTO engineering_docs "
            "(doc_id, kind, path, title, content, metadata, version, updated_at) VALUES ("
            + ", ".join(
                _sql_literal(value)
                for value in (
                    document.doc_id,
                    document.kind,
                    document.path,
                    document.title,
                    document.content,
                    metadata,
                    document.version,
                    document.updated_at,
                )
            )
            + ")"
        )
        self._cursor.execute(sql)
        self._connection.commit()

    def count(self) -> int:
        self._cursor.execute("SELECT COUNT(*) FROM engineering_docs")
        row = self._cursor.fetchone()
        return int(row[0]) if row else 0

    def _row_to_document(self, row: tuple[Any, ...]) -> Document:
        doc_id, kind, path, title, content, metadata, version, updated_at = row
        return Document(
            doc_id=str(doc_id),
            kind=str(kind),
            path=str(path),
            title=str(title),
            content=str(content),
            metadata=json.loads(str(metadata)),
            version=str(version),
            updated_at=str(updated_at),
        )

    def get(self, doc_id: str) -> Document | None:
        self._cursor.execute(
            "SELECT doc_id, kind, path, title, content, metadata, version, updated_at "
            f"FROM engineering_docs WHERE doc_id = {_sql_literal(doc_id)}"
        )
        row = self._cursor.fetchone()
        return self._row_to_document(row) if row else None

    def all(self, kind: str | None = None) -> list[Document]:
        where = "" if kind is None else f" WHERE kind = {_sql_literal(kind)}"
        self._cursor.execute(
            "SELECT doc_id, kind, path, title, content, metadata, version, updated_at "
            f"FROM engineering_docs{where} ORDER BY path"
        )
        return [self._row_to_document(row) for row in self._cursor.fetchall()]

    def search(self, kind: str, terms: list[str], *, limit: int = 5) -> list[Document]:
        clean_terms = list(dict.fromkeys(term.strip().lower() for term in terms if term.strip()))
        clauses = [f"kind = {_sql_literal(kind)}"]
        if clean_terms:
            matches = []
            for term in clean_terms:
                literal = _sql_literal(term)
                matches.append(
                    "(INSTR(LOWER(content), "
                    f"{literal}) > 0 OR INSTR(LOWER(title), {literal}) > 0 "
                    f"OR INSTR(LOWER(path), {literal}) > 0)"
                )
            clauses.append("(" + " OR ".join(matches) + ")")
        sql = (
            "SELECT doc_id, kind, path, title, content, metadata, version, updated_at "
            "FROM engineering_docs WHERE " + " AND ".join(clauses) + " ORDER BY path"
        )
        self._cursor.execute(sql)
        rows = self._cursor.fetchall()
        documents = [self._row_to_document(row) for row in rows]
        fallback = False
        if not documents and clean_terms:
            fallback = True
            documents = self.all(kind)
        scored = sorted(
            ((self._score(document, clean_terms), document) for document in documents),
            key=lambda pair: (-pair[0], pair[1].path),
        )
        selected = [document for _, document in scored[:limit]]
        self.queries.append(
            {
                "kind": kind,
                "terms": clean_terms,
                "sql": sql,
                "row_count": len(rows),
                "fallback": fallback,
                "doc_ids": [document.doc_id for document in selected],
            }
        )
        return selected

    @staticmethod
    def _score(document: Document, terms: list[str]) -> float:
        path = document.path.lower()
        title = document.title.lower()
        content = document.content.lower()
        score = 0.0
        for term in terms:
            if term in path:
                score += 4.0
            if term in title:
                score += 2.0
            if term in content:
                score += 1.0
        for marker, weight in (
            ("inventory_search.md", 5.0),
            ("inventory/search.py", 5.0),
            ("test_price_filter.py", 5.0),
            ("test_regression.py", 4.0),
        ):
            if marker in path:
                score += weight
        return score
