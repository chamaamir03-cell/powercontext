from collections.abc import Iterable

from .models import Product

SORT_CHOICES = ("relevance", "price_asc", "price_desc", "rating_desc")


def search_products(
    products: Iterable[Product],
    *,
    query: str | None = None,
    category: str | None = None,
    sort: str = "relevance",
    page: int = 1,
    page_size: int = 10,
) -> list[Product]:
    if page < 1:
        raise ValueError("page must be >= 1")
    if page_size < 1:
        raise ValueError("page_size must be >= 1")
    if sort not in SORT_CHOICES:
        raise ValueError(f"unsupported sort: {sort}")

    results = list(products)

    if query is not None:
        needle = query.strip().lower()
        if needle:
            results = [
                product
                for product in results
                if needle in product.name.lower() or needle in product.category.lower()
            ]

    if category is not None:
        results = [product for product in results if product.category == category]

    if sort == "price_asc":
        results.sort(key=lambda product: (product.price_cents, product.sku))
    elif sort == "price_desc":
        results.sort(key=lambda product: (-product.price_cents, product.sku))
    elif sort == "rating_desc":
        results.sort(key=lambda product: (-product.rating, product.sku))
    else:
        results.sort(key=lambda product: (-product.rating, product.price_cents, product.sku))

    start = (page - 1) * page_size
    stop = start + page_size
    return results[start:stop]
