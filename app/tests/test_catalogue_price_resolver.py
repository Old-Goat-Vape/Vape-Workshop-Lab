from __future__ import annotations

import unittest
from unittest import mock

from services import catalogue_price_resolver


def _row(fid: int, brand: str, name: str, price: float) -> dict:
    return {
        "id": fid,
        "brand": brand,
        "name": name,
        "price_per_ml": price,
        "category": "Flavor",
        "is_nicotine": 0,
        "active": 1,
    }


class CataloguePriceResolverTests(unittest.TestCase):
    def test_inw_biscuit_resolves_to_priced_concentrate(self):
        catalogue = [
            _row(1, "INW", "Biscuit", 0.0),
            _row(2, "INW", "Biscuit Concentrate", 3.90),
            _row(3, "Inawera", "Biscuit", 0.0),
        ]
        ing = {"ingredient_id": 1, "brand": "INW", "name": "Biscuit", "price_per_ml": 0.0}
        result = catalogue_price_resolver.resolve_price_item(ing, catalogue)
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(int(result["catalogue_id"]), 2)
        self.assertAlmostEqual(float(result["price_per_ml"]), 3.90)
        self.assertFalse(result["missing_price"])

    def test_inw_and_inawera_brand_aliases(self):
        self.assertEqual(
            catalogue_price_resolver.normalise_brand("INW"),
            catalogue_price_resolver.normalise_brand("Inawera"),
        )

    def test_cap_vanilla_custard_resolves_priced_item(self):
        catalogue = [
            _row(10, "CAP", "Vanilla Custard", 0.0),
            _row(11, "Capella", "Vanilla Custard V2", 2.50),
        ]
        ing = {"ingredient_id": 10, "brand": "CAP", "name": "Vanilla Custard", "price_per_ml": 0.0}
        result = catalogue_price_resolver.resolve_price_item(ing, catalogue)
        self.assertEqual(result["status"], "resolved")
        self.assertAlmostEqual(float(result["price_per_ml"]), 2.50)

    def test_fw_butter_pecan_resolves_priced_item(self):
        catalogue = [
            _row(20, "FW", "Butter Pecan", 0.0),
            _row(21, "Flavor West", "Butter Pecan", 1.85),
        ]
        ing = {"ingredient_id": 20, "brand": "Flavor West", "name": "Butter Pecan", "price_per_ml": 0.0}
        result = catalogue_price_resolver.resolve_price_item(ing, catalogue)
        self.assertEqual(result["status"], "resolved")
        self.assertAlmostEqual(float(result["price_per_ml"]), 1.85)

    def test_unpriced_not_preferred_over_priced_compatible(self):
        catalogue = [
            _row(1, "INW", "Biscuit", 0.0),
            _row(2, "INW", "Biscuit Concentrate", 3.90),
        ]
        ing = {"ingredient_id": 99, "brand": "INW", "name": "Biscuit", "price_per_ml": 0.0}
        result = catalogue_price_resolver.resolve_price_item(ing, catalogue)
        self.assertNotEqual(int(result["catalogue_id"]), 1)
        self.assertGreater(float(result["price_per_ml"] or 0), 0)

    def test_missing_when_no_priced_catalogue_item(self):
        catalogue = [
            _row(1, "INW", "Biscuit", 0.0),
            _row(2, "Inawera", "Biscuit", 0.0),
        ]
        ing = {"ingredient_id": 1, "brand": "INW", "name": "Biscuit", "price_per_ml": 0.0}
        result = catalogue_price_resolver.resolve_price_item(ing, catalogue)
        self.assertEqual(result["status"], "missing")
        self.assertTrue(result["missing_price"])

    def test_manual_catalogue_override(self):
        catalogue = [
            _row(1, "INW", "Biscuit", 0.0),
            _row(2, "INW", "Biscuit Concentrate", 3.90),
            _row(3, "FA", "Biscuit", 4.50),
        ]
        ing = {"ingredient_id": 1, "brand": "INW", "name": "Biscuit", "price_per_ml": 0.0}
        result = catalogue_price_resolver.resolve_price_item(ing, catalogue, override_catalogue_id=3)
        self.assertEqual(result["status"], "manual")
        self.assertEqual(int(result["catalogue_id"]), 3)
        self.assertAlmostEqual(float(result["price_per_ml"]), 4.50)

    def test_freebase_nicotine_uses_canonical_blck_variant(self):
        catalogue = [
            _row(100, "AuNic", "Freebase Nicotine VG - 1000ml", 1.20),
            _row(101, "AuNic", "Salt Nicotine VG - 1000ml", 1.25),
        ]
        ing = {
            "ingredient_id": 50,
            "brand": "AuNic",
            "name": "Freebase 100mg",
            "price_per_ml": 0.0,
            "is_nicotine": 1,
        }
        fake = {
            "label": "Gold Nic · 100mg/ml PG · 1 Litre",
            "product_title": "Gold Nic (Nicotine)",
            "variant_title": "100mg/ml PG 1 Litre",
            "price_per_ml": 1.79,
            "pack_price": 1790.0,
            "pack_volume_ml": 1000.0,
            "source_variant_id": "188698296334",
            "last_scraped": "2026-09-16 12:00:00",
            "product_url": "https://blck.co.za/products/nicotine-blck-flavour",
            "status": "active",
        }
        with mock.patch(
            "services.blck_canonical_costing_service.resolve_nicotine_price_per_ml",
            return_value=fake,
        ):
            result = catalogue_price_resolver.resolve_price_item(ing, catalogue)
        self.assertIsNone(result["catalogue_id"])
        self.assertAlmostEqual(float(result["price_per_ml"]), 1.79)
        self.assertIn("Gold Nic", result["resolved_label"])

    def test_freebase_falls_back_to_legacy_catalogue_when_canonical_missing(self):
        catalogue = [
            _row(100, "AuNic", "Freebase Nicotine VG - 1000ml", 1.20),
            _row(101, "AuNic", "Salt Nicotine VG - 1000ml", 1.25),
        ]
        ing = {
            "ingredient_id": 50,
            "brand": "AuNic",
            "name": "Freebase 100mg",
            "price_per_ml": 0.0,
            "is_nicotine": 1,
        }
        with mock.patch(
            "services.blck_canonical_costing_service.resolve_nicotine_price_per_ml",
            return_value=None,
        ):
            result = catalogue_price_resolver.resolve_price_item(ing, catalogue)
        self.assertEqual(int(result["catalogue_id"]), 100)
        self.assertAlmostEqual(float(result["price_per_ml"]), 1.20)

    def test_name_normalisation_strips_concentrate_suffix(self):
        self.assertEqual(
            catalogue_price_resolver.normalise_flavour_name("Biscuit Concentrate"),
            catalogue_price_resolver.normalise_flavour_name("Biscuit"),
        )


if __name__ == "__main__":
    unittest.main()
