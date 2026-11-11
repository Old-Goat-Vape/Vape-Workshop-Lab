"""Supplier-researched product eligibility, including deliberately incorrect legacy labels."""

import unittest
from werkzeug.datastructures import MultiDict
from app import app as flask_app
from config.catalogue_product_policy import product_kind
from config.reference_category_infer import merge_category_bucket
from db import get_db
from models import recipes_repo
from services.stash_service import fetch_stash_quick_add_options
from tests import test_flavour_search as fixtures


class ProductPolicyTests(unittest.TestCase):
    def test_confirmed_finished_liquids_and_powders_are_not_liquid_flavours(self):
        for brand, name, expected in [
            (
                "Clyrolinx",
                "Mango ice 100ml 3mg/ml (It&#8217;s E-liquid)",
                "Finished Liquid",
            ),
            ("Clyrolinx", "Mango ice 30ml 50mg/ml (It’s Nic Salt)", "Finished Liquid"),
            ("Unknown", "Ready-to-vape 0mg Vanilla", "Finished Liquid"),
            ('Unknown','Vanilla 100ml 3mg','Pending Verification'),
            ('Clyrolinx','ClyroNic PG 100mg/ml','Nicotine Product'),
            ('SUPA','Scandinavian Fruits Concentrate','Pending Verification'),
            ("BLCK", "Cap Bottle Opener", "Hardware"),
            ("Bewolk Industries", "SteamCraft AK-47 3.0mm", "Hardware"),
            (
                "Clyrolinx",
                "Arôme. A fragrance similar to Aromatics Elixir (Clinique) 100ml",
                "Fragrance",
            ),
            ("BLCK", "Erythritol Crystals", "Powder Additive"),
            ("Clyrolinx", "Malic acid DL – 250gm", "Powder Additive"),
            ("LCLC", "Sterilizing Fluid (LCL)", "Other Chemical"),
            ("SA", "Scandinavian Fruits Concentrate (S/SUPA)", "Pending Verification"),
            ("SA", "Fruit Hard Candy Concentrate (S/SUPA)", "Pending Verification"),
        ]:
            with self.subTest(name=name):
                self.assertEqual(product_kind(brand, name), expected)

    def test_verified_concentrates_and_liquid_additives_stay_available(self):
        for brand, name in [
            ("Mr Hardwicks", "Breakfast Batter Blended Concentrate (HW) (30ml)"),
            ("SA", "Ice Candies Concentrate (S/SUPA)"),
            ("TFA", "Koolada 10 PG"),
            ("CAP", "Cotton Candy"),
        ]:
            self.assertIsNone(product_kind(brand, name))
        for size in (10, 30, 100):
            self.assertEqual(
                merge_category_bucket(
                    "Clyrolinx", f"Cappuccino &#8211; {size}ml", "Hardware", 0
                ),
                "Flavor",
            )

    def test_clyrolinx_import_skips_finished_liquids_and_does_not_match_nic_in_unicorn(
        self,
    ):
        from scrapers.clyrolinx_scraper import (
            _classify_category_from_slugs,
            parse_flavor_data,
        )

        self.assertEqual(
            _classify_category_from_slugs(
                ["bottles-syringes"], "Unicorn bottle black PET 100ml"
            ),
            "Hardware",
        )
        self.assertEqual(
            _classify_category_from_slugs(
                ["flavour-concentrates-additives"], "Vanilla (VG)"
            ),
            "Flavor",
        )
        self.assertEqual(
            _classify_category_from_slugs(["nicotine"], "ClyroNic PG 100mg/ml"),
            "Nicotine",
        )
        self.assertIsNone(
            parse_flavor_data(
                {
                    "name": "Mango ice 100ml 3mg/ml (It’s E-liquid)",
                    "permalink": "https://clyrolinx.co.za/shop/vape-juice/test",
                    "categories": [],
                }
            )
        )
        self.assertIsNone(
            parse_flavor_data(
                {
                    "name": "A new brand name",
                    "permalink": "https://clyrolinx.co.za/shop/vape-juice/test",
                    "categories": [],
                }
            )
        )


class ProductPickerTests(unittest.TestCase):
    setUp = fixtures.FlavourSearchTests.setUp
    tearDown = fixtures.FlavourSearchTests.tearDown

    def test_admin_review_explains_why_a_flavour_label_is_excluded(self):
        with flask_app.app_context():
            db = get_db()
            self.seed_products(db)
            db.execute("UPDATE users SET role='Master' WHERE id=1")
            db.commit()
        with self.client.session_transaction() as session:
            session["role"] = "Master"
        response = self.client.get("/admin/flavours/cleanup?search=Mango")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Excluded from recipe choices: Finished Liquid", response.data)

    def seed_products(self, db):
        products = [
            (301, "Clyrolinx", "Mango ice 100ml 3mg/ml (It’s E-liquid)"),
            (302, "BLCK", "Cap Bottle Opener"),
            (303, "BLCK", "Erythritol Crystals"),
            (304, "SA", "Scandinavian Fruits Concentrate (S/SUPA)"),
            (305, "Clyrolinx", "A fragrance similar to Aromatics Elixir"),
            (306, "CAP", "Cotton Candy"),
        ]
        for fid, brand, name in products:
            db.execute(
                "INSERT INTO flavor_reference(id,brand,name,category,active,price_per_ml) VALUES(?,?,?,'Flavor',1,2.5)",
                (fid, brand, name),
            )
            db.execute(
                "INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(?,?,?,'Flavor',?)",
                (fid, brand, name, fid),
            )
        return products

    def test_all_pickers_exclude_bad_products_despite_flavour_labels_without_changing_records(
        self,
    ):
        with flask_app.app_context():
            db = get_db()
            products = self.seed_products(db)
            db.execute(
                "INSERT INTO library_items(reference_id,current_stock,cost_per_unit) VALUES(301,25,2.5)"
            )
            before = [
                tuple(r)
                for r in db.execute("SELECT * FROM flavor_reference WHERE id>=301")
            ]
            self.assertEqual(
                [r["id"] for r in recipes_repo.fetch_tvw_flavours()[0]], [306]
            )
            self.assertEqual([r["id"] for r in fetch_stash_quick_add_options()], [306])
            for fid, brand, name in products[:-1]:
                self.assertEqual(
                    recipes_repo.search_flavour_reference_for_builder(query=name), []
                )
                self.assertNotIn(
                    fid,
                    {r["id"] for r in recipes_repo.fetch_flavour_reference_options()},
                )
            self.assertEqual(
                before,
                [
                    tuple(r)
                    for r in db.execute("SELECT * FROM flavor_reference WHERE id>=301")
                ],
            )
            self.assertEqual(
                tuple(
                    db.execute(
                        "SELECT reference_id,current_stock,cost_per_unit FROM library_items"
                    ).fetchone()
                ),
                (301, 25, 2.5),
            )

    def test_forged_builder_selection_rejected_but_existing_history_lookup_retained(
        self,
    ):
        from services.recipe_builder_service import (
            parse_recipe_builder_form,
            RecipeBuilderFormError,
        )

        with flask_app.app_context():
            db = get_db()
            self.seed_products(db)
            with self.assertRaises(RecipeBuilderFormError) as caught:
                parse_recipe_builder_form(
                    MultiDict(
                        [
                            ("name", "Test"),
                            ("ingredient_id[]", "301"),
                            ("ingredient_percent[]", "5"),
                        ]
                    )
                )
            self.assertIn(
                "unavailable as a mixing ingredient",
                caught.exception.errors["flavours"],
            )
            self.assertIn(301, recipes_repo.fetch_flavour_reference_map([301]))
            self.assertNotIn(
                301,
                recipes_repo.fetch_flavour_reference_map(
                    [301], recipe_eligible_only=True
                ),
            )


if __name__ == "__main__":
    unittest.main()
