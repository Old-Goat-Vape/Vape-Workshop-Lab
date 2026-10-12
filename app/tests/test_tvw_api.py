from __future__ import annotations

import os
import tempfile
import unittest

from app import app as flask_app
from db import get_db
from migrations import run_startup_migrations
from models import recipes_repo
from services.mixing_engine import finalize_batch_transaction, finalize_inventory_deduction, reconcile_batch_plan
from utils.auth import ROLE_HIERARCHY as TVW_ROLE_HIERARCHY
from services import recipe_service
from werkzeug.security import generate_password_hash

from core.calculator import ingredient_pour_tolerance_grams, lab_mix_policy, tvw_mix_policy
from services.guided_mix_session_service import complete_guided_step_with_policy, create_guided_mix_session


class TVWApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "tvw_test.db")
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
                "INSERT INTO users (id, username, password_hash, role, status) VALUES (2, 'Old Goat', ?, 'Old Goat', 'Active')",
                (generate_password_hash("123456789"),),
            )
            db.execute(
                "INSERT INTO flavor_reference (id, brand, name, category, density, active) VALUES (101, 'Test', 'Vanilla', 'Flavor', 1.036, 1)"
            )
            db.execute(
                "INSERT INTO recipes (id, name, status, visibility, moderation_status, active, created_by) VALUES (1, 'Recipe1', 'WIP', 'public', 'active', 1, 1)"
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

    def test_mix_quick_post(self):
        response = self.client.post(
            "/api/mix/quick",
            json={
                "recipe_id": 1,
                "version_id": 1,
                "target_ml": 60,
                "target_nic_mg_ml": 3,
                "nic_base_mg_ml": 100,
                "pg_ratio": 30,
                "vg_ratio": 70,
                "nicotine_carrier": "PG",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "success")
        self.assertIn("result", body)
        self.assertEqual(body["resolved_version_id"], 1)
        hist = self.client.get("/api/mix/history?filter=quick")
        self.assertEqual(hist.status_code, 200)
        self.assertTrue(any(i.get("mode") == "quick" for i in hist.get_json().get("items", [])))

    def test_my_mixes_pagination_renders(self):
        for _ in range(22):
            self.client.post(
                "/api/mix/quick",
                json={
                    "recipe_id": 1,
                    "version_id": 1,
                    "target_ml": 30,
                    "target_nic_mg_ml": 0,
                    "nic_base_mg_ml": 100,
                    "pg_ratio": 30,
                    "vg_ratio": 70,
                    "nicotine_carrier": "PG",
                },
            )
        page1 = self.client.get("/my-mixes")
        self.assertEqual(page1.status_code, 200)
        self.assertIn(b"Page 1 of 2", page1.data)
        page2 = self.client.get("/my-mixes?page=2")
        self.assertEqual(page2.status_code, 200)
        self.assertIn(b"Page 2 of 2", page2.data)

    def test_mix_guided_post_and_get(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        self.assertEqual(create.status_code, 201)
        payload = create.get_json()
        sid = payload["mix_session_id"]
        self.assertEqual(payload["resolved_version_id"], 1)

        fetch = self.client.get(f"/api/mix/guided/session/{sid}")
        self.assertEqual(fetch.status_code, 200)
        data = fetch.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["session"]["id"], sid)
        self.assertTrue(len(data["session"]["steps"]) > 0)
        self.assertIn("progress", data["session"])
        add_steps = [
            s for s in data["session"]["steps"]
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        ]
        self.assertTrue(len(add_steps) > 0)
        self.assertEqual((add_steps[0].get("payload") or {}).get("category"), "concentrate")
        self.assertEqual(data["session"].get("nicotine_carrier"), "PG")
        self.assertEqual(float(data["session"].get("nic_base_mg_ml") or 0), 100.0)
        self.assertTrue(bool((data["session"].get("coach_summary") or {}).get("label")))

        lst = self.client.get("/api/mix/guided/sessions")
        self.assertEqual(lst.status_code, 200)
        self.assertTrue(any(row["id"] == sid for row in lst.get_json().get("items", [])))

        done = self.client.post(f"/api/mix/guided/session/{sid}/steps/1/complete", json={})
        self.assertEqual(done.status_code, 200)
        prog = done.get_json()["session"]["progress"]
        self.assertGreaterEqual(prog.get("completed_steps", 0), 1)

    def test_guided_session_sets_completed_at_when_done(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        self.assertEqual(create.status_code, 201)
        sid = create.get_json()["mix_session_id"]
        details = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        self.assertIsNone(details.get("completed_at"))
        for step in details.get("steps", []):
            payload = step.get("payload") or {}
            body = {}
            if str(payload.get("step_type") or "").lower() == "add_ingredient":
                body["measured_grams"] = float(payload.get("target_grams") or payload.get("grams") or 0.0)
            self.client.post(f"/api/mix/guided/session/{sid}/steps/{int(step['step_no'])}/complete", json=body)
        done = self.client.get(f"/api/mix/guided/session/{sid}")
        self.assertEqual(done.status_code, 200)
        payload = done.get_json()["session"]
        self.assertIsNotNone(payload.get("completed_at"))
        self.assertTrue(payload.get("progress", {}).get("all_done"))

    def test_finalize_batch_maps_synthetic_nicotine_minus_two_to_recipe_reference(self):
        """Queue ingredient_id -2 must deduct library_items for the version's nicotine row, not ref -2."""
        with flask_app.app_context():
            db = get_db()
            db.execute(
                """
                INSERT INTO flavor_reference (id, brand, name, category, density, active, is_nicotine)
                VALUES (102, 'NicBrand', 'Nic Base', 'Nicotine', 1.0, 1, 1)
                """
            )
            db.execute(
                "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (1, 102, 0.5)"
            )
            db.execute(
                """
                INSERT INTO library_items (
                    reference_id, current_stock, unit, low_stock_threshold, status, active
                ) VALUES (102, 50.0, 'ml', 10.0, 'In Use', 1)
                """
            )
            db.commit()

            body, code = finalize_batch_transaction(
                payload={
                    "recipe_id": 1,
                    "version_id": 1,
                    "total_volume": 60.0,
                    "has_nicotine": True,
                    "batch_number": "TEST-NIC-FINALIZE",
                    "ingredients": [
                        {"ingredient_id": -2, "actual_grams": 2.0, "target_grams": 2.0},
                    ],
                    "safety_checks": {"gloves": True},
                },
                user_role="Master",
                role_hierarchy=TVW_ROLE_HIERARCHY,
                user_id=1,
                mix_token=None,
                safety_data=None,
                user_is_old_goat=False,
            )
            self.assertEqual(code, 200, msg=str(body))
            row = db.execute(
                "SELECT current_stock FROM library_items WHERE reference_id = 102 AND active = 1"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertAlmostEqual(float(row["current_stock"]), 48.0, places=5)

    def test_finalize_minus_two_without_nicotine_row_skips_library_writes(self):
        """No recipe nicotine row: never touch library_items.reference_id=-2; flavour stock unchanged."""
        with flask_app.app_context():
            db = get_db()
            db.execute(
                """
                INSERT INTO library_items (
                    reference_id, current_stock, unit, low_stock_threshold, status, active
                ) VALUES (101, 100.0, 'ml', 10.0, 'In Use', 1)
                """
            )
            db.commit()

            body, code = finalize_batch_transaction(
                payload={
                    "recipe_id": 1,
                    "version_id": 1,
                    "total_volume": 60.0,
                    "has_nicotine": False,
                    "batch_number": "TEST-NIC-NO-MAP",
                    "ingredients": [
                        {"ingredient_id": -2, "actual_grams": 3.0, "target_grams": 3.0},
                    ],
                    "safety_checks": {"gloves": True},
                },
                user_role="member",
                role_hierarchy=TVW_ROLE_HIERARCHY,
                user_id=1,
                mix_token=None,
                safety_data=None,
                user_is_old_goat=False,
            )
            self.assertEqual(code, 200, msg=str(body))
            bad_ref = db.execute(
                "SELECT COUNT(1) AS c FROM library_items WHERE reference_id = -2"
            ).fetchone()
            self.assertEqual(int(bad_ref["c"]), 0)
            vanilla = db.execute(
                "SELECT current_stock FROM library_items WHERE reference_id = 101 AND active = 1"
            ).fetchone()
            self.assertAlmostEqual(float(vanilla["current_stock"]), 100.0, places=4)

    def test_finalize_scrapped_batch_still_deducts_mapped_nicotine_stock(self):
        """Scrapping after overpour must still deduct physical pours (-2 mapped to nicotine reference)."""
        with flask_app.app_context():
            db = get_db()
            db.execute(
                """
                INSERT INTO flavor_reference (id, brand, name, category, density, active, is_nicotine)
                VALUES (102, 'NicBrand2', 'Nic Base 2', 'Nicotine', 1.0, 1, 1)
                """
            )
            db.execute(
                "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (1, 102, 0.5)"
            )
            db.execute(
                """
                INSERT INTO library_items (
                    reference_id, current_stock, unit, low_stock_threshold, status, active
                ) VALUES (102, 40.0, 'ml', 10.0, 'In Use', 1)
                """
            )
            db.commit()

            body, code = finalize_batch_transaction(
                payload={
                    "recipe_id": 1,
                    "version_id": 1,
                    "total_volume": 60.0,
                    "has_nicotine": False,
                    "scrapped": True,
                    "scrap_reason": "overpour_lab_verify",
                    "batch_number": "TEST-SCRAP-NIC",
                    "ingredients": [
                        {"ingredient_id": -2, "actual_grams": 2.5, "target_grams": 2.5},
                    ],
                    "safety_checks": {"gloves": True},
                },
                user_role="member",
                role_hierarchy=TVW_ROLE_HIERARCHY,
                user_id=1,
                mix_token=None,
                safety_data=None,
                user_is_old_goat=False,
            )
            self.assertEqual(code, 200, msg=str(body))
            row = db.execute(
                "SELECT ready_for_sale, formula_origin FROM mix_history WHERE id = ?",
                (int(body.get("batch_id")),),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIn("scrap", str(row["formula_origin"] or "").lower())
            if row["ready_for_sale"] is not None:
                self.assertEqual(int(row["ready_for_sale"]), 0)
            nic = db.execute(
                "SELECT current_stock FROM library_items WHERE reference_id = 102 AND active = 1"
            ).fetchone()
            self.assertAlmostEqual(float(nic["current_stock"]), 37.5, places=5)

    def test_guided_lab_policy_blocked_progression_persists_dynamic_steps(self):
        """Lab-strict ordering: out-of-sequence attempt inserts policy rows and commits before returning."""
        with flask_app.app_context():
            sid, _, _ = create_guided_mix_session(
                user_id=1,
                recipe_id=1,
                requested_version_id=None,
                target_ml=30,
                target_nic_mg_ml=0,
                nic_base_mg_ml=100,
                pg_ratio=30,
                vg_ratio=70,
                nicotine_carrier="PG",
            )
            db = get_db()
            before_n = int(
                db.execute(
                    "SELECT COUNT(1) AS c FROM mix_session_steps WHERE mix_session_id = ?",
                    (sid,),
                ).fetchone()["c"]
            )
            outcome = complete_guided_step_with_policy(
                session_id=sid,
                user_id=1,
                step_no=9_999,
                measured_grams=None,
                policy=lab_mix_policy(),
            )
            self.assertFalse(outcome.get("ok"))
            after_n = int(
                db.execute(
                    "SELECT COUNT(1) AS c FROM mix_session_steps WHERE mix_session_id = ?",
                    (sid,),
                ).fetchone()["c"]
            )
            self.assertGreater(after_n, before_n)

    def test_guided_mix_web_pages_render(self):
        with flask_app.app_context():
            db = get_db()
            db.execute("UPDATE recipes SET slug='recipe-one', current_version_id=1 WHERE id=1")
            db.commit()
        r_hub = self.client.get("/guided-mix", follow_redirects=True)
        self.assertEqual(r_hub.status_code, 200)
        self.assertIn(b"My mixes", r_hub.data)
        self.assertEqual(self.client.get("/my-mixes").status_code, 200)
        legacy = self.client.get("/recipes/recipe-one/guided-mix", follow_redirects=False)
        self.assertEqual(legacy.status_code, 302)
        self.assertIn("/recipes/recipe-one", legacy.headers.get("Location", ""))
        c = self.client.post("/api/mix/guided/session", json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0})
        sid = c.get_json()["mix_session_id"]
        self.assertEqual(self.client.get(f"/guided-mix/session/{sid}").status_code, 200)

    def test_guided_dynamic_event_inserts_recovery_step(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        self.assertEqual(create.status_code, 201)
        sid = create.get_json()["mix_session_id"]
        before = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        total_before = int(before.get("progress", {}).get("total_steps") or 0)

        ev = self.client.post(
            f"/api/mix/guided/session/{sid}/events",
            json={
                "event_type": "overpour",
                "message": "Overpour detected on active step.",
                "detail": "Run reconcile before continuing.",
                "severity": "danger",
                "tone_profile": "lab",
                "context": {
                    "ingredient_id": 11,
                    "ingredient_name": "Test Ingredient",
                    "target_amount": 1.2,
                    "measured_amount": 1.5,
                },
            },
        )
        self.assertEqual(ev.status_code, 200)
        payload = ev.get_json()
        self.assertIsNotNone(payload.get("inserted_step_no"))
        sess = payload.get("session") or {}
        total_after = int(sess.get("progress", {}).get("total_steps") or 0)
        self.assertGreater(total_after, total_before)
        dyn = [
            s for s in (sess.get("steps") or [])
            if bool((s.get("payload") or {}).get("dynamic"))
            and str((s.get("payload") or {}).get("source_event") or "") == "overpour"
        ]
        self.assertTrue(len(dyn) > 0)
        self.assertEqual(str(dyn[0].get("step_type") or ""), "recovery")
        refresh = self.client.get(f"/api/mix/guided/session/{sid}")
        self.assertEqual(refresh.status_code, 200)
        self.assertEqual(
            len(refresh.get_json().get("session", {}).get("steps") or []),
            len((sess.get("steps") or [])),
        )
        dp = dyn[0].get("payload") or {}
        self.assertEqual(dp.get("ingredient_id"), 11)
        self.assertEqual(dp.get("ingredient_name"), "Test Ingredient")
        self.assertAlmostEqual(float(dp.get("target_amount") or 0.0), 1.2, places=3)
        self.assertAlmostEqual(float(dp.get("measured_amount") or 0.0), 1.5, places=3)
        self.assertAlmostEqual(float(dp.get("delta_amount") or 0.0), 0.3, places=3)

    def test_guided_add_ingredient_requires_measured_reading(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        self.assertEqual(create.status_code, 201)
        sid = create.get_json()["mix_session_id"]
        sess = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        add_step = next(
            s for s in (sess.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        )
        step_no = int(add_step["step_no"])
        target = float((add_step.get("payload") or {}).get("target_grams") or 0.0)
        self.assertGreater(target, 0.0)

        miss = self.client.post(f"/api/mix/guided/session/{sid}/steps/{step_no}/complete", json={})
        self.assertEqual(miss.status_code, 409)

        # Tiny under-pour must allow progression guidance (not hard stop), but wait for accept.
        slight = round(max(0.01, round(target, 2) - 0.01), 2)
        under = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": slight},
        )
        self.assertEqual(under.status_code, 200)
        body = under.get_json() or {}
        classification = str((body.get("reading") or {}).get("classification") or "")
        self.assertIn(classification, {"slight_under_target", "large_under_target", "within_tolerance"})
        self.assertFalse(bool((body.get("policy") or {}).get("hard_stops")))
        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        original = next(s for s in after["steps"] if int(s["step_no"]) == step_no)
        if classification in {"slight_under_target", "large_under_target"}:
            self.assertTrue(bool(body.get("awaiting_under_decision")))
            self.assertIsNone(original.get("completed_at"))
            accepted = self.client.post(
                f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
                json={"measured_grams": slight, "accept_under": True},
            )
            self.assertEqual(accepted.status_code, 200)
            done = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
            original = next(s for s in done["steps"] if int(s["step_no"]) == step_no)
            self.assertIsNotNone(original.get("completed_at"))
        else:
            self.assertIsNotNone(original.get("completed_at"))

    def test_guided_slight_under_pour_allows_continue(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        sess = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        add_step = next(
            s for s in (sess.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        )
        step_no = int(add_step["step_no"])
        # Force a tiny display target case via payload override is not needed — use display rounding.
        raw_target = float((add_step.get("payload") or {}).get("target_grams") or 0.0)
        display = round(raw_target, 2)
        measured_030 = round(display - 0.01, 2)
        resp = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured_030},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json() or {}
        reading = body.get("reading") or {}
        self.assertAlmostEqual(float(reading.get("display_target_grams") or 0), display, places=2)
        self.assertIn(
            str(reading.get("classification") or ""),
            {"slight_under_target", "within_tolerance", "large_under_target"},
        )
        warnings = (body.get("policy") or {}).get("warnings") or []
        hard = (body.get("policy") or {}).get("hard_stops") or []
        self.assertEqual(hard, [])
        if str(reading.get("classification")) == "slight_under_target":
            self.assertTrue(bool(body.get("awaiting_under_decision")))
            self.assertTrue(any("Slightly under by" in str(w) for w in warnings))
            self.assertTrue(any("Close enough for this pour" in str(w) for w in warnings))
            self.assertTrue(any("One more drop may put you over target" in str(w) for w in warnings))

    def test_guided_slight_under_decision_accept_moves_to_bottle_checkpoint(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        sess = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        add_step = next(
            s for s in (sess.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        )
        step_no = int(add_step["step_no"])
        display = round(float((add_step.get("payload") or {}).get("target_grams") or 0.0), 2)
        measured = round(display - 0.01, 2)
        under = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured},
        )
        self.assertEqual(under.status_code, 200)
        body = under.get_json() or {}
        classification = str((body.get("reading") or {}).get("classification") or "")
        if classification == "within_tolerance":
            self.skipTest("measurement landed in tolerance for this recipe target")
        self.assertTrue(bool(body.get("awaiting_under_decision")))
        pending = next(
            s for s in (body.get("session") or {}).get("steps") or []
            if int(s["step_no"]) == step_no
        )
        self.assertIsNone(pending.get("completed_at"))
        self.assertTrue(bool((pending.get("payload") or {}).get("under_decision_pending")))

        accepted = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured, "accept_under": True},
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertFalse(bool(accepted.get_json().get("awaiting_under_decision")))
        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        original = next(s for s in after["steps"] if int(s["step_no"]) == step_no)
        self.assertIsNotNone(original.get("completed_at"))
        self.assertFalse(bool((original.get("payload") or {}).get("under_decision_pending")))
        # Bottle-moved checkpoint immediately after this pour (by step_no), not global current.
        transition = next(
            (
                s
                for s in after["steps"]
                if int(s["step_no"]) > step_no
                and str((s.get("payload") or {}).get("step_type") or "").lower() == "transition"
            ),
            None,
        )
        self.assertIsNotNone(transition)
        name = str((add_step.get("payload") or {}).get("name") or "")
        tname = str((transition.get("payload") or {}).get("name") or (transition.get("payload") or {}).get("ingredient_name") or "")
        if name:
            self.assertIn(name.split()[0].lower(), tname.lower() or name.lower())

    def test_guided_try_one_drop_keeps_ingredient_active(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        sess = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        add_step = next(
            s for s in (sess.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        )
        step_no = int(add_step["step_no"])
        display = round(float((add_step.get("payload") or {}).get("target_grams") or 0.0), 2)
        measured = round(display - 0.01, 2)
        under = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured},
        )
        body = under.get_json() or {}
        if str((body.get("reading") or {}).get("classification") or "") == "within_tolerance":
            self.skipTest("measurement landed in tolerance for this recipe target")
        self.assertTrue(bool(body.get("awaiting_under_decision")))
        # Client "TRY ONE DROP" does not complete — re-check same reading stays pending.
        again = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured},
        )
        self.assertEqual(again.status_code, 200)
        again_body = again.get_json() or {}
        self.assertTrue(bool(again_body.get("awaiting_under_decision")))
        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        original = next(s for s in after["steps"] if int(s["step_no"]) == step_no)
        self.assertIsNone(original.get("completed_at"))
        self.assertTrue(bool((original.get("payload") or {}).get("under_decision_pending")))
        self.assertEqual(
            str((original.get("payload") or {}).get("step_type") or "").lower(),
            "add_ingredient",
        )

    def test_guided_try_one_drop_then_over_enters_recovery(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        sess = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        add_step = next(
            s for s in (sess.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        )
        step_no = int(add_step["step_no"])
        target = float((add_step.get("payload") or {}).get("target_grams") or 0.0)
        display = round(target, 2)
        under_m = round(display - 0.01, 2)
        under = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": under_m},
        )
        if str(((under.get_json() or {}).get("reading") or {}).get("classification") or "") == "within_tolerance":
            self.skipTest("measurement landed in tolerance for this recipe target")
        over_m = round(target * 1.5, 4)
        over = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": over_m},
        )
        self.assertEqual(over.status_code, 200)
        body = over.get_json() or {}
        self.assertFalse(bool(body.get("awaiting_under_decision")))
        self.assertIn(
            str((body.get("reading") or {}).get("classification") or ""),
            {"over_target", "over_target_recovery_required"},
        )
        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        current_no = int((after.get("progress") or {}).get("current_step_no") or 0)
        current = next(s for s in after["steps"] if int(s["step_no"]) == current_no)
        self.assertTrue(bool((current.get("payload") or {}).get("dynamic")))
        self.assertEqual(str((current.get("payload") or {}).get("source_event") or ""), "overpour")

    def test_guided_try_one_drop_then_hit_target_completes(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        sess = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        add_step = next(
            s for s in (sess.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        )
        step_no = int(add_step["step_no"])
        target = float((add_step.get("payload") or {}).get("target_grams") or 0.0)
        display = round(target, 2)
        under_m = round(display - 0.01, 2)
        under = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": under_m},
        )
        if str(((under.get_json() or {}).get("reading") or {}).get("classification") or "") == "within_tolerance":
            self.skipTest("measurement landed in tolerance for this recipe target")
        hit = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": target},
        )
        self.assertEqual(hit.status_code, 200)
        body = hit.get_json() or {}
        self.assertFalse(bool(body.get("awaiting_under_decision")))
        self.assertEqual(str((body.get("reading") or {}).get("classification") or ""), "within_tolerance")
        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        original = next(s for s in after["steps"] if int(s["step_no"]) == step_no)
        self.assertIsNotNone(original.get("completed_at"))

    def test_guided_hidden_decimal_under_does_not_block(self):
        """Internal 0.314 shown as 0.31; measured 0.30 must not hard-block."""
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        sess = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        add_step = next(
            s for s in (sess.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        )
        step_no = int(add_step["step_no"])
        # Patch step payload to a hidden-decimal target (survives only if we write via DB).
        with flask_app.app_context():
            from db import get_db
            import json as _json

            db = get_db()
            row = db.execute(
                "SELECT id, payload_json FROM mix_session_steps WHERE mix_session_id=? AND step_no=?",
                (sid, step_no),
            ).fetchone()
            payload = _json.loads(row["payload_json"])
            payload["target_grams"] = 0.314
            payload["grams"] = 0.314
            payload["ml"] = 0.303
            db.execute(
                "UPDATE mix_session_steps SET payload_json=? WHERE id=?",
                (_json.dumps(payload), row["id"]),
            )
            db.commit()

        resp = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": 0.30},
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:300])
        body = resp.get_json() or {}
        reading = body.get("reading") or {}
        self.assertAlmostEqual(float(reading.get("display_target_grams") or 0), 0.31, places=2)
        self.assertNotEqual(str(reading.get("classification")), "under_target")
        self.assertEqual((body.get("policy") or {}).get("hard_stops") or [], [])
        self.assertIn(
            str(reading.get("classification") or ""),
            {"slight_under_target", "large_under_target", "within_tolerance"},
        )

    def test_guided_over_target_triggers_recovery_warning(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        sess = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        add_step = next(
            s for s in (sess.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        )
        step_no = int(add_step["step_no"])
        target = float((add_step.get("payload") or {}).get("target_grams") or 0.0)
        display = round(target, 2)
        # Small over: controlled slight-over, not vague drift / not recovery panic.
        measured = round(display + 0.01, 2)
        resp = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json() or {}
        classification = str((body.get("reading") or {}).get("classification") or "")
        self.assertEqual(classification, "slight_over_target")
        warnings = (body.get("policy") or {}).get("warnings") or []
        joined = "\n".join(str(w) for w in warnings)
        self.assertIn("Slightly over by", joined)
        self.assertIn("Small overpour detected", joined)
        self.assertNotIn("Minor drift detected", joined)

    def test_guided_slight_over_checkpoint_note_and_optional_base_adjust(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        sess = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        add_step = next(
            s for s in (sess.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        )
        step_no = int(add_step["step_no"])
        display = round(float((add_step.get("payload") or {}).get("target_grams") or 0.0), 2)
        measured = round(display + 0.01, 2)
        before_bases = [
            s
            for s in (sess.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
            and str((s.get("payload") or {}).get("category") or "").lower() in {"pg", "vg", "base", "base_liquid"}
            and int(s["step_no"]) != step_no
        ]
        resp = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json() or {}
        reading = body.get("reading") or {}
        self.assertEqual(str(reading.get("classification") or ""), "slight_over_target")
        warnings = (body.get("policy") or {}).get("warnings") or []
        joined = "\n".join(str(w) for w in warnings)
        self.assertRegex(joined, r"Slightly over by 0\.0[0-9] g")
        self.assertNotRegex(joined, r"(?i)minor drift")
        if before_bases:
            self.assertTrue(
                "pg was adjusted" in joined.lower()
                or "keep the next pours controlled" in joined.lower()
            )
            if reading.get("base_adjusted"):
                self.assertIn("pg was adjusted", joined.lower())
                after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
                after_by_no = {int(s["step_no"]): s for s in after["steps"]}
                trimmed = False
                for b in before_bases:
                    old_g = float((b.get("payload") or {}).get("target_grams") or 0.0)
                    new_g = float((after_by_no[int(b["step_no"])].get("payload") or {}).get("target_grams") or 0.0)
                    if new_g + 1e-9 < old_g:
                        trimmed = True
                self.assertTrue(trimmed)
            else:
                self.assertIn("keep the next pours controlled", joined.lower())
        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        original = next(s for s in after["steps"] if int(s["step_no"]) == step_no)
        self.assertIsNotNone(original.get("completed_at"))
        transition = next(
            (
                s
                for s in after["steps"]
                if int(s["step_no"]) > step_no
                and str((s.get("payload") or {}).get("step_type") or "").lower() == "transition"
            ),
            None,
        )
        self.assertIsNotNone(transition)
        note = str((transition.get("payload") or {}).get("checkpoint_note") or "")
        self.assertIn("Slightly over by", note)
        self.assertIn("Small overpour detected", note)
        coach = str(((after.get("coach_summary") or {}).get("detail") or ""))
        self.assertNotIn("Minor drift detected", coach)

    def test_guided_serious_overpour_still_enters_recovery(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        sess = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        add_step = next(
            s for s in (sess.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        )
        step_no = int(add_step["step_no"])
        target = float((add_step.get("payload") or {}).get("target_grams") or 0.0)
        measured = round(target * 1.5, 4)
        resp = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json() or {}
        self.assertIn(
            str((body.get("reading") or {}).get("classification") or ""),
            {"over_target", "over_target_recovery_required"},
        )
        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        current_no = int((after.get("progress") or {}).get("current_step_no") or 0)
        current = next(s for s in after["steps"] if int(s["step_no"]) == current_no)
        self.assertTrue(bool((current.get("payload") or {}).get("dynamic")))
        self.assertEqual(str((current.get("payload") or {}).get("source_event") or ""), "overpour")
        self.assertTrue(bool(body.get("awaiting_overpour_recovery")))

    def _first_add_ingredient_step(self, sid: int) -> tuple[int, float]:
        sess = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        add_step = next(
            s for s in (sess.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        )
        step_no = int(add_step["step_no"])
        target = float((add_step.get("payload") or {}).get("target_grams") or 0.0)
        return step_no, target

    def _trigger_serious_flavour_overpour(self, sid: int) -> tuple[dict, dict]:
        step_no, target = self._first_add_ingredient_step(sid)
        measured = round(target * 1.5, 4)
        resp = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured},
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:400])
        body = resp.get_json() or {}
        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        return body, after

    def test_guided_serious_overpour_defers_pour_until_recovery_resolved(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        body, after = self._trigger_serious_flavour_overpour(sid)
        self.assertTrue(bool(body.get("awaiting_overpour_recovery")))
        pending = [
            s for s in (after.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
            and bool((s.get("payload") or {}).get("overpour_recovery_pending"))
        ]
        self.assertEqual(len(pending), 1)
        self.assertIsNone(pending[0].get("completed_at"))
        current_no = int((after.get("progress") or {}).get("current_step_no") or 0)
        current = next(s for s in after["steps"] if int(s["step_no"]) == current_no)
        self.assertEqual(str((current.get("payload") or {}).get("step_type") or "").lower(), "recovery")

    def test_guided_serious_overpour_recovery_ack_completes_deferred_pour(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        _body, after = self._trigger_serious_flavour_overpour(sid)
        recovery_no = int((after.get("progress") or {}).get("current_step_no") or 0)
        ack = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{recovery_no}/complete",
            json={},
        )
        self.assertEqual(ack.status_code, 200)
        after_ack = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        resolved = [
            s for s in (after_ack.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
            and bool((s.get("payload") or {}).get("overpour_recovery_resolved"))
        ]
        self.assertEqual(len(resolved), 1)
        self.assertIsNotNone(resolved[0].get("completed_at"))

    def test_guided_serious_overpour_recovery_step_action_label(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        _body, after = self._trigger_serious_flavour_overpour(sid)
        current_no = int((after.get("progress") or {}).get("current_step_no") or 0)
        current = next(s for s in after["steps"] if int(s["step_no"]) == current_no)
        label = str((current.get("payload") or {}).get("action_label") or "")
        self.assertIn(label.lower(), {"review recovery plan", "apply correction"})
        self.assertNotEqual(label.lower(), "continue")

    def test_guided_serious_overpour_recovery_ui_template(self):
        import os

        tpl_path = os.path.join(flask_app.root_path, "templates", "tvw_guided_mix_session.html")
        with open(tpl_path, encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("seriousOverpourRecoveryNote", html)
        self.assertIn("Review recovery plan", html)
        self.assertNotIn('recovery: "Continue"', html)
        self.assertIn("Resolve ${resolveName} before moving on.", html)
        self.assertIn('stepType === "recovery" || stepType === "caution"', html)
        self.assertIn("overpoured by", html)

    def _create_guided_session_with_nicotine(self, *, target_ml: float = 60, strictness: str = "personal") -> int:
        create = self.client.post(
            "/api/mix/guided/session",
            json={
                "recipe_id": 1,
                "target_ml": target_ml,
                "target_nic_mg_ml": 3,
                "nic_base_mg_ml": 100,
                "pg_ratio": 30,
                "vg_ratio": 70,
                "nicotine_carrier": "PG",
                "nicotine_strictness_mode": strictness,
            },
        )
        self.assertIn(create.status_code, (200, 201), create.get_data(as_text=True)[:300])
        return int(create.get_json()["mix_session_id"])

    def _advance_to_nicotine_add_step(self, sid: int) -> tuple[int, dict]:
        while True:
            sess = self._guided_session_steps(sid)
            current = next(s for s in sess if not s.get("completed_at"))
            payload = current.get("payload") or {}
            step_type = str(payload.get("step_type") or "").lower()
            if step_type == "add_ingredient" and str(payload.get("category") or "").lower() == "nicotine":
                return int(current["step_no"]), current
            done = self.client.post(
                f"/api/mix/guided/session/{sid}/steps/{int(current['step_no'])}/complete",
                json={},
            )
            self.assertEqual(done.status_code, 200, f"step {current['step_no']} type {step_type}")

    def _nicotine_recovery_log(self, sid: int) -> list[dict]:
        with flask_app.app_context():
            import json as _json

            row = get_db().execute("SELECT result_json FROM mix_sessions WHERE id=?", (sid,)).fetchone()
            meta = _json.loads(row["result_json"] or "{}")
            log = meta.get("nicotine_recovery_log")
            return list(log) if isinstance(log, list) else []

    def _base_step_targets(self, sid: int) -> dict[int, float]:
        out: dict[int, float] = {}
        for s in self._guided_session_steps(sid):
            payload = s.get("payload") or {}
            if str(payload.get("step_type") or "").lower() != "add_ingredient":
                continue
            cat = str(payload.get("category") or "").lower()
            if cat not in {"pg", "vg", "base", "base_liquid"}:
                continue
            out[int(s["step_no"])] = float(payload.get("target_grams") or 0.0)
        return out

    def test_guided_nicotine_overpour_no_pg_vg_rebalance(self):
        sid = self._create_guided_session_with_nicotine()
        nic_no, nic_step = self._advance_to_nicotine_add_step(sid)
        bases_before = self._base_step_targets(sid)
        target = float((nic_step.get("payload") or {}).get("target_grams") or 0.0)
        self.assertGreater(target, 0.0)
        over = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": round(target + 0.15, 2)},
        )
        self.assertEqual(over.status_code, 200)
        body = over.get_json() or {}
        self.assertTrue(bool(body.get("awaiting_nicotine_recovery")))
        self.assertEqual(str((body.get("reading") or {}).get("classification") or ""), "nicotine_over_target")
        self.assertTrue(bool((body.get("session") or {}).get("steps")))
        payload = next(
            s for s in (body.get("session") or {}).get("steps") or []
            if int(s["step_no"]) == nic_no
        ).get("payload") or {}
        self.assertTrue(bool(payload.get("nicotine_overpour_pending")))
        self.assertNotIn("base_adjusted", body.get("reading") or {})
        after = self._guided_session_steps(sid)
        nic_open = next(s for s in after if int(s["step_no"]) == nic_no)
        self.assertIsNone(nic_open.get("completed_at"))
        self.assertFalse(any(bool((s.get("payload") or {}).get("dynamic")) for s in after))
        bases_after = self._base_step_targets(sid)
        self.assertEqual(bases_before, bases_after)
        events = {str(e.get("action") or e.get("event") or "") for e in self._nicotine_recovery_log(sid)}
        self.assertIn("nicotine_overpour_detected", events)

    def test_guided_nicotine_overpour_pour_back_recovery(self):
        sid = self._create_guided_session_with_nicotine()
        nic_no, nic_step = self._advance_to_nicotine_add_step(sid)
        target = float((nic_step.get("payload") or {}).get("target_grams") or 0.0)
        over = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": round(target + 0.20, 2)},
        )
        self.assertTrue(bool((over.get_json() or {}).get("awaiting_nicotine_recovery")))
        poured = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"nicotine_action": "poured_back"},
        )
        self.assertEqual(poured.status_code, 200)
        body = poured.get_json() or {}
        self.assertTrue(bool(body.get("nicotine_recovery_active")))
        payload = next(
            s for s in (body.get("session") or {}).get("steps") or []
            if int(s["step_no"]) == nic_no
        ).get("payload") or {}
        self.assertTrue(bool(payload.get("nicotine_recovery_mode")))
        self.assertFalse(bool(payload.get("nicotine_overpour_pending")))
        events = [str(e.get("event") or "") for e in self._nicotine_recovery_log(sid)]
        self.assertIn("nicotine_poured_back", events)

    def test_guided_nicotine_recovery_keeps_same_step_active(self):
        sid = self._create_guided_session_with_nicotine()
        nic_no, nic_step = self._advance_to_nicotine_add_step(sid)
        target = float((nic_step.get("payload") or {}).get("target_grams") or 0.0)
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": round(target + 0.12, 2)},
        )
        after_over = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        self.assertEqual(int((after_over.get("progress") or {}).get("current_step_no") or 0), nic_no)
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"nicotine_action": "poured_back"},
        )
        after_back = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        self.assertEqual(int((after_back.get("progress") or {}).get("current_step_no") or 0), nic_no)
        nic_row = next(s for s in after_back["steps"] if int(s["step_no"]) == nic_no)
        self.assertIsNone(nic_row.get("completed_at"))

    def test_guided_nicotine_recovery_needed_from_current_scale(self):
        sid = self._create_guided_session_with_nicotine()
        nic_no, nic_step = self._advance_to_nicotine_add_step(sid)
        target = float((nic_step.get("payload") or {}).get("target_grams") or 0.0)
        display = round(target, 2)
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": round(display + 0.25, 2)},
        )
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"nicotine_action": "poured_back"},
        )
        residue = 0.08
        check = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": residue},
        )
        self.assertEqual(check.status_code, 200)
        reading = (check.get_json() or {}).get("reading") or {}
        self.assertAlmostEqual(float(reading.get("needed_grams") or 0), round(display - residue, 2), places=2)
        self.assertAlmostEqual(float(reading.get("recovery_current_scale_grams") or 0), residue, places=2)

    def test_guided_nicotine_recovery_warns_do_not_tare(self):
        sid = self._create_guided_session_with_nicotine()
        nic_no, nic_step = self._advance_to_nicotine_add_step(sid)
        target = float((nic_step.get("payload") or {}).get("target_grams") or 0.0)
        over = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": round(target + 0.15, 2)},
        )
        warnings = "\n".join(str(w) for w in ((over.get_json() or {}).get("policy") or {}).get("warnings") or [])
        self.assertIn("Do not tare", warnings)
        poured = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"nicotine_action": "poured_back"},
        )
        warnings2 = "\n".join(str(w) for w in ((poured.get_json() or {}).get("policy") or {}).get("warnings") or [])
        self.assertIn("Do not tare", warnings2)

    def test_guided_nicotine_recovery_still_over_after_pour_back(self):
        sid = self._create_guided_session_with_nicotine()
        nic_no, nic_step = self._advance_to_nicotine_add_step(sid)
        target = float((nic_step.get("payload") or {}).get("target_grams") or 0.0)
        display = round(target, 2)
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": round(display + 0.30, 2)},
        )
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"nicotine_action": "poured_back"},
        )
        still_over = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": round(display + 0.05, 2)},
        )
        body = still_over.get_json() or {}
        self.assertTrue(bool(body.get("awaiting_nicotine_recovery")))
        self.assertEqual(str((body.get("reading") or {}).get("classification") or ""), "nicotine_still_over_target")
        joined = "\n".join(str(w) for w in (body.get("policy") or {}).get("warnings") or [])
        self.assertIn("Still over target", joined)
        self.assertIn("Do not tare", joined)

    def test_guided_nicotine_recovery_complete_logs_warning(self):
        sid = self._create_guided_session_with_nicotine()
        nic_no, nic_step = self._advance_to_nicotine_add_step(sid)
        target = float((nic_step.get("payload") or {}).get("target_grams") or 0.0)
        display = round(target, 2)
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": round(display + 0.18, 2)},
        )
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"nicotine_action": "poured_back"},
        )
        hit = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": display},
        )
        self.assertEqual(hit.status_code, 200)
        self.assertFalse(bool((hit.get_json() or {}).get("awaiting_nicotine_recovery")))
        events = self._nicotine_recovery_log(sid)
        self.assertTrue(any(str(e.get("event") or "") == "nicotine_recovery_complete" for e in events))
        with flask_app.app_context():
            import json as _json

            row = get_db().execute("SELECT result_json FROM mix_sessions WHERE id=?", (sid,)).fetchone()
            meta = _json.loads(row["result_json"] or "{}")
            self.assertEqual(meta.get("nicotine_recovery_warning"), "Nicotine recovery used. Scale was not tared.")

    def test_guided_flavour_overpour_still_uses_existing_rebalance_logic(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        add_step = self._guided_first_add_step(sid)
        step_no = int(add_step["step_no"])
        self.assertEqual(str((add_step.get("payload") or {}).get("category") or "").lower(), "concentrate")
        display = round(float((add_step.get("payload") or {}).get("target_grams") or 0.0), 2)
        resp = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": round(display + 0.01, 2)},
        )
        self.assertEqual(
            str(((resp.get_json() or {}).get("reading") or {}).get("classification") or ""),
            "slight_over_target",
        )
        self.assertFalse(bool((resp.get_json() or {}).get("awaiting_nicotine_recovery")))

    def test_guided_session_exposes_batch_size_and_target_ratio(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={
                "recipe_id": 1,
                "target_ml": 30,
                "target_nic_mg_ml": 0,
                "pg_ratio": 50,
                "vg_ratio": 50,
            },
        )
        sid = create.get_json()["mix_session_id"]
        sess = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        self.assertAlmostEqual(float(sess.get("batch_size_ml") or 0), 30.0)
        self.assertAlmostEqual(float(sess.get("target_pg_percent") or 0), 50.0)
        self.assertAlmostEqual(float(sess.get("target_vg_percent") or 0), 50.0)

    def test_guided_30ml_vg_target_scales_with_batch_not_100(self):
        create30 = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0, "pg_ratio": 30, "vg_ratio": 70},
        )
        sid30 = create30.get_json()["mix_session_id"]
        create100 = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 100, "target_nic_mg_ml": 0, "pg_ratio": 30, "vg_ratio": 70},
        )
        sid100 = create100.get_json()["mix_session_id"]
        _vg30_no, vg30 = self._advance_to_add_ingredient_category(sid30, "vg")
        _vg100_no, vg100 = self._advance_to_add_ingredient_category(sid100, "vg")
        p30 = vg30.get("payload") or {}
        p100 = vg100.get("payload") or {}
        ml30 = float(p30.get("ml") or p30.get("target_ml") or 0.0)
        ml100 = float(p100.get("ml") or p100.get("target_ml") or 0.0)
        self.assertGreater(ml30, 0.0)
        self.assertGreater(ml100, 0.0)
        self.assertAlmostEqual(ml30 / ml100, 0.3, delta=0.06)
        self.assertLess(abs(ml30 - 21.0), 3.0)

    def test_guided_60ml_flavour_over_pg_shows_adjusted_targets(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={
                "recipe_id": 1,
                "target_ml": 60,
                "target_nic_mg_ml": 0,
                "pg_ratio": 30,
                "vg_ratio": 70,
            },
        )
        sid = create.get_json()["mix_session_id"]
        fl_step = self._guided_first_add_step(sid)
        fl_no = int(fl_step["step_no"])
        display = round(float((fl_step.get("payload") or {}).get("target_grams") or 0.0), 2)
        resp = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{fl_no}/complete",
            json={"measured_grams": round(display + 0.01, 2)},
        )
        self.assertEqual(resp.status_code, 200)
        reading = (resp.get_json() or {}).get("reading") or {}
        self.assertTrue(bool(reading.get("base_adjusted")))
        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        pg_step = next(
            s
            for s in (after.get("steps") or [])
            if str((s.get("payload") or {}).get("category") or "").lower() == "pg"
            and str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        )
        pg_payload = pg_step.get("payload") or {}
        note = str(pg_payload.get("pg_adjustment_note") or "")
        self.assertIn("PG adjusted to protect final batch volume and ratio.", note)
        self.assertIn("Original PG target:", note)
        self.assertIn("Adjusted PG target:", note)
        self.assertIn("Target ratio: 30/70", note)
        self.assertGreater(
            float(pg_payload.get("original_pg_target_grams") or 0.0),
            float(pg_payload.get("target_grams") or 0.0),
        )

    def test_guided_vg_tolerance_scales_with_batch_size(self):
        policy = tvw_mix_policy()
        tol10 = ingredient_pour_tolerance_grams(
            ingredient_category="vg",
            target_grams=7.0,
            batch_size_ml=10.0,
            policy=policy,
            direction="over",
        )
        tol100 = ingredient_pour_tolerance_grams(
            ingredient_category="vg",
            target_grams=70.0,
            batch_size_ml=100.0,
            policy=policy,
            direction="over",
        )
        self.assertGreater(tol100, tol10)
        self.assertGreater(tol10, 0.15)
        self.assertLess(tol10, 0.35)

    def test_guided_flavour_over_adjusts_pg_without_altering_nicotine(self):
        sid = self._create_guided_session_with_nicotine(target_ml=60)
        nic_no, nic_step = self._advance_to_nicotine_add_step(sid)
        nic_target = float((nic_step.get("payload") or {}).get("target_grams") or 0.0)
        nic_done = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": round(nic_target, 2)},
        )
        self.assertEqual(nic_done.status_code, 200)
        bases_before = self._base_step_targets(sid)
        fl_no, fl_step = self._advance_to_add_ingredient_category(sid, "concentrate")
        display = round(float((fl_step.get("payload") or {}).get("target_grams") or 0.0), 2)
        over = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{fl_no}/complete",
            json={"measured_grams": round(display + 0.01, 2)},
        )
        self.assertEqual(over.status_code, 200)
        self.assertTrue(bool(((over.get_json() or {}).get("reading") or {}).get("base_adjusted")))
        bases_after = self._base_step_targets(sid)
        self.assertTrue(any(bases_after[k] + 1e-9 < bases_before[k] for k in bases_before))
        nic_row = next(s for s in self._guided_session_steps(sid) if int(s["step_no"]) == nic_no)
        nic_payload = nic_row.get("payload") or {}
        self.assertAlmostEqual(float(nic_payload.get("measured_grams") or 0.0), round(nic_target, 2), places=2)
        self.assertAlmostEqual(float(nic_payload.get("target_grams") or 0.0), nic_target, places=4)

    def test_guided_mix_summary_includes_batch_and_estimated_ratio(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0, "pg_ratio": 30, "vg_ratio": 70},
        )
        sid = create.get_json()["mix_session_id"]
        self._complete_all_guided_steps(sid)
        sess = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        summary = sess.get("mix_summary") or {}
        self.assertAlmostEqual(float(summary.get("batch_size_ml") or 0), 30.0)
        self.assertAlmostEqual(float(summary.get("target_pg_percent") or 0), 30.0)
        self.assertAlmostEqual(float(summary.get("target_vg_percent") or 0), 70.0)
        self.assertEqual(summary.get("target_ratio_label"), "30/70")
        self.assertIn("estimated_final_ratio_label", summary)
        self.assertIn("estimated_final_pg_percent", summary)
        self.assertIn("estimated_final_vg_percent", summary)
        self.assertGreater(float(summary.get("estimated_final_volume_ml") or 0), 0.0)
        self.assertIn("completion_status", summary)
        self.assertIn("notes_for_next_time", summary)

    def test_guided_pg_vg_batch_html_markers(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        html = self.client.get(f"/guided-mix/session/{sid}").data.decode("utf-8")
        self.assertIn("Add VG to finish the bottle", html)
        self.assertIn("Final bottle target:", html)
        self.assertIn("sessionBatchMl", html)
        self.assertIn("renderMixSummary", html)
        self.assertIn("pg_adjustment_note", html)
        self.assertIn("mixSummaryDetail", html)
        self.assertIn("tvw-ws-batch-report", html)
        self.assertIn('stepType === "completion"', html)

    def _mix_event_log(self, sid: int) -> list[dict]:
        with flask_app.app_context():
            import json as _json

            row = get_db().execute("SELECT result_json FROM mix_sessions WHERE id=?", (sid,)).fetchone()
            meta = _json.loads(row["result_json"] or "{}")
            log = meta.get("mix_event_log")
            return list(log) if isinstance(log, list) else []

    def _left_side_bottle_moved_steps(self, steps: list[dict]) -> list[dict]:
        out: list[dict] = []
        for s in steps:
            payload = s.get("payload") or {}
            if str(payload.get("step_type") or "").lower() != "add_ingredient":
                continue
            if bool(payload.get("bottle_moved")):
                out.append(s)
        return out

    def _linked_pour_before_transition(self, steps: list[dict], transition_step_no: int) -> dict:
        candidates = [
            s
            for s in steps
            if int(s["step_no"]) < int(transition_step_no)
            and str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        ]
        self.assertTrue(candidates, "expected pour step before transition")
        return max(candidates, key=lambda s: int(s["step_no"]))

    def _advance_to_open_transition_after_pour(self, sid: int) -> tuple[dict, dict]:
        for _ in range(40):
            sess = self._guided_session_steps(sid)
            current = next(s for s in sess if not s.get("completed_at"))
            payload = current.get("payload") or {}
            step_type = str(payload.get("step_type") or "").lower()
            if step_type == "transition":
                pour = self._linked_pour_before_transition(sess, int(current["step_no"]))
                return pour, current
            json_body: dict = {}
            if step_type == "add_ingredient":
                tg = float(payload.get("display_target_grams") or payload.get("target_grams") or 0.0)
                json_body = {"measured_grams": round(tg, 2)}
            resp = self.client.post(
                f"/api/mix/guided/session/{sid}/steps/{int(current['step_no'])}/complete",
                json=json_body,
            )
            self.assertEqual(resp.status_code, 200, f"step {current['step_no']} type {step_type}")
        self.fail("did not reach bottle-move transition")

    def _complete_bottle_moved_transition(self, sid: int, transition_step_no: int) -> None:
        resp = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{int(transition_step_no)}/complete",
            json={},
        )
        self.assertEqual(resp.status_code, 200)

    def test_guided_exact_pour_left_side_only_after_bottle_moved(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        pour, transition = self._advance_to_open_transition_after_pour(sid)
        pour_payload = pour.get("payload") or {}
        self.assertIsNotNone(pour.get("completed_at"))
        self.assertFalse(bool(pour_payload.get("bottle_moved")))
        self.assertEqual(
            str(pour_payload.get("pour_state") or ""),
            "pour_completed_pending_bottle_move",
        )
        self.assertEqual(self._left_side_bottle_moved_steps(self._guided_session_steps(sid)), [])
        self._complete_bottle_moved_transition(sid, int(transition["step_no"]))
        after = self._guided_session_steps(sid)
        pour_after = next(s for s in after if int(s["step_no"]) == int(pour["step_no"]))
        self.assertTrue(bool((pour_after.get("payload") or {}).get("bottle_moved")))
        self.assertEqual(len(self._left_side_bottle_moved_steps(after)), 1)
        events = [str(e.get("event") or "") for e in self._mix_event_log(sid)]
        self.assertIn("bottle_moved", events)

    def test_guided_slight_under_accept_left_side_only_after_bottle_moved(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        add_step = self._guided_first_add_step(sid)
        step_no = int(add_step["step_no"])
        display = round(float((add_step.get("payload") or {}).get("target_grams") or 0.0), 2)
        measured = round(display - 0.01, 2)
        under = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured},
        )
        body = under.get_json() or {}
        if str((body.get("reading") or {}).get("classification") or "") == "within_tolerance":
            self.skipTest("measurement landed in tolerance for this recipe target")
        accepted = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured, "accept_under": True},
        )
        self.assertEqual(accepted.status_code, 200)
        sess = self._guided_session_steps(sid)
        transition = next(
            s
            for s in sess
            if not s.get("completed_at")
            and str((s.get("payload") or {}).get("step_type") or "").lower() == "transition"
        )
        pour = self._linked_pour_before_transition(sess, int(transition["step_no"]))
        self.assertFalse(bool((pour.get("payload") or {}).get("bottle_moved")))
        self.assertEqual(self._left_side_bottle_moved_steps(sess), [])
        self._complete_bottle_moved_transition(sid, int(transition["step_no"]))
        after = self._guided_session_steps(sid)
        self.assertEqual(len(self._left_side_bottle_moved_steps(after)), 1)

    def test_guided_serious_over_recovery_left_side_only_after_bottle_moved(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        _body, after = self._trigger_serious_flavour_overpour(sid)
        recovery_no = int((after.get("progress") or {}).get("current_step_no") or 0)
        ack = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{recovery_no}/complete",
            json={},
        )
        self.assertEqual(ack.status_code, 200)
        sess = self._guided_session_steps(sid)
        transition = next(
            s
            for s in sess
            if not s.get("completed_at")
            and str((s.get("payload") or {}).get("step_type") or "").lower() == "transition"
        )
        pour = self._linked_pour_before_transition(sess, int(transition["step_no"]))
        self.assertTrue(bool((pour.get("payload") or {}).get("overpour_recovery_resolved")))
        self.assertFalse(bool((pour.get("payload") or {}).get("bottle_moved")))
        self.assertEqual(self._left_side_bottle_moved_steps(sess), [])
        self._complete_bottle_moved_transition(sid, int(transition["step_no"]))
        self.assertEqual(len(self._left_side_bottle_moved_steps(self._guided_session_steps(sid))), 1)

    def test_guided_nicotine_pour_left_side_only_after_bottle_moved(self):
        sid = self._create_guided_session_with_nicotine(target_ml=60)
        nic_no, nic_step = self._advance_to_nicotine_add_step(sid)
        target = float((nic_step.get("payload") or {}).get("target_grams") or 0.0)
        done = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": round(target, 2)},
        )
        self.assertEqual(done.status_code, 200)
        sess = self._guided_session_steps(sid)
        transition = next(
            s
            for s in sess
            if not s.get("completed_at")
            and str((s.get("payload") or {}).get("step_type") or "").lower() == "transition"
        )
        nic_row = next(s for s in sess if int(s["step_no"]) == nic_no)
        self.assertFalse(bool((nic_row.get("payload") or {}).get("bottle_moved")))
        self.assertEqual(self._left_side_bottle_moved_steps(sess), [])
        self._complete_bottle_moved_transition(sid, int(transition["step_no"]))
        after = self._guided_session_steps(sid)
        nic_after = next(s for s in after if int(s["step_no"]) == nic_no)
        self.assertTrue(bool((nic_after.get("payload") or {}).get("bottle_moved")))
        self.assertEqual(len(self._left_side_bottle_moved_steps(after)), 1)

    def test_guided_bench_html_uses_bottle_moved_for_left_side(self):
        tpl_path = os.path.join(flask_app.root_path, "templates", "tvw_guided_mix_session.html")
        with open(tpl_path, encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("isBottlePhysicallyLeft", html)
        self.assertIn("physicallyLeft", html)

    def test_guided_personal_nicotine_slight_over_allows_accept_or_fix(self):
        sid = self._create_guided_session_with_nicotine(strictness="personal")
        nic_no, nic_step = self._advance_to_nicotine_add_step(sid)
        target = float((nic_step.get("payload") or {}).get("target_grams") or 0.0)
        display = round(target, 2)
        over = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": round(display + 0.04, 2)},
        )
        body = over.get_json() or {}
        self.assertTrue(bool(body.get("awaiting_nicotine_over_decision")))
        self.assertEqual(str((body.get("reading") or {}).get("classification") or ""), "nicotine_over_decision")
        self.assertTrue(bool((body.get("reading") or {}).get("nicotine_accept_allowed")))
        payload = next(s for s in body["session"]["steps"] if int(s["step_no"]) == nic_no)["payload"]
        self.assertTrue(bool(payload.get("nicotine_over_decision_pending")))
        self.assertFalse(bool(payload.get("nicotine_overpour_pending")))

    def test_guided_personal_nicotine_010_over_requires_recovery(self):
        sid = self._create_guided_session_with_nicotine(strictness="personal")
        nic_no, nic_step = self._advance_to_nicotine_add_step(sid)
        target = float((nic_step.get("payload") or {}).get("target_grams") or 0.0)
        display = round(target, 2)
        over = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": round(display + 0.11, 2)},
        )
        body = over.get_json() or {}
        self.assertTrue(bool(body.get("awaiting_nicotine_recovery")))
        self.assertFalse(bool(body.get("awaiting_nicotine_over_decision")))
        payload = next(s for s in body["session"]["steps"] if int(s["step_no"]) == nic_no)["payload"]
        self.assertTrue(bool(payload.get("nicotine_overpour_pending")))
        self.assertFalse(bool(payload.get("nicotine_accept_allowed", True)))

    def test_guided_lab_client_nicotine_006_over_requires_recovery(self):
        sid = self._create_guided_session_with_nicotine(strictness="lab_client")
        nic_no, nic_step = self._advance_to_nicotine_add_step(sid)
        target = float((nic_step.get("payload") or {}).get("target_grams") or 0.0)
        display = round(target, 2)
        over = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": round(display + 0.06, 2)},
        )
        body = over.get_json() or {}
        self.assertTrue(bool(body.get("awaiting_nicotine_recovery")))
        payload = next(s for s in body["session"]["steps"] if int(s["step_no"]) == nic_no)["payload"]
        self.assertTrue(bool(payload.get("nicotine_overpour_pending")))
        self.assertFalse(bool(payload.get("nicotine_accept_allowed", True)))

    def test_guided_lab_client_blocks_accept_above_threshold(self):
        sid = self._create_guided_session_with_nicotine(strictness="lab_client")
        nic_no, nic_step = self._advance_to_nicotine_add_step(sid)
        target = float((nic_step.get("payload") or {}).get("target_grams") or 0.0)
        display = round(target, 2)
        over = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": round(display + 0.06, 2)},
        )
        body = over.get_json() or {}
        reading = body.get("reading") or {}
        self.assertFalse(bool(reading.get("nicotine_accept_allowed", True)))
        payload = next(s for s in body["session"]["steps"] if int(s["step_no"]) == nic_no)["payload"]
        self.assertFalse(bool(payload.get("nicotine_over_decision_pending")))
        self.assertTrue(bool(payload.get("nicotine_overpour_pending")))
        self.assertEqual(str(payload.get("nicotine_over_tier") or ""), "severe")

    def test_guided_accept_nicotine_over_logs_decision(self):
        sid = self._create_guided_session_with_nicotine(strictness="personal")
        nic_no, nic_step = self._advance_to_nicotine_add_step(sid)
        target = float((nic_step.get("payload") or {}).get("target_grams") or 0.0)
        display = round(target, 2)
        measured = round(display + 0.04, 2)
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": measured},
        )
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"nicotine_action": "accept_over"},
        )
        accepted = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"nicotine_action": "confirm_accept_over"},
        )
        self.assertEqual(accepted.status_code, 200)
        actions = [str(e.get("action") or "") for e in self._nicotine_recovery_log(sid)]
        self.assertIn("accepted_nicotine_over", actions)

    def test_guided_fix_nicotine_dose_enters_pour_back_recovery(self):
        sid = self._create_guided_session_with_nicotine(strictness="personal")
        nic_no, nic_step = self._advance_to_nicotine_add_step(sid)
        target = float((nic_step.get("payload") or {}).get("target_grams") or 0.0)
        display = round(target, 2)
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"measured_grams": round(display + 0.04, 2)},
        )
        fixed = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{nic_no}/complete",
            json={"nicotine_action": "fix_dose"},
        )
        body = fixed.get_json() or {}
        self.assertTrue(bool(body.get("awaiting_nicotine_recovery")))
        payload = next(s for s in body["session"]["steps"] if int(s["step_no"]) == nic_no)["payload"]
        self.assertTrue(bool(payload.get("nicotine_overpour_pending")))
        actions = [str(e.get("action") or "") for e in self._nicotine_recovery_log(sid)]
        self.assertIn("nicotine_recovery_started", actions)

    def _guided_first_add_step(self, sid: int) -> dict:
        sess = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        return next(
            s
            for s in (sess.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        )

    def _advance_to_add_ingredient_category(self, sid: int, category: str) -> tuple[int, dict]:
        want = str(category or "").strip().lower()
        for _ in range(80):
            sess = self._guided_session_steps(sid)
            current = next((s for s in sess if not s.get("completed_at")), None)
            if current is None:
                self.fail(f"No open step while advancing to category {want!r}")
            payload = current.get("payload") or {}
            step_type = str(payload.get("step_type") or "").lower()
            if step_type == "add_ingredient" and str(payload.get("category") or "").lower() == want:
                return int(current["step_no"]), current
            json_body: dict = {}
            if step_type == "add_ingredient":
                tg = float(payload.get("display_target_grams") or payload.get("target_grams") or 0.0)
                if tg > 0:
                    json_body = {"measured_grams": round(tg, 2)}
            done = self.client.post(
                f"/api/mix/guided/session/{sid}/steps/{int(current['step_no'])}/complete",
                json=json_body,
            )
            self.assertEqual(done.status_code, 200, f"step {current['step_no']} type {step_type}")
        self.fail(f"Could not reach add_ingredient category {want!r}")

    def _complete_all_guided_steps(self, sid: int) -> None:
        for _ in range(120):
            sess = self._guided_session_steps(sid)
            open_steps = [s for s in sess if not s.get("completed_at")]
            if not open_steps:
                return
            current = open_steps[0]
            payload = current.get("payload") or {}
            step_type = str(payload.get("step_type") or "").lower()
            json_body: dict = {}
            if step_type == "add_ingredient":
                if payload.get("under_decision_pending"):
                    tg = float(payload.get("target_grams") or payload.get("display_target_grams") or 0.0)
                    json_body = {"measured_grams": round(tg * 0.98, 2), "accept_under": True}
                else:
                    tg = float(payload.get("display_target_grams") or payload.get("target_grams") or 0.0)
                    if tg > 0:
                        json_body = {"measured_grams": round(tg, 2)}
            resp = self.client.post(
                f"/api/mix/guided/session/{sid}/steps/{int(current['step_no'])}/complete",
                json=json_body,
            )
            self.assertEqual(resp.status_code, 200, f"step {current['step_no']} type {step_type}")
        self.fail("Guided session did not finish within step limit")

    def _patch_guided_step_target_grams(self, sid: int, step_no: int, grams: float) -> None:
        with flask_app.app_context():
            from db import get_db
            import json as _json

            db = get_db()
            row = db.execute(
                "SELECT id, payload_json FROM mix_session_steps WHERE mix_session_id=? AND step_no=?",
                (sid, step_no),
            ).fetchone()
            payload = _json.loads(row["payload_json"])
            payload["target_grams"] = float(grams)
            payload["grams"] = float(grams)
            db.execute(
                "UPDATE mix_session_steps SET payload_json=? WHERE id=?",
                (_json.dumps(payload), row["id"]),
            )
            db.commit()

    def test_guided_large_under_pour_does_not_hard_block(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        add_step = self._guided_first_add_step(sid)
        step_no = int(add_step["step_no"])
        self._patch_guided_step_target_grams(sid, step_no, 3.20)
        measured = 2.20
        resp = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json() or {}
        self.assertEqual(str((body.get("reading") or {}).get("classification") or ""), "large_under_target")
        self.assertTrue(bool(body.get("awaiting_under_decision")))
        self.assertEqual((body.get("policy") or {}).get("hard_stops") or [], [])
        warnings = (body.get("policy") or {}).get("warnings") or []
        joined = "\n".join(str(w) for w in warnings)
        self.assertIn("Still under by 1.00 g", joined)
        self.assertIn("does not look finished yet", joined.lower())
        self.assertNotIn("close enough", joined.lower())
        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        original = next(s for s in after["steps"] if int(s["step_no"]) == step_no)
        self.assertIsNone(original.get("completed_at"))
        self.assertTrue(bool((original.get("payload") or {}).get("under_decision_pending")))

    def test_guided_large_under_continue_pouring_keeps_ingredient_active(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        add_step = self._guided_first_add_step(sid)
        step_no = int(add_step["step_no"])
        self._patch_guided_step_target_grams(sid, step_no, 3.20)
        measured = 2.20
        under = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured},
        )
        self.assertEqual(under.status_code, 200)
        cont = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured, "under_action": "continue_pouring"},
        )
        self.assertEqual(cont.status_code, 200)
        body = cont.get_json() or {}
        self.assertTrue(bool(body.get("under_pour_active")))
        self.assertEqual(str((body.get("reading") or {}).get("under_pour_mode") or ""), "continue")
        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        original = next(s for s in after["steps"] if int(s["step_no"]) == step_no)
        self.assertIsNone(original.get("completed_at"))
        self.assertEqual(str((original.get("payload") or {}).get("under_pour_mode") or ""), "continue")
        self.assertAlmostEqual(float((original.get("payload") or {}).get("measured_grams") or 0), measured, places=2)

    def test_guided_large_under_save_partial_preserves_measured_amount(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        add_step = self._guided_first_add_step(sid)
        step_no = int(add_step["step_no"])
        self._patch_guided_step_target_grams(sid, step_no, 3.20)
        measured = 2.20
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured},
        )
        partial = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured, "under_action": "save_partial"},
        )
        self.assertEqual(partial.status_code, 200)
        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        original = next(s for s in after["steps"] if int(s["step_no"]) == step_no)
        payload = original.get("payload") or {}
        self.assertAlmostEqual(float(payload.get("partial_grams") or 0), measured, places=2)
        self.assertAlmostEqual(float(payload.get("measured_grams") or 0), measured, places=2)
        self.assertEqual(str(payload.get("under_pour_mode") or ""), "change_bottle")
        ack = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured, "under_action": "ack_change_bottle"},
        )
        self.assertEqual(ack.status_code, 200)
        after_ack = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        payload_ack = next(s for s in after_ack["steps"] if int(s["step_no"]) == step_no)["payload"]
        self.assertEqual(str(payload_ack.get("under_pour_mode") or ""), "continue")
        self.assertAlmostEqual(float(payload_ack.get("partial_grams") or 0), measured, places=2)

    def test_guided_large_under_accept_underpour_completes_step(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        add_step = self._guided_first_add_step(sid)
        step_no = int(add_step["step_no"])
        self._patch_guided_step_target_grams(sid, step_no, 3.20)
        measured = 2.20
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured},
        )
        accepted = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured, "accept_under": True},
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertFalse(bool(accepted.get_json().get("awaiting_under_decision")))
        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        original = next(s for s in after["steps"] if int(s["step_no"]) == step_no)
        self.assertIsNotNone(original.get("completed_at"))
        self.assertFalse(bool((original.get("payload") or {}).get("under_decision_pending")))

    def _advance_to_second_add_ingredient_after_accepted_under(
        self,
        sid: int,
        *,
        first_target: float = 0.31,
        first_measured: float = 0.30,
        second_target: float = 1.04,
    ) -> tuple[int, dict]:
        """Complete first flavour with accepted slight under, tare, then open second pour."""
        add_steps = [
            s
            for s in self._guided_session_steps(sid)
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        ]
        self.assertGreaterEqual(len(add_steps), 2)
        first_no = int(add_steps[0]["step_no"])
        second_no = int(add_steps[1]["step_no"])
        self._patch_guided_step_target_grams(sid, first_no, first_target)
        self._patch_guided_step_target_grams(sid, second_no, second_target)

        under = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{first_no}/complete",
            json={"measured_grams": first_measured},
        )
        self.assertEqual(under.status_code, 200)
        accepted = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{first_no}/complete",
            json={"measured_grams": first_measured, "accept_under": True},
        )
        self.assertEqual(accepted.status_code, 200)

        sess = self._guided_session_steps(sid)
        first_done = next(s for s in sess if int(s["step_no"]) == first_no)
        self.assertIsNotNone(first_done.get("completed_at"))
        first_payload = first_done.get("payload") or {}
        self.assertAlmostEqual(float(first_payload.get("measured_grams") or 0), first_measured, places=2)
        self.assertTrue(
            str(first_payload.get("under_classification") or "") in {"", "slight_under_target", "large_under_target"}
            or float(first_payload.get("measured_grams") or 0) < float(first_payload.get("target_grams") or 0)
        )

        while True:
            sess = self._guided_session_steps(sid)
            current = next(s for s in sess if not s.get("completed_at"))
            cur_no = int(current["step_no"])
            cur_type = str((current.get("payload") or {}).get("step_type") or "").lower()
            if cur_no == second_no and cur_type == "add_ingredient":
                break
            if cur_no >= second_no:
                break
            done = self.client.post(f"/api/mix/guided/session/{sid}/steps/{cur_no}/complete", json={})
            self.assertEqual(done.status_code, 200, f"step {cur_no} type {cur_type}")

        sess = self._guided_session_steps(sid)
        second = next(s for s in sess if int(s["step_no"]) == second_no)
        return second_no, second

    def _guided_session_steps(self, sid: int) -> list[dict]:
        return self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]["steps"]

    def test_guided_next_pour_remaining_ignores_previous_accepted_underpour(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        second_no, second_step = self._advance_to_second_add_ingredient_after_accepted_under(sid)
        payload = second_step.get("payload") or {}
        self.assertEqual(str(payload.get("step_type") or "").lower(), "add_ingredient")
        self.assertNotIn("measured_grams", payload)
        self.assertNotIn("partial_grams", payload)
        self.assertNotIn("under_decision_pending", payload)

        probe = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{second_no}/complete",
            json={"measured_grams": 0.0},
        )
        self.assertEqual(probe.status_code, 200)
        reading = probe.get_json().get("reading") or {}
        self.assertAlmostEqual(float(reading.get("display_target_grams") or 0), 1.04, places=2)
        self.assertAlmostEqual(float(reading.get("measured_grams") or 0), 0.0, places=2)
        self.assertAlmostEqual(float(reading.get("delta_grams") or 0), -1.04, places=2)
        self.assertAlmostEqual(float(reading.get("remaining_grams") or 0), 1.04, places=2)

    def test_guided_next_pour_payload_sanitized_despite_stale_db_fields(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = create.get_json()["mix_session_id"]
        second_no, _ = self._advance_to_second_add_ingredient_after_accepted_under(sid)
        with flask_app.app_context():
            from db import get_db
            import json as _json

            db = get_db()
            row = db.execute(
                "SELECT id, payload_json FROM mix_session_steps WHERE mix_session_id=? AND step_no=?",
                (sid, second_no),
            ).fetchone()
            payload = _json.loads(row["payload_json"])
            payload["measured_grams"] = 0.30
            payload["partial_grams"] = 0.30
            payload["under_classification"] = "slight_under_target"
            payload.pop("under_decision_pending", None)
            db.execute(
                "UPDATE mix_session_steps SET payload_json=? WHERE id=?",
                (_json.dumps(payload), row["id"]),
            )
            db.commit()

        bundle = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        second = next(s for s in bundle["steps"] if int(s["step_no"]) == second_no)
        sp = second.get("payload") or {}
        self.assertNotIn("measured_grams", sp)
        self.assertNotIn("partial_grams", sp)
        self.assertNotIn("under_classification", sp)

        probe = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{second_no}/complete",
            json={"measured_grams": 0.0},
        )
        reading = probe.get_json().get("reading") or {}
        self.assertAlmostEqual(float(reading.get("remaining_grams") or 0), 1.04, places=2)

    def test_reconcile_prefers_actual_ml_over_grams_as_volume(self):
        with flask_app.app_context():
            # actual_grams alone historically meant volume; with density+actual_ml, use volume.
            result = reconcile_batch_plan(
                vessel_size=100.0,
                previously_poured=[
                    {
                        "ingredient_id": 101,
                        "actual_grams": 3.108,  # mass
                        "actual_ml": 3.0,  # true volume at ~1.036 density
                        "density": 1.036,
                    }
                ],
                unit_count=1,
                version_id=1,
                style="MTL",
                mix_mode="longfill",
                nic_target_mg_per_ml=0.0,
                nic_base_strength_mg_per_ml=100.0,
                nic_carrier="PG",
            )
            self.assertEqual(result.get("status"), "success")
            # 5% flavour → 3.0 ml poured implies batch 60 ml; must not treat 3.108 as ml.
            self.assertAlmostEqual(float(result.get("new_total_volume") or 0), 60.0, places=1)

    def test_guided_add_ingredient_normal_pour_progresses(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        self.assertEqual(create.status_code, 201)
        sid = create.get_json()["mix_session_id"]
        sess = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        add_step = next(
            s for s in (sess.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        )
        step_no = int(add_step["step_no"])
        target = float((add_step.get("payload") or {}).get("target_grams") or 0.0)
        self.assertGreater(target, 0.0)

        ok = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": target},
        )
        self.assertEqual(ok.status_code, 200)
        body = ok.get_json() or {}
        self.assertEqual(str((body.get("reading") or {}).get("classification") or ""), "within_tolerance")
        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        perf = after.get("performance_summary") or {}
        self.assertGreaterEqual(int(perf.get("precise_pour_count") or 0), 1)

    def test_guided_overpour_promotes_recovery_step_and_completes_original_step(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        self.assertEqual(create.status_code, 201)
        sid = int(create.get_json()["mix_session_id"])

        sess_before = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        add_step = next(
            s for s in (sess_before.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
        )
        step_no = int(add_step["step_no"])
        target = float((add_step.get("payload") or {}).get("target_grams") or 0.0)
        self.assertGreater(target, 0.0)

        # Force an overpour well beyond both tolerance and reconcile threshold.
        measured = round(target * 1.5, 4)
        complete = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"measured_grams": measured},
        )
        self.assertEqual(complete.status_code, 200)
        body = complete.get_json() or {}
        self.assertIn(
            str((body.get("reading") or {}).get("classification") or ""),
            {"over_target", "over_target_recovery_required"},
        )

        sess_after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        progress = sess_after.get("progress") or {}
        current_step_no = int(progress.get("current_step_no") or 0)
        current = next(s for s in (sess_after.get("steps") or []) if int(s.get("step_no") or 0) == current_step_no)
        current_payload = current.get("payload") or {}

        # Active step must be the inserted dynamic recovery/caution decision branch.
        self.assertTrue(bool(current_payload.get("dynamic")))
        self.assertEqual(str(current_payload.get("source_event") or ""), "overpour")

        # Original ingredient pour is deferred until recovery is acknowledged (bench LEFT SIDE timing).
        original = next(
            s for s in (sess_after.get("steps") or [])
            if str((s.get("payload") or {}).get("step_type") or "").lower() == "add_ingredient"
            and int(((s.get("payload") or {}).get("ingredient_id") or 0))
            == int(((add_step.get("payload") or {}).get("ingredient_id") or 0))
        )
        self.assertIsNone(original.get("completed_at"))
        self.assertTrue(bool((original.get("payload") or {}).get("overpour_recovery_pending")))
        self.assertTrue(bool(body.get("awaiting_overpour_recovery")))

    def test_reconcile_bigger_vessel_returns_success_and_delta_queue(self):
        with flask_app.app_context():
            result = reconcile_batch_plan(
                vessel_size=60.0,
                previously_poured=[{"ingredient_id": 101, "actual_grams": 2.5}],
                unit_count=1,
                version_id=1,
                style="MTL",
                mix_mode="longfill",
                nic_target_mg_per_ml=0.0,
                nic_base_strength_mg_per_ml=100.0,
                nic_carrier="PG",
                formula_rows=[{"ingredient_id": 101, "final_pct": 5.0, "name": "Vanilla"}],
            )
        self.assertEqual(result.get("status"), "success")
        self.assertTrue(bool(result.get("reconcile_mode")))
        self.assertEqual(int(result.get("restarted_from_step") or 0), 1)
        steps = result.get("reconciled_steps") or []
        flv = next((s for s in steps if int(s.get("ingredient_id") or 0) == 101), None)
        self.assertIsNotNone(flv)
        self.assertGreater(float(flv.get("delta_to_add") or 0.0), 0.0)
        self.assertEqual(str(flv.get("status") or ""), "pending")

    def test_reconcile_delta_only_skips_already_satisfied_step(self):
        with flask_app.app_context():
            result = reconcile_batch_plan(
                vessel_size=60.0,
                previously_poured=[{"ingredient_id": 101, "actual_grams": 3.0}],
                unit_count=1,
                version_id=1,
                style="MTL",
                mix_mode="longfill",
                nic_target_mg_per_ml=0.0,
                nic_base_strength_mg_per_ml=100.0,
                nic_carrier="PG",
                formula_rows=[{"ingredient_id": 101, "final_pct": 5.0, "name": "Vanilla"}],
            )
        self.assertEqual(result.get("status"), "success")
        flv = next((s for s in (result.get("reconciled_steps") or []) if int(s.get("ingredient_id") or 0) == 101), None)
        self.assertIsNotNone(flv)
        self.assertAlmostEqual(float(flv.get("delta_to_add") or 0.0), 0.0, places=3)
        self.assertEqual(str(flv.get("status") or ""), "skip")

    def test_tvw_reconcile_continue_with_drift_when_no_bigger_container(self):
        # new_total_volume <= vessel, but adjusted longfill volume > vessel triggers drift branch.
        with flask_app.app_context():
            result = reconcile_batch_plan(
                vessel_size=45.0,
                previously_poured=[{"ingredient_id": 101, "actual_grams": 2.05}],
                unit_count=1,
                version_id=1,
                style="MTL",
                mix_mode="longfill",
                nic_target_mg_per_ml=0.0,
                nic_base_strength_mg_per_ml=100.0,
                nic_carrier="PG",
                formula_rows=[{"ingredient_id": 101, "final_pct": 5.0, "name": "Vanilla"}],
                allow_continue_with_drift=True,
                no_bigger_container=True,
            )
        self.assertEqual(result.get("status"), "continue_with_drift")
        self.assertAlmostEqual(float(result.get("new_total_volume") or 0.0), 45.0, places=2)

    def test_recipe_status_post_and_list(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent) VALUES (2, 1, '1.1', 5.0)"
            )
            db.commit()
        post = self.client.post(
            "/api/recipes/1/status",
            json={"status": "help", "reason": "Need input", "recipe_version_id": 1},
        )
        self.assertEqual(post.status_code, 200)
        self.client.post(
            "/api/recipes/1/status",
            json={"status": "ready", "reason": "Done", "recipe_version_id": 2},
        )

        get = self.client.get("/api/recipes/1/status")
        self.assertEqual(get.status_code, 200)
        items = get.get_json()["items"]
        self.assertTrue(len(items) >= 2)
        self.assertIn(items[0]["to_status"], {"HELP", "READY"})
        filtered = self.client.get("/api/recipes/1/status?recipe_version_id=2")
        self.assertEqual(filtered.status_code, 200)
        filtered_items = filtered.get_json()["items"]
        self.assertEqual(len(filtered_items), 1)
        self.assertEqual(filtered_items[0]["recipe_version_id"], 2)

    def test_recipe_feedback_post_and_list(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent) VALUES (2, 1, '1.1', 6.0)"
            )
            db.commit()
        post = self.client.post(
            "/api/recipes/1/feedback",
            json={
                "feedback_type": "structured",
                "body": "Nice balance",
                "score_overall": 8,
                "recipe_version_id": 1,
                "metrics": {"sweetness": 3, "balance": 4},
            },
        )
        self.assertEqual(post.status_code, 201)
        self.client.post(
            "/api/recipes/1/feedback",
            json={"feedback_type": "structured", "body": "v2 is better", "score_overall": 9, "recipe_version_id": 2},
        )

        get = self.client.get("/api/recipes/1/feedback")
        self.assertEqual(get.status_code, 200)
        items = get.get_json()["items"]
        self.assertTrue(len(items) >= 2)
        self.assertIn("metrics", items[0])
        filtered = self.client.get("/api/recipes/1/feedback?recipe_version_id=1")
        self.assertEqual(filtered.status_code, 200)
        filtered_items = filtered.get_json()["items"]
        self.assertEqual(len(filtered_items), 1)
        self.assertEqual(filtered_items[0]["recipe_version_id"], 1)

    def test_recipe_favorite_rating_and_library_favorites_filter(self):
        add = self.client.post("/api/recipes/1/favorite", json={"favorited": True})
        self.assertEqual(add.status_code, 200)
        self.assertTrue(add.get_json().get("is_favorited"))

        eng = self.client.get("/api/recipes/1/engagement")
        self.assertEqual(eng.status_code, 200)
        self.assertTrue(eng.get_json()["is_favorited"])

        r1 = self.client.post("/api/recipes/1/rating", json={"rating": 4})
        self.assertEqual(r1.status_code, 200)
        j1 = r1.get_json()
        self.assertAlmostEqual(float(j1["avg_rating"]), 4.0)
        self.assertEqual(j1["rating_count"], 1)
        self.assertEqual(j1["my_rating"], 4)

        r2 = self.client.post("/api/recipes/1/rating", json={"rating": 5})
        self.assertEqual(r2.status_code, 200)
        j2 = r2.get_json()
        self.assertEqual(j2["my_rating"], 5)
        self.assertEqual(j2["rating_count"], 1)
        self.assertAlmostEqual(float(j2["avg_rating"]), 5.0)

        with flask_app.app_context():
            rows, total = recipes_repo.fetch_tvw_library(
                favorites_user_id=1, page=1, page_size=20, viewer_user_id=1
            )
        self.assertEqual(total, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["id"]), 1)

        rm = self.client.post("/api/recipes/1/favorite", json={"favorited": False})
        self.assertEqual(rm.status_code, 200)
        self.assertFalse(rm.get_json().get("is_favorited"))

    def test_flavour_notes_post_and_list(self):
        post = self.client.post("/api/flavours/1/notes", json={"note": "Creamy after 7 days"})
        self.assertEqual(post.status_code, 201)

        get = self.client.get("/api/flavours/1/notes")
        self.assertEqual(get.status_code, 200)
        items = get.get_json()["items"]
        self.assertTrue(len(items) >= 1)

    def test_flavour_reviews_post_and_list(self):
        post = self.client.post(
            "/api/flavours/1/reviews",
            json={"rating": 4.5, "review": "Solid standalone", "steep_days": 5},
        )
        self.assertEqual(post.status_code, 201)

        get = self.client.get("/api/flavours/1/reviews")
        self.assertEqual(get.status_code, 200)
        items = get.get_json()["items"]
        self.assertTrue(len(items) >= 1)

    def test_stash_post_and_list(self):
        post = self.client.post("/api/stash", json={"flavour_id": 1, "quantity_ml": 25, "is_favorite": True})
        self.assertEqual(post.status_code, 201)

        get = self.client.get("/api/stash")
        self.assertEqual(get.status_code, 200)
        body = get.get_json()
        self.assertEqual(body.get("source"), "user_stash_items")
        items = body["items"]
        self.assertTrue(len(items) >= 1)
        self.assertEqual(items[0]["user_id"], 1)
        self.assertIn("id", items[0])

    def test_recipe_library_page_renders(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "UPDATE recipes SET current_version_id = 1, slug = 'recipe-one' WHERE id = 1"
            )
            db.commit()
        r = self.client.get("/recipes")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Recipe1", r.data)
        self.assertIn(b"Recipe Library", r.data)
        self.assertIn(b"Create recipe", r.data)
        self.assertIn(b"Search recipe names, authors, tags or flavours", r.data)

    def test_recipe_detail_renders_stash_and_versions(self):
        with flask_app.app_context():
            db = get_db()
            db.execute("UPDATE recipes SET current_version_id = 1, slug = 'recipe-one' WHERE id = 1")
            db.commit()
        self.client.post("/api/stash", json={"flavour_id": 1, "quantity_ml": 50})
        r = self.client.get("/recipes/recipe-one")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Recipe1", r.data)
        self.assertIn(b"Start guided mix", r.data)
        self.assertIn(b"Ingredients", r.data)
        self.assertIn(b"Can make now", r.data)
        self.assertIn(b"View version history", r.data)
        self.assertIn(b"Owner controls", r.data)

    def test_recipe_detail_hides_owner_controls_for_non_owner(self):
        with flask_app.app_context():
            db = get_db()
            db.execute("UPDATE recipes SET current_version_id = 1, slug = 'recipe-one' WHERE id = 1")
            db.execute(
                "INSERT OR IGNORE INTO users (id, username, password_hash, role, status) "
                "VALUES (3, 'member2', 'x', 'member', 'Active')"
            )
            db.commit()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 3
            sess["username"] = "member2"
            sess["role"] = "member"
        r = self.client.get("/recipes/recipe-one")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Start guided mix", r.data)
        self.assertNotIn(b"Owner controls", r.data)
        self.assertNotIn(b'data-status="wip"', r.data)

    def test_member_stash_post_upserts_same_flavour(self):
        self.client.post("/api/stash", json={"flavour_id": 1, "quantity_ml": 10})
        self.client.post("/api/stash", json={"flavour_id": 1, "quantity_ml": 20})
        get = self.client.get("/api/stash")
        items = get.get_json()["items"]
        self.assertEqual(len(items), 1)
        self.assertAlmostEqual(float(items[0]["quantity_ml"]), 20.0, places=4)

    def test_stash_page_renders(self):
        r = self.client.get("/stash")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"My Stash", r.data)
        self.assertIn(b"What Can I Make", r.data)

    def test_old_goat_stash_uses_lab_shared_holdings(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"

        post = self.client.post("/api/stash", json={"flavour_id": 1, "quantity_ml": 42})
        self.assertEqual(post.status_code, 201)
        post_body = post.get_json()
        self.assertEqual(post_body["source"], "lab_shared_holdings")

        get = self.client.get("/api/stash")
        self.assertEqual(get.status_code, 200)
        body = get.get_json()
        self.assertEqual(body["source"], "lab_shared_holdings")
        self.assertTrue(len(body["items"]) >= 1)
        self.assertEqual(body["items"][0]["flavour_id"], 1)

        with flask_app.app_context():
            db = get_db()
            row = db.execute(
                "SELECT current_stock FROM library_items WHERE reference_id = 101 AND active = 1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertAlmostEqual(float(row["current_stock"]), 42.0, places=4)

    def test_lab_mix_deduction_reflects_in_old_goat_stash(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO library_items (reference_id, current_stock, unit, status, active) VALUES (101, 100.0, 'ml', 'In Use', 1)"
            )
            db.commit()
            body, status = finalize_inventory_deduction(
                payload={
                    "recipe_id": 1,
                    "version_id": 1,
                    "total_volume": 30,
                    "ingredients": [{"ingredient_id": 101, "poured_g": 10.36}],
                },
                user_id=2,
            )
            self.assertEqual(status, 200, body)

        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"
        get = self.client.get("/api/stash")
        self.assertEqual(get.status_code, 200)
        items = get.get_json()["items"]
        vanilla = next((i for i in items if i.get("flavour_id") is not None and int(i["flavour_id"]) == 1), None)
        self.assertIsNotNone(vanilla)
        self.assertAlmostEqual(float(vanilla["quantity_ml"]), 90.0, places=2)

    def test_old_goat_stash_lists_lab_holding_without_tv_flavour_row(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO flavor_reference (id, brand, name, category, density, active) "
                "VALUES (1999, 'Solo', 'Unmapped', 'Flavor', 1.0, 1)"
            )
            db.execute(
                "INSERT INTO library_items (reference_id, current_stock, unit, status, active) "
                "VALUES (1999, 15.0, 'ml', 'In Use', 1)"
            )
            db.commit()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"
        get = self.client.get("/api/stash")
        self.assertEqual(get.status_code, 200)
        body = get.get_json()
        row = next((i for i in body["items"] if int(i["reference_id"]) == 1999), None)
        self.assertIsNotNone(row)
        self.assertIsNone(row["flavour_id"])
        self.assertFalse(row.get("tvw_mapped", True))
        self.assertAlmostEqual(float(row["quantity_ml"]), 15.0, places=3)

    def test_old_goat_delete_stash_archives_lab_holding(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"
        post = self.client.post("/api/stash", json={"flavour_id": 1, "quantity_ml": 30})
        self.assertEqual(post.status_code, 201)
        del_resp = self.client.delete("/api/stash/1")
        self.assertEqual(del_resp.status_code, 200)
        self.assertEqual(del_resp.get_json().get("source"), "lab_shared_holdings")
        with flask_app.app_context():
            db = get_db()
            c = db.execute(
                "SELECT COUNT(1) AS c FROM library_items WHERE reference_id = 101 AND active = 1"
            ).fetchone()
            self.assertEqual(int(c["c"]), 0)

    def test_old_goat_stash_upsert_consolidates_duplicate_library_rows(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO library_items (reference_id, current_stock, unit, status, active) "
                "VALUES (101, 40.0, 'ml', 'In Use', 1)"
            )
            db.execute(
                "INSERT INTO library_items (reference_id, current_stock, unit, status, active) "
                "VALUES (101, 50.0, 'ml', 'In Use', 1)"
            )
            db.commit()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"
        res = self.client.post("/api/stash", json={"flavour_id": 1, "quantity_ml": 12})
        self.assertEqual(res.status_code, 201)
        with flask_app.app_context():
            db = get_db()
            active = db.execute(
                "SELECT COUNT(1) AS c FROM library_items WHERE reference_id = 101 AND active = 1"
            ).fetchone()
            self.assertEqual(int(active["c"]), 1)
            stock = db.execute(
                "SELECT current_stock FROM library_items WHERE reference_id = 101 AND active = 1"
            ).fetchone()
            self.assertAlmostEqual(float(stock["current_stock"]), 12.0, places=3)

    def test_member_stash_delete_removes_row(self):
        post = self.client.post("/api/stash", json={"flavour_id": 1, "quantity_ml": 25, "is_favorite": False})
        self.assertEqual(post.status_code, 201)
        r = self.client.delete("/api/stash/1")
        self.assertEqual(r.status_code, 200)
        get = self.client.get("/api/stash")
        self.assertEqual(get.status_code, 200)
        self.assertEqual(len(get.get_json()["items"]), 0)

    def test_current_version_id_maintained_by_recipe_save(self):
        with flask_app.app_context():
            body, status = recipe_service.save_recipe_ajax(
                session={"user_id": 1, "username": "Old Goat", "role": "Old Goat"},
                payload={
                    "name": "Version Pointer Recipe",
                    "status": "Production",
                    "ratio": "70/30",
                    "total_pct": 5,
                    "ingredients": [{"ingredient_id": 101, "percent": 5}],
                },
            )
            self.assertEqual(status, 200)
            recipe_id = int(body["recipe_id"])
            db = get_db()
            row = db.execute("SELECT current_version_id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
            first_version = int(row["current_version_id"])

            body2, status2 = recipe_service.save_recipe_ajax(
                session={"user_id": 1, "username": "Old Goat", "role": "Old Goat"},
                payload={
                    "recipe_id": recipe_id,
                    "name": "Version Pointer Recipe",
                    "status": "Production",
                    "ratio": "70/30",
                    "total_pct": 5,
                    "ingredients": [{"ingredient_id": 101, "percent": 5}],
                },
            )
            self.assertEqual(status2, 200)
            row2 = db.execute("SELECT current_version_id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
            self.assertNotEqual(first_version, int(row2["current_version_id"]))

    def test_tvw_recipe_save_succeeds(self):
        response = self.client.post(
            "/recipes/save",
            data={
                "name": "Lock Safe Custard",
                "status": "wip",
                "visibility": "private",
                "notes": "steep 5 days",
                "tags": "dessert, custard",
                "target_ratio": "70/30",
                "calc_target_ml": "60",
                "calc_target_nic": "3",
                "calc_pg_ratio": "30",
                "calc_vg_ratio": "70",
                "calc_nic_base": "100",
                "calc_nicotine_carrier": "PG",
                "ingredient_id[]": ["101"],
                "ingredient_percent[]": ["8"],
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Recipe created successfully", response.data)
        with flask_app.app_context():
            db = get_db()
            row = db.execute(
                "SELECT id, name FROM recipes WHERE name = ?", ("Lock Safe Custard",)
            ).fetchone()
            self.assertIsNotNone(row)

    def test_tvw_recipe_save_retries_then_succeeds_on_transient_lock(self):
        from unittest.mock import patch

        import sqlite3

        calls = {"n": 0}
        real_insert = recipes_repo.insert_recipe

        def flaky_insert(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise sqlite3.OperationalError("database is locked")
            return real_insert(**kwargs)

        with patch("db.time.sleep"), patch.object(recipes_repo, "insert_recipe", side_effect=flaky_insert):
            response = self.client.post(
                "/recipes/save",
                data={
                    "name": "Retry Custard",
                    "status": "wip",
                    "visibility": "private",
                    "target_ratio": "70/30",
                    "calc_target_ml": "60",
                    "calc_target_nic": "3",
                    "calc_pg_ratio": "30",
                    "calc_vg_ratio": "70",
                    "calc_nic_base": "100",
                    "calc_nicotine_carrier": "PG",
                    "ingredient_id[]": ["101"],
                    "ingredient_percent[]": ["6"],
                },
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Recipe created successfully", response.data)
        self.assertGreaterEqual(calls["n"], 2)
        with flask_app.app_context():
            db = get_db()
            count = db.execute(
                "SELECT COUNT(1) AS c FROM recipes WHERE name = ?", ("Retry Custard",)
            ).fetchone()
            self.assertEqual(int(count["c"]), 1)

    def test_tvw_recipe_save_locked_shows_friendly_message_and_no_partial(self):
        from unittest.mock import patch

        import sqlite3

        from db import SQLITE_BUSY_USER_MESSAGE

        with flask_app.app_context():
            before = int(
                get_db().execute("SELECT COUNT(1) AS c FROM recipes").fetchone()["c"] or 0
            )

        with patch("db.time.sleep"), patch.object(
            recipes_repo,
            "insert_recipe",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            response = self.client.post(
                "/recipes/save",
                data={
                    "name": "Should Not Persist Locked",
                    "status": "wip",
                    "visibility": "private",
                    "target_ratio": "70/30",
                    "ingredient_id[]": ["101"],
                    "ingredient_percent[]": ["5"],
                },
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        html = response.data.decode("utf-8", errors="ignore")
        self.assertIn(SQLITE_BUSY_USER_MESSAGE, html)
        self.assertNotIn("database is locked", html.lower())
        with flask_app.app_context():
            db = get_db()
            after = int(db.execute("SELECT COUNT(1) AS c FROM recipes").fetchone()["c"] or 0)
            orphan = db.execute(
                "SELECT COUNT(1) AS c FROM recipes WHERE name = ?",
                ("Should Not Persist Locked",),
            ).fetchone()
            self.assertEqual(after, before)
            self.assertEqual(int(orphan["c"]), 0)

    def test_lab_save_recipe_ajax_locked_returns_friendly_json(self):
        from unittest.mock import patch

        import sqlite3

        from db import SQLITE_BUSY_USER_MESSAGE

        with flask_app.app_context():
            with patch("db.time.sleep"), patch.object(
                recipes_repo,
                "insert_recipe",
                side_effect=sqlite3.OperationalError("database is locked"),
            ):
                body, status = recipe_service.save_recipe_ajax(
                    session={"user_id": 2, "username": "Old Goat", "role": "Old Goat"},
                    payload={
                        "name": "Ajax Locked Recipe",
                        "status": "Development",
                        "ratio": "70/30",
                        "total_pct": 5,
                        "ingredients": [{"ingredient_id": 101, "percent": 5}],
                    },
                )
        self.assertEqual(status, 503)
        self.assertEqual(body.get("status"), "error")
        self.assertEqual(body.get("message"), SQLITE_BUSY_USER_MESSAGE)
        with flask_app.app_context():
            count = get_db().execute(
                "SELECT COUNT(1) AS c FROM recipes WHERE name = ?",
                ("Ajax Locked Recipe",),
            ).fetchone()
            self.assertEqual(int(count["c"]), 0)

    def test_login_old_goat_success(self):
        client = flask_app.test_client()
        response = client.post(
            "/login",
            data={"username": "Old Goat", "password": "123456789"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/community", response.headers.get("Location", ""))

    def test_community_route_renders_html(self):
        response = self.client.get("/community")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.content_type)
        self.assertIn(b"The Vape Workshop Calculator", response.data)

    def test_community_home_dashboard_foundation(self):
        response = self.client.get("/community")
        self.assertEqual(response.status_code, 200)
        html = response.data
        self.assertIn(b"Welcome back,", html)
        self.assertIn(b"What would you like to do?", html)
        self.assertIn(b"Start mixing", html)
        self.assertIn(b"Start a mix", html)
        self.assertIn(b"Create a recipe", html)
        self.assertIn(b"View my recipes", html)
        self.assertIn(b'tvw-dashboard-route', html)
        self.assertIn(b'tvw-workflow-steps', html)
        self.assertIn(b'tvw-card-mix-layout', html)
        self.assertIn(b'tvw-dashboard-title', html)
        self.assertIn(b'tvw-discover', html)
        self.assertIn(b"Discover recipes", html)
        self.assertIn(b'data-tvw-discover-tabs', html)
        self.assertNotIn(b"Featured / newest", html)
        self.assertNotIn(b"Community picks", html)
        self.assertNotIn(b"Shortcuts", html)

    def test_community_discovery_featured_excluded_from_default_tab(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "UPDATE recipes SET slug = 'recipe-one', current_version_id = 1, created_at = datetime('now'), updated_at = datetime('now') WHERE id = 1"
            )
            db.execute(
                "INSERT INTO recipes (id, name, slug, status, visibility, moderation_status, active, created_by, current_version_id, created_at, updated_at) "
                "VALUES (2, 'Recipe Two', 'recipe-two', 'ready', 'public', 'active', 1, 1, 2, datetime('now', '-1 day'), datetime('now'))"
            )
            db.execute(
                "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent) VALUES (2, 2, '1', 8.0)"
            )
            db.commit()
        with flask_app.test_request_context("/community"):
            from services import community_discovery_service

            ctx = community_discovery_service.build_community_discovery_context(
                viewer_user_id=1,
                viewer_is_old_goat=False,
            )
        self.assertIsNotNone(ctx["discovery_featured"])
        featured_id = int(ctx["discovery_featured"]["id"])
        default_tab = ctx["discovery_tabs"][0]
        self.assertEqual(default_tab["id"], "updated")
        tab_ids = {int(r["id"]) for r in default_tab["recipes"]}
        self.assertNotIn(featured_id, tab_ids)
        html = self.client.get("/community").data
        self.assertIn(b"Popular", html)
        self.assertNotIn(b"Community picks", html)

    def test_community_discovery_no_fake_zero_rating(self):
        response = self.client.get("/community")
        html = response.data.decode("utf-8", errors="ignore")
        self.assertNotIn("Rated 0.0", html)
        self.assertNotIn("Rated 0 ", html)
        self.assertNotIn("★ 0.0", html)

    def test_community_discovery_hides_other_users_private_recipe(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO recipes (id, name, slug, status, visibility, moderation_status, active, created_by, current_version_id, created_at, updated_at) "
                "VALUES (9, 'Secret Private Blend', 'secret-private-blend', 'ready', 'private', 'active', 1, 2, 1, datetime('now'), datetime('now'))"
            )
            db.commit()
        html = self.client.get("/community").data.decode("utf-8", errors="ignore")
        self.assertNotIn("Secret Private Blend", html)
        self.assertNotIn("secret-private-blend", html)
        self.assertNotIn('tvw-discover-panel-rated" aria-labelledby="tvw-discover-tab-rated" hidden>Secret', html)

    def test_community_discovery_hides_own_private_from_discovery(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "UPDATE recipes SET name = 'My Private Draft', slug = 'my-private-draft', visibility = 'private', "
                "moderation_status = 'active', is_hidden = 0, active = 1, created_by = 1, "
                "created_at = datetime('now'), updated_at = datetime('now'), current_version_id = 1 WHERE id = 1"
            )
            db.commit()
        html = self.client.get("/community").data.decode("utf-8", errors="ignore")
        self.assertNotIn("My Private Draft", html)
        self.assertNotIn("my-private-draft", html)
        mix = self.client.post(
            "/api/mix/quick",
            json={
                "recipe_id": 1,
                "version_id": 1,
                "target_ml": 30,
                "target_nic_mg_ml": 0,
                "nic_base_mg_ml": 100,
                "pg_ratio": 30,
                "vg_ratio": 70,
                "nicotine_carrier": "PG",
            },
        )
        self.assertEqual(mix.status_code, 200)

    def test_community_discovery_top_rated_excludes_unrated(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "UPDATE recipes SET visibility = 'public', slug = 'recipe-one', name = 'Recipe1', "
                "created_at = datetime('now'), updated_at = datetime('now'), current_version_id = 1 WHERE id = 1"
            )
            db.commit()
        with flask_app.test_request_context("/community"):
            from services import community_discovery_service

            ctx = community_discovery_service.build_community_discovery_context(
                viewer_user_id=1,
                viewer_is_old_goat=False,
            )
        rated_tab = next(t for t in ctx["discovery_tabs"] if t["id"] == "rated")
        self.assertEqual(len(rated_tab["recipes"]), 0)
        html = self.client.get("/community").data.decode("utf-8", errors="ignore")
        self.assertIn("No recipes in this collection yet.", html)

    def test_community_discovery_top_rated_shows_only_rated_recipes(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "UPDATE recipes SET visibility = 'public', slug = 'recipe-one', created_at = datetime('now'), "
                "updated_at = datetime('now'), current_version_id = 1 WHERE id = 1"
            )
            db.execute(
                "INSERT INTO recipes (id, name, slug, status, visibility, moderation_status, active, created_by, current_version_id, created_at, updated_at) "
                "VALUES (10, 'Rated Public', 'rated-public', 'ready', 'public', 'active', 1, 1, 1, datetime('now', '-2 day'), datetime('now', '-30 day'))"
            )
            db.execute(
                "INSERT INTO recipes (id, name, slug, status, visibility, moderation_status, active, created_by, current_version_id, created_at, updated_at) "
                "VALUES (12, 'Fresh Update', 'fresh-update', 'ready', 'public', 'active', 1, 1, 1, datetime('now', '-1 day'), datetime('now'))"
            )
            db.execute(
                "INSERT INTO recipe_ratings (recipe_id, user_id, rating) VALUES (10, 1, 5)"
            )
            db.commit()
        with flask_app.test_request_context("/community"):
            from services import community_discovery_service

            ctx = community_discovery_service.build_community_discovery_context(
                viewer_user_id=1,
                viewer_is_old_goat=False,
            )
        rated_tab = next(t for t in ctx["discovery_tabs"] if t["id"] == "rated")
        rated_ids = {int(r["id"]) for r in rated_tab["recipes"]}
        self.assertIn(10, rated_ids)
        self.assertNotIn(1, rated_ids)
        for recipe in rated_tab["recipes"]:
            self.assertGreater(int(recipe.get("recipe_rating_count") or 0), 0)

    def test_community_discovery_old_goat_may_see_private_in_discovery(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO recipes (id, name, slug, status, visibility, moderation_status, active, created_by, current_version_id, created_at, updated_at) "
                "VALUES (11, 'Goat Private Reserve', 'goat-private-reserve', 'ready', 'private', 'active', 1, 2, 1, datetime('now'), datetime('now'))"
            )
            db.commit()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"
        html = self.client.get("/community").data.decode("utf-8", errors="ignore")
        self.assertIn("Goat Private Reserve", html)

    def test_community_route_renders_in_required_discovery_states(self):
        """GET /community must return 200 through the real discovery builder in key states."""
        import inspect
        from services import community_discovery_service

        src = inspect.getsource(community_discovery_service.build_community_discovery_context)
        self.assertIn("rated_rows =", src)
        self.assertIn("updated_rows =", src)
        self.assertIn("popular_rows =", src)
        self.assertIn("created_rows =", src)
        self.assertLess(src.index("rated_rows ="), src.index("for row in rated_rows"))

        # 1) Ordinary authenticated viewer, no ratings
        response = self.client.get("/community")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Discover recipes", response.data)
        self.assertIn(b"Top rated", response.data)
        self.assertNotIn(b"Secret Private Blend", response.data)

        with flask_app.test_request_context("/community"):
            ctx = community_discovery_service.build_community_discovery_context(
                viewer_user_id=1, viewer_is_old_goat=False
            )
        rated_tab = next(t for t in ctx["discovery_tabs"] if t["id"] == "rated")
        self.assertEqual(rated_tab["recipes"], [])

        # 2) At least one genuinely rated public recipe
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "UPDATE recipes SET visibility = 'public', slug = 'recipe-one', current_version_id = 1, "
                "created_at = datetime('now'), updated_at = datetime('now') WHERE id = 1"
            )
            db.execute(
                "INSERT INTO recipes (id, name, slug, status, visibility, moderation_status, active, created_by, current_version_id, created_at, updated_at) "
                "VALUES (20, 'Rated Bench Juice', 'rated-bench-juice', 'ready', 'public', 'active', 1, 1, 1, "
                "datetime('now', '-3 day'), datetime('now', '-20 day'))"
            )
            db.execute(
                "INSERT INTO recipe_ratings (recipe_id, user_id, rating) VALUES (20, 1, 5)"
            )
            db.execute(
                "INSERT INTO recipes (id, name, slug, status, visibility, moderation_status, active, created_by, current_version_id, created_at, updated_at) "
                "VALUES (21, 'Secret Private Blend', 'secret-private-blend', 'ready', 'private', 'active', 1, 2, 1, "
                "datetime('now'), datetime('now'))"
            )
            db.commit()

        response = self.client.get("/community")
        self.assertEqual(response.status_code, 200)
        html = response.data.decode("utf-8", errors="ignore")
        self.assertIn("Rated Bench Juice", html)
        self.assertNotIn("Secret Private Blend", html)
        self.assertNotIn("secret-private-blend", html)

        with flask_app.test_request_context("/community"):
            ctx = community_discovery_service.build_community_discovery_context(
                viewer_user_id=1, viewer_is_old_goat=False
            )
        rated_tab = next(t for t in ctx["discovery_tabs"] if t["id"] == "rated")
        rated_ids = {int(r["id"]) for r in rated_tab["recipes"]}
        self.assertIn(20, rated_ids)
        for recipe in rated_tab["recipes"]:
            self.assertGreater(int(recipe.get("recipe_rating_count") or 0), 0)

        # 3) Old Goat / admin viewer — private still visible under existing permission model
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"
        response = self.client.get("/community")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Secret Private Blend", response.data)

        with flask_app.test_request_context("/community"):
            ctx = community_discovery_service.build_community_discovery_context(
                viewer_user_id=2, viewer_is_old_goat=True
            )
        self.assertTrue(ctx["has_discovery"])
        self.assertIsInstance(ctx["discovery_tabs"], list)
        self.assertEqual(len(ctx["discovery_tabs"]), 3)

    def test_recipe_library_mine_filter(self):
        response = self.client.get("/recipes?mine=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"My recipes", response.data)
        self.assertIn(b"is-active", response.data)

    def test_community_feed_api_returns_json(self):
        response = self.client.get("/api/community/feed")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "success")
        self.assertIn("community_feed", body)

    def test_tvw_admin_requires_admin_role(self):
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 403)

    def test_tvw_admin_dashboard_for_admin_role(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"TVW Admin Console", response.data)

    def test_hidden_recipe_blocked_for_member_api(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "UPDATE recipes SET is_hidden = 1, moderation_status = 'hidden', created_by = 2 WHERE id = 1"
            )
            db.commit()
        response = self.client.post(
            "/api/mix/quick",
            json={
                "recipe_id": 1,
                "version_id": 1,
                "target_ml": 30,
                "target_nic_mg_ml": 0,
                "nic_base_mg_ml": 100,
                "pg_ratio": 30,
                "vg_ratio": 70,
                "nicotine_carrier": "PG",
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_hidden_recipe_detail_redirects_with_safe_message(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "UPDATE recipes SET slug = 'recipe-one', is_hidden = 1, moderation_status = 'hidden', created_by = 2 WHERE id = 1"
            )
            db.commit()
        response = self.client.get("/recipes/recipe-one", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Recipe Library", response.data)

    def test_hidden_recipe_allowed_for_tv_admin_api(self):
        with flask_app.app_context():
            db = get_db()
            db.execute("UPDATE recipes SET is_hidden = 1, moderation_status = 'hidden' WHERE id = 1")
            db.commit()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"
        response = self.client.post(
            "/api/mix/quick",
            json={
                "recipe_id": 1,
                "version_id": 1,
                "target_ml": 30,
                "target_nic_mg_ml": 0,
                "nic_base_mg_ml": 100,
                "pg_ratio": 30,
                "vg_ratio": 70,
                "nicotine_carrier": "PG",
            },
        )
        self.assertEqual(response.status_code, 200)

    def test_private_recipe_hidden_from_non_owner_admin(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO users (id, username, password_hash, role, status) VALUES (3, 'adminonly', 'x', 'admin', 'Active')"
            )
            db.execute(
                "UPDATE recipes SET visibility = 'private', moderation_status = 'active', is_hidden = 0, active = 1, created_by = 2 WHERE id = 1"
            )
            db.commit()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 3
            sess["username"] = "adminonly"
            sess["role"] = "admin"
        response = self.client.post(
            "/api/mix/quick",
            json={
                "recipe_id": 1,
                "version_id": 1,
                "target_ml": 30,
                "target_nic_mg_ml": 0,
                "nic_base_mg_ml": 100,
                "pg_ratio": 30,
                "vg_ratio": 70,
                "nicotine_carrier": "PG",
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_private_recipe_visible_to_owner(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "UPDATE recipes SET visibility = 'private', moderation_status = 'active', is_hidden = 0, active = 1, created_by = 1 WHERE id = 1"
            )
            db.commit()
        response = self.client.post(
            "/api/mix/quick",
            json={
                "recipe_id": 1,
                "version_id": 1,
                "target_ml": 30,
                "target_nic_mg_ml": 0,
                "nic_base_mg_ml": 100,
                "pg_ratio": 30,
                "vg_ratio": 70,
                "nicotine_carrier": "PG",
            },
        )
        self.assertEqual(response.status_code, 200)

    def test_community_feed_excludes_hidden_recipes(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "UPDATE recipes SET is_hidden = 1, moderation_status = 'hidden', created_by = 2 WHERE id = 1"
            )
            db.commit()
        response = self.client.get("/api/community/feed")
        self.assertEqual(response.status_code, 200)
        feed = response.get_json()["community_feed"]
        self.assertTrue(all(int(r["id"]) != 1 for r in feed))

    def test_tvw_admin_recipes_list_renders(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"
        response = self.client.get("/admin/recipes")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Recipe moderation", response.data)

    def test_moderated_flavour_note_owner_visible_but_not_public(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO users (id, username, password_hash, role, status) VALUES (3, 'member2', 'x', 'member', 'Active')"
            )
            db.execute(
                "INSERT INTO flavour_notes (flavour_id, author_user_id, note) VALUES (1, 1, 'ok note')"
            )
            db.execute(
                "INSERT INTO flavour_notes (flavour_id, author_user_id, note, is_hidden, moderation_status) "
                "VALUES (1, 1, 'spam note', 1, 'spam')"
            )
            db.commit()

        owner_view = self.client.get("/api/flavours/1/notes")
        self.assertEqual(owner_view.status_code, 200)
        owner_notes = owner_view.get_json()["items"]
        self.assertTrue(any(n["note"] == "spam note" for n in owner_notes))
        spam_note = next(n for n in owner_notes if n["note"] == "spam note")
        self.assertTrue(spam_note.get("owner_moderation_visible"))
        self.assertFalse(spam_note.get("public_visible"))

        with self.client.session_transaction() as sess:
            sess["user_id"] = 3
            sess["username"] = "member2"
            sess["role"] = "member"
        public_view = self.client.get("/api/flavours/1/notes")
        self.assertEqual(public_view.status_code, 200)
        public_notes = public_view.get_json()["items"]
        self.assertFalse(any(n["note"] == "spam note" for n in public_notes))

    def test_tvw_admin_community_moderation_renders(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"
        response = self.client.get("/admin/community?type=note")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Community content", response.data)

    def test_suspended_user_blocked_from_api_with_403(self):
        with flask_app.app_context():
            db = get_db()
            db.execute("UPDATE users SET is_suspended = 1 WHERE id = 1")
            db.commit()
        response = self.client.get("/api/community/feed")
        self.assertEqual(response.status_code, 403)
        body = response.get_json()
        self.assertEqual(body.get("code"), "suspended")

    def test_muted_user_blocked_from_ugc_post_not_from_mix(self):
        with flask_app.app_context():
            db = get_db()
            db.execute("UPDATE users SET is_muted = 1 WHERE id = 1")
            db.commit()
        fb = self.client.post(
            "/api/recipes/1/feedback",
            json={"feedback_type": "structured", "body": "x", "score_overall": 1},
        )
        self.assertEqual(fb.status_code, 403)
        self.assertEqual(fb.get_json().get("code"), "muted")
        mix = self.client.post(
            "/api/mix/quick",
            json={
                "recipe_id": 1,
                "version_id": 1,
                "target_ml": 60,
                "target_nic_mg_ml": 3,
                "nic_base_mg_ml": 100,
                "pg_ratio": 30,
                "vg_ratio": 70,
                "nicotine_carrier": "PG",
            },
        )
        self.assertEqual(mix.status_code, 200)

    def test_admin_cannot_assign_admin_role_via_role_form(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO users (id, username, password_hash, role, status) VALUES (3, 'admonly', 'x', 'admin', 'Active')"
            )
            db.commit()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 3
            sess["username"] = "admonly"
            sess["role"] = "admin"
        response = self.client.post(
            "/admin/users/1/role",
            data={"new_role": "admin", "reason": "try escalate"},
        )
        self.assertEqual(response.status_code, 302)
        with flask_app.app_context():
            db = get_db()
            row = db.execute("SELECT role FROM users WHERE id = 1").fetchone()
            self.assertEqual(str(row["role"]), "member")

    def test_old_goat_can_promote_member_to_admin_audited(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"
        response = self.client.post(
            "/admin/users/1/role",
            data={"new_role": "admin", "reason": "trusted operator"},
        )
        self.assertEqual(response.status_code, 302)
        with flask_app.app_context():
            db = get_db()
            row = db.execute("SELECT role FROM users WHERE id = 1").fetchone()
            self.assertEqual(str(row["role"]), "admin")
            log = db.execute(
                "SELECT action, reason FROM tvw_admin_audit_logs WHERE action = 'user_role_change' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(log)
            self.assertIn("trusted", str(log["reason"]))

    def test_master_can_assign_foreman_but_not_admin_or_master(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO users (id, username, password_hash, role, status) VALUES (4, 'master1', 'x', 'Master', 'Active')"
            )
            db.commit()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 4
            sess["username"] = "master1"
            sess["role"] = "Master"
        with flask_app.app_context():
            db = get_db()
            db.execute("UPDATE users SET role = 'member' WHERE id = 1")
            db.commit()
        ok = self.client.post(
            "/admin/users/1/role",
            data={"new_role": "Foreman", "reason": "needs foreman access"},
        )
        self.assertEqual(ok.status_code, 302)
        with flask_app.app_context():
            db = get_db()
            self.assertEqual(str(db.execute("SELECT role FROM users WHERE id = 1").fetchone()["role"]), "Foreman")
        reject_admin = self.client.post(
            "/admin/users/1/role",
            data={"new_role": "admin", "reason": "should fail"},
        )
        self.assertEqual(reject_admin.status_code, 302)
        with flask_app.app_context():
            db = get_db()
            self.assertEqual(str(db.execute("SELECT role FROM users WHERE id = 1").fetchone()["role"]), "Foreman")
        bad = self.client.post(
            "/admin/users/1/role",
            data={"new_role": "Master", "reason": "should fail"},
        )
        self.assertEqual(bad.status_code, 302)
        with flask_app.app_context():
            db = get_db()
            self.assertEqual(str(db.execute("SELECT role FROM users WHERE id = 1").fetchone()["role"]), "Foreman")

    def test_last_old_goat_cannot_be_removed_from_account(self):
        from models import tvw_admin_repo

        with flask_app.app_context():
            with self.assertRaises(ValueError) as ctx:
                tvw_admin_repo.assert_actor_can_change_user_role(
                    actor_user_id=1,
                    actor_role="Old Goat",
                    target_user_id=2,
                    target_role="Old Goat",
                    new_role="member",
                )
            self.assertIn("last remaining old goat", str(ctx.exception).lower())

    def test_tvw_request_syncs_session_role_from_database(self):
        with flask_app.app_context():
            db = get_db()
            db.execute("UPDATE users SET role = 'moderator' WHERE id = 1")
            db.commit()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "member1"
            sess["role"] = "member"
        response = self.client.get("/community", follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("role"), "moderator")

    def test_tvw_admin_users_list_and_detail_renders(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"
        lst = self.client.get("/admin/users")
        self.assertEqual(lst.status_code, 200)
        self.assertIn(b"User moderation", lst.data)
        detail = self.client.get("/admin/users/1")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"@member1", detail.data)

    def test_flavour_identity_cleanup_renders(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"
        response = self.client.get("/admin/flavours/cleanup?view=duplicates")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Flavour identity cleanup", response.data)

    def test_flavour_full_merge_repoints_recipe_items(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO flavor_reference (id, brand, name, category, density, active) "
                "VALUES (102, 'Test', 'VanillaDup', 'Flavor', 1.036, 1)"
            )
            db.execute(
                "INSERT INTO flavours (id, canonical_name, brand, category, source_flavor_ref_id) "
                "VALUES (2, 'VanillaDup', 'Test', 'Flavor', 102)"
            )
            db.execute(
                "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (1, 102, 3.0)"
            )
            db.commit()

        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"

        response = self.client.post(
            "/admin/flavours/merge/2/1",
            data={"action": "execute_merge", "reason": "test merge"},
        )
        self.assertEqual(response.status_code, 302)

        with flask_app.app_context():
            db = get_db()
            rows = db.execute(
                "SELECT amount_percent FROM recipe_items WHERE version_id = 1 AND ingredient_id = 101"
            ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertAlmostEqual(float(rows[0]["amount_percent"]), 8.0, places=4)
            src = db.execute(
                "SELECT merged_into_flavour_id, moderation_status FROM flavours WHERE id = 2"
            ).fetchone()
            self.assertEqual(int(src["merged_into_flavour_id"]), 1)
            self.assertEqual(str(src["moderation_status"]).lower(), "merged")
            log = db.execute(
                "SELECT action FROM tvw_admin_audit_logs WHERE action = 'flavour_merge_execute' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(log)

    def test_content_report_and_admin_queue(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"
        post = self.client.post(
            "/api/reports",
            json={"target_type": "recipe", "target_id": 1, "reason": "spam", "detail": "test"},
        )
        self.assertEqual(post.status_code, 201, post.get_data(as_text=True))
        self.assertEqual(post.get_json().get("status"), "success")

        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"
        admin_get = self.client.get("/admin/reports")
        self.assertEqual(admin_get.status_code, 200)
        self.assertIn(b"Content reports", admin_get.data)

        rid = post.get_json().get("report_id")
        self.assertIsNotNone(rid)
        upd = self.client.post(
            f"/admin/reports/{rid}/update",
            data={
                "action": "mark_reviewed",
                "note": "seen",
                "return_target_type": "",
                "return_status": "",
                "return_search": "",
                "return_page": 1,
            },
        )
        self.assertEqual(upd.status_code, 302)
        with flask_app.app_context():
            db = get_db()
            row = db.execute(
                "SELECT status FROM tvw_content_reports WHERE id = ?", (int(rid),)
            ).fetchone()
            self.assertEqual(str(row["status"]).lower(), "reviewed")
            log = db.execute(
                "SELECT action FROM tvw_admin_audit_logs WHERE target_type = 'report' AND target_id = ? ORDER BY id DESC LIMIT 1",
                (int(rid),),
            ).fetchone()
            self.assertIsNotNone(log)

    def test_report_reject_self_authored_feedback(self):
        with flask_app.app_context():
            db = get_db()
            cur = db.execute(
                """
                INSERT INTO recipe_feedback (recipe_id, recipe_version_id, author_user_id, feedback_type, body, score_overall)
                VALUES (1, 1, 1, 'structured', 'mine', 5.0)
                """
            )
            fid = int(cur.lastrowid)
            db.commit()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "member1"
            sess["role"] = "member"
        post = self.client.post(
            "/api/reports",
            json={"target_type": "feedback", "target_id": fid, "reason": "spam"},
        )
        self.assertEqual(post.status_code, 400)

    def test_flavour_merge_forbidden_for_admin_role(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO flavor_reference (id, brand, name, category, density, active) "
                "VALUES (103, 'Test', 'VanillaDup2', 'Flavor', 1.036, 1)"
            )
            db.execute(
                "INSERT INTO flavours (id, canonical_name, brand, category, source_flavor_ref_id) "
                "VALUES (3, 'VanillaDup2', 'Test', 'Flavor', 103)"
            )
            db.commit()
            db.execute(
                "INSERT INTO users (id, username, password_hash, role, status) VALUES (3, 'adm', 'x', 'admin', 'Active')"
            )
            db.commit()

        with self.client.session_transaction() as sess:
            sess["user_id"] = 3
            sess["username"] = "adm"
            sess["role"] = "admin"

        response = self.client.post(
            "/admin/flavours/merge/3/1",
            data={"action": "execute_merge", "reason": "x"},
        )
        self.assertEqual(response.status_code, 302)
        with flask_app.app_context():
            db = get_db()
            row = db.execute("SELECT merged_into_flavour_id FROM flavours WHERE id = 3").fetchone()
            self.assertFalse(int(row["merged_into_flavour_id"] or 0))

    def test_what_can_i_make_member_stash_and_page(self):
        with flask_app.app_context():
            db = get_db()
            db.execute("UPDATE recipes SET current_version_id = 1, slug = 'recipe1' WHERE id = 1")
            db.execute(
                "INSERT INTO flavor_reference (id, brand, name, category, density, active) "
                "VALUES (102, 'Other', 'Berry', 'Flavor', 1.0, 1)"
            )
            db.execute(
                "INSERT INTO flavours (id, canonical_name, brand, category, source_flavor_ref_id) "
                "VALUES (2, 'Berry', 'Other', 'Flavor', 102)"
            )
            db.execute(
                "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (1, 102, 2.0)"
            )
            db.commit()
        self.client.post("/api/stash", json={"flavour_id": 1, "quantity_ml": 30})
        r = self.client.get("/what-can-i-make")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Your stash", r.data)
        self.assertIn(b"Missing 1 flavour", r.data)
        self.assertIn(b"Berry", r.data)
        self.client.post("/api/stash", json={"flavour_id": 2, "quantity_ml": 20})
        r2 = self.client.get("/what-can-i-make")
        self.assertEqual(r2.status_code, 200)
        self.assertIn(b"Can make now", r2.data)

    def test_tvw_flavour_catalog_page_renders_table(self):
        r = self.client.get("/flavours")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Flavour catalogue", r.data)
        self.assertIn(b"Manufacturer", r.data)
        self.assertIn(b"Used in", r.data)
        self.assertIn(b"Vanilla", r.data)

    def test_tvw_flavour_detail_renders_usage_and_recipes(self):
        with flask_app.app_context():
            db = get_db()
            db.execute("UPDATE recipes SET current_version_id = 1, slug = 'recipe-one' WHERE id = 1")
            db.execute(
                "UPDATE flavours SET avg_usage = 4.25, default_percent = 3.0 WHERE id = 1"
            )
            db.commit()
        r = self.client.get("/flavours/1")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Recipe1", r.data)
        self.assertIn(b"Catalogue data", r.data)
        self.assertIn(b"4.25", r.data)
        self.assertIn(b"Recipes using this flavour", r.data)

    def test_tvw_flavour_detail_redirects_merged_alias(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO flavours (id, canonical_name, brand, category, merged_into_flavour_id, source_flavor_ref_id) "
                "VALUES (99, 'AliasVanilla', 'Test', 'Flavor', 1, 101)"
            )
            db.commit()
        r = self.client.get("/flavours/99", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/flavours/1", r.headers.get("Location", ""))

    def test_tvw_flavour_detail_usage_fallback_default_percent(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "UPDATE flavours SET avg_usage = NULL, default_percent = 2.5 WHERE id = 1"
            )
            db.commit()
        r = self.client.get("/flavours/1")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Catalogue default", r.data)
        self.assertIn(b"2.50", r.data)

    def test_what_can_i_make_old_goat_lab_holdings(self):
        with flask_app.app_context():
            db = get_db()
            db.execute("UPDATE recipes SET current_version_id = 1, slug = 'recipe1' WHERE id = 1")
            db.execute(
                "INSERT INTO library_items (reference_id, current_stock, unit, status, active) "
                "VALUES (101, 12.0, 'ml', 'In Use', 1)"
            )
            db.commit()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "Old Goat"
            sess["role"] = "Old Goat"
        r = self.client.get("/what-can-i-make")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Lab holdings (linked)", r.data)
        self.assertIn(b"Can make now", r.data)

    def test_what_can_i_make_qty_short_for_batch(self):
        with flask_app.app_context():
            db = get_db()
            db.execute("UPDATE recipes SET current_version_id = 1, slug = 'recipe1' WHERE id = 1")
            db.commit()
        self.client.post("/api/stash", json={"flavour_id": 1, "quantity_ml": 1.0})
        r = self.client.get("/what-can-i-make?batch_ml=30")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Can make but low stock", r.data)
        self.assertIn(b"does not have enough ml", r.data)

    def test_what_can_i_make_qty_tight_headroom(self):
        with flask_app.app_context():
            db = get_db()
            db.execute("UPDATE recipes SET current_version_id = 1, slug = 'recipe1' WHERE id = 1")
            db.commit()
        self.client.post("/api/stash", json={"flavour_id": 1, "quantity_ml": 1.55})
        r = self.client.get("/what-can-i-make?batch_ml=30")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Can make but low stock", r.data)
        self.assertIn(b"tight on headroom", r.data)

    def test_guided_client_bundle_omits_heavy_fields(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = int(create.get_json()["mix_session_id"])
        done = self.client.post(f"/api/mix/guided/session/{sid}/steps/1/complete", json={})
        self.assertEqual(done.status_code, 200)
        session = (done.get_json() or {}).get("session") or {}
        self.assertNotIn("result_json", session)
        for step in session.get("steps") or []:
            self.assertNotIn("payload_json", step)
        self.assertIsNone(session.get("mix_summary"))

    def test_guided_late_session_step_complete_stays_responsive(self):
        import json as _json
        import time

        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = int(create.get_json()["mix_session_id"])
        for _ in range(100):
            sess = self._guided_session_steps(sid)
            open_steps = [s for s in sess if not s.get("completed_at")]
            if len(open_steps) <= 2:
                break
            current = open_steps[0]
            payload = current.get("payload") or {}
            step_type = str(payload.get("step_type") or "").lower()
            json_body: dict = {}
            if step_type == "add_ingredient":
                if payload.get("under_decision_pending"):
                    tg = float(payload.get("target_grams") or payload.get("display_target_grams") or 0.0)
                    json_body = {"measured_grams": round(tg * 0.98, 2), "accept_under": True}
                else:
                    tg = float(payload.get("display_target_grams") or payload.get("target_grams") or 0.0)
                    if tg > 0:
                        json_body = {"measured_grams": round(tg, 2)}
            resp = self.client.post(
                f"/api/mix/guided/session/{sid}/steps/{int(current['step_no'])}/complete",
                json=json_body,
            )
            self.assertEqual(resp.status_code, 200, resp.get_json())
        with flask_app.app_context():
            from services.guided_mix_session_service import _read_session_meta, _write_session_meta

            db = get_db()
            meta = _read_session_meta(db, sid)
            meta["mix_event_log"] = [{"event": "accepted_carrier_under", "n": i} for i in range(120)]
            _write_session_meta(db, sid, meta)
            db.commit()
        t0 = time.perf_counter()
        last_resp = None
        for _ in range(20):
            sess = self._guided_session_steps(sid)
            open_steps = [s for s in sess if not s.get("completed_at")]
            if not open_steps:
                break
            current = open_steps[0]
            payload = current.get("payload") or {}
            step_type = str(payload.get("step_type") or "").lower()
            json_body = {}
            if step_type == "add_ingredient":
                tg = float(payload.get("display_target_grams") or payload.get("target_grams") or 0.0)
                if tg > 0:
                    json_body = {"measured_grams": round(tg, 2)}
            last_resp = self.client.post(
                f"/api/mix/guided/session/{sid}/steps/{int(current['step_no'])}/complete",
                json=json_body,
            )
            self.assertEqual(last_resp.status_code, 200, last_resp.get_json())
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 8.0, f"late-session step completes took {elapsed:.2f}s")
        self.assertIsNotNone(last_resp)
        payload_bytes = len(_json.dumps((last_resp.get_json() or {}).get("session") or {}))
        self.assertLess(payload_bytes, 400_000, f"session payload grew to {payload_bytes} bytes")

    def test_guided_step_complete_retries_transient_lock_without_user_busy_message(self):
        import sqlite3
        from unittest.mock import patch

        import db as db_mod
        from services import guided_mix_session_service as gms

        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = int(create.get_json()["mix_session_id"])
        open_step = next(s for s in self._guided_session_steps(sid) if not s.get("completed_at"))
        step_no = int(open_step["step_no"])
        real_retry = db_mod.sqlite_retry
        lock_calls = {"n": 0}

        def retry_with_one_lock(fn, *, attempts=12, base_delay_s=0.05, stats=None):
            def inner():
                lock_calls["n"] += 1
                if lock_calls["n"] == 1:
                    raise sqlite3.OperationalError("database is locked")
                return fn()

            return real_retry(inner, attempts=attempts, base_delay_s=0, stats=stats)

        with patch.object(gms, "sqlite_retry", retry_with_one_lock):
            resp = self.client.post(f"/api/mix/guided/session/{sid}/steps/{step_no}/complete", json={})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json() or {}
        self.assertEqual(body.get("status"), "success")
        self.assertGreaterEqual(lock_calls["n"], 2)
        self.assertNotIn("database is busy", str(body.get("message") or "").lower())

    def test_guided_duplicate_step_complete_does_not_advance_twice(self):
        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = int(create.get_json()["mix_session_id"])
        open_step = next(s for s in self._guided_session_steps(sid) if not s.get("completed_at"))
        step_no = int(open_step["step_no"])
        first = self.client.post(f"/api/mix/guided/session/{sid}/steps/{step_no}/complete", json={})
        self.assertEqual(first.status_code, 200)
        sess_after_first = self._guided_session_steps(sid)
        open_after_first = next(s for s in sess_after_first if not s.get("completed_at"))
        next_open_no = int(open_after_first["step_no"])
        completed_count_after_first = sum(1 for s in sess_after_first if s.get("completed_at"))
        second = self.client.post(f"/api/mix/guided/session/{sid}/steps/{step_no}/complete", json={})
        self.assertEqual(second.status_code, 200)
        sess_after_second = self._guided_session_steps(sid)
        open_after_second = next(s for s in sess_after_second if not s.get("completed_at"))
        self.assertEqual(int(open_after_second["step_no"]), next_open_no)
        self.assertEqual(
            sum(1 for s in sess_after_second if s.get("completed_at")),
            completed_count_after_first,
        )

    def test_guided_mix_summary_not_built_on_mid_session_step_complete(self):
        from unittest.mock import patch

        from services import guided_mix_session_service as gms

        create = self.client.post(
            "/api/mix/guided/session",
            json={"recipe_id": 1, "target_ml": 30, "target_nic_mg_ml": 0},
        )
        sid = int(create.get_json()["mix_session_id"])
        open_step = next(s for s in self._guided_session_steps(sid) if not s.get("completed_at"))
        step_no = int(open_step["step_no"])
        build_calls = {"n": 0}
        original = gms._build_guided_mix_summary

        def counting_build(*args, **kwargs):
            build_calls["n"] += 1
            return original(*args, **kwargs)

        with patch.object(gms, "_build_guided_mix_summary", counting_build):
            resp = self.client.post(f"/api/mix/guided/session/{sid}/steps/{step_no}/complete", json={})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(build_calls["n"], 0)
        self.assertIsNone((resp.get_json() or {}).get("session", {}).get("mix_summary"))


if __name__ == "__main__":
    unittest.main()

