from __future__ import annotations

import difflib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BEFORE = ROOT / "baseline" / "inventory" / "search.py"
AFTER = ROOT / "demo_project" / "inventory" / "search.py"
PATCH = ROOT / "patches" / "price_filter.patch"


def main() -> None:
    before = BEFORE.read_text(encoding="utf-8").splitlines(keepends=True)
    after = AFTER.read_text(encoding="utf-8").splitlines(keepends=True)
    patch = "".join(
        difflib.unified_diff(
            before,
            after,
            fromfile="a/inventory/search.py",
            tofile="b/inventory/search.py",
            n=3,
        )
    )
    PATCH.parent.mkdir(parents=True, exist_ok=True)
    PATCH.write_text(patch, encoding="utf-8", newline="\n")
    print(f"{PATCH} ({len(patch)} bytes)")


if __name__ == "__main__":
    main()
