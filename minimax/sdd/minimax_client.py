from __future__ import annotations

import difflib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MiniMaxError(RuntimeError):
    pass


def _run(command: list[str], *, cwd: Path, stdin: str | None = None, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def apply_replay(workspace: Path, patch_path: Path) -> dict[str, Any]:
    if not workspace.is_dir():
        raise MiniMaxError(f"workspace does not exist: {workspace}")
    if not patch_path.is_file():
        raise MiniMaxError(f"patch does not exist: {patch_path}")
    git = shutil.which("git")
    if git:
        check = _run([git, "apply", "--check", str(patch_path)], cwd=workspace, timeout=60)
        if check.returncode != 0:
            raise MiniMaxError(f"git apply --check failed: {check.stderr.strip()}")
        applied = _run([git, "apply", str(patch_path)], cwd=workspace, timeout=60)
        if applied.returncode != 0:
            raise MiniMaxError(f"git apply failed: {applied.stderr.strip()}")
        return {"mode": "replay", "patch": str(patch_path), "returncode": applied.returncode}
    patch = shutil.which("patch")
    if not patch:
        raise MiniMaxError("neither git nor patch is available")
    with patch_path.open("r", encoding="utf-8") as handle:
        applied = subprocess.run(
            [patch, "-p1"],
            cwd=str(workspace),
            stdin=handle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    if applied.returncode != 0:
        raise MiniMaxError(f"patch failed: {applied.stderr.strip()}")
    return {"mode": "replay", "patch": str(patch_path), "returncode": applied.returncode}


def _mcode_command() -> list[str]:
    configured = os.environ.get("MCODE_BIN")
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    for name in ("mcode", "mcode.cmd"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    appdata = os.environ.get("APPDATA")
    if appdata:
        fallback = Path(appdata) / "npm" / "node_modules" / "@minimax-ai" / "code" / "cli.js"
        if fallback.is_file():
            candidates.append(str(fallback))
    for candidate in candidates:
        path = Path(candidate)
        if path.suffix.lower() == ".js":
            node = shutil.which("node") or "node"
            return [node, str(path)]
        if path.suffix.lower() in {".cmd", ".bat"}:
            return ["cmd.exe", "/c", str(path)]
        if path.is_file():
            return [str(path)]
    raise MiniMaxError(
        "MiniMax Code CLI not found. Install it, run `mcode login --region cn`, "
        "or set MCODE_BIN to the CLI path."
    )


def apply_live(
    workspace: Path,
    prompt: str,
    records_dir: Path,
    *,
    timeout: str = "10m",
    max_steps: int = 30,
) -> dict[str, Any]:
    if not workspace.is_dir():
        raise MiniMaxError(f"workspace does not exist: {workspace}")
    records_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = records_dir / "minimax_prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    command = [
        *_mcode_command(),
        "exec",
        "--cwd",
        str(workspace),
        "--permission",
        "full",
        "--output-format",
        "json",
        "--timeout",
        timeout,
        "--max-steps",
        str(max_steps),
        "--output-last-message",
        str(records_dir / "minimax_last_message.md"),
        "--input",
        "-",
    ]
    started = time.time()
    completed = _run(command, cwd=workspace, stdin=prompt, timeout=1200)
    duration = time.time() - started
    (records_dir / "minimax_response.json").write_text(completed.stdout, encoding="utf-8")
    (records_dir / "minimax_stderr.log").write_text(completed.stderr, encoding="utf-8")
    log = {
        "command": command,
        "cwd": str(workspace),
        "returncode": completed.returncode,
        "duration_seconds": round(duration, 3),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "prompt_path": str(prompt_path),
        "stdout_path": str(records_dir / "minimax_response.json"),
        "stderr_path": str(records_dir / "minimax_stderr.log"),
    }
    (records_dir / "minimax_live.log").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if completed.returncode != 0:
        raise MiniMaxError(
            f"MiniMax exec failed with exit code {completed.returncode}; see "
            f"{records_dir / 'minimax_stderr.log'}"
        )
    _write_live_patch(workspace, records_dir / "live.patch")
    return {"mode": "live", **log}


def _write_live_patch(workspace: Path, patch_path: Path) -> None:
    before = Path(__file__).resolve().parents[1] / "baseline" / "inventory" / "search.py"
    after = workspace / "inventory" / "search.py"
    if not before.is_file() or not after.is_file():
        return
    patch = "".join(
        difflib.unified_diff(
            before.read_text(encoding="utf-8").splitlines(keepends=True),
            after.read_text(encoding="utf-8").splitlines(keepends=True),
            fromfile="a/inventory/search.py",
            tofile="b/inventory/search.py",
            n=3,
        )
    )
    patch_path.write_text(patch, encoding="utf-8", newline="\n")


def apply(
    mode: str,
    workspace: Path,
    *,
    patch_path: Path,
    records_dir: Path,
    prompt: str | None = None,
) -> dict[str, Any]:
    if mode == "replay":
        return apply_replay(workspace, patch_path)
    if mode == "live":
        if not prompt:
            raise MiniMaxError("live mode requires a prompt")
        return apply_live(workspace, prompt, records_dir)
    raise MiniMaxError(f"unsupported mode: {mode}")
