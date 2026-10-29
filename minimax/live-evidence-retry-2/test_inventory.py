import unittest

from inventory import list_inventory


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.items = [
            {"id": 3, "warehouse": "north", "supplier": "alpha"},
            {"id": 1, "warehouse": "south", "supplier": "alpha"},
            {"id": 2, "warehouse": "north", "supplier": "beta"},
        ]

    def test_default_preserves_order(self):
        self.assertEqual(list_inventory(self.items), self.items)

    def test_warehouse_filter(self):
        self.assertEqual(
            [item["id"] for item in list_inventory(self.items, warehouse="north")],
            [3, 2],
        )

    def test_supplier_filter(self):
        self.assertEqual(
            [item["id"] for item in list_inventory(self.items, supplier="alpha")],
            [3, 1],
        )

    def test_combined_filters(self):
        self.assertEqual(
            [item["id"] for item in list_inventory(
                self.items, warehouse="north", supplier="alpha"
            )],
            [3],
        )

    def test_no_matches(self):
        self.assertEqual(list_inventory(self.items, supplier="missing"), [])


if __name__ == "__main__":
    unittest.main()
