from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import TASK_DESCRIPTION
from .workflow import Workflow


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minimax-sdd",
        description="Spec-Driven Development context module for the MiniMax demo task.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Import specs, code, and tests into seekdb and start task state.")

    context = sub.add_parser("context", help="Build and print the task context.")
    context.add_argument("--task", default=TASK_DESCRIPTION)

    apply = sub.add_parser("apply", help="Apply the MiniMax patch to a clean baseline workspace.")
    apply.add_argument("--mode", choices=("replay", "live"), default="replay")
    apply.add_argument("--workspace", default=None)
    apply.add_argument("--prompt-file", default=None)

    test = sub.add_parser("test", help="Run target and regression tests in the applied workspace.")
    test.add_argument("--workspace", default=None)

    sub.add_parser("before-after", help="Prove target tests fail before and pass after the patch.")

    spec = sub.add_parser("spec-update", help="Replace the spec in seekdb with the updated sample.")
    spec.add_argument("--file", default=None)

    sub.add_parser("status", help="Read task state from PowerContext in a new process.")

    reset = sub.add_parser("reset", help="Delete generated seekdb/PowerContext/workspace state.")
    reset.add_argument("--yes", action="store_true")

    sub.add_parser("validate", help="Run the end-to-end acceptance checks.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "reset" and not args.yes:
        print(json.dumps({"ok": False, "error": "pass --yes to delete generated state"}, ensure_ascii=False))
        return 2
    workflow = Workflow(open_store=args.command != "validate")
    try:
        if args.command == "init":
            result: dict[str, Any] = workflow.init()
        elif args.command == "context":
            result = workflow.context(args.task)
        elif args.command == "apply":
            result = workflow.apply(
                mode=args.mode,
                workspace=args.workspace,
                prompt_file=args.prompt_file,
            )
        elif args.command == "test":
            result = workflow.test(workspace=args.workspace)
        elif args.command == "before-after":
            result = workflow.before_after()
        elif args.command == "spec-update":
            result = workflow.spec_update(args.file)
        elif args.command == "status":
            result = workflow.status()
        elif args.command == "reset":
            result = workflow.reset()
        elif args.command == "validate":
            result = workflow.validate()
        else:
            raise RuntimeError(f"unknown command: {args.command}")
    except Exception as error:
        print(
            json.dumps(
                {"ok": False, "command": args.command, "error": f"{type(error).__name__}: {error}"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    finally:
        try:
            workflow.close()
        except Exception:
            pass
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
