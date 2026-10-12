"""Regression: instruction/setup step completes without measured_grams."""
from __future__ import annotations

import os
import re
import tempfile
import unittest

from app import app as flask_app
from db import get_db
from migrations import run_startup_migrations
from services.guided_mix_session_service import create_guided_mix_session


class GuidedSetupStepTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "guided_setup.db")
        flask_app.config["TESTING"] = True
        flask_app.config["DATABASE"] = self.db_path
        flask_app.config["WTF_CSRF_ENABLED"] = False

        with flask_app.app_context():
            db = get_db()
            with open(os.path.join(os.path.dirname(__file__), "..", "schema.sql"), "r", encoding="utf-8") as f:
                db.executescript(f.read())
            run_startup_migrations()
            db.execute(
                "INSERT INTO users (id, username, password_hash, role, status) VALUES (1, 'member1', 'x', 'member', 'Active')"
            )
            db.execute(
                "INSERT INTO flavor_reference (id, brand, name, category, density, active) "
                "VALUES (101, 'Test', 'Vanilla', 'Flavor', 1.036, 1)"
            )
            db.execute(
                "INSERT INTO recipes (id, name, slug, status, visibility, moderation_status, active, created_by, current_version_id) "
                "VALUES (1, 'Recipe1', 'recipe-one', 'WIP', 'public', 'active', 1, 1, 1)"
            )
            db.execute(
                "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent) VALUES (1, 1, '1', 5.0)"
            )
            db.execute("INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (1, 101, 5.0)")
            db.execute(
                "INSERT INTO flavours (id, canonical_name, brand, category, source_flavor_ref_id) "
                "VALUES (1, 'Vanilla', 'Test', 'Flavor', 101)"
            )
            db.commit()

        self.client = flask_app.test_client()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "member1"
            sess["role"] = "member"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_session(self) -> int:
        with flask_app.app_context():
            sid, _, _ = create_guided_mix_session(
                user_id=1,
                recipe_id=1,
                requested_version_id=1,
                target_ml=60,
                target_nic_mg_ml=3,
                nic_base_mg_ml=100,
                pg_ratio=30,
                vg_ratio=70,
                nicotine_carrier="PG",
            )
            return int(sid)

    def test_setup_step_page_has_action_button_with_step_no(self):
        sid = self._create_session()
        html = self.client.get(f"/guided-mix/session/{sid}").data.decode("utf-8")
        self.assertIn('id="guidedSessionBundle"', html)
        self.assertIn("js-done-step", html)
        self.assertIn("/api/mix/guided/session/", html)
        import json
        import re

        match = re.search(
            r'<script type="application/json" id="guidedSessionBundle">(.*?)</script>',
            html,
            re.S,
        )
        self.assertIsNotNone(match)
        bundle = json.loads(match.group(1))
        open_step = next(s for s in bundle["steps"] if not s.get("completed_at"))
        self.assertEqual(int(open_step["step_no"]), 1)
        self.assertEqual(str((open_step.get("payload") or {}).get("action_label")), "Station ready")

    def test_complete_instruction_setup_step_advances_session(self):
        sid = self._create_session()
        before = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        first = before["steps"][0]
        self.assertIsNone(first.get("completed_at"))
        step_type = str((first.get("payload") or {}).get("step_type") or "").lower()
        self.assertEqual(step_type, "instruction")
        step_no = int(first["step_no"])

        done = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{step_no}/complete",
            json={"undo": False, "measured_grams": ""},
        )
        self.assertEqual(done.status_code, 200, done.get_json())
        self.assertEqual(done.get_json().get("status"), "success")

        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        self.assertIsNotNone(after["steps"][0].get("completed_at"))
        open_step = next(s for s in after["steps"] if not s.get("completed_at"))
        self.assertGreater(int(open_step["step_no"]), step_no)

    def test_embedded_bundle_json_is_valid_for_apply_session(self):
        sid = self._create_session()
        html = self.client.get(f"/guided-mix/session/{sid}").data.decode("utf-8")
        import json
        import re

        match = re.search(
            r'<script type="application/json" id="guidedSessionBundle">(.*?)</script>',
            html,
            re.S,
        )
        self.assertIsNotNone(match, "guidedSessionBundle JSON script missing")
        bundle = json.loads(match.group(1))
        self.assertEqual(int(bundle["id"]), sid)
        self.assertTrue(bundle["steps"])
        self.assertNotIn("applySession({", html)

    def test_v4_workstation_presentation_markers(self):
        sid = self._create_session()
        html = self.client.get(f"/guided-mix/session/{sid}").data.decode("utf-8")
        self.assertIn('id="focusRole"', html)
        self.assertIn('id="focusIngredient"', html)
        self.assertIn('id="workflowPhase"', html)
        self.assertIn("Scale Zeroed", html)
        self.assertIn("Ready to Pour", html)
        self.assertIn("Pour Complete", html)
        self.assertIn("RIGHT side of your bench", html)
        self.assertIn("Arrange these bottles", html)

    def test_v7_bench_layout_markers(self):
        sid = self._create_session()
        html = self.client.get(f"/guided-mix/session/{sid}").data.decode("utf-8")
        self.assertIn('id="usedPanel"', html)
        self.assertIn('id="todoPanel"', html)
        self.assertIn("USED", html)
        self.assertIn("LEFT SIDE", html)
        self.assertIn("TO ADD", html)
        self.assertIn("RIGHT SIDE", html)
        self.assertNotIn("Mission control", html)
        self.assertNotIn("benchLanesHtml", html)
        self.assertIn("Arrange these bottles on the RIGHT side of your bench", html)
        self.assertIn('id="guidedToast"', html)
        self.assertIn("stepAckMessage", html)
        self.assertIn("showUndoFor", html)
        self.assertIn("renderBenchPanels", html)
        self.assertIn('id="remainingReadout"', html)
        self.assertIn('id="centerRecipeName"', html)

    def test_v7_three_column_bench_layout(self):
        sid = self._create_session()
        html = self.client.get(f"/guided-mix/session/{sid}").data.decode("utf-8")
        self.assertIn("tvw-ws-bench-layout", html)
        self.assertIn('aria-label="Used bottles left side"', html)
        self.assertIn('aria-label="Bottles to add right side"', html)
        self.assertIn("updateCenterBatchMeta", html)

    def test_v5_physical_action_headers_after_setup(self):
        sid = self._create_session()
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/1/complete",
            json={"undo": False, "measured_grams": ""},
        )
        html = self.client.get(f"/guided-mix/session/{sid}").data.decode("utf-8")
        self.assertIn("Pick up", html)
        self.assertIn("Zero the scale", html)
        self.assertIn("physicalActionFor", html)

    def test_v5_no_contradictory_undo_on_tare(self):
        sid = self._create_session()
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/1/complete",
            json={"undo": False, "measured_grams": ""},
        )
        html = self.client.get(f"/guided-mix/session/{sid}").data.decode("utf-8")
        self.assertIn('if (stepType === "tare") return false;', html)
        self.assertIn("showUndoFor", html)
        self.assertIn("isTarePhase", html)
        self.assertIn("is-prep-hidden", html)

    def test_v4_natural_action_labels_after_setup(self):
        sid = self._create_session()
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/1/complete",
            json={"undo": False, "measured_grams": ""},
        )
        html = self.client.get(f"/guided-mix/session/{sid}").data.decode("utf-8")
        self.assertIn('tare: "Scale Zeroed"', html)
        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        open_step = next(s for s in after["steps"] if not s.get("completed_at"))
        self.assertEqual(str((open_step.get("payload") or {}).get("step_type")).lower(), "tare")

    def test_v5_tare_has_no_contradictory_undo(self):
        sid = self._create_session()
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/1/complete",
            json={"undo": False, "measured_grams": ""},
        )
        html = self.client.get(f"/guided-mix/session/{sid}").data.decode("utf-8")
        self.assertIn('if (stepType === "tare") return false', html)
        self.assertNotIn("Not zeroed yet", html)

    def test_v6_nicotine_pour_before_flavour_concentrates(self):
        sid = self._create_session()
        with flask_app.app_context():
            from services.tvw_mix_service import guided_mix_steps_for_recipe_version

            steps = guided_mix_steps_for_recipe_version(
                recipe_id=1,
                version_id=1,
                target_ml=60,
                target_nic_mg_ml=3,
                nic_base_mg_ml=100,
                pg_ratio=30,
                vg_ratio=70,
                nicotine_carrier="PG",
            )
        pours = [s for s in steps if str(s.get("step_type") or "").lower() == "add_ingredient"]
        self.assertGreaterEqual(len(pours), 2, "expected nicotine and at least one concentrate pour")
        first = pours[0]
        self.assertEqual(str(first.get("category") or "").lower(), "nicotine")
        self.assertIn("nicotine", str(first.get("name") or "").lower())

        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/1/complete",
            json={"undo": False, "measured_grams": ""},
        )
        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        open_step = next(s for s in after["steps"] if not s.get("completed_at"))
        payload = open_step.get("payload") or {}
        if str(payload.get("step_type") or "").lower() == "tare":
            self.assertEqual(str(payload.get("category") or "").lower(), "nicotine")

    def test_g_ml_clarity_markers_and_display_helpers(self):
        sid = self._create_session()
        html = self.client.get(f"/guided-mix/session/{sid}").data.decode("utf-8")
        self.assertIn('id="leaveBench"', html)
        self.assertIn("← Dashboard", html)
        self.assertIn('id="pourEquivBlock"', html)
        self.assertIn("Target weight:", html)
        self.assertIn("Equivalent volume:", html)
        self.assertIn("Batch progress:", html)
        self.assertIn("cleanDisplayName", html)
        self.assertIn("Up next:", html)
        self.assertNotIn(">Next <strong", html)
        self.assertIn("Mixed by weight. Batch progress is shown in ml.", html)
        self.assertIn("VG-heavy ingredients weigh more than their ml volume.", html)
        self.assertIn("Bottle Moved", html)
        self.assertIn("complete", html)
        self.assertIn("LEFT SIDE", html)
        self.assertIn("tvw-ws-bench-note", html)
        self.assertIn("underPourNoteFromPolicy", html)
        self.assertIn("Close enough for this pour", html)
        self.assertIn("Accept This Pour", html)
        self.assertIn("Try One Drop", html)
        self.assertIn("Careful top-up", html)
        self.assertIn("js-accept-under", html)
        self.assertIn("js-try-one-drop", html)
        self.assertIn("js-check-topup", html)
        self.assertIn("awaiting_under_decision", html)
        self.assertIn("Continue Pouring", html)
        self.assertIn("Save As Partial / Change Bottle", html)
        self.assertIn("Accept Underpour", html)
        self.assertIn("js-continue-pouring", html)
        self.assertIn("js-save-partial", html)
        self.assertIn("under_action", html)
        self.assertIn("activePourTargetGrams", html)
        self.assertIn("activePourScaleGrams", html)
        self.assertIn("syncActivePourReadout", html)
        self.assertIn("guided_scale_input.js", html)
        self.assertIn("scaleInputUi", html)
        self.assertIn("attachScaleInput", html)
        self.assertIn("resolveMeasuredGramsForStep", html)
        self.assertIn('type="text"', html)
        self.assertIn("checkpoint_note", html)
        self.assertIn("overPourNoteFromText", html)
        self.assertIn("Slightly over by", html)
        self.assertIn("is-prep-hidden", html)
        self.assertIn('transition: "Bottle Moved"', html)
        self.assertNotIn("Next Ingredient", html)

    def test_nicotine_pour_payload_includes_ml_and_grams(self):
        with flask_app.app_context():
            from services.tvw_mix_service import guided_mix_steps_for_recipe_version

            steps = guided_mix_steps_for_recipe_version(
                recipe_id=1,
                version_id=1,
                target_ml=100,
                target_nic_mg_ml=3,
                nic_base_mg_ml=100,
                pg_ratio=30,
                vg_ratio=70,
                nicotine_carrier="VG",
            )
        pours = [s for s in steps if str(s.get("step_type") or "").lower() == "add_ingredient"]
        nic = next(s for s in pours if str(s.get("category") or "").lower() == "nicotine")
        grams = float(nic.get("target_grams") or 0)
        ml = float(nic.get("ml") or 0)
        self.assertGreater(grams, 0)
        self.assertGreater(ml, 0)
        self.assertGreater(grams, ml, "VG-carrier nicotine should weigh more than its ml")


if __name__ == "__main__":
    unittest.main()
