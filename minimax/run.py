from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
for candidate in (ROOT, REPO_ROOT / "src"):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from sdd.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
