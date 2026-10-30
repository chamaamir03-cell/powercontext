from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import minimax_client
from .config import (
    DEFAULT_PATHS,
    REQUIRED_SOURCES,
    TASK_DESCRIPTION,
    TASK_ID,
    Paths,
)
from .context import ContextBuilder, public_context
from .state import PowerContextStateStore
from .store import Document, EngineeringStore, document_from_file

_SPEC_VERSION = re.compile(r"SPEC_VERSION:\s*(\S+)")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Workflow:
    def __init__(self, paths: Paths = DEFAULT_PATHS, *, open_store: bool = True) -> None:
        self.paths = paths
        self.paths.ensure_state_dirs()
        self.store = EngineeringStore(paths.seekdb_dir) if open_store else None
        self.state = PowerContextStateStore(paths.powercontext_db, paths.scheduler_db)

    def close(self) -> None:
        if self.store is not None:
            self.store.close()

    def init(self) -> dict[str, Any]:
        self.store.initialize()
        ingested: list[dict[str, Any]] = []

        spec_paths = sorted(self.paths.specs_dir.glob("*.md"))
        code_paths = sorted((self.paths.baseline_dir / "inventory").glob("*.py"))
        test_paths = sorted((self.paths.baseline_dir / "tests").glob("*.py"))
        for kind, paths in (("spec", spec_paths), ("code", code_paths), ("test", test_paths)):
            for path in paths:
                document = document_from_file(
                    path,
                    kind=kind,
                    minimax_dir=self.paths.root,
                    repo_root=self.paths.repo_root,
                )
                self.store.upsert(document)
                ingested.append(
                    {
                        "doc_id": document.doc_id,
                        "kind": document.kind,
                        "path": document.path,
                        "version": document.version,
                    }
                )

        state = {
            "task_id": TASK_ID,
            "status": "initialized",
            "completed": ["materials_imported"],
            "pending": ["build_context", "apply_patch", "run_target_tests", "run_regression_tests"],
            "validation": {"target": "pending", "regression": "pending"},
            "required_sources": list(REQUIRED_SOURCES),
            "updated_at": _now(),
        }
        saved = self.state.save_sync(state)
        result = {
            "ok": True,
            "step": "init",
            "documents": ingested,
            "document_count": self.store.count(),
            "seekdb": str(self.paths.seekdb_dir),
            "powercontext": saved,
        }
        self._write_json("ingest.json", result)
        return result

    def context(self, task: str = TASK_DESCRIPTION) -> dict[str, Any]:
        state_info = self.state.load_sync()
        before = state_info.get("state") or {}
        state = _merge_state(
            before,
            status="context_ready",
            completed=["context_built"],
        )
        builder = ContextBuilder(
            self.store,
            seekdb_label=self._relative(self.paths.seekdb_dir),
        )
        context = builder.build(task, state_info, state)
        saved = self.state.save_sync(state)
        public = public_context(context)
        public["ok"] = True
        public["powercontext"]["save"] = saved
        self._write_json("context.json", public)
        self._write_queries()
        return public

    def apply(
        self,
        *,
        mode: str = "replay",
        workspace: str | Path | None = None,
        prompt_file: str | Path | None = None,
    ) -> dict[str, Any]:
        target = Path(workspace) if workspace else self.paths.work_dir / "demo"
        _copy_project(self.paths.baseline_dir, target)
        prompt = None
        if mode == "live":
            prompt_path = Path(prompt_file) if prompt_file else self.paths.prompt_file
            if not prompt_path.is_file():
                raise RuntimeError(
                    f"live mode requires a prompt file; expected {prompt_path}. "
                    "Run `python minimax/scripts/build_prompt.py` first or pass --prompt-file."
                )
            prompt = prompt_path.read_text(encoding="utf-8")
        applied = minimax_client.apply(
            mode,
            target,
            patch_path=self.paths.patch_file,
            records_dir=self.paths.records_dir,
            prompt=prompt,
        )
        state_info = self.state.load_sync()
        state = _merge_state(
            state_info.get("state") or {},
            status="patch_applied",
            completed=["patch_applied"],
            pending=["run_target_tests", "run_regression_tests"],
            validation={"target": "pending", "regression": "pending"},
        )
        self.state.save_sync(state)
        result = {
            "ok": True,
            "step": "apply",
            "mode": mode,
            "workspace": str(target),
            "applied": applied,
            "patch": self._relative(self.paths.patch_file),
            "task_state": state,
        }
        self._write_json("apply.json", result)
        return result

    def test(self, *, workspace: str | Path | None = None) -> dict[str, Any]:
        target = Path(workspace) if workspace else self.paths.work_dir / "demo"
        if not (target / "inventory" / "search.py").is_file():
            raise RuntimeError(f"workspace is not prepared; run `apply` first: {target}")
        target_code, target_output = _run_pytest(
            target, "test_price_filter.py", self.paths.records_dir / "test_target.log"
        )
        regression_code, regression_output = _run_pytest(
            target, "test_regression.py", self.paths.records_dir / "test_regression.log"
        )
        target_status = "passed" if target_code == 0 else "failed"
        regression_status = "passed" if regression_code == 0 else "failed"
        state_info = self.state.load_sync()
        state = _merge_state(
            state_info.get("state") or {},
            status="validated" if target_code == 0 and regression_code == 0 else "tests_failed",
            completed=["tests_run"] if target_code == 0 and regression_code == 0 else [],
            pending=[]
            if target_code == 0 and regression_code == 0
            else ["fix_target_tests" if target_code != 0 else "fix_regression_tests"],
            validation={"target": target_status, "regression": regression_status},
        )
        state["validation"]["commands"] = {
            "target": "python -m pytest tests/test_price_filter.py -q",
            "regression": "python -m pytest tests/test_regression.py -q",
        }
        self.state.save_sync(state)
        result = {
            "ok": target_code == 0 and regression_code == 0,
            "step": "test",
            "workspace": str(target),
            "target": {
                "exit_code": target_code,
                "status": target_status,
                "tail": target_output[-1200:],
            },
            "regression": {
                "exit_code": regression_code,
                "status": regression_status,
                "tail": regression_output[-1200:],
            },
            "task_state": state,
        }
        self._write_json("test_result.json", result)
        return result

    def before_after(self) -> dict[str, Any]:
        workspace = self.paths.work_dir / "before_after"
        _copy_project(self.paths.baseline_dir, workspace)
        before_target_code, before_target_output = _run_pytest(
            workspace, "test_price_filter.py", self.paths.records_dir / "test_before_target.log"
        )
        before_regression_code, before_regression_output = _run_pytest(
            workspace, "test_regression.py", self.paths.records_dir / "test_before_regression.log"
        )
        applied = minimax_client.apply_replay(workspace, self.paths.patch_file)
        after_target_code, after_target_output = _run_pytest(
            workspace, "test_price_filter.py", self.paths.records_dir / "test_after_target.log"
        )
        after_regression_code, after_regression_output = _run_pytest(
            workspace, "test_regression.py", self.paths.records_dir / "test_after_regression.log"
        )
        target_failed_before = (
            before_target_code != 0
            and "TypeError" in before_target_output
            and "min_price" in before_target_output
        )
        result = {
            "ok": target_failed_before
            and after_target_code == 0
            and before_regression_code == 0
            and after_regression_code == 0,
            "step": "before_after",
            "workspace": str(workspace),
            "applied": applied,
            "before": {
                "target_exit": before_target_code,
                "regression_exit": before_regression_code,
                "target_failed_for_missing_behavior": target_failed_before,
                "target_tail": before_target_output[-1200:],
                "regression_tail": before_regression_output[-800:],
            },
            "after": {
                "target_exit": after_target_code,
                "regression_exit": after_regression_code,
                "target_tail": after_target_output[-800:],
                "regression_tail": after_regression_output[-800:],
            },
        }
        _write_combined_log(
            self.paths.records_dir / "test_before.log",
            "before",
            before_target_output,
            before_regression_output,
            before_target_code,
            before_regression_code,
        )
        _write_combined_log(
            self.paths.records_dir / "test_after.log",
            "after",
            after_target_output,
            after_regression_output,
            after_target_code,
            after_regression_code,
        )
        state_info = self.state.load_sync()
        state = _merge_state(
            state_info.get("state") or {},
            completed=["before_after_verified"] if result["ok"] else [],
            before_after={
                "target_failed_before": target_failed_before,
                "target_exit_after": after_target_code,
                "regression_exit_before": before_regression_code,
                "regression_exit_after": after_regression_code,
            },
        )
        self.state.save_sync(state)
        self._write_json("before_after.json", result)
        return result

    def spec_update(self, file: str | Path | None = None) -> dict[str, Any]:
        source = Path(file).resolve() if file else self.paths.updated_spec_file
        document = document_from_file(
            source,
            kind="spec",
            minimax_dir=self.paths.root,
            repo_root=self.paths.repo_root,
            imported_from=self._relative(source),
        )
        document = replace(
            document,
            doc_id="spec:specs/inventory_search.md",
            path="minimax/specs/inventory_search.md",
        )
        # Keep the v1 spec for audit, then make the live spec path match the
        # v2 content that seekdb actually serves.
        backup = self.paths.records_dir / "inventory_search.v1.md"
        if self.paths.spec_file.is_file() and not backup.exists():
            backup.write_text(
                self.paths.spec_file.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        self.paths.spec_file.write_text(document.content, encoding="utf-8")
        self.store.upsert(document)
        match = _SPEC_VERSION.search(document.content)
        marker = match.group(1) if match else None
        state_info = self.state.load_sync()
        state = _merge_state(
            state_info.get("state") or {},
            status="spec_updated",
            completed=["spec_updated"],
            spec_marker=marker,
            spec_version=document.version,
        )
        self.state.save_sync(state)
        result = {
            "ok": True,
            "step": "spec_update",
            "source": self._relative(source),
            "doc_id": document.doc_id,
            "path": document.path,
            "version": document.version,
            "marker": marker,
        }
        self._write_json("spec_update.json", result)
        return result

    def status(self) -> dict[str, Any]:
        state_info = self.state.load_sync()
        result = {"ok": True, "step": "status", **state_info}
        self._write_json("powercontext_state.json", result)
        return result

    def reset(self) -> dict[str, Any]:
        self.store.close()
        shutil.rmtree(self.paths.state_dir, ignore_errors=True)
        shutil.rmtree(self.paths.work_dir, ignore_errors=True)
        backup = self.paths.records_dir / "inventory_search.v1.md"
        if backup.is_file():
            self.paths.spec_file.write_text(
                backup.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        self.paths.ensure_state_dirs()
        return {"ok": True, "step": "reset", "removed": [str(self.paths.state_dir), str(self.paths.work_dir)]}

    def validate(self) -> dict[str, Any]:
        records = self.paths.records_dir
        records.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, Any]] = []
        checks: list[dict[str, Any]] = []

        def run(args: list[str]) -> dict[str, Any]:
            env = os.environ.copy()
            paths = [str(self.paths.repo_root / "src"), str(self.paths.root)]
            if env.get("PYTHONPATH"):
                paths.append(env["PYTHONPATH"])
            env["PYTHONPATH"] = os.pathsep.join(paths)
            proc = subprocess.run(
                [sys.executable, str(self.paths.root / "run.py"), *args],
                cwd=str(self.paths.root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=600,
            )
            entries.append(
                {
                    "command": " ".join(["python", "minimax/run.py", *args]),
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout[-20000:],
                    "stderr": proc.stderr[-10000:],
                }
            )
            try:
                parsed = json.loads(proc.stdout)
            except json.JSONDecodeError:
                parsed = None
            return {"exit_code": proc.returncode, "json": parsed, "stdout": proc.stdout, "stderr": proc.stderr}

        def check(name: str, passed: bool, detail: Any) -> None:
            checks.append({"name": name, "passed": bool(passed), "detail": detail})

        reset = run(["reset", "--yes"])
        check("reset_exit", reset["exit_code"] == 0, reset["exit_code"])
        init = run(["init"])
        check("init_exit", init["exit_code"] == 0 and bool(init["json"]), init["exit_code"])
        context1 = run(["context", "--task", TASK_DESCRIPTION])
        ctx1 = context1["json"] or {}
        (records / "context_v1.json").write_text(
            json.dumps(ctx1, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        source_paths = {item.get("path") for item in ctx1.get("sources", [])}
        check(
            "context_required_sources",
            not ctx1.get("missing_required") and set(REQUIRED_SOURCES).issubset(source_paths),
            sorted(source_paths),
        )
        check("context_has_spec", bool(ctx1.get("specs")), [item.get("path") for item in ctx1.get("specs", [])])
        check("context_has_code", bool(ctx1.get("code")), [item.get("path") for item in ctx1.get("code", [])])
        check("context_has_tests", len(ctx1.get("tests", [])) >= 2, [item.get("path") for item in ctx1.get("tests", [])])
        apply_result = run(["apply", "--mode", "replay"])
        check("apply_exit", apply_result["exit_code"] == 0, apply_result["exit_code"])
        test_result = run(["test"])
        check(
            "tests_pass_after_patch",
            test_result["exit_code"] == 0
            and (test_result["json"] or {}).get("target", {}).get("status") == "passed"
            and (test_result["json"] or {}).get("regression", {}).get("status") == "passed",
            test_result["json"],
        )
        status = run(["status"])
        status_json = status["json"] or {}
        state = status_json.get("state") or {}
        check(
            "powercontext_state_reload",
            status["exit_code"] == 0
            and bool(state)
            and "patch_applied" in state.get("completed", [])
            and state.get("validation", {}).get("target") == "passed"
            and state.get("validation", {}).get("regression") == "passed"
            and bool(status_json.get("prepared_contains_state")),
            state,
        )
        spec_update = run(["spec-update", "--file", str(self.paths.updated_spec_file)])
        check("spec_update_exit", spec_update["exit_code"] == 0, spec_update["exit_code"])
        context2 = run(["context", "--task", TASK_DESCRIPTION])
        ctx2 = context2["json"] or {}
        check(
            "context_uses_updated_spec",
            context2["exit_code"] == 0
            and ctx2.get("spec_marker") == "v2"
            and "REQ-PRICE-STRICT" in ctx2.get("context_text", "")
            and ctx2.get("spec_version") != ctx1.get("spec_version"),
            {"marker": ctx2.get("spec_marker"), "version": ctx2.get("spec_version")},
        )
        before_after = run(["before-after"])
        before_after_json = before_after["json"] or {}
        check(
            "target_tests_fail_before_pass_after",
            before_after["exit_code"] == 0
            and before_after_json.get("before", {}).get("target_exit") != 0
            and before_after_json.get("after", {}).get("target_exit") == 0,
            before_after_json,
        )
        check(
            "regression_tests_pass_before_and_after",
            before_after_json.get("before", {}).get("regression_exit") == 0
            and before_after_json.get("after", {}).get("regression_exit") == 0,
            before_after_json,
        )
        before_log = (records / "test_before_target.log").read_text(encoding="utf-8", errors="replace")
        check(
            "before_failure_is_functional",
            "TypeError" in before_log and "min_price" in before_log,
            before_log[-800:],
        )
        all_passed = all(item["passed"] for item in checks)
        result = {
            "ok": all_passed,
            "step": "validate",
            "checks": checks,
            "commands": entries,
            "context_v1": str(records / "context_v1.json"),
        }
        (records / "validation.log").write_text(
            "\n\n".join(
                [
                    f"# {entry['command']}\nexit_code: {entry['exit_code']}\n\n"
                    f"## stdout\n{entry['stdout']}\n\n## stderr\n{entry['stderr']}"
                    for entry in entries
                ]
            ),
            encoding="utf-8",
        )
        self._write_json("validation.json", result)
        return result

    def _write_queries(self) -> None:
        path = self.paths.records_dir / "seekdb_queries.log"
        path.write_text(
            "\n".join(json.dumps(query, ensure_ascii=False) for query in self.store.queries) + "\n",
            encoding="utf-8",
        )

    def _write_json(self, name: str, payload: dict[str, Any]) -> None:
        path = self.paths.records_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.paths.repo_root).as_posix()
        except ValueError:
            return path.as_posix()


def _merge_state(
    base: dict[str, Any],
    *,
    completed: list[str] | None = None,
    pending: list[str] | None = None,
    validation: dict[str, Any] | None = None,
    before_after: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    state = dict(base) if base else {"task_id": TASK_ID}
    state.setdefault("task_id", TASK_ID)
    state["completed"] = list(
        dict.fromkeys([*(state.get("completed") or []), *(completed or [])])
    )
    if pending is not None:
        state["pending"] = list(pending)
    else:
        state.setdefault("pending", [])
    if validation:
        merged = dict(state.get("validation") or {})
        merged.update(validation)
        state["validation"] = merged
    if before_after:
        merged = dict(state.get("validation") or {})
        merged["before_after"] = before_after
        state["validation"] = merged
    state.update({key: value for key, value in extra.items() if value is not None})
    state["updated_at"] = _now()
    return state


def _copy_project(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    shutil.copytree(source / "inventory", target / "inventory")
    shutil.copytree(source / "tests", target / "tests")


def _run_pytest(workspace: Path, test_file: str, log_path: Path) -> tuple[int, str]:
    command = [sys.executable, "-m", "pytest", f"tests/{test_file}", "-q"]
    started = _now()
    code_path = workspace / "inventory" / "search.py"
    code_version = (
        "sha256:" + hashlib.sha256(code_path.read_bytes()).hexdigest()[:12]
        if code_path.is_file()
        else "missing"
    )
    proc = subprocess.run(
        command,
        cwd=str(workspace),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    output = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
    log_path.write_text(
        "\n".join(
            [
                f"command: {' '.join(command)}",
                f"cwd: {workspace}",
                f"code_version: {code_version}",
                f"exit_code: {proc.returncode}",
                f"python: {sys.version.split()[0]}",
                f"started_at: {started}",
                "",
                output,
            ]
        ),
        encoding="utf-8",
    )
    return proc.returncode, output


def _write_combined_log(
    path: Path,
    phase: str,
    target_output: str,
    regression_output: str,
    target_code: int,
    regression_code: int,
) -> None:
    path.write_text(
        "\n".join(
            [
                f"phase: {phase}",
                f"target_exit_code: {target_code}",
                f"regression_exit_code: {regression_code}",
                "",
                "===== target: tests/test_price_filter.py =====",
                target_output,
                "",
                "===== regression: tests/test_regression.py =====",
                regression_output,
            ]
        ),
        encoding="utf-8",
    )
