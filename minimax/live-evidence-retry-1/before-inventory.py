def list_inventory(items, warehouse=None, supplier=None):
    """Return inventory in its original order, optionally filtered by warehouse."""
    return [
        item for item in items
        if warehouse is None or item["warehouse"] == warehouse
    ]
