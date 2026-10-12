"""Integration tests for recipe builder create flow and database persistence."""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from unittest.mock import patch

from app import app as flask_app
from db import get_db
from migrations import run_startup_migrations
from models import recipes_repo
from werkzeug.security import generate_password_hash


def _valid_create_form(**overrides):
    data = {
        "name": "Builder Test Custard",
        "status": "wip",
        "visibility": "private",
        "notes": "Steep 5 days",
        "tags": "dessert, custard",
        "target_ratio": "70/30",
        "calc_target_ml": "60",
        "calc_target_nic": "3",
        "calc_pg_ratio": "30",
        "calc_vg_ratio": "70",
        "calc_nic_base": "100",
        "calc_nicotine_carrier": "PG",
        "ingredient_id[]": ["101", "102"],
        "ingredient_percent[]": ["4.5", "3.5"],
        "ingredient_name[]": ["Vanilla", "Cream"],
        "ingredient_brand[]": ["Test", "Test"],
    }
    data.update(overrides)
    return data


class RecipeBuilderCreateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "recipe_builder_test.db")
        flask_app.config["TESTING"] = True
        flask_app.config["DATABASE"] = self.db_path
        flask_app.config["WTF_CSRF_ENABLED"] = False

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
                "INSERT INTO users (id, username, password_hash, role, status) VALUES (2, 'member2', 'x', 'member', 'Active')"
            )
            db.execute(
                "INSERT INTO users (id, username, password_hash, role, status) VALUES (3, 'Old Goat', ?, 'Old Goat', 'Active')",
                (generate_password_hash("123456789"),),
            )
            for fid, name in [(101, "Vanilla"), (102, "Cream")]:
                db.execute(
                    "INSERT INTO flavor_reference (id, brand, name, category, density, active) VALUES (?, 'Test', ?, 'Flavor', 1.036, 1)",
                    (fid, name),
                )
            db.commit()

        self.client = flask_app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _login(self, user_id: int, username: str, role: str):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["username"] = username
            sess["role"] = role

    def test_create_page_renders_builder(self):
        self._login(1, "member1", "member")
        response = self.client.get("/recipes/new")
        self.assertEqual(response.status_code, 200)
        html = response.data.decode("utf-8", errors="ignore")
        self.assertIn("Create a recipe", html)
        self.assertIn("Recipe summary", html)
        self.assertIn("Create recipe", html)
        self.assertNotIn("Recipe Workstation", html)

    def test_valid_private_recipe_persists_completely(self):
        self._login(1, "member1", "member")
        before_recipes = self._count("recipes")
        before_versions = self._count("recipe_versions")
        before_items = self._count("recipe_items")

        response = self.client.post("/recipes/save", data=_valid_create_form(), follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        loc = response.headers.get("Location", "")
        self.assertIn("/recipes/", loc)

        slug_match = re.search(r"/recipes/([^/?]+)", loc)
        self.assertIsNotNone(slug_match)
        slug = slug_match.group(1)

        with flask_app.app_context():
            db = get_db()
            recipe = db.execute("SELECT * FROM recipes WHERE name = ?", ("Builder Test Custard",)).fetchone()
            self.assertIsNotNone(recipe)
            self.assertEqual(int(recipe["created_by"]), 1)
            self.assertEqual(recipe["visibility"], "private")
            self.assertEqual(str(recipe["status"]).upper(), "WIP")
            self.assertEqual(recipe["slug"], slug)
            self.assertIsNotNone(recipe["current_version_id"])

            version = db.execute(
                "SELECT * FROM recipe_versions WHERE id = ?",
                (int(recipe["current_version_id"]),),
            ).fetchone()
            self.assertIsNotNone(version)
            self.assertEqual(version["version_number"], "1.0")
            self.assertEqual(version["target_vg_pg_ratio"], "70/30")
            self.assertEqual(float(version["total_flavor_percent"]), 8.0)
            self.assertEqual(version["notes"], "Steep 5 days")

            items = db.execute(
                "SELECT ingredient_id, amount_percent FROM recipe_items WHERE version_id = ? ORDER BY ingredient_id",
                (int(version["id"]),),
            ).fetchall()
            self.assertEqual(len(items), 2)
            self.assertEqual(int(items[0]["ingredient_id"]), 101)
            self.assertEqual(float(items[0]["amount_percent"]), 4.5)
            self.assertEqual(int(items[1]["ingredient_id"]), 102)
            self.assertEqual(float(items[1]["amount_percent"]), 3.5)

            tags = db.execute(
                "SELECT tag FROM recipe_tags WHERE recipe_id = ? ORDER BY tag",
                (int(recipe["id"]),),
            ).fetchall()
            self.assertEqual([r["tag"] for r in tags], ["custard", "dessert"])

            self.assertEqual(self._count("recipes"), before_recipes + 1)
            self.assertEqual(self._count("recipe_versions"), before_versions + 1)
            self.assertEqual(self._count("recipe_items"), before_items + 2)

        get_resp = self.client.get(loc)
        self.assertEqual(get_resp.status_code, 200)
        html = get_resp.data.decode("utf-8", errors="ignore")
        self.assertIn("Builder Test Custard", html)
        self.assertIn("Vanilla", html)

    def test_private_recipe_not_visible_to_other_member(self):
        self._login(1, "member1", "member")
        post = self.client.post("/recipes/save", data=_valid_create_form(name="Private Only"), follow_redirects=False)
        slug = re.search(r"/recipes/([^/?]+)", post.headers["Location"]).group(1)

        self._login(2, "member2", "member")
        other = self.client.get(f"/recipes/{slug}")
        self.assertIn(other.status_code, (302, 403, 404))

        lib = self.client.get("/recipes?mine=1")
        self.assertNotIn(b"Private Only", lib.data)

    def test_private_visible_to_old_goat(self):
        self._login(1, "member1", "member")
        post = self.client.post("/recipes/save", data=_valid_create_form(name="Goat Can See"), follow_redirects=False)
        slug = re.search(r"/recipes/([^/?]+)", post.headers["Location"]).group(1)

        self._login(3, "Old Goat", "Old Goat")
        resp = self.client.get(f"/recipes/{slug}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Goat Can See", resp.data)

    def test_valid_public_recipe_in_library(self):
        self._login(1, "member1", "member")
        self.client.post(
            "/recipes/save",
            data=_valid_create_form(name="Public Custard", visibility="public"),
            follow_redirects=True,
        )
        self._login(2, "member2", "member")
        lib = self.client.get("/recipes?visibility=public")
        self.assertIn(b"Public Custard", lib.data)

    def test_invalid_recipe_shows_errors_and_writes_nothing(self):
        self._login(1, "member1", "member")
        before = self._count("recipes")
        form = _valid_create_form()
        form["name"] = ""
        form["ingredient_id[]"] = [""]
        form["ingredient_percent[]"] = [""]
        response = self.client.post("/recipes/save", data=form)
        self.assertEqual(response.status_code, 200)
        html = response.data.decode("utf-8", errors="ignore")
        self.assertIn("Recipe name is required", html)
        self.assertEqual(self._count("recipes"), before)
        self.assertEqual(self._count("recipe_versions"), 0)
        self.assertEqual(self._count("recipe_items"), 0)

    def test_unknown_flavour_rejected(self):
        self._login(1, "member1", "member")
        before = self._count("recipes")
        form = _valid_create_form()
        form["ingredient_id[]"] = ["9999"]
        form["ingredient_percent[]"] = ["5"]
        response = self.client.post("/recipes/save", data=form)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Unknown flavour", response.data)
        self.assertEqual(self._count("recipes"), before)

    def test_flavour_insert_failure_rolls_back(self):
        self._login(1, "member1", "member")
        before = self._count("recipes")
        real_insert_items = recipes_repo.insert_recipe_items

        def fail_on_items(**kwargs):
            raise RuntimeError("simulated insert failure")

        with patch.object(recipes_repo, "insert_recipe_items", side_effect=fail_on_items):
            response = self.client.post("/recipes/save", data=_valid_create_form(name="Rollback Test"), follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Could not save recipe", response.data)
        self.assertEqual(self._count("recipes"), before)
        self.assertEqual(self._count("recipe_versions"), 0)
        self.assertEqual(self._count("recipe_items"), 0)

    def test_blank_recipe_code_does_not_collide_on_second_create(self):
        self._login(1, "member1", "member")
        for name in ("Code Collision One", "Code Collision Two"):
            resp = self.client.post(
                "/recipes/save",
                data=_valid_create_form(name=name),
                follow_redirects=False,
            )
            self.assertEqual(resp.status_code, 302)
        with flask_app.app_context():
            codes = get_db().execute(
                "SELECT recipe_code FROM recipes WHERE name LIKE 'Code Collision %'"
            ).fetchall()
            self.assertEqual(len(codes), 2)
            for row in codes:
                self.assertTrue(row["recipe_code"] in (None, ""))

    def test_prg_refresh_does_not_duplicate(self):
        self._login(1, "member1", "member")
        post = self.client.post(
            "/recipes/save",
            data=_valid_create_form(name="PRG Custard"),
            follow_redirects=False,
        )
        loc = post.headers["Location"]
        self.client.get(loc)
        self.client.get(loc)
        count = self._count_where("recipes", "name = ?", ("PRG Custard",))
        self.assertEqual(count, 1)

    def _count(self, table: str) -> int:
        with flask_app.app_context():
            return int(get_db().execute(f"SELECT COUNT(1) AS c FROM {table}").fetchone()["c"])

    def _count_where(self, table: str, clause: str, params: tuple) -> int:
        with flask_app.app_context():
            return int(get_db().execute(f"SELECT COUNT(1) AS c FROM {table} WHERE {clause}", params).fetchone()["c"])


if __name__ == "__main__":
    unittest.main()
