Done. Price filtering is implemented and the full suite is green.

## Code change

Only [`inventory/search.py`](C:\Users\asus\AppData\Local\Temp\opencode\qoder-work\minimax\.work\live\inventory\search.py) was modified — tests, models, and repository are untouched, and no dependencies were added.

**1. New optional keyword arguments** (`inventory/search.py:36-37`) — `min_price: int | None = None` and `max_price: int | None = None`, appended after the existing parameters so every current call signature and default is unchanged.

**2. Validation helper** (`inventory/search.py:8-21`) — `_validate_price_bound(name, value)` enforces REQ-PRICE-VALIDATION:
- `None` → unbounded on that side (returns `None`)
- `bool` → rejected explicitly, before the `int` check, since `bool` subclasses `int` and `True`/`False` would otherwise pass as 0/1
- non-`int` (float, str) → rejected via `not isinstance(value, int)`
- negative → rejected

All failures raise `ValueError`.

**3. Range check** (`inventory/search.py:44-45`) — when both bounds are present, `min_price > max_price` raises `ValueError`. This runs *after* type validation, so `"5000"` vs `9000` raises `ValueError` rather than a `TypeError` from comparing a `str` to an `int`.

**4. Filtering** (`inventory/search.py:61-64`) — inclusive `>=` / `<=` comparisons on `price_cents`, applied in the existing filter chain after `query` and `category` and before sorting/pagination. Since it is a pure conjunctive filter, it composes with query, category, sorting, and pagination without altering their behavior (REQ-PRICE-COMBINE).

The file kept its original LF line endings and existing code style (keyword-only args, `ValueError` messages).

## Test result

Ran from the workspace root as specified:

```
py -3.14 -m pytest tests/test_price_filter.py tests/test_regression.py -q
```

**`24 passed in 0.07s`** — 12 target tests from `test_price_filter.py` (including all 7 invalid-bound parametrizations: `6000-5000`, `-1-5000`, `0--1`, `True-5000`, `5000-False`, `10.5-5000`, `5000-9000`) and all 12 regression tests from `test_regression.py`. Zero failures, zero errors.