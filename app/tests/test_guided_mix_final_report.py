"""Tests for Guided Mix final batch report / mix_summary display shape."""

from __future__ import annotations

import unittest

from services.guided_mix_session_service import (
    _format_accepted_deviation_row,
    _format_pg_adjustment_row,
    _ratio_shift_is_meaningful,
)

from tests.test_tvw_api import TVWApiTests, flask_app


class GuidedMixFinalReportTests(TVWApiTests):
    def _create_guided_session(self, *, target_ml: int = 60) -> int:
        resp = self.client.post(
            "/api/mix/guided/session",
            json={
                "recipe_id": 1,
                "target_ml": target_ml,
                "target_nic_mg_ml": 0,
                "pg_ratio": 30,
                "vg_ratio": 70,
            },
        )
        self.assertIn(resp.status_code, (200, 201))
        return int(resp.get_json()["mix_session_id"])

    def test_mix_summary_has_readable_batch_report_fields(self):
        sid = self._create_guided_session(target_ml=60)
        self._complete_all_guided_steps(sid)
        summary = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]["mix_summary"]
        self.assertIsNotNone(summary)
        self.assertAlmostEqual(float(summary["batch_size_ml"]), 60.0)
        self.assertEqual(summary["target_ratio_label"], "30/70")
        self.assertIn("estimated_final_ratio_label", summary)
        self.assertIn("completion_status", summary)
        self.assertIn("notes_for_next_time", summary)
        self.assertTrue(isinstance(summary["notes_for_next_time"], list))

    def test_flavour_over_summary_uses_human_readable_rows(self):
        sid = self._create_guided_session(target_ml=60)
        fl_step = self._guided_first_add_step(sid)
        fl_no = int(fl_step["step_no"])
        display = round(float((fl_step.get("payload") or {}).get("target_grams") or 0.0), 2)
        over = self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{fl_no}/complete",
            json={"measured_grams": round(display + 0.01, 2)},
        )
        self.assertEqual(over.status_code, 200)
        self.assertTrue(bool(((over.get_json() or {}).get("reading") or {}).get("base_adjusted")))
        self._complete_all_guided_steps(sid)
        summary = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]["mix_summary"]
        self.assertTrue(summary["pg_adjustments"])
        pg_row = summary["pg_adjustments"][0]
        self.assertIn("label", pg_row)
        self.assertIn("→ PG reduced by", pg_row["label"])
        self.assertNotIn("pg_adjusted", pg_row["label"])
        dev_rows = summary["accepted_deviations"]
        self.assertTrue(dev_rows)
        self.assertTrue(any("accepted over target" in str(r.get("label") or "") for r in dev_rows))
        self.assertFalse(any(str(r.get("label") or "").startswith("accepted_flavour_over") for r in dev_rows))
        if summary.get("ratio_shift_explanation"):
            self.assertIn("PG was adjusted", summary["ratio_shift_explanation"])

    def test_final_report_template_markers(self):
        sid = self._create_guided_session(target_ml=60)
        html = self.client.get(f"/guided-mix/session/{sid}").data.decode("utf-8")
        self.assertIn("tvw-ws-batch-report", html)
        self.assertIn("renderMixSummary", html)
        self.assertIn('stepType === "completion"', html)
        self.assertIn("MIX AGAIN", html)
        self.assertIn("BACK TO RECIPE", html)
        self.assertIn("DASHBOARD", html)
        self.assertIn("Adjustments made", html)
        self.assertIn("Accepted deviations", html)
        self.assertIn("Notes for next time", html)
        self.assertIn("Mixer notes", html)
        self.assertIn("SAVE NOTES", html)
        self.assertIn("saveMixerNotes", html)
        render_chunk = html.split("renderMixSummary", 1)[1][:5000]
        self.assertNotIn("accepted_flavour_over", render_chunk)
        self.assertNotIn("pg_adjusted", render_chunk)

    def test_formatting_helpers(self):
        pg = _format_pg_adjustment_row(
            {"source_ingredient": "Brown Sugar Concentrate", "excess_grams": 0.12, "cut_ml": 0.1}
        )
        self.assertIn("Brown Sugar", pg["label"])
        self.assertIn("0.12 g", pg["label"])
        dev = _format_accepted_deviation_row(
            {"event": "accepted_flavour_over", "ingredient": "Brulee Concentrate"}
        )
        self.assertEqual(dev["label"], "Brulee accepted over target")
        self.assertTrue(_ratio_shift_is_meaningful(30, 70, 20, 80))
        self.assertFalse(_ratio_shift_is_meaningful(30, 70, 31, 69))

    def test_completed_mix_can_save_and_reload_user_notes(self):
        sid = self._create_guided_session(target_ml=30)
        self._complete_all_guided_steps(sid)
        before = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        self.assertEqual(before.get("user_notes") or "", "")
        save = self.client.post(
            f"/api/mix/guided/session/{sid}/notes",
            json={"user_notes": "Steep 2 weeks. Tastes better after a day."},
        )
        self.assertEqual(save.status_code, 200)
        body = save.get_json()
        self.assertEqual(body.get("message"), "Notes saved.")
        self.assertIn("Steep 2 weeks", body.get("user_notes") or "")
        self.assertTrue(body.get("user_notes_updated_at"))
        after = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        self.assertEqual(after.get("user_notes"), "Steep 2 weeks. Tastes better after a day.")
        summary = after.get("mix_summary") or {}
        self.assertEqual(summary.get("user_notes"), "Steep 2 weeks. Tastes better after a day.")
        history = self.client.get("/api/mix/history?filter=completed&limit=20").get_json()
        row = next((i for i in (history.get("items") or []) if int(i.get("id") or 0) == sid), None)
        self.assertIsNotNone(row)
        self.assertIn("Steep 2 weeks", row.get("user_notes") or "")

    def test_empty_notes_do_not_block_completion(self):
        sid = self._create_guided_session(target_ml=30)
        self._complete_all_guided_steps(sid)
        sess = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
        self.assertTrue((sess.get("progress") or {}).get("all_done"))
        self.assertEqual(sess.get("user_notes") or "", "")

    def test_saving_notes_does_not_overwrite_mix_event_log(self):
        sid = self._create_guided_session(target_ml=60)
        fl_step = self._guided_first_add_step(sid)
        fl_no = int(fl_step["step_no"])
        display = round(float((fl_step.get("payload") or {}).get("target_grams") or 0.0), 2)
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{fl_no}/complete",
            json={"measured_grams": round(display + 0.01, 2)},
        )
        self._complete_all_guided_steps(sid)
        with flask_app.app_context():
            import json as _json

            from db import get_db

            row = get_db().execute("SELECT result_json FROM mix_sessions WHERE id=?", (sid,)).fetchone()
            meta_before = _json.loads(row["result_json"] or "{}")
            log_before = list(meta_before.get("mix_event_log") or [])
        self.client.post(
            f"/api/mix/guided/session/{sid}/notes",
            json={"user_notes": "Used backup bottle."},
        )
        with flask_app.app_context():
            import json as _json

            from db import get_db

            row = get_db().execute("SELECT result_json FROM mix_sessions WHERE id=?", (sid,)).fetchone()
            meta_after = _json.loads(row["result_json"] or "{}")
        self.assertEqual(meta_after.get("user_notes"), "Used backup bottle.")
        self.assertEqual(meta_after.get("mix_event_log"), log_before)
        summary = self.client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]["mix_summary"]
        self.assertTrue(summary.get("pg_adjustments") or summary.get("accepted_deviations"))
        self.assertNotIn("raw_event_log", summary)

    def test_notes_save_blocked_before_mix_complete(self):
        sid = self._create_guided_session(target_ml=30)
        blocked = self.client.post(
            f"/api/mix/guided/session/{sid}/notes",
            json={"user_notes": "Too early"},
        )
        self.assertEqual(blocked.status_code, 409)

    def test_saved_notes_surface_on_recipe_mixer_notes_api(self):
        sid = self._create_guided_session(target_ml=60)
        self._complete_all_guided_steps(sid)
        note_text = "Too sweet at 10 days. Reduce Sweetness next time."
        save = self.client.post(
            f"/api/mix/guided/session/{sid}/notes",
            json={"user_notes": note_text},
        )
        self.assertEqual(save.status_code, 200)
        api = self.client.get("/api/recipes/1/mixer-notes?limit=3")
        self.assertEqual(api.status_code, 200)
        body = api.get_json()
        self.assertGreaterEqual(body.get("total") or 0, 1)
        notes = body.get("notes") or []
        self.assertTrue(any(note_text in str(n.get("note_text") or "") for n in notes))
        first = notes[0]
        self.assertIn("batch_size_ml", first)
        self.assertIn("target_pgvg", first)
        self.assertIn("updated_at", first)

    def test_recipe_detail_shows_mixer_notes_not_raw_events(self):
        with flask_app.app_context():
            from db import get_db

            db = get_db()
            db.execute("UPDATE recipes SET slug = 'recipe-one' WHERE id = 1")
            db.commit()
        sid = self._create_guided_session(target_ml=30)
        self._complete_all_guided_steps(sid)
        note_text = "Brulee slightly high in this batch, but tasted better."
        self.client.post(
            f"/api/mix/guided/session/{sid}/notes",
            json={"user_notes": note_text},
        )
        html = self.client.get("/recipes/recipe-one").data.decode("utf-8")
        self.assertIn("Mixer notes", html)
        self.assertIn(note_text, html)
        self.assertIn("Batch history", html)
        self.assertNotIn("accepted_flavour_over", html)
        self.assertNotIn("pg_adjusted", html)
        self.assertNotIn("nicotine_recovery_log", html)

    def test_recipe_mixer_notes_api_limits_to_latest_few(self):
        notes_saved: list[str] = []
        for idx in range(3):
            sid = self._create_guided_session(target_ml=30)
            self._complete_all_guided_steps(sid)
            text = f"Batch note number {idx + 1}"
            notes_saved.append(text)
            self.client.post(
                f"/api/mix/guided/session/{sid}/notes",
                json={"user_notes": text},
            )
        api = self.client.get("/api/recipes/1/mixer-notes?limit=2")
        body = api.get_json()
        self.assertEqual(len(body.get("notes") or []), 2)
        self.assertGreaterEqual(body.get("total") or 0, 3)

    def test_recipe_batch_history_exposes_full_logs(self):
        with flask_app.app_context():
            from db import get_db

            db = get_db()
            db.execute("UPDATE recipes SET slug = 'recipe-one' WHERE id = 1")
            db.commit()
        sid = self._create_guided_session(target_ml=60)
        fl_step = self._guided_first_add_step(sid)
        fl_no = int(fl_step["step_no"])
        display = round(float((fl_step.get("payload") or {}).get("target_grams") or 0.0), 2)
        self.client.post(
            f"/api/mix/guided/session/{sid}/steps/{fl_no}/complete",
            json={"measured_grams": round(display + 0.01, 2)},
        )
        self._complete_all_guided_steps(sid)
        self.client.post(
            f"/api/mix/guided/session/{sid}/notes",
            json={"user_notes": "VG bubbles made fill look low. Let it settle."},
        )
        hist = self.client.get("/api/recipes/1/batch-history").get_json()
        self.assertGreaterEqual(hist.get("total") or 0, 1)
        entry = next((i for i in (hist.get("items") or []) if int(i.get("session_id") or 0) == sid), None)
        self.assertIsNotNone(entry)
        self.assertIn("VG bubbles", entry.get("user_notes") or "")
        summary = entry.get("mix_summary") or {}
        self.assertIn("completion_status", summary)
        self.assertIn("raw_event_log", summary)
        if summary.get("pg_adjustments"):
            self.assertIn("label", summary["pg_adjustments"][0])
        page = self.client.get("/recipes/recipe-one/mix-logs")
        self.assertEqual(page.status_code, 200)
        page_html = page.data.decode("utf-8")
        self.assertIn("Batch history", page_html)
        self.assertIn("Adjustments made", page_html)
        self.assertNotIn("accepted_flavour_over", page_html)


if __name__ == "__main__":
    unittest.main()
