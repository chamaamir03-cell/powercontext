"""Run the competition demo with live MiniMax, seekdb, and PowerContext."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TASK = (
    "Add inventory supplier filtering, combine it with warehouse filtering, "
    "and preserve the original order. Modify inventory.py only; do not modify tests or specifications."
)


def run(command: list[str], cwd: Path, env: dict[str, str], log: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False)
    log.write_text(
        json.dumps({"command": command, "exit_code": result.returncode}, ensure_ascii=False)
        + "\n--- stdout ---\n" + result.stdout + "\n--- stderr ---\n" + result.stderr,
        encoding="utf-8",
    )
    return result


def require_success(result: subprocess.CompletedProcess[str], label: str, log: Path) -> None:
    if result.returncode:
        raise RuntimeError(f"{label} failed (exit {result.returncode}); see {log}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New directory for non-secret evidence.")
    args = parser.parse_args()
    output = args.output.resolve()
    for name in ("MINIMAX_API_KEY", "POWERCONTEXT_URL", "POWERCONTEXT_TOKEN"):
        if not os.environ.get(name):
            parser.error(f"{name} must be set in this terminal.")
    if output.exists():
        parser.error(f"Output already exists: {output}; choose a new directory.")
    output.mkdir(parents=True)

    with tempfile.TemporaryDirectory(prefix="minimax-acceptance-") as temp:
        workspace = Path(temp)
        project = workspace / "project"
        shutil.copytree(ROOT / "minimax" / "demo", project)
        env = os.environ.copy()
        env.pop("ENGINEERING_DATABASE_URL", None)
        env["ENGINEERING_SEEKDB_PATH"] = str(workspace / "seekdb")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        project_id = f"minimax-live-{uuid.uuid4().hex}"
        task_id = "supplier-filter"
        common = ["--project", project_id, "--task-id", task_id]
        cli = [sys.executable, "-m", "minimax.engineering_context"]
        tests = [sys.executable, "-m", "unittest", "-v", "test_inventory"]

        for cmd in (
            ["git", "init", "-q"],
            ["git", "add", "--", "inventory.py", "test_inventory.py", "specs", "decisions"],
        ):
            result = run(cmd, project, env, output / "git-setup.log")
            require_success(result, "Git setup", output / "git-setup.log")

        original = (project / "inventory.py").read_bytes()
        test_bytes = (project / "test_inventory.py").read_bytes()
        (output / "before-inventory.py").write_bytes(original)
        (output / "test_inventory.py").write_bytes(test_bytes)
        baseline = run(tests, project, env, output / "before-tests.log")
        if baseline.returncode == 0 or "FAILED (failures=3)" not in baseline.stderr:
            raise RuntimeError(f"Expected three functional failures; see {output / 'before-tests.log'}")

        prepare = cli + ["prepare", "--root", str(project)] + common + ["--task", TASK]
        result = run(prepare, ROOT, env, output / "context.log")
        require_success(result, "Context preparation", output / "context.log")
        context = json.loads(result.stdout)
        expected = {"inventory.py", "test_inventory.py", "specs/inventory.md", "decisions/filtering.md"}
        paths = {source["path"] for source in context["sources"]}
        if not expected.issubset(paths) or "specs/old-inventory.md" in paths:
            raise RuntimeError(f"Context sources do not match the sample; see {output / 'context.log'}")

        spec_path = project / "specs" / "inventory.md"
        spec_text = spec_path.read_text(encoding="utf-8")
        previous_requirement = "No matches returns an empty list."
        updated_requirement = (
            "No matches returns an empty list. Supplier comparison is exact and case-sensitive."
        )
        if previous_requirement not in spec_text:
            raise RuntimeError("The live demo's original supplier requirement was not found.")
        spec_path.write_text(
            spec_text.replace(previous_requirement, updated_requirement, 1),
            encoding="utf-8",
        )
        refreshed = run(prepare, ROOT, env, output / "spec-refresh.log")
        require_success(refreshed, "Specification re-import", output / "spec-refresh.log")
        refreshed_context = json.loads(refreshed.stdout)
        current_spec = next(
            (
                source for source in refreshed_context["sources"]
                if source["path"] == "specs/inventory.md"
            ),
            None,
        )
        if not current_spec or updated_requirement not in current_spec["excerpt"]:
            raise RuntimeError(
                f"Re-imported context did not include the updated specification; "
                f"see {output / 'spec-refresh.log'}"
            )
        stage_spec = run(
            ["git", "add", "--", "specs/inventory.md"],
            project,
            env,
            output / "spec-stage.log",
        )
        require_success(stage_spec, "Updated specification staging", output / "spec-stage.log")
        context = refreshed_context

        command = cli + [
            "implement", "--root", str(project), *common, "--task", TASK,
            "--test-command", f"{sys.executable} -m unittest -v test_inventory",
        ]
        result = run(command, ROOT, env, output / "implementation.log")
        diff = run(["git", "diff", "--", "inventory.py"], project, env, output / "diff-command.log")
        require_success(diff, "Diff", output / "diff-command.log")
        (output / "change.diff").write_text(diff.stdout, encoding="utf-8")
        (output / "after-inventory.py").write_bytes((project / "inventory.py").read_bytes())
        require_success(result, "Live MiniMax implementation", output / "implementation.log")

        changed = run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            project, env, output / "changed-files.log",
        )
        require_success(changed, "Changed files", output / "changed-files.log")
        worktree_changes = [
            line for line in changed.stdout.splitlines()
            if line.startswith("??") or line[1] != " "
        ]
        if worktree_changes != ["AM inventory.py"] or (project / "test_inventory.py").read_bytes() != test_bytes:
            raise RuntimeError("The model modified files other than inventory.py or changed the tests.")
        final = run(tests, project, env, output / "after-tests.log")
        require_success(final, "Independent post-change tests", output / "after-tests.log")

        status = run(cli + ["status", *common], ROOT, env, output / "state.log")
        require_success(status, "PowerContext state read", output / "state.log")
        state = json.loads(status.stdout)["state"]
        if state.get("status") != "completed" or state.get("last_validation", {}).get("result") != "passed":
            raise RuntimeError(f"Missing completed state and passing validation; see {output / 'state.log'}")
        restored = run(prepare, ROOT, env, output / "restored-context.log")
        require_success(restored, "PowerContext state reuse", output / "restored-context.log")
        reloaded = json.loads(restored.stdout)
        if reloaded["task"]["status"] != "completed" or reloaded["context_freshness"]["verification"] != "current":
            raise RuntimeError(f"Task state was not reused as current; see {output / 'restored-context.log'}")
        metadata = {
            "project": project_id,
            "task": TASK,
            "test_command": "python -m unittest -v test_inventory",
            "before_code_sha256": hashlib.sha256(original).hexdigest(),
            "after_code_sha256": hashlib.sha256((project / "inventory.py").read_bytes()).hexdigest(),
            "test_sha256": hashlib.sha256(test_bytes).hexdigest(),
            "database": "seekdb 1.4.0 via pylibseekdb 1.4.0.post2 and PyMySQL",
            "memory": "PowerContext HTTP API",
            "model": state.get("last_implementation", {}).get("model"),
            "request_id": state.get("last_implementation", {}).get("request_id"),
        }
        (output / "run-metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(f"Live demo passed. Evidence saved in {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"live-demo: {error}", file=sys.stderr)
        raise SystemExit(1) from error
