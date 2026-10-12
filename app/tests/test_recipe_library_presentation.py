"""Tests for recipe library presentation helpers."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from services import recipe_library_presentation as rlp


class RecipeLibraryPresentationTests(unittest.TestCase):
    def test_human_recipe_date_weeks_ago(self):
        raw = (datetime.now(timezone.utc) - timedelta(days=21)).strftime("%Y-%m-%d %H:%M:%S")
        self.assertEqual(rlp.human_recipe_date(raw), "Updated 3 weeks ago")

    def test_detect_library_scope(self):
        self.assertEqual(rlp.detect_library_scope(favorites_only=True, mine_only=False, visibility=""), "favorites")
        self.assertEqual(rlp.detect_library_scope(favorites_only=False, mine_only=True, visibility=""), "mine")
        self.assertEqual(rlp.detect_library_scope(favorites_only=False, mine_only=False, visibility="public"), "public")

    def test_sort_label_user_friendly_order(self):
        self.assertEqual(rlp.sort_label(sort_by="updated", sort_dir="desc"), "Recently updated")
        self.assertEqual(rlp.sort_label(sort_by="updated", sort_dir="asc"), "Recently updated (oldest first)")

    def test_parse_pg_vg_from_ratio(self):
        pg, vg = rlp.parse_pg_vg_from_ratio("70/30")
        self.assertEqual((pg, vg), (30, 70))

    def test_build_recipe_detail_presentation_imported(self):
        detail = rlp.build_recipe_detail_presentation(
            recipe={"name": "Malva", "status": "reference", "visibility": "private", "owner_username": "Unknown"},
            tags=[],
            active_version={"version_number": "1.0", "notes": "Imported VC DTL reference (https://example.com/r/1)", "target_vg_pg_ratio": "70/30"},
            ingredients=[{"ingredient_id": 1, "name": "A", "brand": "FLV", "amount_percent": 1.0}],
            total_flavour_pct=1.0,
            stash_panel={"batch_ml": 60, "stash_summary": {}},
            mixer_notes_preview=[],
        )
        self.assertTrue(detail["is_imported"])
        self.assertEqual(detail["imported_source_label"], "VC DTL Reference")
        self.assertEqual(detail["facts_pg_vg_label"], "30/70 PG/VG")


if __name__ == "__main__":
    unittest.main()
