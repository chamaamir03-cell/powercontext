from __future__ import annotations

import json
import sys
import tempfile
import requests
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from sqlalchemy import create_engine

from minimax.engineering_context import (
    ContextDatabase,
    ContextError,
    EngineeringContext,
    MiniMaxClient,
    PowerContextClient,
    SCHEMA_VERSION,
    _validate_patch_paths,
    _metadata_from_file,
)


class FakePowerContext:
    def __init__(self):
        self.records: list[dict[str, Any]] = []
        self.scope_id = "scope-1"

    def scope(self, project: str) -> str:
        return self.scope_id

    def latest_state(self, scope_id: str, task_id: str) -> dict[str, Any] | None:
        matching = [
            item for item in self.records
            if item["task_id"] == task_id and item["project"] == "inventory"
        ]
        return max(
            matching,
            key=lambda item: (item.get("revision", 0), item["updated_at"]),
        ) if matching else None

    def save_state(self, scope_id: str, state: dict[str, Any]) -> str:
        stored = {"schema": SCHEMA_VERSION, **state}
        self.records.append(stored)
        return f"artifact-{len(self.records)}"


class FakeMiniMax:
    model = "MiniMax-M3.1-Flash"

    def __init__(self, patch: str, repaired_patch: str | None = None):
        self.patch = patch
        self.repaired_patch = repaired_patch

    def generate_patch(self, context: dict[str, Any]) -> dict[str, Any]:
        self.context = context
        return {"summary": "Implement the requested tenant filter.", "patch": self.patch}

    def repair_patch(
        self,
        context: dict[str, Any],
        proposal: dict[str, Any],
        error: str,
    ) -> dict[str, Any]:
        self.repair_error = error
        return {
            "summary": "Repair the malformed patch.",
            "patch": self.repaired_patch or "",
            "model": self.model,
        }


class EngineeringContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "project"
        (self.root / "specs").mkdir(parents=True)
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        self.spec = self.root / "specs" / "filtering.md"
        self.spec.write_text(
            "---\nkind: spec\nstatus: accepted\n---\n"
            "The inventory list must filter by warehouse and preserve default sorting.\n",
            encoding="utf-8",
        )
        (self.root / "specs" / "old-filter.md").write_text(
            "---\nkind: spec\nstatus: superseded\n---\n"
            "The retired filter matches by warehouse only.\n",
            encoding="utf-8",
        )
        self.source = self.root / "src" / "inventory.py"
        self.source.write_text(
            "def list_inventory(items, warehouse=None):\n"
            "    return [item for item in items if warehouse is None or item['warehouse'] == warehouse]\n",
            encoding="utf-8",
        )
        (self.root / "tests" / "test_inventory.py").write_text(
            "def test_warehouse_filter_keeps_default_order():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        self.database = ContextDatabase(create_engine("sqlite:///:memory:"))
        self.memory = FakePowerContext()
        self.service = EngineeringContext(self.database, self.memory)
        import subprocess

        subprocess.run(["git", "init", "-q", str(self.root)], check=True)

    def tearDown(self):
        self.temporary.cleanup()

    def test_metadata_marks_superseded_decisions_as_historical(self):
        kind, status, _ = _metadata_from_file(
            Path("specs/contract.md"),
            "---\nkind: decision\nstatus: superseded\n---\nOld decision",
        )
        self.assertEqual((kind, status), ("decision", "superseded"))

    def test_prepare_returns_active_specs_relevant_code_and_tests(self):
        result = self.service.prepare(
            self.root,
            "inventory",
            "warehouse-filter",
            "Filter inventory by warehouse and retain default sorting.",
        )
        refs = {(item["kind"], item["path"]) for item in result["sources"]}
        self.assertIn(("spec", "specs/filtering.md"), refs)
        self.assertIn(("code", "src/inventory.py"), refs)
        self.assertIn(("test", "tests/test_inventory.py"), refs)
        self.assertNotIn(("spec", "specs/old-filter.md"), refs)
        self.assertEqual(result["context_freshness"]["verification"], "not_run")
        self.assertEqual(result["saved_state"]["powercontext_artifact"], "artifact-1")

    def test_reindex_uses_updated_specification_content(self):
        self.database.sync_project(self.root, "inventory")
        self.spec.write_text(
            "---\nkind: spec\nstatus: accepted\n---\n"
            "The inventory list filters by supplier and preserves default sorting.\n",
            encoding="utf-8",
        )
        self.database.sync_project(self.root, "inventory")
        supplier_hits = self.database.search("inventory", "Filter inventory by supplier")
        warehouse_hits = self.database.search("inventory", "Filter inventory by warehouse")
        self.assertTrue(any("supplier" in item["excerpt"] for item in supplier_hits))
        current_spec_warehouse_hits = [
            item for item in warehouse_hits
            if item["path"] == "specs/filtering.md"
        ]
        self.assertFalse(any(
            "warehouse" in item["excerpt"].lower()
            for item in current_spec_warehouse_hits
        ))

    def test_validation_freshness_becomes_stale_after_code_changes(self):
        self.service.prepare(
            self.root, "inventory", "warehouse-filter", "Filter inventory by warehouse."
        )
        self.service.record_validation(
            self.root, "inventory", "warehouse-filter", [sys.executable, "-c", "pass"]
        )
        current = self.service.prepare(
            self.root, "inventory", "warehouse-filter", "Filter inventory by warehouse."
        )
        self.assertEqual(current["context_freshness"]["verification"], "current")
        self.source.write_text(
            "def list_inventory(items, warehouse=None):\n"
            "    return sorted(items)\n",
            encoding="utf-8",
        )
        changed = self.service.prepare(
            self.root, "inventory", "warehouse-filter", "Filter inventory by warehouse."
        )
        self.assertEqual(changed["context_freshness"]["verification"], "stale")

    def test_requirements_change_invalidates_validation_and_completion(self):
        task = "Filter inventory by warehouse."
        self.service.prepare(self.root, "inventory", "warehouse-filter", task)
        self.service.record_validation(
            self.root, "inventory", "warehouse-filter", [sys.executable, "-c", "pass"]
        )
        self.service.update_status("inventory", "warehouse-filter", "completed", root=self.root)
        self.spec.write_text(self.spec.read_text(encoding="utf-8") + "Keep items stable.\n", encoding="utf-8")
        with self.assertRaises(ContextError):
            self.service.update_status("inventory", "warehouse-filter", "completed", root=self.root)
        result = self.service.prepare(self.root, "inventory", "warehouse-filter", task)
        self.assertEqual(result["context_freshness"]["verification"], "stale")
        self.assertEqual(result["task"]["status"], "in_progress")

    def test_task_change_invalidates_validation(self):
        self.service.prepare(self.root, "inventory", "warehouse-filter", "Filter by warehouse.")
        self.service.record_validation(
            self.root, "inventory", "warehouse-filter", [sys.executable, "-c", "pass"]
        )
        result = self.service.prepare(
            self.root, "inventory", "warehouse-filter", "Filter by warehouse and supplier."
        )
        self.assertEqual(result["context_freshness"]["verification"], "stale")
        with self.assertRaises(ContextError):
            self.service.update_status("inventory", "warehouse-filter", "completed", root=self.root)

    def test_unknown_status_fails_closed(self):
        self.spec.write_text("---\nkind: spec\nstatus: archived\n---\nOld requirement.\n", encoding="utf-8")
        with self.assertRaisesRegex(ContextError, "Unknown lifecycle status"):
            self.database.sync_project(self.root, "inventory")

    def test_search_respects_small_top_k(self):
        self.database.sync_project(self.root, "inventory")
        self.assertEqual(len(self.database.search("inventory", "inventory warehouse", 1)), 1)

    def test_task_state_survives_service_restart(self):
        first = self.service.prepare(
            self.root, "inventory", "warehouse-filter", "Filter inventory by warehouse."
        )
        artifact = first["saved_state"]["powercontext_artifact"]
        restarted = EngineeringContext(self.database, self.memory)
        next_run = restarted.prepare(
            self.root, "inventory", "warehouse-filter", "Filter inventory by warehouse."
        )
        self.assertEqual(artifact, "artifact-1")
        self.assertEqual(next_run["task"]["status"], "in_progress")
        self.assertEqual(len(self.memory.records), 2)

    def test_validation_is_saved_and_reloaded_with_current_fingerprints(self):
        self.service.prepare(
            self.root, "inventory", "warehouse-filter", "Filter inventory by warehouse."
        )
        state, exit_code = self.service.record_validation(
            self.root,
            "inventory",
            "warehouse-filter",
            [sys.executable, "-c", "print('tests passed')"],
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(state["last_validation"]["result"], "passed")
        context = self.service.prepare(
            self.root, "inventory", "warehouse-filter", "Filter inventory by warehouse."
        )
        self.assertEqual(context["context_freshness"]["verification"], "current")

    def test_failed_validation_is_persisted_without_a_success_shaped_result(self):
        self.service.prepare(
            self.root, "inventory", "warehouse-filter", "Filter inventory by warehouse."
        )
        state, exit_code = self.service.record_validation(
            self.root,
            "inventory",
            "warehouse-filter",
            [sys.executable, "-c", "raise SystemExit(4)"],
        )
        self.assertEqual(exit_code, 4)
        self.assertEqual(state["last_validation"]["result"], "failed")
        self.assertEqual(state["last_validation"]["exit_code"], 4)

    def test_completion_requires_a_passing_validation_for_current_code(self):
        self.service.prepare(
            self.root, "inventory", "warehouse-filter", "Filter inventory by warehouse."
        )
        with self.assertRaises(ContextError):
            self.service.update_status(
                "inventory", "warehouse-filter", "completed", root=self.root
            )
        self.service.record_validation(
            self.root,
            "inventory",
            "warehouse-filter",
            [sys.executable, "-c", "pass"],
        )
        completed = self.service.update_status(
            "inventory", "warehouse-filter", "completed", root=self.root
        )
        self.assertEqual(completed["status"], "completed")
        self.source.write_text(
            self.source.read_text(encoding="utf-8") + "# post-completion edit\n",
            encoding="utf-8",
        )
        reopened = self.service.prepare(
            self.root, "inventory", "warehouse-filter", "Filter inventory by warehouse."
        )
        self.assertEqual(reopened["task"]["status"], "in_progress")
        with self.assertRaises(ContextError):
            self.service.update_status(
                "inventory", "warehouse-filter", "completed", root=self.root
            )

    def test_minimax_patch_is_applied_and_real_test_command_is_recorded(self):
        old_source = self.source.read_text(encoding="utf-8")
        new_source = old_source.replace("return [item for item in items", "return sorted([item for item in items")
        new_source = new_source.replace("item['warehouse'] == warehouse]\n", "item['warehouse'] == warehouse])\n")
        patch = (
            "diff --git a/src/inventory.py b/src/inventory.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/src/inventory.py\n"
            "+++ b/src/inventory.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def list_inventory(items, warehouse=None):\n"
            "-    return [item for item in items if warehouse is None or item['warehouse'] == warehouse]\n"
            "+    return sorted([item for item in items if warehouse is None or item['warehouse'] == warehouse])\n"
        )
        result = self.service.implement(
            self.root,
            "inventory",
            "warehouse-filter",
            "Filter inventory by warehouse while preserving stable ordering.",
            [
                sys.executable,
                "-c",
                "from pathlib import Path; text=Path('src/inventory.py').read_text(); "
                "assert 'return sorted(' in text",
            ],
            FakeMiniMax(patch),
        )
        actual_source = self.source.read_text(encoding="utf-8")
        self.assertNotEqual(old_source, actual_source)
        self.assertNotEqual(new_source, old_source)
        self.assertIn("return sorted(", actual_source)
        self.assertEqual(result["model"], "MiniMax-M3.1-Flash")
        self.assertEqual(result["modified_files"], ["src/inventory.py"])
        self.assertEqual(result["validation"]["result"], "passed")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["task_state"], "completed")
        self.assertEqual(self.memory.records[-1]["status"], "completed")
        self.assertEqual(self.memory.records[-1]["last_context_query"]["database"], "sqlite")
        restored = self.service.prepare(
            self.root,
            "inventory",
            "warehouse-filter",
            "Filter inventory by warehouse while preserving stable ordering.",
        )
        self.assertIn("Implementation applied", restored["progress"]["completed"])
        self.assertIn("Validation passed", restored["progress"]["completed"])
        self.assertEqual(restored["progress"]["pending"], [])
        self.assertEqual(restored["saved_state"]["last_implementation"]["model"], "MiniMax-M3.1-Flash")

    def test_minimax_patch_rejects_paths_outside_the_project(self):
        with self.assertRaises(ContextError):
            _validate_patch_paths(
                "diff --git a/../../outside.py b/../../outside.py\n"
                "--- a/../../outside.py\n"
                "+++ b/../../outside.py\n",
                self.root,
            )

    def test_invalid_minimax_patch_is_retried_once_and_repaired(self):
        source = self.source.read_text(encoding="utf-8")
        old_line = "    return [item for item in items if warehouse is None or item['warehouse'] == warehouse]\n"
        new_line = (
            "    return [item for item in items "
            "if warehouse is None or item['warehouse'] == warehouse and item['supplier'] == 'alpha']\n"
        )
        repaired = (
            "diff --git a/src/inventory.py b/src/inventory.py\n"
            "--- a/src/inventory.py\n"
            "+++ b/src/inventory.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def list_inventory(items, warehouse=None):\n"
            f"-{old_line}"
            f"+{new_line}"
        )
        malformed = repaired.replace("@@ -1,2 +1,2 @@", "@@ -1,2 +1,4 @@")
        model = FakeMiniMax(malformed, repaired)
        result = self.service.implement(
            self.root,
            "inventory",
            "warehouse-filter",
            "Filter the inventory to supplier alpha.",
            [sys.executable, "-c", "from pathlib import Path; assert 'supplier' in Path('src/inventory.py').read_text()"],
            model,
        )
        self.assertIn("corrupt patch", model.repair_error)
        self.assertNotEqual(source, self.source.read_text(encoding="utf-8"))
        self.assertEqual(result["task_state"], "completed")

    def test_minimax_client_uses_official_endpoint_and_parses_patch_json(self):
        client = MiniMaxClient("test-token")
        response = Mock()
        response.content = b'{"choices":[{"message":{"content":"{\\"summary\\":\\"ok\\",\\"patch\\":\\"\\"}"}}]}'
        response.json.return_value = {
            "choices": [{"message": {"content": '{"summary":"ok","patch":""}'}}],
            "model": "MiniMax-M3.1-Flash",
            "id": "request-123",
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 4,
                "total_tokens": 16,
                "api_key": "must-not-be-recorded",
            },
        }
        response.raise_for_status.return_value = None
        client.session.post = Mock(return_value=response)
        result = client.generate_patch({
            "task": {"id": "test-task", "description": "test"},
            "git_commit": "abc123",
            "sources": [{"path": "src/example.py", "excerpt": "print('hello')"}],
        })
        self.assertEqual(result["summary"], "ok")
        self.assertEqual(result["patch"], "")
        self.assertEqual(result["request_id"], "request-123")
        self.assertEqual(result["usage"], {
            "prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16,
        })
        args, kwargs = client.session.post.call_args
        self.assertEqual(args[0], "https://api.minimax.io/v1/chat/completions")
        self.assertEqual(client.session.headers["Authorization"], "Bearer test-token")
        self.assertEqual(kwargs["json"]["model"], "MiniMax-M3.1-Flash")
        self.assertEqual(kwargs["json"]["response_format"], {"type": "json_object"})

    def test_powercontext_client_reads_and_confirms_persisted_records(self):
        client = PowerContextClient("http://powercontext.test", "local-token")
        state = {
            "project": "inventory",
            "task_id": "warehouse-filter",
            "updated_at": "2026-10-29T00:00:00+00:00",
        }
        client.session.post = Mock(side_effect=[
            Mock(json=lambda: {"scope_id": "scope-1"}),
            Mock(json=lambda: {
                "items": [{"text": json.dumps({"schema": SCHEMA_VERSION, **state})}],
                "next_cursor": None,
            }),
            Mock(json=lambda: {
                "changed": True,
                "records": [{"artifact": {
                    "family": "atomic-memory", "artifact_id": "artifact-1", "revision": 1,
                }}],
            }),
        ])
        scope_id = client.scope("inventory")
        loaded = client.latest_state(scope_id, "warehouse-filter")
        artifact = client.save_state(scope_id, state)
        self.assertEqual(loaded["project"], "inventory")
        self.assertEqual(artifact, "artifact-1")
        calls = client.session.post.call_args_list
        self.assertEqual(calls[0].args[0], "http://powercontext.test/v1/scopes")
        self.assertEqual(calls[1].kwargs["json"]["kind"], "engineering-task-state-v1")
        self.assertEqual(calls[2].kwargs["json"]["scope_id"], "scope-1")

    def test_powercontext_scope_retries_one_idempotent_timeout(self):
        client = PowerContextClient("http://powercontext.test", "local-token")
        response = Mock(json=lambda: {"scope_id": "scope-1"})
        response.raise_for_status.return_value = None
        client.session.post = Mock(side_effect=[requests.ReadTimeout(), response])
        self.assertEqual(client.scope("inventory"), "scope-1")
        self.assertEqual(client.session.post.call_count, 2)
        self.assertEqual(
            client.session.post.call_args_list[0].kwargs["json"]["idempotency_key"],
            client.session.post.call_args_list[1].kwargs["json"]["idempotency_key"],
        )

    def test_powercontext_memory_read_retries_transient_timeout(self):
        client = PowerContextClient("http://powercontext.test", "local-token")
        response = Mock(json=lambda: {"items": [], "next_cursor": None})
        response.raise_for_status.return_value = None
        client.session.post = Mock(side_effect=[requests.ReadTimeout(), response])
        with patch("minimax.engineering_context.time.sleep"):
            self.assertEqual(client._states("scope-1", "task-1"), [])
        self.assertEqual(client.session.post.call_count, 2)
        self.assertEqual(
            client.session.post.call_args_list[0].kwargs["json"],
            client.session.post.call_args_list[1].kwargs["json"],
        )


if __name__ == "__main__":
    unittest.main()
