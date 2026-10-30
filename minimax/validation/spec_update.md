# Inventory Search Specification

SPEC_VERSION: v2

## Scope

`inventory.search.search_products` is the single search entry point for the demo
inventory project. It accepts an iterable of `Product` records and returns the
matching page as a list.

## Functional requirements

- REQ-SEARCH-QUERY: `query` matches product name or category, case-insensitively.
- REQ-SEARCH-CATEGORY: `category` is an exact match when supplied.
- REQ-SEARCH-SORT: supported values are `relevance`, `price_asc`, `price_desc`,
  and `rating_desc`; unknown values raise `ValueError`.
- REQ-SEARCH-PAGE: `page` and `page_size` are one-based positive integers;
  invalid values raise `ValueError`.
- REQ-PRICE-BOUNDS: add optional keyword arguments `min_price` and `max_price`.
  Prices are integer cents. Both bounds are inclusive. A missing bound means
  unbounded on that side.
- REQ-PRICE-RANGE: when both bounds are present, `min_price` must be less than
  or equal to `max_price`; otherwise raise `ValueError`.
- REQ-PRICE-VALIDATION: bounds must be integers (not `bool`) and must be
  non-negative; invalid values raise `ValueError`.
- REQ-PRICE-STRICT: newly clarified in v2: reject `bool`, `float`, and `str`
  explicitly, allow zero, and return an empty list when no product matches.
- REQ-PRICE-COMBINE: price bounds combine with `query`, `category`, sorting, and
  pagination without changing their existing behavior.

## Verification

- Target tests: `tests/test_price_filter.py`
- Regression tests: `tests/test_regression.py`

The target tests are expected to fail before the price filter exists because the
keyword arguments are not accepted. They must pass after the change, with every
regression test still passing.
