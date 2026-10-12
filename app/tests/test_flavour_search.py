from __future__ import annotations

import os
import tempfile
import unittest

from app import app as flask_app
from db import get_db
from migrations import run_startup_migrations
from models import recipes_repo


class FlavourSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "flavour_search.db")
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
            flavours = [
                (101, "INW", "Biscuit"),
                (102, "FA", "Biscuit"),
                (103, "Test", "Biscuit Base"),
                (104, "Test", "Biscotto"),
                (105, "Test", "Vanilla Biscuit"),
                (106, "Test", "Bisket"),  # fuzzy-ish; should rank after starts-with
            ]
            for fid, brand, name in flavours:
                db.execute(
                    "INSERT INTO flavor_reference (id, brand, name, category, density, active) "
                    "VALUES (?, ?, ?, 'Flavor', 1.036, 1)",
                    (fid, brand, name),
                )
            db.commit()

        self.client = flask_app.test_client()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "member1"
            sess["role"] = "member"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _names(self, query: str) -> list[str]:
        with flask_app.app_context():
            rows = recipes_repo.search_flavour_reference_for_builder(query=query, limit=100)
        return [str(r["name"]) for r in rows]

    def test_bis_returns_all_starts_with_matches(self):
        names = self._names("Bis")
        self.assertIn("Biscuit", names)
        self.assertIn("Biscuit Base", names)
        self.assertIn("Biscotto", names)
        starts = [n for n in names if n.lower().startswith("bis")]
        self.assertGreaterEqual(len(starts), 4)

    def test_bis_case_insensitive(self):
        upper = self._names("Bis")
        lower = self._names("bis")
        self.assertEqual(upper, lower)

    def test_biscuit_inw_before_vanilla_biscuit(self):
        names = self._names("Bis")
        self.assertIn("Biscuit", names)
        self.assertIn("Vanilla Biscuit", names)
        biscuit_idx = names.index("Biscuit")
        vanilla_idx = names.index("Vanilla Biscuit")
        self.assertLess(biscuit_idx, vanilla_idx)

    def test_starts_with_rank_above_contains(self):
        names = self._names("Bis")
        first_contains_only = names.index("Vanilla Biscuit")
        last_starts_with = max(i for i, n in enumerate(names) if n.lower().startswith("bis"))
        self.assertLess(last_starts_with, first_contains_only)

    def test_api_flavour_search_endpoint(self):
        res = self.client.get("/api/recipes/flavour-search?q=Bis")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertEqual(body.get("status"), "success")
        names = [item["name"] for item in body.get("items", [])]
        self.assertIn("Biscuit", names)
        self.assertGreaterEqual(len(names), 4)

    def test_api_empty_query_returns_empty_list(self):
        res = self.client.get("/api/recipes/flavour-search?q=")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json().get("items"), [])

    def test_catalogue_canonical_name_match_when_ref_name_differs(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO flavor_reference (id, brand, name, category, density, active) "
                "VALUES (201, 'INW', 'Legacy Import Name', 'Flavor', 1.036, 1)"
            )
            db.execute(
                "INSERT INTO flavours (id, canonical_name, brand, category, source_flavor_ref_id) "
                "VALUES (50, 'Biscuit', 'INW', 'Flavor', 201)"
            )
            db.commit()
            rows = recipes_repo.search_flavour_reference_for_builder(query="Biscuit", limit=100)
        ids = [int(r["id"]) for r in rows]
        self.assertIn(201, ids)

    def test_biscuit_found_when_not_on_library_page_one(self):
        with flask_app.app_context():
            db = get_db()
            for i in range(1, 31):
                ref_id = 200 + i
                db.execute(
                    "INSERT INTO flavor_reference (id, brand, name, category, density, active) "
                    "VALUES (?, 'Fill', ?, 'Flavor', 1.036, 1)",
                    (ref_id, f"AAA Filler {i:02d}"),
                )
                db.execute(
                    "INSERT INTO flavours (id, canonical_name, brand, category, source_flavor_ref_id) "
                    "VALUES (?, ?, 'Fill', 'Flavor', ?)",
                    (300 + i, f"AAA Filler {i:02d}", ref_id),
                )
            db.execute(
                "INSERT INTO flavor_reference (id, brand, name, category, density, active) "
                "VALUES (301, 'CAP', 'Biscuit', 'Flavor', 1.036, 1)"
            )
            db.execute(
                "INSERT INTO flavours (id, canonical_name, brand, category, source_flavor_ref_id) "
                "VALUES (331, 'Biscuit', 'CAP', 'Flavor', 301)"
            )
            db.commit()
            page_one, total = recipes_repo.fetch_tvw_flavours(page=1, page_size=24)
            page_one_names = [str(r.get("canonical_name") or "") for r in page_one]
            self.assertGreater(total, 24)
            self.assertNotIn("Biscuit", page_one_names)
            rows = recipes_repo.search_flavour_reference_for_builder(query="Biscuit", limit=100)
        names = [str(r["name"]) for r in rows]
        self.assertIn("Biscuit", names)

    def test_vanilla_biscuit_contains_match(self):
        names = self._names("Vanilla Biscuit")
        self.assertIn("Vanilla Biscuit", names)

    def test_api_returns_display_label_and_flavour_id(self):
        res = self.client.get("/api/recipes/flavour-search?q=Biscuit")
        self.assertEqual(res.status_code, 200)
        items = res.get_json().get("items", [])
        inw = next((x for x in items if x.get("brand") == "INW" and x.get("name") == "Biscuit"), None)
        self.assertIsNotNone(inw)
        self.assertEqual(int(inw["id"]), 101)
        self.assertIn("display_label", inw)
        self.assertIn("#101", str(inw["display_label"]))


if __name__ == "__main__":
    unittest.main()
