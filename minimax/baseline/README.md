# Baseline snapshot

`baseline/` is the demo project **before** the MiniMax change. It exists so the
acceptance flow can prove the same target tests fail before the patch and pass
after it.

```bash
python -m pytest tests/test_price_filter.py -q   # expected: 12 failed
python -m pytest tests/test_regression.py -q     # expected: 12 passed
```

The after snapshot is `../demo_project/`; the diff is
`../patches/price_filter.patch`.
