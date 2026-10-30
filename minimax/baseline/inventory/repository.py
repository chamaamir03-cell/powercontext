from .models import Product

PRODUCTS = (
    Product("SKU-001", "Mechanical Keyboard", "keyboard", 8999, 12, 4.7),
    Product("SKU-002", "Wireless Mouse", "mouse", 3499, 30, 4.5),
    Product("SKU-003", "USB-C Hub", "accessory", 4599, 8, 4.3),
    Product("SKU-004", "Monitor Stand", "accessory", 12999, 5, 4.6),
    Product("SKU-005", "Webcam", "camera", 5999, 0, 4.1),
    Product("SKU-006", "Laptop Sleeve", "accessory", 2599, 20, 4.2),
    Product("SKU-007", "Noise Cancelling Headphones", "audio", 19999, 7, 4.8),
    Product("SKU-008", "Desk Mat", "accessory", 1999, 50, 4.0),
)


def all_products() -> list[Product]:
    return list(PRODUCTS)
