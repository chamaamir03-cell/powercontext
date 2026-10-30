import pytest

from inventory import all_products, search_products


def skus(products):
    return [product.sku for product in products]


def test_query_is_case_insensitive():
    assert skus(search_products(all_products(), query="KEYBOARD")) == ["SKU-001"]


def test_category_filter_preserves_relevance_order():
    assert skus(search_products(all_products(), category="accessory")) == [
        "SKU-004",
        "SKU-003",
        "SKU-006",
        "SKU-008",
    ]


def test_price_ascending_sort():
    assert skus(search_products(all_products(), sort="price_asc")) == [
        "SKU-008",
        "SKU-006",
        "SKU-002",
        "SKU-003",
        "SKU-005",
        "SKU-001",
        "SKU-004",
        "SKU-007",
    ]


def test_rating_sort():
    assert skus(search_products(all_products(), sort="rating_desc")) == [
        "SKU-007",
        "SKU-001",
        "SKU-004",
        "SKU-002",
        "SKU-003",
        "SKU-006",
        "SKU-005",
        "SKU-008",
    ]


def test_pagination_keeps_existing_order():
    assert skus(search_products(all_products(), page=2, page_size=3)) == [
        "SKU-002",
        "SKU-003",
        "SKU-006",
    ]


def test_blank_query_returns_everything():
    assert len(search_products(all_products(), query="   ")) == 8


@pytest.mark.parametrize("sort", ["newest", "", "PRICE_ASC"])
def test_unknown_sort_raises_value_error(sort):
    with pytest.raises(ValueError):
        search_products(all_products(), sort=sort)


@pytest.mark.parametrize(("page", "page_size"), [(0, 10), (1, 0), (-1, 5)])
def test_invalid_pagination_raises_value_error(page, page_size):
    with pytest.raises(ValueError):
        search_products(all_products(), page=page, page_size=page_size)
