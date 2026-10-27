"""Pauly Feedback Round 2: auth, PG/VG sync, recipe defaults, Quick Mix GUI."""

from __future__ import annotations

import os
import re
import tempfile
import unittest

from werkzeug.security import check_password_hash, generate_password_hash

from app import app as flask_app
from db import get_db
from migrations import run_startup_migrations
from models import users_repo
from services.recipe_builder_service import normalize_complementary_pg_vg


class PaulyFeedbackRound2Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "tvw_r2.db")
        flask_app.config["TESTING"] = True
        flask_app.config["DATABASE"] = self.db_path
        flask_app.config["WTF_CSRF_ENABLED"] = False
        flask_app.config["SECRET_KEY"] = "test-secret-pauly-r2"

        with flask_app.app_context():
            db = get_db()
            schema_path = os.path.join(os.path.dirname(__file__), "..", "schema.sql")
            with open(schema_path, "r", encoding="utf-8") as f:
                db.executescript(f.read())
            run_startup_migrations()
            db.execute(
                "INSERT INTO users (id, username, password_hash, role, status) VALUES (1, 'member1', ?, 'member', 'Active')",
                (generate_password_hash("member-pass"),),
            )
            db.execute(
                "INSERT INTO users (id, username, password_hash, role, status) VALUES (2, 'Old Goat', ?, 'Old Goat', 'Active')",
                (generate_password_hash("admin-pass"),),
            )
            db.execute(
                "INSERT INTO flavor_reference (id, brand, name, category, density, active) VALUES (101, 'Test', 'Vanilla', 'Flavor', 1.036, 1)"
            )
            db.commit()

        self.client = flask_app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _login(self, username: str, password: str, *, remember: bool = False):
        data = {"username": username, "password": password}
        if remember:
            data["remember_me"] = "1"
        return self.client.post("/login", data=data, follow_redirects=False)

    def _as_member(self):
        with flask_app.app_context():
            sig = users_repo.password_signature(
                users_repo.fetch_user_by_id(1)["password_hash"]
            )
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "member1"
            sess["role"] = "member"
            sess["pwd_sig"] = sig

    def _as_old_goat(self):
        with flask_app.app_context():
            sig = users_repo.password_signature(
                users_repo.fetch_user_by_id(2)["password_hash"]
            )
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"
            sess["pwd_sig"] = sig

    # --- Auth ---

    def test_forgot_password_path_exists_and_is_neutral(self):
        get_resp = self.client.get("/forgot-password")
        self.assertEqual(get_resp.status_code, 200)
        html = get_resp.data.decode("utf-8")
        self.assertIn("Forgot password", html)
        self.assertIn("username", html.lower())

        post_known = self.client.post(
            "/forgot-password", data={"username": "member1"}, follow_redirects=True
        )
        post_unknown = self.client.post(
            "/forgot-password", data={"username": "no-such-user-xyz"}, follow_redirects=True
        )
        known_html = post_known.data.decode("utf-8")
        unknown_html = post_unknown.data.decode("utf-8")
        self.assertIn("administrator must reset", known_html.lower())
        self.assertIn("administrator must reset", unknown_html.lower())
        # No enumeration: same guidance class of message for both.
        self.assertNotIn("does not exist", known_html.lower())
        self.assertNotIn("does not exist", unknown_html.lower())

    def test_admin_password_reset_stores_hash_only(self):
        self._as_old_goat()
        resp = self.client.post(
            "/admin/users/1/reset-password",
            data={
                "new_password": "TempPass99",
                "confirm_password": "TempPass99",
                "reason": "Pauly test reset",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        with flask_app.app_context():
            user = users_repo.fetch_user_by_id(1)
            self.assertTrue(str(user["password_hash"]).startswith("pbkdf2:") or "$" in str(user["password_hash"]) or len(str(user["password_hash"])) > 20)
            self.assertNotEqual(user["password_hash"], "TempPass99")
            self.assertTrue(check_password_hash(user["password_hash"], "TempPass99"))
            self.assertEqual(int(user.get("must_change_password") or 0), 1)

    def test_remember_me_sets_permanent_session(self):
        resp = self._login("member1", "member-pass", remember=True)
        self.assertIn(resp.status_code, (302, 303))
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("user_id"), 1)
            self.assertTrue(sess.permanent)

    def test_logout_clears_authentication(self):
        self._login("member1", "member-pass", remember=True)
        self.client.get("/logout", follow_redirects=False)
        with self.client.session_transaction() as sess:
            self.assertNotIn("user_id", sess)
        gated = self.client.get("/recipes/new", follow_redirects=False)
        self.assertIn(gated.status_code, (302, 303))
        self.assertIn("/login", gated.headers.get("Location", ""))

    def test_password_reset_invalidates_old_session_sig(self):
        self._login("member1", "member-pass", remember=True)
        with flask_app.app_context():
            users_repo.update_user_password_hash(
                user_id=1,
                password_hash=generate_password_hash("brand-new"),
                must_change_password=True,
            )
            get_db().commit()
        blocked = self.client.get("/recipes/new", follow_redirects=False)
        self.assertIn(blocked.status_code, (302, 303))

    # --- PG/VG ---

    def test_pg_50_implies_vg_50_server_normalize(self):
        pg, vg = normalize_complementary_pg_vg(50, 70)
        self.assertAlmostEqual(pg + vg, 100.0, places=2)
        # Prefer complementary snap when inconsistent — PG anchored.
        pg2, vg2 = normalize_complementary_pg_vg(50, 50)
        self.assertEqual(pg2, 50)
        self.assertEqual(vg2, 50)

    def test_vg_80_implies_pg_20(self):
        # When VG is 80 and PG is leftover 20 already complementary.
        pg, vg = normalize_complementary_pg_vg(20, 80)
        self.assertAlmostEqual(pg, 20.0, places=2)
        self.assertAlmostEqual(vg, 80.0, places=2)
        # Inconsistent: VG-heavy pair normalizes proportionally then snaps.
        pg_n, vg_n = normalize_complementary_pg_vg(10, 80)
        self.assertAlmostEqual(pg_n + vg_n, 100.0, places=2)

    def test_builder_server_normalizes_pg_vg_on_save(self):
        self._as_member()
        resp = self.client.post(
            "/recipes/save",
            data={
                "name": "Ratio Recipe",
                "visibility": "private",
                "status": "wip",
                "tags": "",
                "notes": "",
                "target_ratio": "70/30",
                "calc_target_ml": "60",
                "calc_target_nic": "3",
                "calc_pg_ratio": "40",
                "calc_vg_ratio": "70",  # inconsistent — server normalizes
                "calc_nic_base": "100",
                "calc_nicotine_carrier": "PG",
                "ingredient_id[]": "101",
                "ingredient_percent[]": "5",
                "ingredient_name[]": "Vanilla",
                "ingredient_brand[]": "Test",
            },
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, (302, 303))
        with flask_app.app_context():
            row = get_db().execute(
                "SELECT target_vg_pg_ratio FROM recipe_versions ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            # Normalized complementary VG/PG string
            m = re.match(r"^(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)$", str(row["target_vg_pg_ratio"]))
            self.assertIsNotNone(m)
            vg = float(m.group(1))
            pg = float(m.group(2))
            self.assertAlmostEqual(pg + vg, 100.0, places=2)

    # --- Recipe persistence ---

    def _create_recipe(self, *, batch: str, nic: str, pg: str, vg: str, name: str = "Persist Me"):
        self._as_member()
        resp = self.client.post(
            "/recipes/save",
            data={
                "name": name,
                "visibility": "private",
                "status": "ready",
                "tags": "test",
                "notes": "",
                "target_ratio": f"{vg}/{pg}",
                "calc_target_ml": batch,
                "calc_target_nic": nic,
                "calc_pg_ratio": pg,
                "calc_vg_ratio": vg,
                "calc_nic_base": "100",
                "calc_nicotine_carrier": "PG",
                "ingredient_id[]": "101",
                "ingredient_percent[]": "8",
                "ingredient_name[]": "Vanilla",
                "ingredient_brand[]": "Test",
            },
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, (302, 303), resp.data.decode("utf-8", errors="replace")[:500])
        loc = resp.headers.get("Location") or ""
        self.assertTrue(loc)
        return loc

    def test_recipe_persists_10ml_0mg_5050(self):
        loc = self._create_recipe(batch="10", nic="0", pg="50", vg="50", name="Ten Zero Fifty")
        detail = self.client.get(loc, follow_redirects=True)
        self.assertEqual(detail.status_code, 200)
        html = detail.data.decode("utf-8")
        self.assertIn('id="target_ml"', html)
        self.assertRegex(html, r'id="target_ml"[^>]*value="10"')
        self.assertRegex(html, r'id="target_nic"[^>]*value="0"')
        self.assertRegex(html, r'id="mix_pg"[^>]*value="50"')
        self.assertRegex(html, r'id="mix_vg"[^>]*value="50"')
        self.assertIn("10 ml", html)
        self.assertIn("0 mg/ml", html)
        with flask_app.app_context():
            ver = get_db().execute(
                "SELECT default_batch_ml, default_nic_mg_ml, target_vg_pg_ratio FROM recipe_versions ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertAlmostEqual(float(ver["default_batch_ml"]), 10.0)
            self.assertAlmostEqual(float(ver["default_nic_mg_ml"]), 0.0)

    def test_recipe_persists_120ml_6mg_4060(self):
        loc = self._create_recipe(batch="120", nic="6", pg="40", vg="60", name="OneTwenty Six")
        detail = self.client.get(loc, follow_redirects=True)
        html = detail.data.decode("utf-8")
        self.assertRegex(html, r'id="target_ml"[^>]*value="120"')
        self.assertRegex(html, r'id="target_nic"[^>]*value="6"')
        self.assertRegex(html, r'id="mix_pg"[^>]*value="40"')
        self.assertRegex(html, r'id="mix_vg"[^>]*value="60"')

    def test_guided_and_quick_mix_inherit_saved_values(self):
        loc = self._create_recipe(batch="10", nic="0", pg="50", vg="50", name="Inherit Mix")
        with flask_app.app_context():
            recipe = get_db().execute("SELECT id FROM recipes ORDER BY id DESC LIMIT 1").fetchone()
            version = get_db().execute(
                "SELECT id FROM recipe_versions WHERE recipe_id = ? ORDER BY id DESC LIMIT 1",
                (int(recipe["id"]),),
            ).fetchone()
            rid = int(recipe["id"])
            vid = int(version["id"])

        quick = self.client.post(
            "/api/mix/quick",
            json={
                "recipe_id": rid,
                "version_id": vid,
                "target_ml": 10,
                "target_nic_mg_ml": 0,
                "nic_base_mg_ml": 100,
                "pg_ratio": 50,
                "vg_ratio": 50,
                "nicotine_carrier": "PG",
            },
        )
        self.assertEqual(quick.status_code, 200)
        body = quick.get_json()
        totals = (body.get("result") or {}).get("totals") or {}
        self.assertAlmostEqual(float(totals["target_ml"]), 10.0)
        self.assertAlmostEqual(float(totals["target_nic_mg_ml"]), 0.0)
        self.assertIn("line_items", body.get("result") or {})

        # Detail page (HTML) embeds saved defaults for guided start form.
        detail = self.client.get(loc, follow_redirects=True)
        html = detail.data.decode("utf-8")
        self.assertIn('name="target_ml"', html)
        self.assertRegex(html, r'name="target_ml"[^>]*value="10"|id="target_ml"[^>]*value="10"')
        self.assertRegex(html, r'name="target_nic_mg_ml"[^>]*value="0"|id="target_nic"[^>]*value="0"')

    # --- Quick Mix GUI ---

    def test_quick_mix_html_page_has_gui_not_raw_code_dump(self):
        loc = self._create_recipe(batch="60", nic="3", pg="30", vg="70", name="Quick Gui Recipe")
        detail = self.client.get(loc, follow_redirects=True)
        html = detail.data.decode("utf-8")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("js-quick-mix", html)
        self.assertIn("renderQuickMixGui", html)
        self.assertNotIn("JSON.stringify(body, null, 2)", html)
        self.assertIn("Quick mix", html)
        # Mix card is HTML form UI, not a raw API dump page.
        self.assertIn("Mix this recipe", html)
        self.assertIn("Target nicotine strength", html)


if __name__ == "__main__":
    unittest.main()
