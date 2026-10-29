---
kind: spec
status: accepted
---

# Inventory filter contract

`list_inventory(items, warehouse=None, supplier=None)` returns inventory in the
same order as the input. If warehouse or supplier is supplied, return only
items matching every supplied filter. With neither filter, return all items.
Existing warehouse-only calls remain valid. No matches returns an empty list.
