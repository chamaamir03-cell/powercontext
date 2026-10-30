from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .config import SCOPE_KEY, SCOPE_TITLE, STATE_PREFIX, TASK_ID


def _imports() -> SimpleNamespace:
    try:
        from powercontext.builtin.artifacts.memory import MemoryEntryInput
        from powercontext.builtin.persistence.sqlite import SQLiteConfig
        from powercontext.builtin.runtime import (
            BuiltinConfig,
            PrepareContextRequest,
            RememberMemoryRequest,
            SearchMemoryRequest,
            open_builtin_runtime,
        )
        from powercontext.builtin.scope import ScopeDraft
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "PowerContext is required. Run from the repository root with `src` on "
            "PYTHONPATH, or install the PowerContext package."
        ) from error
    return SimpleNamespace(
        MemoryEntryInput=MemoryEntryInput,
        SQLiteConfig=SQLiteConfig,
        BuiltinConfig=BuiltinConfig,
        PrepareContextRequest=PrepareContextRequest,
        RememberMemoryRequest=RememberMemoryRequest,
        SearchMemoryRequest=SearchMemoryRequest,
        open_builtin_runtime=open_builtin_runtime,
        ScopeDraft=ScopeDraft,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PowerContextStateStore:
    """Persist and re-read task state through PowerContext Memory artifacts."""

    def __init__(self, db_path: Path, scheduler_path: Path) -> None:
        self.db_path = db_path
        self.scheduler_path = scheduler_path

    async def save(self, state: dict[str, Any]) -> dict[str, Any]:
        pc = _imports()
        state = dict(state)
        state["task_id"] = state.get("task_id", TASK_ID)
        state["updated_at"] = _now()
        text = STATE_PREFIX + json.dumps(state, ensure_ascii=False, sort_keys=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        config = pc.BuiltinConfig(
            database=pc.SQLiteConfig(url=f"sqlite+aiosqlite:///{self.db_path.as_posix()}")
        )
        async with pc.open_builtin_runtime(config, scheduler_path=str(self.scheduler_path)) as runtime:
            if runtime.scopes is None:
                raise RuntimeError("PowerContext Scope registry is unavailable")
            scope = await runtime.scopes.create(
                pc.ScopeDraft(
                    title=SCOPE_TITLE,
                    summary="Task state and validation history for the MiniMax SDD demo.",
                    idempotency_key=SCOPE_KEY,
                )
            )
            saved = await runtime.memory.for_scope(scope.scope_id).remember(
                pc.RememberMemoryRequest(entries=(pc.MemoryEntryInput(kind="outcome", text=text),))
            )
            entry = saved.entry
            citation = entry.citation if entry is not None else None
            return {
                "scope_id": scope.scope_id,
                "entry_id": None if citation is None else citation.entry_id,
                "memory_ref": saved.memory_ref.model_dump(mode="json"),
                "saved_at": state["updated_at"],
            }

    async def load(self) -> dict[str, Any]:
        pc = _imports()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        config = pc.BuiltinConfig(
            database=pc.SQLiteConfig(url=f"sqlite+aiosqlite:///{self.db_path.as_posix()}")
        )
        async with pc.open_builtin_runtime(config, scheduler_path=str(self.scheduler_path)) as runtime:
            if runtime.scopes is None:
                raise RuntimeError("PowerContext Scope registry is unavailable")
            scope = await runtime.scopes.create(
                pc.ScopeDraft(
                    title=SCOPE_TITLE,
                    summary="Task state and validation history for the MiniMax SDD demo.",
                    idempotency_key=SCOPE_KEY,
                )
            )
            page = await runtime.memory.for_scope(scope.scope_id).search(
                pc.SearchMemoryRequest(query="SDD_STATE", limit=50, mode="fts")
            )
            states: list[dict[str, Any]] = []
            entry_ids: list[str] = []
            for hit in page.hits:
                if not hit.text.startswith(STATE_PREFIX):
                    continue
                try:
                    parsed = json.loads(hit.text[len(STATE_PREFIX) :])
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    states.append(parsed)
                    entry_ids.append(getattr(hit, "entry_id", None))
            state = max(states, key=lambda item: str(item.get("updated_at", ""))) if states else None
            prepared = await runtime.context.for_scope(scope.scope_id).prepare(
                pc.PrepareContextRequest(query="SDD_STATE", max_bytes=4000)
            )
            return {
                "state": state,
                "scope_id": scope.scope_id,
                "hit_count": len(page.hits),
                "entry_count": len(states),
                "latest_entry_id": (
                    entry_ids[states.index(state)]
                    if state in states and len(entry_ids) == len(states)
                    else None
                ),
                "prepared_status": prepared.status,
                "prepared_bytes": prepared.content_bytes,
                "prepared_contains_state": bool(
                    prepared.content and "SDD_STATE" in prepared.content
                ),
            }

    def save_sync(self, state: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(self.save(state))

    def load_sync(self) -> dict[str, Any]:
        return asyncio.run(self.load())
