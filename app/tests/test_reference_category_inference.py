"""Regression examples from the server's reviewed catalogue preview."""

import unittest

from config.reference_category_infer import (
    merge_category_bucket,
    suggest_category_correction,
)


class ReferenceCategoryInferenceTests(unittest.TestCase):
    def assert_category(self, brand, name, current, expected, flag=0, source=None):
        self.assertEqual(merge_category_bucket(brand, name, current, flag), expected)
        corrected, new_flag, _ = suggest_category_correction(
            brand, name, current, flag, source
        )
        self.assertEqual(corrected or current, expected)
        if expected == "Nicotine":
            self.assertEqual(flag if new_flag is None else new_flag, 1)

    def test_clyronic_all_strengths_and_carriers_are_nicotine(self):
        for strength in (36, 100):
            for carrier in ("PG", "VG"):
                for size in ("100ml", "500ml", "2 litres", "5 litres"):
                    with self.subTest(strength=strength, carrier=carrier, size=size):
                        name = f"ClyroNic &#8211; {carrier} &#8211; {strength}mg/ml &#8211; {size}"
                        self.assert_category("Clyrolinx", name, "Nicotine", "Nicotine")
                        self.assert_category("Clyrolinx", name, "Flavor", "Nicotine")

    def test_existing_nicotine_or_positive_flag_is_never_automatically_downgraded(self):
        self.assert_category("Unknown", "PG 100ml", "Nicotine", "Nicotine")
        self.assert_category(
            "Unknown", "Vegetable glycerine", "Base", "Nicotine", flag=1
        )

    def test_supplier_name_alone_does_not_make_a_product_nicotine(self):
        self.assert_category("Nicotine River", "Vanilla", "Flavor", "Flavor")
        self.assert_category(
            "Liquid Nicotine Wholesalers", "Vegetable glycerine", "Base", "Base"
        )

    def test_atomizer_stand_is_hardware_even_for_flavour_vendor_or_vc_source(self):
        for current in ("Hardware", "Flavor"):
            self.assert_category("BLCK", "510 Atomizer Stand", current, "Hardware")
            self.assert_category(
                "BLCK", "510 Atomizer Stand", current, "Hardware", source="VC"
            )

    def test_named_flavours_with_carriers_or_ratios_are_flavours(self):
        examples = {
            "Get Suckered": [
                "Cake Batter (PG)",
                "Cake Batter (VG)",
                "Pumpkin (PG)",
                "Pumpkin (VG)",
                "Pumpkin Pie (PG)",
                "Pumpkin Pie (VG)",
            ],
            "Kandi Hed": [
                "Cake Batter(VG)",
                "Cosmic Fog Sunset(VG)",
                "Milk Bottles(VG)",
            ],
            "One On One": [
                "Aromatic Leaf Tobacco (PG)",
                "Aromatic Leaf Tobacco (VG)",
                "50/50 Bar: Lime & Ice Cream",
                "50/50 Bar:Strawberry and Ice Cream",
                "50/50 Bar:Cherry and Ice Cream",
                "50/50 Bar:Orange and Ice Cream",
                "50/50 Bar:Raspberry and Ice Cream",
            ],
            "Real Flavors": [
                "Cake Batter (VG)",
                "Caramel Cappuccino (VG)",
                "French Vanilla Cappuccino (VG)",
                "Funnel Cake (VG)",
                "Pumpkin Pie (VG)",
                "Tomato (VG)",
            ],
            "Diy Flavor Shack": ["50/50 Bar"],
        }
        for brand, names in examples.items():
            for name in names:
                with self.subTest(brand=brand, name=name):
                    self.assert_category(brand, name, "Hardware", "Flavor")
                    self.assert_category(brand, name, "Flavor", "Flavor")

    def test_real_base_products_still_classify_as_base(self):
        for name in (
            "Vegetable glycerine (VG) - 100ml in glass bottle",
            "Vegetable glycerin",
            "Propylene glycol",
            "PG 100ml",
            "VG (USP) 1 litre",
            "50/50 base 500ml",
        ):
            with self.subTest(name=name):
                self.assert_category("Clyrolinx", name, "Hardware", "Base")

    def test_unknown_carrier_flavour_is_not_automatically_called_a_base(self):
        self.assert_category("Unknown", "Cake Batter (VG)", "Flavor", "Flavor")
        self.assert_category("Unknown", "50/50 Bar", "Flavor", "Flavor")


if __name__ == "__main__":
    unittest.main()
