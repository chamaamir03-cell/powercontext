from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "records" / "minimax_prompt.txt"
CONTEXT_JSON = ROOT / "records" / "context.json"


def load_context() -> tuple[dict, str]:
    if not CONTEXT_JSON.is_file():
        raise SystemExit(
            f"{CONTEXT_JSON} is missing; run `python minimax/run.py context` before building the prompt"
        )
    data = json.loads(CONTEXT_JSON.read_text(encoding="utf-8"))
    state = data.get("task_state")
    context_text = data.get("context_text")
    if not isinstance(state, dict) or not context_text:
        raise SystemExit(f"{CONTEXT_JSON} does not contain task_state/context_text")
    return state, context_text


def main() -> None:
    state, context_text = load_context()
    prompt = f"""You are MiniMax Code acting as the coding agent in a Spec-Driven Development workflow.
Use the task context below. Modify only the demo inventory project in the current workspace.

TASK
----
Add optional `min_price` and `max_price` filtering to
`inventory.search.search_products`, and make the target tests pass without
breaking the regression tests.

WORKSPACE
---------
- inventory/search.py: the only production file you should modify.
- inventory/models.py and inventory/repository.py: read-only data model.
- tests/test_price_filter.py: target tests; do not edit.
- tests/test_regression.py: regression tests; do not edit.
- Do not add dependencies. Do not edit the tests.

CONTEXT FROM SDD MODULE
-----------------------
The following context was produced by `python minimax/run.py context` from
seekdb documents and PowerContext state. Use it as the source of truth.

{context_text}

Saved task state:
```json
{json.dumps(state, ensure_ascii=False, indent=2)}
```

ACCEPTANCE
----------
- `min_price` and `max_price` are optional keyword arguments.
- Prices are integer cents; bounds are inclusive.
- Reject bool, float, str, and negative values with ValueError.
- Raise ValueError when min_price > max_price.
- Keep query, category, sort, and pagination behavior unchanged.
- Run `py -3.14 -m pytest tests/test_price_filter.py tests/test_regression.py -q`
  from the workspace and report the result.
- Finish with a short summary of the code change and the exact test result.
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(prompt, encoding="utf-8")
    print(OUT)
    print(len(prompt), "chars")


if __name__ == "__main__":
    main()
