# Validation task: inventory price filter

## Task description

Add optional `min_price` and `max_price` filtering to
`inventory.search.search_products` in the demo inventory project.

## Required sources

- Spec: `minimax/specs/inventory_search.md`
- Code: `minimax/baseline/inventory/search.py`
- Target tests: `minimax/baseline/tests/test_price_filter.py`
- Regression tests: `minimax/baseline/tests/test_regression.py`

## Initial data

The demo repository contains eight in-memory products in
`inventory/repository.py`. Prices are integer cents.

## Steps

1. Reset generated state.
2. Import specs, code, and tests into seekdb.
3. Build the task context and save `context_built` in PowerContext.
4. Apply the recorded MiniMax patch to a clean baseline work copy.
5. Run target and regression tests.
6. Restart the process and read the saved task state.
7. Import the updated spec and rebuild the context.

## Expected result

- Context contains the four required sources above.
- Target tests fail on the baseline and pass after the patch.
- Regression tests pass before and after.
- PowerContext state is readable after a process restart.
- Rebuilt context contains `SPEC_VERSION: v2` after the spec update.

## Reset

```bash
python minimax/run.py reset --yes
```
