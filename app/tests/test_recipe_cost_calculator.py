from __future__ import annotations

import os
import tempfile
import unittest

from app import app as flask_app
from db import get_db
from migrations import run_startup_migrations
from models import cost_catalogue_repo
from services.recipe_cost_calculator_service import (
    BatchCostInputs,
    build_batch_cost_for_recipe,
    calculate_batch_cost,
    resolve_ingredients_for_costing,
)


class RecipeCostCalculatorServiceTests(unittest.TestCase):
    def test_500ml_batch_into_15ml_bottles(self):
        ingredients = [
            {"ingredient_id": 1, "name": "Vanilla", "brand": "Test", "amount_percent": 10.0, "price_per_ml": 0.10},
        ]
        inputs = BatchCostInputs(
            batch_volume_ml=500.0,
            bottle_size_ml=15.0,
            selling_price_per_bottle=120.0,
            base_cost_per_ml=0.05,
            bottle_cost=3.0,
            label_cost=1.0,
            packaging_cost=2.0,
        )
        result = calculate_batch_cost(ingredients=ingredients, inputs=inputs)
        out = result["outputs"]
        self.assertEqual(out["full_bottles"], 33)
        self.assertAlmostEqual(out["leftover_ml"], 5.0)
        self.assertAlmostEqual(out["total_flavour_cost"], 5.0)
        self.assertAlmostEqual(out["total_batch_cost"], 228.0)

    def test_default_bottle_and_label_costs(self):
        inputs = BatchCostInputs(batch_volume_ml=30.0, bottle_size_ml=30.0, selling_price_per_bottle=100.0)
        self.assertAlmostEqual(inputs.bottle_cost, 5.0)
        self.assertAlmostEqual(inputs.label_cost, 4.0)

    def test_manual_price_override_used_in_batch_cost(self):
        ingredients = [
            {
                "ingredient_id": 1,
                "name": "Biscuit",
                "brand": "INW",
                "amount_percent": 10.0,
                "price_per_ml": 5.0,
                "missing_price": False,
                "manual_price_active": True,
            }
        ]
        inputs = BatchCostInputs(batch_volume_ml=100.0, bottle_size_ml=10.0, selling_price_per_bottle=50.0)
        result = calculate_batch_cost(ingredients=ingredients, inputs=inputs)
        self.assertAlmostEqual(result["outputs"]["total_flavour_cost"], 50.0)  # 10% * 5.0 * 100ml

    def test_missing_ingredient_price_warning(self):
        ingredients = [
            {"ingredient_id": 1, "name": "Priced", "brand": "X", "amount_percent": 5.0, "price_per_ml": 0.50},
            {"ingredient_id": 2, "name": "Unpriced", "brand": "Y", "amount_percent": 3.0, "price_per_ml": 0.0, "missing_price": True},
        ]
        inputs = BatchCostInputs(batch_volume_ml=100.0, bottle_size_ml=10.0, selling_price_per_bottle=50.0)
        result = calculate_batch_cost(ingredients=ingredients, inputs=inputs)
        self.assertTrue(result["recipe"]["pricing_incomplete"])


class RecipeCostCalculatorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "cost_calc.db")
        flask_app.config["TESTING"] = True
        flask_app.config["DATABASE"] = self.db_path
        flask_app.config["WTF_CSRF_ENABLED"] = False
        flask_app.config["TVW_ENABLE_LAB_MODULES"] = True
        from routes.recipes import recipes_bp
        from routes.library_main import library_bp
        from routes.mix import mix_bp

        if "recipes" not in flask_app.blueprints:
            flask_app.register_blueprint(recipes_bp)
        if "library" not in flask_app.blueprints:
            flask_app.register_blueprint(library_bp)
        if "mix" not in flask_app.blueprints:
            flask_app.register_blueprint(mix_bp)
        try:
            from routes.master_admin_console import master_admin_bp
        except Exception:
            master_admin_bp = None
        if master_admin_bp is not None and "master_admin" not in flask_app.blueprints:
            flask_app.register_blueprint(master_admin_bp)

        with flask_app.app_context():
            db = get_db()
            with open(os.path.join(os.path.dirname(__file__), "..", "schema.sql"), "r", encoding="utf-8") as f:
                db.executescript(f.read())
            run_startup_migrations()
            db.execute(
                "INSERT INTO users (id, username, password_hash, role, status) "
                "VALUES (1, 'Old Goat', 'x', 'Old Goat', 'Active')"
            )
            # Linked recipe row: unpriced INW Biscuit
            db.execute(
                "INSERT INTO flavor_reference (id, brand, name, category, density, price_per_ml, active) "
                "VALUES (201, 'INW', 'Biscuit', 'Flavor', 1.036, 0.0, 1)"
            )
            db.execute(
                "INSERT INTO flavor_reference (id, brand, name, category, density, price_per_ml, active) "
                "VALUES (202, 'INW', 'Biscuit Concentrate', 'Flavor', 1.036, 3.90, 1)"
            )
            db.execute(
                "INSERT INTO flavor_reference (id, brand, name, category, density, price_per_ml, active) "
                "VALUES (203, 'Inawera', 'Biscuit', 'Flavor', 1.036, 0.0, 1)"
            )
            db.execute(
                "INSERT INTO flavor_reference (id, brand, name, category, density, price_per_ml, active, is_nicotine) "
                "VALUES (300, 'AuNic', 'Freebase Nicotine VG - 1000ml', 'Nicotine', 1.0, 1.20, 1, 1)"
            )
            db.execute(
                "INSERT INTO recipes (id, name, recipe_code, status, created_by, current_version_id) "
                "VALUES (1, 'Biscuit Juice', 'BIS', 'Production', 1, 1)"
            )
            db.execute(
                "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent) "
                "VALUES (1, 1, '1.0', 5.0)"
            )
            db.execute(
                "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (1, 201, 5.0)"
            )
            db.commit()

        self.client = flask_app.test_client()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_inw_biscuit_resolves_via_build_batch_cost(self):
        with flask_app.app_context():
            result = build_batch_cost_for_recipe(
                recipe_id=1,
                recipe_version_id=1,
                inputs=BatchCostInputs(
                    batch_volume_ml=100.0,
                    bottle_size_ml=10.0,
                    selling_price_per_bottle=50.0,
                ),
            )
        lines = result["recipe"]["ingredients"]
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertFalse(line["missing_price"])
        self.assertAlmostEqual(float(line["price_per_ml"]), 3.90)
        self.assertIn("Biscuit Concentrate", line["resolved_label"])
        # 5% @ 3.90/ml on 100ml batch
        self.assertAlmostEqual(result["outputs"]["total_flavour_cost"], 19.5)

    def test_manual_catalogue_override_persists(self):
        with flask_app.app_context():
            cost_catalogue_repo.upsert_catalogue_override(
                recipe_id=1, version_id=1, ingredient_id=201, catalogue_reference_id=203
            )
            overrides = cost_catalogue_repo.fetch_cost_overrides_for_version(recipe_id=1, version_id=1)
        self.assertEqual(overrides[201]["catalogue_reference_id"], 203)

        res = self.client.get(
            "/recipes/cost-calculator/calculate?recipe_id=1&recipe_version_id=1"
            "&batch_volume_ml=100&bottle_size_ml=10&selling_price_per_bottle=50"
        )
        body = res.get_json()
        line = body["result"]["recipe"]["ingredients"][0]
        self.assertEqual(int(line["resolved_catalogue_id"]), 203)

    def test_manual_price_override_persists_and_used(self):
        with flask_app.app_context():
            cost_catalogue_repo.upsert_manual_price_override(
                recipe_id=1, version_id=1, ingredient_id=201, manual_price_per_ml=6.50
            )
        res = self.client.get(
            "/recipes/cost-calculator/calculate?recipe_id=1&recipe_version_id=1"
            "&batch_volume_ml=100&bottle_size_ml=10&selling_price_per_bottle=50"
        )
        line = res.get_json()["result"]["recipe"]["ingredients"][0]
        self.assertTrue(line["manual_price_active"])
        self.assertAlmostEqual(float(line["price_per_ml"]), 6.50)

    def test_clear_manual_price_restores_catalogue_resolution(self):
        with flask_app.app_context():
            cost_catalogue_repo.upsert_manual_price_override(
                recipe_id=1, version_id=1, ingredient_id=201, manual_price_per_ml=6.50
            )
            cost_catalogue_repo.clear_manual_price_override(recipe_id=1, version_id=1, ingredient_id=201)
            raw = cost_catalogue_repo.fetch_all_catalogue_for_costing()
            overrides = cost_catalogue_repo.fetch_cost_overrides_for_version(recipe_id=1, version_id=1)
            resolved = resolve_ingredients_for_costing(
                recipe_id=1,
                version_id=1,
                raw_ingredients=[{
                    "ingredient_id": 201,
                    "brand": "INW",
                    "name": "Biscuit",
                    "amount_percent": 5.0,
                    "price_per_ml": 0.0,
                    "is_nicotine": 0,
                }],
                catalogue_rows=raw,
                overrides=overrides,
            )
        self.assertAlmostEqual(float(resolved[0]["price_per_ml"]), 3.90)

    def test_calculate_endpoint_defaults_bottle_label_costs(self):
        res = self.client.get(
            "/recipes/cost-calculator/calculate?recipe_id=1&recipe_version_id=1"
            "&batch_volume_ml=30&bottle_size_ml=30&selling_price_per_bottle=100"
        )
        inputs = res.get_json()["result"]["inputs"]
        self.assertAlmostEqual(float(inputs["bottle_cost"]), 5.0)
        self.assertAlmostEqual(float(inputs["label_cost"]), 4.0)

    def test_post_catalogue_override_endpoint(self):
        res = self.client.post(
            "/recipes/cost-calculator/override",
            json={"recipe_id": 1, "version_id": 1, "ingredient_id": 201, "catalogue_id": 202},
        )
        self.assertEqual(res.status_code, 200)

    def test_post_price_override_endpoint(self):
        res = self.client.post(
            "/recipes/cost-calculator/price-override",
            json={"recipe_id": 1, "version_id": 1, "ingredient_id": 201, "manual_price_per_ml": 7.25},
        )
        self.assertEqual(res.status_code, 200)

    def test_cost_calculator_page_renders_selection_wiring(self):
        res = self.client.get("/recipes/cost-calculator")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('id="recipeIdField"', html)
        self.assertIn('id="versionIdField"', html)
        self.assertIn('id="recipeSelect"', html)
        self.assertIn("applyRecipeSelection", html)
        self.assertIn("getRecipeState", html)
        self.assertIn('value="1"', html)
        self.assertIn("Biscuit Juice", html)
        self.assertIn("/recipes/cost-calculator/calculate", html)


if __name__ == "__main__":
    unittest.main()
