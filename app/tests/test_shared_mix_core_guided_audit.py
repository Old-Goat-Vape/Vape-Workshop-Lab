"""Guards for shared_mix_core import path and Guided Mix pour order."""

from __future__ import annotations

import unittest
from pathlib import Path

from app import app as flask_app


class GuidedMixSharedCoreAuditTests(unittest.TestCase):
    def test_runtime_imports_workspace_shared_mix_core_not_release_mirror(self):
        """Dev tree must load project-root shared_mix_core, not release/TVW_Calculator copy."""
        app_dir = Path(__file__).resolve().parents[1]
        expected = (app_dir.parent / "shared_mix_core" / "guided_steps.py").resolve()
        if not expected.is_file():
            self.skipTest("workspace shared_mix_core not present beside app/")

        with flask_app.app_context():
            import shared_mix_core.guided_steps as guided_steps_mod

            actual = Path(guided_steps_mod.__file__).resolve()
            self.assertEqual(
                actual,
                expected,
                f"Expected workspace package at {expected}; got {actual}. "
                "Check PYTHONPATH or stale release copy on sys.path.",
            )
            self.assertNotIn(
                "release",
                str(actual).lower(),
                "Running dev tests should not import release/TVW_Calculator/shared_mix_core.",
            )

    def test_guided_pour_order_nicotine_before_flavours_when_nicotine_ml_positive(self):
        """When nicotine_ml > 0, first add_ingredient pour must be nicotine (not raw breakdown order)."""
        with flask_app.app_context():
            from core.calculator import (
                FlavorInput,
                MixInput,
                build_guided_assistant_steps,
                compute_mix,
                compute_mix_step_breakdown,
            )

            mix_input = MixInput(
                target_ml=60.0,
                target_nic_mg_ml=3.0,
                nic_base_mg_ml=100.0,
                pg_ratio=30.0,
                vg_ratio=70.0,
                nicotine_carrier="PG",
                flavors=[
                    FlavorInput(name="Vanilla", brand="Test", percent=5.0, density=1.036),
                ],
            )
            mix_result = compute_mix(mix_input)
            self.assertGreater(
                float(mix_result.nicotine_ml),
                0.0,
                "fixture must produce positive nicotine_ml",
            )

            raw = compute_mix_step_breakdown(mix_input)
            raw_cats = [str(s.get("category") or "").lower() for s in raw]
            self.assertEqual(
                raw_cats[0],
                "concentrate",
                "raw breakdown lists flavours first — guided path must re-sort",
            )

            guided = build_guided_assistant_steps(mix_input, tone_profile="tvw")
            pours = [
                s
                for s in guided
                if str(s.get("step_type") or "").lower() == "add_ingredient"
            ]
            self.assertGreaterEqual(len(pours), 1)
            first = pours[0]
            self.assertEqual(str(first.get("category") or "").lower(), "nicotine")

            flavour_idx = next(
                i
                for i, s in enumerate(pours)
                if str(s.get("category") or "").lower() == "concentrate"
            )
            nic_idx = next(
                i
                for i, s in enumerate(pours)
                if str(s.get("category") or "").lower() == "nicotine"
            )
            self.assertLess(nic_idx, flavour_idx, "nicotine pour must precede all flavour pours")
