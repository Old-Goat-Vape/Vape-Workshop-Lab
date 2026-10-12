"""Lab Production Jobs Phase 1 — job linkage, isolation, inventory, operators."""

from __future__ import annotations

import os
import tempfile
import unittest

from app import app as flask_app
from db import get_db
from migrations import run_startup_migrations
from services import guided_mix_session_service, production_job_service as pjs
from utils.auth import (
    CAP_PRODUCTION_JOBS_PRODUCE,
    can_access_production_jobs,
    can_manage_production_jobs,
    can_produce_on_production_jobs,
    grant_user_capability,
    production_jobs_access_level,
)


class ProductionJobsPhase1Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "prod_jobs.db")
        flask_app.config["TESTING"] = True
        flask_app.config["DATABASE"] = self.db_path
        flask_app.config["WTF_CSRF_ENABLED"] = False
        flask_app.config["TVW_ENABLE_LAB_MODULES"] = True

        from routes.production_jobs import production_jobs_bp

        if "production_jobs" not in flask_app.blueprints:
            flask_app.register_blueprint(production_jobs_bp)

        with flask_app.app_context():
            db = get_db()
            schema_path = os.path.join(os.path.dirname(__file__), "..", "schema.sql")
            with open(schema_path, "r", encoding="utf-8") as f:
                db.executescript(f.read())
            run_startup_migrations()
            db.execute(
                "INSERT INTO users (id, username, password_hash, role, status) "
                "VALUES (1, 'Wynand', 'x', 'Old Goat', 'Active')"
            )
            db.execute(
                "INSERT INTO users (id, username, password_hash, role, status) "
                "VALUES (2, 'Riku', 'x', 'member', 'Active')"
            )
            db.execute(
                "INSERT INTO flavor_reference (id, brand, name, category, density, price_per_ml, active) "
                "VALUES (101, 'INW', 'Biscuit', 'Flavor', 1.036, 1.0, 1)"
            )
            db.execute(
                "INSERT INTO flavor_reference (id, brand, name, category, density, price_per_ml, active) "
                "VALUES (102, 'TFA', 'Strawberry', 'Flavor', 1.036, 1.0, 1)"
            )
            # Recipe A — Yogi-style
            db.execute(
                "INSERT INTO recipes (id, name, recipe_code, status, created_by, current_version_id, visibility) "
                "VALUES (10, 'Yogi the Pink', 'YOGI', 'Production', 1, 100, 'private')"
            )
            db.execute(
                "INSERT INTO recipe_versions (id, recipe_id, version_number, target_vg_pg_ratio, total_flavor_percent) "
                "VALUES (100, 10, '1.3', '70/30', 10.0)"
            )
            db.execute(
                "INSERT INTO recipe_versions (id, recipe_id, version_number, target_vg_pg_ratio, total_flavor_percent) "
                "VALUES (101, 10, '1.4', '70/30', 12.0)"
            )
            db.execute(
                "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (100, 101, 8.0)"
            )
            db.execute(
                "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (101, 101, 10.0)"
            )
            # Recipe B — shares INW Biscuit for roll-up
            db.execute(
                "INSERT INTO recipes (id, name, recipe_code, status, created_by, current_version_id, visibility) "
                "VALUES (11, 'Poison Rally', 'POIS', 'Production', 1, 200, 'private')"
            )
            db.execute(
                "INSERT INTO recipe_versions (id, recipe_id, version_number, target_vg_pg_ratio, total_flavor_percent) "
                "VALUES (200, 11, '1.0', '70/30', 15.0)"
            )
            db.execute(
                "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (200, 101, 5.0)"
            )
            db.execute(
                "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (200, 102, 10.0)"
            )
            db.commit()
            grant_user_capability(2, CAP_PRODUCTION_JOBS_PRODUCE)

        self.client = flask_app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_trinidad(self) -> tuple[int, int]:
        job_id = pjs.create_job(
            name="Trinidad",
            job_type="event_stock",
            created_by_user_id=1,
        )
        jr_id = pjs.add_recipe_to_job(
            job_id=job_id,
            recipe_id=10,
            recipe_version_id=100,
            target_bottle_size_ml=120,
            target_bottle_count=30,
            mix_target_nic_mg_ml=3.0,
            mix_pg_ratio=30,
            mix_vg_ratio=70,
            mix_nic_base_mg_ml=100,
            mix_nicotine_carrier="PG",
        )
        return job_id, jr_id

    def test_job_creation_and_recipe_lock(self):
        with flask_app.app_context():
            job_id, jr_id = self._create_trinidad()
            job = pjs.get_job(job_id)
            self.assertEqual(job["name"], "Trinidad")
            self.assertEqual(job["status"], "planning")
            jr = pjs.get_job_recipe(jr_id)
            self.assertEqual(int(jr["recipe_version_id"]), 100)
            self.assertEqual(float(jr["target_total_ml"]), 3600.0)
            self.assertEqual(int(jr["target_bottle_count"]), 30)
            overview = pjs.job_overview(job_id)
            self.assertAlmostEqual(overview["completed_bottles"], 0.0)
            self.assertAlmostEqual(overview["remaining_bottles"], 30.0)

    def test_version_lock_ignores_newer_version(self):
        with flask_app.app_context():
            job_id, jr_id = self._create_trinidad()
            # Point recipe current to v1.4 — job must stay on v1.3
            db = get_db()
            db.execute("UPDATE recipes SET current_version_id = 101 WHERE id = 10")
            db.commit()
            jr = pjs.get_job_recipe(jr_id)
            self.assertEqual(int(jr["recipe_version_id"]), 100)
            req = pjs.compute_job_requirement_lines(job_id)
            biscuit = next(x for x in req if x["display_name"].endswith("Biscuit"))
            # 8% of 3600 on locked v1.3 — not 10% from v1.4
            self.assertAlmostEqual(biscuit["required_ml"], 288.0)

    def test_job_linked_mix_and_completion(self):
        with flask_app.app_context():
            job_id, jr_id = self._create_trinidad()
            started = pjs.start_job_linked_mix(
                job_recipe_id=jr_id,
                operator_user_id=1,
                bottle_count=5,
            )
            self.assertEqual(float(started["target_ml"]), 600.0)
            sid = int(started["mix_session_id"])
            row = get_db().execute(
                "SELECT production_job_id, production_job_recipe_id, target_ml FROM mix_sessions WHERE id = ?",
                (sid,),
            ).fetchone()
            self.assertEqual(int(row["production_job_id"]), job_id)
            self.assertEqual(int(row["production_job_recipe_id"]), jr_id)

            overview = pjs.job_overview(job_id)
            self.assertAlmostEqual(overview["in_progress_ml"], 600.0)
            self.assertAlmostEqual(overview["completed_bottles"], 0.0)

            result = pjs.complete_batch_for_guided_session(mix_session_id=sid)
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["posted"])

            overview = pjs.job_overview(job_id)
            self.assertAlmostEqual(overview["completed_ml"], 600.0)
            self.assertAlmostEqual(overview["completed_bottles"], 5.0)
            self.assertAlmostEqual(overview["remaining_bottles"], 25.0)

            inv = pjs.list_job_inventory(job_id)
            biscuit = next(x for x in inv if "Biscuit" in x["display_name"])
            self.assertAlmostEqual(biscuit["used_ml"], 48.0)  # 8% of 600

            batches = pjs.list_job_batches(job_id)
            self.assertEqual(len(batches), 1)
            self.assertEqual(batches[0]["operator_username"], "Wynand")
            self.assertEqual(batches[0]["status"], "completed")

    def test_personal_mix_isolation(self):
        with flask_app.app_context():
            job_id, jr_id = self._create_trinidad()
            pjs.start_job_linked_mix(job_recipe_id=jr_id, operator_user_id=1, bottle_count=5)
            sid_job = get_db().execute(
                "SELECT id FROM mix_sessions WHERE production_job_id = ?",
                (job_id,),
            ).fetchone()["id"]
            pjs.complete_batch_for_guided_session(mix_session_id=int(sid_job))

            # Personal Workshop mix — same recipe, no job ids
            sid, _vid, _steps = guided_mix_session_service.create_guided_mix_session(
                user_id=1,
                recipe_id=10,
                requested_version_id=100,
                target_ml=120.0,
                target_nic_mg_ml=3.0,
                nic_base_mg_ml=100.0,
                pg_ratio=30.0,
                vg_ratio=70.0,
                nicotine_carrier="PG",
            )
            row = get_db().execute(
                "SELECT production_job_id, production_job_recipe_id FROM mix_sessions WHERE id = ?",
                (sid,),
            ).fetchone()
            self.assertIsNone(row["production_job_id"])
            self.assertIsNone(row["production_job_recipe_id"])

            # Completing personal session must not touch job
            get_db().execute(
                "UPDATE mix_sessions SET completed_at = datetime('now') WHERE id = ?",
                (sid,),
            )
            get_db().commit()
            noop = pjs.complete_batch_for_guided_session(mix_session_id=int(sid))
            self.assertIsNone(noop)

            overview = pjs.job_overview(job_id)
            self.assertAlmostEqual(overview["completed_bottles"], 5.0)
            self.assertAlmostEqual(overview["remaining_bottles"], 25.0)
            self.assertEqual(len(pjs.list_job_batches(job_id)), 1)
            biscuit = next(x for x in pjs.list_job_inventory(job_id) if "Biscuit" in x["display_name"])
            self.assertAlmostEqual(biscuit["used_ml"], 48.0)

    def test_cancel_does_not_count(self):
        with flask_app.app_context():
            _job_id, jr_id = self._create_trinidad()
            started = pjs.start_job_linked_mix(job_recipe_id=jr_id, operator_user_id=1, bottle_count=5)
            pjs.cancel_batch(batch_id=int(started["batch_id"]))
            overview = pjs.job_overview(_job_id)
            self.assertAlmostEqual(overview["completed_ml"], 0.0)
            self.assertAlmostEqual(overview["in_progress_ml"], 0.0)
            biscuit = next(x for x in pjs.list_job_inventory(_job_id) if "Biscuit" in x["display_name"])
            self.assertAlmostEqual(biscuit["used_ml"], 0.0)

    def test_operators_wynand_and_riku(self):
        with flask_app.app_context():
            job_id, jr_id = self._create_trinidad()
            a = pjs.start_job_linked_mix(job_recipe_id=jr_id, operator_user_id=1, bottle_count=2)
            b = pjs.start_job_linked_mix(job_recipe_id=jr_id, operator_user_id=2, bottle_count=3)
            pjs.complete_batch_for_guided_session(mix_session_id=int(a["mix_session_id"]))
            pjs.complete_batch_for_guided_session(mix_session_id=int(b["mix_session_id"]))
            batches = pjs.list_job_batches(job_id)
            ops = {int(x["operator_user_id"]): x["operator_username"] for x in batches}
            self.assertEqual(ops[1], "Wynand")
            self.assertEqual(ops[2], "Riku")
            overview = pjs.job_overview(job_id)
            self.assertAlmostEqual(overview["completed_bottles"], 5.0)

    def test_ingredient_rollup_across_recipes(self):
        with flask_app.app_context():
            job_id = pjs.create_job(name="Multi", created_by_user_id=1)
            pjs.add_recipe_to_job(
                job_id=job_id,
                recipe_id=10,
                recipe_version_id=100,
                target_bottle_size_ml=100,
                target_bottle_count=10,  # 1000 ml @ 8% = 80
            )
            pjs.add_recipe_to_job(
                job_id=job_id,
                recipe_id=11,
                recipe_version_id=200,
                target_bottle_size_ml=100,
                target_bottle_count=9,  # 900 ml @ 5% = 45
            )
            lines = pjs.compute_job_requirement_lines(job_id)
            biscuit = next(x for x in lines if x.get("ingredient_id") == 101)
            self.assertAlmostEqual(biscuit["required_ml"], 125.0)

    def test_inventory_maths(self):
        with flask_app.app_context():
            job_id, _jr_id = self._create_trinidad()
            inv = pjs.list_job_inventory(job_id)
            biscuit = next(x for x in inv if "Biscuit" in x["display_name"])
            self.assertAlmostEqual(biscuit["required_ml"], 288.0)
            self.assertEqual(biscuit["stock_status"], "not_purchased")
            pjs.record_purchase(
                job_id=job_id,
                inventory_id=int(biscuit["id"]),
                quantity_ml=200.0,
                recorded_by_user_id=1,
            )
            inv2 = pjs.list_job_inventory(job_id)
            biscuit2 = next(x for x in inv2 if "Biscuit" in x["display_name"])
            self.assertAlmostEqual(biscuit2["purchased_ml"], 200.0)
            self.assertAlmostEqual(biscuit2["still_to_buy_ml"], 88.0)
            self.assertEqual(biscuit2["stock_status"], "short")

    def test_idempotent_completion(self):
        with flask_app.app_context():
            job_id, jr_id = self._create_trinidad()
            started = pjs.start_job_linked_mix(job_recipe_id=jr_id, operator_user_id=1, bottle_count=5)
            sid = int(started["mix_session_id"])
            pjs.complete_batch_for_guided_session(mix_session_id=sid)
            pjs.complete_batch_for_guided_session(mix_session_id=sid)
            overview = pjs.job_overview(job_id)
            self.assertAlmostEqual(overview["completed_ml"], 600.0)
            biscuit = next(x for x in pjs.list_job_inventory(job_id) if "Biscuit" in x["display_name"])
            self.assertAlmostEqual(biscuit["used_ml"], 48.0)

    def test_riku_permissions(self):
        with flask_app.app_context():
            self.assertEqual(production_jobs_access_level("Old Goat", 1), "manage")
            self.assertTrue(can_manage_production_jobs("Old Goat", 1))
            self.assertEqual(production_jobs_access_level("member", 2), "produce")
            self.assertTrue(can_produce_on_production_jobs("member", 2))
            self.assertFalse(can_manage_production_jobs("member", 2))
            self.assertTrue(can_access_production_jobs("member", 2))
            self.assertIsNone(production_jobs_access_level("member", 999))


if __name__ == "__main__":
    unittest.main()
