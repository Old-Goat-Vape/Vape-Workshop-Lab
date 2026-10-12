"""Unit tests for guided_scale_input.js (Node required)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS_PATH = os.path.join(_APP_ROOT, "static", "guided_scale_input.js")
_TEMPLATE_PATH = os.path.join(_APP_ROOT, "templates", "tvw_guided_mix_session.html")


def _run_node(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node not installed")
    proc = subprocess.run(
        [node, "-e", script],
        capture_output=True,
        text=True,
        cwd=_APP_ROOT,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr or proc.stdout}")
    return json.loads(proc.stdout.strip())


class GuidedScaleInputJsTests(unittest.TestCase):
    def _g(self):
        return _run_node(
            f"""
            const G = require({json.dumps(_JS_PATH)});
            module.exports = G;
            console.log(JSON.stringify(G));
            """
        )

    def test_parse_dot_decimal(self):
        out = _run_node(
            f"""
            const G = require({json.dumps(_JS_PATH)});
            console.log(JSON.stringify({{
              dot: G.parseScaleGrams("0.78"),
              comma: G.parseScaleGrams("0,78"),
              spaced: G.parseScaleGrams(" 1,25 "),
            }}));
            """
        )
        self.assertAlmostEqual(out["dot"], 0.78, places=4)
        self.assertAlmostEqual(out["comma"], 0.78, places=4)
        self.assertAlmostEqual(out["spaced"], 1.25, places=4)

    def test_suspicious_reading_flagged(self):
        out = _run_node(
            f"""
            const G = require({json.dumps(_JS_PATH)});
            console.log(JSON.stringify({{
              suspicious: G.isScaleReadingSuspicious(780, 0.78, "780"),
              partial: G.isScaleReadingSuspicious(780, 0.78, "0."),
              normal: G.isScaleReadingSuspicious(0.80, 0.78, "0.80"),
              vg: G.isScaleReadingSuspicious(35, 30, "35"),
            }}));
            """
        )
        self.assertTrue(out["suspicious"])
        self.assertFalse(out["partial"])
        self.assertFalse(out["normal"])
        self.assertFalse(out["vg"])

    def test_typing_031_after_select_all(self):
        out = _run_node(
            f"""
            const G = require({json.dumps(_JS_PATH)});
            const visible = G.simulateTypingAfterSelectAll("0.00", ["0", ".", "3", "1"]);
            const comma = G.simulateTypingAfterSelectAll("0.00", ["0", ",", "3", "1"]);
            console.log(JSON.stringify({{
              visible,
              parsedDot: G.parseScaleGrams(visible),
              parsedComma: G.parseScaleGrams(comma),
              partialDot: G.isPartialScaleEntry("0."),
              partial031: G.isPartialScaleEntry("0.31"),
            }}));
            """
        )
        self.assertEqual(out["visible"], "0.31")
        self.assertAlmostEqual(out["parsedDot"], 0.31, places=4)
        self.assertAlmostEqual(out["parsedComma"], 0.31, places=4)
        self.assertTrue(out["partialDot"])
        self.assertFalse(out["partial031"])

    def test_reselect_mid_typing_regression_example(self):
        """If the field is wrongly select-all'd again after '0.', the next key replaces everything."""
        out = _run_node(
            f"""
            const G = require({json.dumps(_JS_PATH)});
            const afterPartial = G.simulateTypingAfterSelectAll("0.00", ["0", "."]);
            console.log(JSON.stringify({{
              correct: G.simulateTypingAfterSelectAll("0.00", ["0", ".", "3", "1"]),
              afterPartial,
              wrongIfReselected: G.simulateTypingAfterSelectAll(afterPartial, ["3", "1"]),
            }}));
            """
        )
        self.assertEqual(out["correct"], "0.31")
        self.assertEqual(out["afterPartial"], "0.")
        self.assertEqual(out["wrongIfReselected"], "31")

    def test_seed_value_detection(self):
        out = _run_node(
            f"""
            const G = require({json.dumps(_JS_PATH)});
            console.log(JSON.stringify({{
              zero: G.isSeedScaleValue("0.00"),
              comma: G.isSeedScaleValue("0,00"),
              poured: G.isSeedScaleValue("0.78"),
            }}));
            """
        )
        self.assertTrue(out["zero"])
        self.assertTrue(out["comma"])
        self.assertFalse(out["poured"])

    def test_template_loads_scale_input_module(self):
        with open(_TEMPLATE_PATH, encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("guided_scale_input.js", html)
        self.assertIn("attachScaleInput", html)
        self.assertIn("scaleInputUi", html)
        self.assertIn("resetScaleInputUi", html)
        self.assertIn("resolveMeasuredGramsForStep", html)
        self.assertIn('type="text"', html)
        self.assertIn("inputmode=\"decimal\"", html)
        self.assertIn("Scale reading looks too high for this step", html)


if __name__ == "__main__":
    unittest.main()
