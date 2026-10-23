"""Regression tests for Pauly Guided Mix feedback (zero-nic, finished PG/VG, labels)."""

from __future__ import annotations

import unittest

from app import app as flask_app


class PaulyGuidedMixFeedbackFixes(unittest.TestCase):
    def test_zero_nicotine_produces_no_nicotine_pour_step(self):
        with flask_app.app_context():
            from core.calculator import (
                FlavorInput,
                MixInput,
                build_guided_assistant_steps,
                compute_mix,
                compute_mix_step_breakdown,
            )

            mix_input = MixInput(
                target_ml=120.0,
                target_nic_mg_ml=0.0,
                nic_base_mg_ml=100.0,
                pg_ratio=30.0,
                vg_ratio=70.0,
                nicotine_carrier="PG",
                flavors=[
                    FlavorInput(name="Custard", percent=10.0, density=1.036, carrier="PG"),
                    FlavorInput(name="Vanilla", percent=4.5, density=1.036, carrier="PG"),
                ],
            )
            result = compute_mix(mix_input)
            self.assertEqual(result.nicotine_ml, 0.0)
            self.assertEqual(result.nicotine_g, 0.0)

            breakdown = compute_mix_step_breakdown(mix_input)
            cats = [str(s.get("category") or "").lower() for s in breakdown]
            self.assertNotIn("nicotine", cats)

            guided = build_guided_assistant_steps(mix_input, tone_profile="tvw")
            pours = [
                s
                for s in guided
                if str(s.get("step_type") or "").lower() == "add_ingredient"
            ]
            self.assertFalse(
                any(str(s.get("category") or "").lower() == "nicotine" for s in pours)
            )
            self.assertFalse(
                any(float(s.get("target_grams") or 0) <= 0.02 for s in pours),
                "zero-requirement pour steps must not be generated",
            )

    def test_zero_required_steps_are_not_treated_as_underpours(self):
        with flask_app.app_context():
            from services.guided_mix_session_service import (
                _classify_nicotine_under_pour,
                _classify_under_pour,
                _classify_under_pour_for_step,
            )

            for target in (0.0, 0.01, 0.02):
                cls, msg = _classify_under_pour(display_target=target, measured=0.0)
                self.assertEqual(cls, "within_tolerance", f"target={target}")
                self.assertEqual(msg, "")
                cls_n, msg_n = _classify_nicotine_under_pour(
                    display_target=target, measured=0.0
                )
                self.assertEqual(cls_n, "within_tolerance", f"nic target={target}")
                self.assertEqual(msg_n, "")
                cls_s, msg_s = _classify_under_pour_for_step(
                    payload={"category": "nicotine", "name": "Nicotine base"},
                    display_target=target,
                    measured=0.0,
                )
                self.assertEqual(cls_s, "within_tolerance")
                self.assertEqual(msg_s, "")
                self.assertNotIn("under", (msg + msg_n + msg_s).lower())

    def test_pg_flavour_contributes_to_final_pg_and_reduces_standalone_pg(self):
        with flask_app.app_context():
            from core.calculator import FlavorInput, MixInput, compute_mix

            mix_input = MixInput(
                target_ml=100.0,
                target_nic_mg_ml=0.0,
                nic_base_mg_ml=100.0,
                pg_ratio=30.0,
                vg_ratio=70.0,
                nicotine_carrier="PG",
                flavors=[
                    FlavorInput(name="PG Flavour", percent=15.0, density=1.036, carrier="PG"),
                ],
            )
            result = compute_mix(mix_input)
            self.assertAlmostEqual(result.flavor_pg_ml, 15.0, places=3)
            self.assertAlmostEqual(result.flavor_vg_ml, 0.0, places=3)
            # Standalone PG = target PG − flavour PG = 30 − 15
            self.assertAlmostEqual(result.pg_ml, 15.0, places=3)
            self.assertAlmostEqual(result.vg_ml, 70.0, places=3)
            self.assertAlmostEqual(result.final_pg_ml, 30.0, places=3)
            self.assertAlmostEqual(result.final_vg_ml, 70.0, places=3)
            self.assertAlmostEqual(result.final_pg_percent, 30.0, places=2)
            self.assertAlmostEqual(result.final_vg_percent, 70.0, places=2)
            self.assertTrue(result.ratio_achievable)

    def test_pg_nicotine_contributes_to_final_pg(self):
        with flask_app.app_context():
            from core.calculator import FlavorInput, MixInput, compute_mix

            mix_input = MixInput(
                target_ml=100.0,
                target_nic_mg_ml=3.0,
                nic_base_mg_ml=100.0,
                pg_ratio=30.0,
                vg_ratio=70.0,
                nicotine_carrier="PG",
                flavors=[
                    FlavorInput(name="PG Flavour", percent=15.0, density=1.036, carrier="PG"),
                ],
            )
            result = compute_mix(mix_input)
            self.assertAlmostEqual(result.nicotine_ml, 3.0, places=3)
            self.assertAlmostEqual(result.nicotine_pg_ml, 3.0, places=3)
            self.assertAlmostEqual(result.nicotine_vg_ml, 0.0, places=3)
            # Standalone PG = 30 − 15 − 3 = 12
            self.assertAlmostEqual(result.pg_ml, 12.0, places=3)
            self.assertAlmostEqual(result.final_pg_ml, 30.0, places=3)
            self.assertAlmostEqual(result.final_vg_ml, 70.0, places=3)
            self.assertTrue(result.ratio_achievable)

    def test_vg_nicotine_contributes_to_final_vg(self):
        with flask_app.app_context():
            from core.calculator import FlavorInput, MixInput, compute_mix

            mix_input = MixInput(
                target_ml=100.0,
                target_nic_mg_ml=3.0,
                nic_base_mg_ml=100.0,
                pg_ratio=30.0,
                vg_ratio=70.0,
                nicotine_carrier="VG",
                flavors=[
                    FlavorInput(name="PG Flavour", percent=10.0, density=1.036, carrier="PG"),
                ],
            )
            result = compute_mix(mix_input)
            self.assertAlmostEqual(result.nicotine_vg_ml, 3.0, places=3)
            self.assertAlmostEqual(result.nicotine_pg_ml, 0.0, places=3)
            # Standalone VG = 70 − 3 = 67; PG = 30 − 10 = 20
            self.assertAlmostEqual(result.vg_ml, 67.0, places=3)
            self.assertAlmostEqual(result.pg_ml, 20.0, places=3)
            self.assertAlmostEqual(result.final_pg_ml, 30.0, places=3)
            self.assertAlmostEqual(result.final_vg_ml, 70.0, places=3)

    def test_vg_flavour_carrier_reduces_standalone_vg(self):
        with flask_app.app_context():
            from core.calculator import FlavorInput, MixInput, compute_mix

            mix_input = MixInput(
                target_ml=100.0,
                target_nic_mg_ml=0.0,
                nic_base_mg_ml=100.0,
                pg_ratio=30.0,
                vg_ratio=70.0,
                nicotine_carrier="PG",
                flavors=[
                    FlavorInput(name="VG Flavour", percent=10.0, density=1.26, carrier="VG"),
                ],
            )
            result = compute_mix(mix_input)
            self.assertAlmostEqual(result.flavor_vg_ml, 10.0, places=3)
            self.assertAlmostEqual(result.pg_ml, 30.0, places=3)
            self.assertAlmostEqual(result.vg_ml, 60.0, places=3)
            self.assertAlmostEqual(result.final_pg_percent, 30.0, places=2)
            self.assertAlmostEqual(result.final_vg_percent, 70.0, places=2)

    def test_impossible_ratio_warns_and_reports_truthful_final(self):
        with flask_app.app_context():
            from core.calculator import FlavorInput, MixInput, compute_mix

            mix_input = MixInput(
                target_ml=100.0,
                target_nic_mg_ml=0.0,
                nic_base_mg_ml=100.0,
                pg_ratio=20.0,
                vg_ratio=80.0,
                nicotine_carrier="PG",
                flavors=[
                    FlavorInput(name="Heavy PG", percent=25.0, density=1.036, carrier="PG"),
                ],
            )
            result = compute_mix(mix_input)
            self.assertFalse(result.ratio_achievable)
            self.assertTrue(result.ratio_warning)
            self.assertIn("cannot be reached", result.ratio_warning.lower())
            self.assertAlmostEqual(result.pg_ml, 0.0, places=3)
            self.assertAlmostEqual(result.flavor_pg_ml, 25.0, places=3)
            self.assertAlmostEqual(result.final_pg_ml, 25.0, places=3)
            self.assertGreater(result.final_pg_percent, 20.0)
            # Truthful final is not the impossible 20/80 target.
            self.assertNotAlmostEqual(result.final_pg_percent, 20.0, places=0)

    def test_density_aware_grams_preserved(self):
        with flask_app.app_context():
            from core.calculator import PG_DENSITY, VG_DENSITY, MixInput, compute_mix

            mix_input = MixInput(
                target_ml=100.0,
                target_nic_mg_ml=0.0,
                nic_base_mg_ml=100.0,
                pg_ratio=30.0,
                vg_ratio=70.0,
                nicotine_carrier="PG",
                flavors=[],
            )
            result = compute_mix(mix_input)
            self.assertAlmostEqual(result.pg_g, 30.0 * PG_DENSITY, places=3)
            self.assertAlmostEqual(result.vg_g, 70.0 * VG_DENSITY, places=3)

    def test_explicit_pg_carrier_remains_pg(self):
        with flask_app.app_context():
            from core.calculator import (
                CARRIER_SOURCE_EXPLICIT,
                format_carrier_display,
                resolve_carrier,
            )

            carrier, source = resolve_carrier("PG")
            self.assertEqual(carrier, "PG")
            self.assertEqual(source, CARRIER_SOURCE_EXPLICIT)
            self.assertEqual(format_carrier_display("PG"), "PG")

    def test_explicit_vg_carrier_remains_vg(self):
        with flask_app.app_context():
            from core.calculator import (
                CARRIER_SOURCE_EXPLICIT,
                format_carrier_display,
                resolve_carrier,
            )

            carrier, source = resolve_carrier("VG")
            self.assertEqual(carrier, "VG")
            self.assertEqual(source, CARRIER_SOURCE_EXPLICIT)
            self.assertEqual(format_carrier_display("VG"), "VG")

    def test_unknown_carrier_defaults_to_pg_assumed(self):
        with flask_app.app_context():
            from core.calculator import (
                CARRIER_SOURCE_ASSUMED_DEFAULT,
                format_carrier_display,
                normalize_carrier,
                resolve_carrier,
            )

            for raw in ("", None, "unknown", "base", "mixed"):
                carrier, source = resolve_carrier(raw)
                self.assertEqual(carrier, "PG", f"raw={raw!r}")
                self.assertEqual(source, CARRIER_SOURCE_ASSUMED_DEFAULT, f"raw={raw!r}")
                self.assertEqual(normalize_carrier(raw), "PG")
                self.assertEqual(format_carrier_display(raw), "PG (assumed)")

    def test_high_density_unknown_flavour_does_not_become_vg(self):
        with flask_app.app_context():
            from core.calculator import (
                CARRIER_SOURCE_ASSUMED_DEFAULT,
                FlavorInput,
                MixInput,
                compute_mix,
                compute_mix_step_breakdown,
                resolve_carrier,
            )

            # Density looks VG-like but carrier is unset — must stay PG assumed.
            carrier, source = resolve_carrier("")
            self.assertEqual(carrier, "PG")
            self.assertEqual(source, CARRIER_SOURCE_ASSUMED_DEFAULT)

            mix_input = MixInput(
                target_ml=100.0,
                target_nic_mg_ml=0.0,
                nic_base_mg_ml=100.0,
                pg_ratio=30.0,
                vg_ratio=70.0,
                nicotine_carrier="PG",
                flavors=[
                    FlavorInput(
                        name="Dense unknown",
                        percent=10.0,
                        density=1.26,
                        carrier="",
                    ),
                ],
            )
            result = compute_mix(mix_input)
            self.assertAlmostEqual(result.flavor_pg_ml, 10.0, places=3)
            self.assertAlmostEqual(result.flavor_vg_ml, 0.0, places=3)
            # Standalone PG reduced; VG untouched by this flavour.
            self.assertAlmostEqual(result.pg_ml, 20.0, places=3)
            self.assertAlmostEqual(result.vg_ml, 70.0, places=3)
            # Density still drives grams (10 ml × 1.26).
            self.assertAlmostEqual(result.flavor_g, 12.6, places=3)

            steps = compute_mix_step_breakdown(mix_input)
            conc = next(s for s in steps if s.get("category") == "concentrate")
            self.assertEqual(conc["carrier"], "PG")
            self.assertEqual(conc["carrier_source"], CARRIER_SOURCE_ASSUMED_DEFAULT)
            self.assertEqual(conc["carrier_label"], "PG (assumed)")

    def test_final_pgvg_uses_assumed_default_carrier_consistently(self):
        with flask_app.app_context():
            from core.calculator import FlavorInput, MixInput, compute_mix

            # Same maths whether carrier is blank or explicitly PG.
            blank = compute_mix(
                MixInput(
                    target_ml=100.0,
                    target_nic_mg_ml=0.0,
                    nic_base_mg_ml=100.0,
                    pg_ratio=30.0,
                    vg_ratio=70.0,
                    nicotine_carrier="PG",
                    flavors=[
                        FlavorInput(name="A", percent=15.0, density=1.05, carrier=""),
                    ],
                )
            )
            explicit = compute_mix(
                MixInput(
                    target_ml=100.0,
                    target_nic_mg_ml=0.0,
                    nic_base_mg_ml=100.0,
                    pg_ratio=30.0,
                    vg_ratio=70.0,
                    nicotine_carrier="PG",
                    flavors=[
                        FlavorInput(name="A", percent=15.0, density=1.05, carrier="PG"),
                    ],
                )
            )
            self.assertAlmostEqual(blank.final_pg_ml, explicit.final_pg_ml, places=3)
            self.assertAlmostEqual(blank.final_vg_ml, explicit.final_vg_ml, places=3)
            self.assertAlmostEqual(blank.pg_ml, explicit.pg_ml, places=3)
            self.assertAlmostEqual(blank.flavor_pg_ml, 15.0, places=3)

    def test_user_facing_nicotine_labels_are_explicit(self):
        """Mix setup / recipe / production job labels must say nicotine mg/ml explicitly."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        checks = [
            (
                root / "templates" / "tvw_recipe_detail.html",
                "Target nicotine strength (mg/ml)",
            ),
            (
                root / "templates" / "tvw_guided_mix_start.html",
                "Target nicotine strength (mg/ml)",
            ),
            (
                root / "templates" / "tvw_recipe_builder.html",
                "Target nicotine strength (mg/ml)",
            ),
            (
                root / "templates" / "production_jobs" / "detail.html",
                "Target nicotine strength (mg/ml)",
            ),
        ]
        for path, needle in checks:
            text = path.read_text(encoding="utf-8")
            self.assertIn(needle, text, f"missing label in {path.name}")
            self.assertNotIn(">Desired strength<", text)
            self.assertNotIn(">Nic mg/ml<", text)


if __name__ == "__main__":
    unittest.main()
