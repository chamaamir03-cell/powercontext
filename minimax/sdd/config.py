from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
MINIMAX_DIR = PACKAGE_DIR.parent
REPO_ROOT = MINIMAX_DIR.parent

TASK_ID = "inventory-price-filter"
TASK_DESCRIPTION = (
    "Add optional min_price and max_price filters to inventory.search.search_products; "
    "keep the existing query, category, sort and pagination behavior."
)
SCOPE_TITLE = "Inventory price filter SDD task"
SCOPE_KEY = "minimax-sdd:inventory-price-filter"
STATE_PREFIX = "SDD_STATE "

REQUIRED_DOC_IDS = {
    "spec": ("spec:specs/inventory_search.md",),
    "code": ("code:baseline/inventory/search.py",),
    "test": (
        "test:baseline/tests/test_price_filter.py",
        "test:baseline/tests/test_regression.py",
    ),
}
REQUIRED_SOURCES = (
    "minimax/specs/inventory_search.md",
    "minimax/baseline/inventory/search.py",
    "minimax/baseline/tests/test_price_filter.py",
    "minimax/baseline/tests/test_regression.py",
)


@dataclass(frozen=True)
class Paths:
    root: Path = MINIMAX_DIR
    repo_root: Path = REPO_ROOT
    state_dir: Path = MINIMAX_DIR / ".state"
    seekdb_dir: Path = MINIMAX_DIR / ".state" / "seekdb"
    powercontext_db: Path = MINIMAX_DIR / ".state" / "powercontext.db"
    scheduler_db: Path = MINIMAX_DIR / ".state" / "scheduler.db"
    work_dir: Path = MINIMAX_DIR / ".work"
    records_dir: Path = MINIMAX_DIR / "records"
    specs_dir: Path = MINIMAX_DIR / "specs"
    baseline_dir: Path = MINIMAX_DIR / "baseline"
    demo_dir: Path = MINIMAX_DIR / "demo_project"
    patches_dir: Path = MINIMAX_DIR / "patches"
    validation_dir: Path = MINIMAX_DIR / "validation"
    spec_file: Path = MINIMAX_DIR / "specs" / "inventory_search.md"
    updated_spec_file: Path = MINIMAX_DIR / "validation" / "spec_update.md"
    patch_file: Path = MINIMAX_DIR / "patches" / "price_filter.patch"
    prompt_file: Path = MINIMAX_DIR / "records" / "minimax_prompt.txt"

    def ensure_state_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.seekdb_dir.mkdir(parents=True, exist_ok=True)
        self.records_dir.mkdir(parents=True, exist_ok=True)


DEFAULT_PATHS = Paths()
