"""Tests for direct guided-mix start from recipe detail."""

from __future__ import annotations

import os
import tempfile
import unittest

from app import app as flask_app
from db import get_db
from migrations import run_startup_migrations
from services import guided_mix_start_service as gms


class GuidedMixStartServiceTests(unittest.TestCase):
    def test_parse_rejects_pg_vg_not_100(self):
        with self.assertRaises(gms.GuidedMixFormError) as ctx:
            gms.parse_guided_mix_form(
                {
                    "version_id": "1",
                    "target_ml": "30",
                    "target_nic_mg_ml": "3",
                    "pg_ratio": "30",
                    "vg_ratio": "60",
                    "nic_base_mg_ml": "100",
                    "nicotine_carrier": "PG",
                }
            )
        self.assertIn("pg_ratio", ctx.exception.errors)
        self.assertEqual(ctx.exception.values["target_ml"], "30")


class GuidedMixStartRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "guided_mix_start_test.db")
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
                "INSERT INTO flavor_reference (id, brand, name, category, density, active) VALUES (101, 'Test', 'Vanilla', 'Flavor', 1.036, 1)"
            )
            db.execute(
                "INSERT INTO recipes (id, name, status, visibility, moderation_status, active, created_by, slug, current_version_id) "
                "VALUES (1, 'Recipe1', 'WIP', 'public', 'active', 1, 1, 'recipe-one', 1)"
            )
            db.execute(
                "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent) VALUES (1, 1, '1', 5.0)"
            )
            db.execute(
                "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (1, 101, 5.0)"
            )
            db.execute(
                "INSERT INTO flavours (id, canonical_name, brand, category, source_flavor_ref_id) VALUES (1, 'Vanilla', 'Test', 'Flavor', 101)"
            )
            db.commit()

        self.client = flask_app.test_client()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "member1"
            sess["role"] = "member"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _valid_form(self, **overrides):
        data = {
            "version_id": "1",
            "target_ml": "30",
            "target_nic_mg_ml": "3",
            "pg_ratio": "30",
            "vg_ratio": "70",
            "nic_base_mg_ml": "100",
            "nicotine_carrier": "PG",
        }
        data.update(overrides)
        return data

    def test_post_start_redirects_to_session_bench(self):
        r = self.client.post("/recipes/recipe-one/guided-mix/start", data=self._valid_form(), follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/guided-mix/session/", r.headers.get("Location", ""))
        self.assertNotIn("guided-mix?", r.headers.get("Location", ""))

    def test_post_start_does_not_render_duplicate_setup_page(self):
        r = self.client.post("/recipes/recipe-one/guided-mix/start", data=self._valid_form())
        self.assertNotIn(b"Start guided session", r.data)
        self.assertNotIn(b"Confirm version", r.data)

    def test_invalid_pg_vg_rerenders_recipe_with_errors(self):
        r = self.client.post(
            "/recipes/recipe-one/guided-mix/start",
            data=self._valid_form(pg_ratio="30", vg_ratio="60"),
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Mix this recipe", r.data)
        self.assertIn(b"PG and VG must total 100%", r.data)
        self.assertIn(b'value="60"', r.data)
        with flask_app.app_context():
            count = get_db().execute("SELECT COUNT(1) AS c FROM mix_sessions").fetchone()["c"]
        self.assertEqual(int(count), 0)

    def test_invalid_version_rejected(self):
        r = self.client.post(
            "/recipes/recipe-one/guided-mix/start",
            data=self._valid_form(version_id="999"),
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"does not belong", r.data)

    def test_legacy_get_guided_mix_redirects_to_recipe_detail(self):
        r = self.client.get(
            "/recipes/recipe-one/guided-mix?target_ml=60&target_nic_mg_ml=3&pg_ratio=30&vg_ratio=70",
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 302)
        loc = r.headers.get("Location", "")
        self.assertIn("/recipes/recipe-one", loc)
        self.assertIn("target_ml=60", loc)
        self.assertIn("#mix-card", loc)

    def test_legacy_get_does_not_render_setup_template(self):
        r = self.client.get("/recipes/recipe-one/guided-mix", follow_redirects=True)
        self.assertIn(b"Mix this recipe", r.data)
        self.assertNotIn(b"Start guided session", r.data)

    def test_quick_mix_still_works_via_api(self):
        r = self.client.post(
            "/api/mix/quick",
            json={
                "recipe_id": 1,
                "version_id": 1,
                "target_ml": 30,
                "target_nic_mg_ml": 3,
                "nic_base_mg_ml": 100,
                "pg_ratio": 30,
                "vg_ratio": 70,
                "nicotine_carrier": "PG",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json().get("status"), "success")

    def test_prg_refresh_on_session_page_does_not_create_second_session(self):
        first = self.client.post("/recipes/recipe-one/guided-mix/start", data=self._valid_form(), follow_redirects=False)
        self.assertEqual(first.status_code, 302)
        session_url = first.headers["Location"]
        bench = self.client.get(session_url)
        self.assertEqual(bench.status_code, 200)
        refresh = self.client.get(session_url)
        self.assertEqual(refresh.status_code, 200)
        with flask_app.app_context():
            count = get_db().execute("SELECT COUNT(1) AS c FROM mix_sessions").fetchone()["c"]
        self.assertEqual(int(count), 1)


if __name__ == "__main__":
    unittest.main()
