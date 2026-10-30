# Demo inventory project

This is the business project used by the SDD demonstration.

```bash
python -m pytest tests/test_price_filter.py -q
python -m pytest tests/test_regression.py -q
```

`inventory/search.py` is the file MiniMax modified. The target tests cover
`min_price`/`max_price`; the regression tests cover the pre-existing query,
category, sort, and pagination behavior.
