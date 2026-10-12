"""Rally costing integration: recipe save fix + Workshop→Lab visibility."""

from __future__ import annotations

import os
import re
import tempfile
import unittest

from app import app as flask_app
from db import get_db
from migrations import run_startup_migrations
from models import recipes_repo
from services import recipe_cost_calculator_service as costing
from werkzeug.security import generate_password_hash


def _form(**overrides):
    data = {
        "name": "Rally Juice Alpha",
        "status": "wip",
        "visibility": "private",
        "notes": "Poison Rally",
        "tags": "rally, dessert",
        "target_ratio": "70/30",
        "calc_target_ml": "60",
        "calc_target_nic": "3",
        "calc_pg_ratio": "30",
        "calc_vg_ratio": "70",
        "calc_nic_base": "100",
        "calc_nicotine_carrier": "PG",
        "ingredient_id[]": ["101", "102"],
        "ingredient_percent[]": ["4.0", "3.0"],
        "ingredient_name[]": ["Vanilla", "ZzyzxUnmatchedConcentrateXYZ"],
        "ingredient_brand[]": ["Test", "ZZBrandNoMatch"],
    }
    data.update(overrides)
    return data


class RallyCostingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "rally_cost.db")
        flask_app.config["TESTING"] = True
        flask_app.config["DATABASE"] = self.db_path
        flask_app.config["WTF_CSRF_ENABLED"] = False
        flask_app.config["TVW_ENABLE_LAB_MODULES"] = True

        with flask_app.app_context():
            db = get_db()
            schema_path = os.path.join(os.path.dirname(__file__), "..", "schema.sql")
            with open(schema_path, "r", encoding="utf-8") as f:
                db.executescript(f.read())
            run_startup_migrations()
            db.execute(
                "INSERT INTO users (id, username, password_hash, role, status) VALUES (1, 'member1', 'x', 'member', 'Active')"
            )
            db.execute(
                "INSERT INTO users (id, username, password_hash, role, status) VALUES (2, 'Old Goat', ?, 'Old Goat', 'Active')",
                (generate_password_hash("123456789"),),
            )
            db.execute(
                "INSERT INTO users (id, username, password_hash, role, status) VALUES (3, 'member2', 'x', 'member', 'Active')"
            )
            for fid, brand, name, price in [
                (101, "Test", "Vanilla", 1.5),
                (102, "ZZBrandNoMatch", "ZzyzxUnmatchedConcentrateXYZ", None),
            ]:
                db.execute(
                    "INSERT INTO flavor_reference (id, brand, name, category, density, active, price_per_ml) "
                    "VALUES (?, ?, ?, 'Flavor', 1.036, 1, ?)",
                    (fid, brand, name, price),
                )
            # Pre-seed a Workshop create with empty code — previously blocked further creates.
            db.execute(
                "INSERT INTO recipes (id, name, recipe_code, status, created_by, visibility, active) "
                "VALUES (10, 'Prior Empty Code', '', 'WIP', 2, 'private', 1)"
            )
            db.execute(
                "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent) VALUES (10, 10, '1.0', 5.0)"
            )
            db.execute(
                "UPDATE recipes SET current_version_id = 10, recipe_code = NULL WHERE id = 10"
            )
            db.commit()

        self.client = flask_app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _login(self, user_id, username, role):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["username"] = username
            sess["role"] = role

    def test_multiple_creates_with_blank_recipe_code_succeed(self):
        self._login(2, "Old Goat", "Old Goat")
        for idx, name in enumerate(("Rally A", "Rally B", "Rally C"), start=1):
            resp = self.client.post("/recipes/save", data=_form(name=name), follow_redirects=False)
            self.assertEqual(resp.status_code, 302, f"create {idx} failed: {resp.status_code}")
            self.assertIn("/recipes/", resp.headers.get("Location", ""))
        with flask_app.app_context():
            count = get_db().execute(
                "SELECT COUNT(1) AS c FROM recipes WHERE name LIKE 'Rally %'"
            ).fetchone()["c"]
        self.assertEqual(int(count), 3)

    def test_new_workshop_recipe_appears_in_lab_picker_immediately(self):
        self._login(2, "Old Goat", "Old Goat")
        post = self.client.post(
            "/recipes/save",
            data=_form(name="Immediate Lab Visibility"),
            follow_redirects=False,
        )
        self.assertEqual(post.status_code, 302)
        slug = re.search(r"/recipes/([^/?]+)", post.headers["Location"]).group(1)

        with flask_app.app_context():
            rows = costing.recipe_picker_rows(session_user_id=2)
            names = {r["name"] for r in rows}
            self.assertIn("Immediate Lab Visibility", names)
            hit = next(r for r in rows if r["name"] == "Immediate Lab Visibility")
            self.assertIn("Private", hit["badges"])
            self.assertIn("Workshop-created", hit["badges"])
            self.assertTrue(hit["versions"])

        detail = self.client.get(f"/recipes/{slug}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Cost this recipe", detail.data)
        self.assertIn(b"/recipes/cost-calculator?recipe_id=", detail.data)

    def test_lab_cost_calculator_accepts_deep_link_ids(self):
        self._login(2, "Old Goat", "Old Goat")
        post = self.client.post("/recipes/save", data=_form(name="Deep Link Cost"), follow_redirects=False)
        loc = post.headers["Location"]
        with flask_app.app_context():
            recipe = get_db().execute(
                "SELECT id, current_version_id FROM recipes WHERE name = ?",
                ("Deep Link Cost",),
            ).fetchone()
            rid = int(recipe["id"])
            vid = int(recipe["current_version_id"])

        calc_page = self.client.get(f"/recipes/cost-calculator?recipe_id={rid}&recipe_version_id={vid}")
        self.assertEqual(calc_page.status_code, 200)
        self.assertIn(b"Deep Link Cost", calc_page.data)

        api = self.client.get(
            f"/recipes/cost-calculator/calculate?recipe_id={rid}&recipe_version_id={vid}"
            "&batch_volume_ml=150&bottle_size_ml=15&selling_price_per_bottle=120"
            "&base_cost_per_ml=0.05&bottle_cost=5&label_cost=4&packaging_cost=0"
            "&labour_per_bottle=0&waste_percent=0"
        )
        self.assertEqual(api.status_code, 200)
        body = api.get_json()
        self.assertEqual(body["status"], "success")
        result = body["result"]
        self.assertEqual(result["recipe_meta"]["recipe_id"], rid)
        self.assertEqual(result["recipe_meta"]["version_id"], vid)
        self.assertEqual(result["recipe_meta"]["version_number"], "1.0")
        self.assertEqual(result["recipe_meta"]["recipe_name"], "Deep Link Cost")
        ingredients = result["recipe"]["ingredients"]
        self.assertEqual(len(ingredients), 2)
        self.assertTrue(any(i.get("required_ml") for i in ingredients))
        self.assertIn("cost_per_bottle", result["outputs"])
        self.assertIn("rally_quantities", result["outputs"])
        self.assertIn("cost_status_label", result["recipe"])

    def test_private_recipe_not_visible_to_ordinary_member(self):
        self._login(1, "member1", "member")
        post = self.client.post(
            "/recipes/save",
            data=_form(name="Secret Private", visibility="private"),
            follow_redirects=False,
        )
        slug = re.search(r"/recipes/([^/?]+)", post.headers["Location"]).group(1)
        self._login(3, "member2", "member")
        other = self.client.get(f"/recipes/{slug}")
        self.assertIn(other.status_code, (302, 403, 404))
        # Ordinary member cannot open Lab costing
        denied = self.client.get("/recipes/cost-calculator")
        self.assertIn(denied.status_code, (302, 403))

    def test_costing_maths_uses_priced_lines_only(self):
        with flask_app.app_context():
            ingredients = [
                {
                    "ingredient_id": 101,
                    "name": "Vanilla",
                    "brand": "Test",
                    "amount_percent": 10.0,
                    "price_per_ml": 2.0,
                    "missing_price": False,
                },
                {
                    "ingredient_id": 102,
                    "name": "Cream",
                    "brand": "Test",
                    "amount_percent": 5.0,
                    "price_per_ml": None,
                    "missing_price": True,
                },
            ]
            result = costing.calculate_batch_cost(
                ingredients=ingredients,
                inputs=costing.BatchCostInputs(
                    batch_volume_ml=100,
                    bottle_size_ml=10,
                    selling_price_per_bottle=50,
                    base_cost_per_ml=0.0,
                    bottle_cost=0.0,
                    label_cost=0.0,
                ),
            )
            # Only 10% of 100ml * R2/ml = R20 flavour; missing line excluded.
            self.assertAlmostEqual(result["outputs"]["total_flavour_cost"], 20.0, places=4)
            self.assertTrue(result["recipe"]["pricing_incomplete"])
            self.assertEqual(result["outputs"]["full_bottles"], 10)
            self.assertAlmostEqual(result["outputs"]["cost_per_bottle"], 2.0, places=4)
            self.assertAlmostEqual(result["outputs"]["rally_quantities"]["10"], 20.0, places=4)

    def test_failed_create_does_not_appear_in_lab_picker(self):
        self._login(2, "Old Goat", "Old Goat")
        from unittest.mock import patch

        with patch.object(recipes_repo, "insert_recipe_items", side_effect=RuntimeError("boom")):
            resp = self.client.post(
                "/recipes/save",
                data=_form(name="Should Not Appear"),
                follow_redirects=True,
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Could not save recipe", resp.data)
        with flask_app.app_context():
            orphan = get_db().execute(
                "SELECT COUNT(1) AS c FROM recipes WHERE name = ?",
                ("Should Not Appear",),
            ).fetchone()["c"]
            self.assertEqual(int(orphan), 0)
            names = {r["name"] for r in costing.recipe_picker_rows(session_user_id=2)}
            self.assertNotIn("Should Not Appear", names)


if __name__ == "__main__":
    unittest.main()
