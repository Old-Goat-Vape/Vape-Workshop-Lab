"""Guardrails for the Guided Mix bench rules working reference document."""

from __future__ import annotations

import os
import unittest

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOC_PATH = os.path.join(_APP_ROOT, "docs", "GUIDED_MIX_BENCH_RULES.md")

_REQUIRED_HEADINGS = (
    "Nicotine rules",
    "PG rules",
    "VG rules",
    "Batch size and PG/VG ratio",
    "Physical bench model",
)


class GuidedMixBenchRulesDocTests(unittest.TestCase):
    def test_guided_mix_bench_rules_doc_exists(self):
        self.assertTrue(os.path.isfile(_DOC_PATH), _DOC_PATH)

    def test_guided_mix_bench_rules_doc_has_required_headings(self):
        with open(_DOC_PATH, encoding="utf-8") as fh:
            text = fh.read()
        for heading in _REQUIRED_HEADINGS:
            with self.subTest(heading=heading):
                self.assertIn(heading, text)

    def test_guided_mix_bench_rules_doc_marked_as_working_reference(self):
        with open(_DOC_PATH, encoding="utf-8") as fh:
            text = fh.read().lower()
        self.assertIn("working design", text)
        self.assertIn("not final product documentation", text)


if __name__ == "__main__":
    unittest.main()
