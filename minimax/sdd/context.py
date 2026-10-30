from __future__ import annotations

import json
import re
from typing import Any

from .config import REQUIRED_DOC_IDS, REQUIRED_SOURCES, TASK_ID
from .store import Document, EngineeringStore

_SPEC_VERSION = re.compile(r"SPEC_VERSION:\s*(\S+)")
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
_STOP = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "add",
    "keep",
    "make",
    "into",
    "when",
    "must",
    "should",
    "will",
    "have",
    "test",
}
_FIXED_TERMS = (
    "min_price",
    "max_price",
    "price",
    "filter",
    "inventory",
    "search",
    "spec",
    "regression",
    "pagination",
    "bounds",
    "validation",
)


class ContextError(RuntimeError):
    pass


def task_terms(task: str) -> list[str]:
    terms = list(_FIXED_TERMS)
    for match in _TOKEN.findall(task.lower()):
        if match not in _STOP and match not in terms:
            terms.append(match)
    return terms


class ContextBuilder:
    def __init__(self, store: EngineeringStore, *, seekdb_label: str) -> None:
        self.store = store
        self.seekdb_label = seekdb_label

    def build(self, task: str, state_info: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        terms = task_terms(task)
        ranked = {
            "spec": self.store.search("spec", terms, limit=3),
            "code": self.store.search("code", terms, limit=3),
            "test": self.store.search("test", terms, limit=4),
        }
        required: dict[str, list[Document]] = {}
        missing: list[str] = []
        for kind, doc_ids in REQUIRED_DOC_IDS.items():
            required[kind] = []
            for doc_id in doc_ids:
                document = self.store.get(doc_id)
                if document is None:
                    missing.append(doc_id)
                else:
                    required[kind].append(document)
        if missing:
            raise ContextError(
                "Required documents are missing from seekdb; run `init` first. Missing: "
                + ", ".join(missing)
            )

        sources: list[dict[str, Any]] = []
        seen: set[str] = set()
        for kind in ("spec", "code", "test"):
            for document in required[kind]:
                if document.doc_id in seen:
                    continue
                seen.add(document.doc_id)
                sources.append(self._source(document, reason=f"required:{kind}", score=100.0))
            for document in ranked[kind]:
                if document.doc_id in seen:
                    continue
                seen.add(document.doc_id)
                sources.append(self._source(document, reason="ranked", score=10.0))
        specs = [self._source(document, reason="required:spec", score=100.0) for document in required["spec"]]
        code = [self._source(document, reason="required:code", score=100.0) for document in required["code"]]
        tests = [self._source(document, reason="required:test", score=100.0) for document in required["test"]]
        spec_marker = None
        if required["spec"]:
            match = _SPEC_VERSION.search(required["spec"][0].content)
            spec_marker = match.group(1) if match else None

        documents: dict[str, Document] = {}
        for kind in ("spec", "code", "test"):
            for document in [*required[kind], *ranked[kind]]:
                documents[document.doc_id] = document
        context: dict[str, Any] = {
            "_documents": documents,
            "task": task,
            "task_id": TASK_ID,
            "required_sources": list(REQUIRED_SOURCES),
            "missing_required": missing,
            "specs": specs,
            "code": code,
            "tests": tests,
            "sources": sources,
            "state_before": state_info.get("state"),
            "task_state": state,
            "powercontext": {
                "scope_id": state_info.get("scope_id"),
                "prepared_status": state_info.get("prepared_status"),
                "prepared_bytes": state_info.get("prepared_bytes"),
                "prepared_contains_state": state_info.get("prepared_contains_state"),
                "state_entries": state_info.get("entry_count", 0),
            },
            "db": {
                "backend": "seekdb",
                "database": self.seekdb_label,
                "queries": self.store.queries,
            },
            "spec_marker": spec_marker,
            "spec_version": required["spec"][0].version if required["spec"] else None,
        }
        context["context_text"] = render_context(context)
        return context

    @staticmethod
    def _source(document: Document, *, reason: str, score: float) -> dict[str, Any]:
        return {
            "doc_id": document.doc_id,
            "kind": document.kind,
            "path": document.path,
            "title": document.title,
            "version": document.version,
            "symbols": document.symbols,
            "reason": reason,
            "score": score,
            "imported_from": document.metadata.get("imported_from"),
            "excerpt": document.content[:700],
        }


def render_context(context: dict[str, Any]) -> str:
    parts = [
        "# SDD task context",
        "",
        f"Task: {context['task']}",
        "",
        "The specifications, code, and tests below were selected from seekdb.",
        "The task state was loaded from PowerContext.",
        "",
        "## Specifications",
    ]
    for item in context["specs"]:
        parts.extend(
            [
                "",
                f"### {item['path']} (version {item['version']})",
                _full_text(context, item["doc_id"]),
            ]
        )
    parts.extend(["", "## Code"])
    for item in context["code"]:
        parts.extend(
            [
                "",
                f"### {item['path']} (symbols: {', '.join(item['symbols']) or 'n/a'})",
                "```python",
                _full_text(context, item["doc_id"]),
                "```",
            ]
        )
    parts.extend(["", "## Tests"])
    for item in context["tests"]:
        parts.extend(
            [
                "",
                f"### {item['path']}",
                "```python",
                _full_text(context, item["doc_id"]),
                "```",
            ]
        )
    parts.extend(
        [
            "",
            "## Saved task state (PowerContext)",
            "```json",
            json.dumps(context["task_state"], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## Required source paths",
            *[f"- {path}" for path in context["required_sources"]],
        ]
    )
    return "\n".join(parts)


def _full_text(context: dict[str, Any], doc_id: str) -> str:
    # The full content is carried by the store lookup that produced the hit.
    # ContextBuilder stores excerpts only; resolve from the source document map.
    sources = context.get("_documents", {})
    document = sources.get(doc_id)
    return document.content if document is not None else ""


def attach_full_documents(context: dict[str, Any], documents: dict[str, Document]) -> dict[str, Any]:
    context["_documents"] = documents
    context["context_text"] = render_context(context)
    return context


def public_context(context: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in context.items() if not key.startswith("_")}
