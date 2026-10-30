import pytest

from inventory import all_products, search_products


def skus(products):
    return [product.sku for product in products]


def test_min_price_is_inclusive():
    result = search_products(all_products(), min_price=5000)
    assert set(skus(result)) == {"SKU-001", "SKU-004", "SKU-005", "SKU-007"}


def test_max_price_is_inclusive():
    result = search_products(all_products(), max_price=3499)
    assert set(skus(result)) == {"SKU-002", "SKU-006", "SKU-008"}
    assert "SKU-002" in skus(result)


def test_price_range_combines_with_query_and_category():
    result = search_products(
        all_products(),
        query="hub",
        category="accessory",
        min_price=3000,
        max_price=6000,
    )
    assert skus(result) == ["SKU-003"]


def test_price_range_respects_sort_and_pagination():
    result = search_products(
        all_products(),
        min_price=2000,
        max_price=9000,
        sort="price_asc",
        page=2,
        page_size=2,
    )
    assert skus(result) == ["SKU-003", "SKU-005"]


def test_price_bounds_allow_zero_and_empty_results():
    assert search_products(all_products(), min_price=0, max_price=0) == []


@pytest.mark.parametrize(
    ("min_price", "max_price"),
    [
        (6000, 5000),
        (-1, 5000),
        (0, -1),
        (True, 5000),
        (5000, False),
        (10.5, 5000),
        ("5000", 9000),
    ],
)
def test_invalid_price_bounds_raise_value_error(min_price, max_price):
    with pytest.raises(ValueError):
        search_products(all_products(), min_price=min_price, max_price=max_price)
