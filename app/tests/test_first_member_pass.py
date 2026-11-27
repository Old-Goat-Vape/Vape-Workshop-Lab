import unittest

from bs4 import BeautifulSoup

from db import get_db
from models import recipes_repo
from tests import test_tvw_api as fixtures


class FirstMemberPassTests(unittest.TestCase):
    setUp = fixtures.TVWApiTests.setUp
    tearDown = fixtures.TVWApiTests.tearDown

    def test_custard_search_and_usage_counts_agree_across_equivalent_references(self):
        with fixtures.flask_app.app_context():
            db = get_db()
            db.executemany(
                "INSERT INTO flavor_reference (id, brand, name, category) VALUES (?, ?, ?, 'Flavor')",
                [(102, "CAP", "Vanilla Custard V1"), (103, "Capella", "Vanilla Custard"), (104, "CAP", "Vanilla Custard V2")],
            )
            db.executemany(
                "INSERT INTO flavours (id, brand, canonical_name, category, source_flavor_ref_id) VALUES (?, 'CAP', ?, 'Flavor', ?)",
                [(2, "Vanilla Custard V1", 102), (3, "Vanilla Custard V2", 104)],
            )
            db.execute("UPDATE recipes SET current_version_id=1, visibility='private' WHERE id=1")
            db.executemany(
                "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (1, ?, 2)",
                [(102,), (103,)],
            )
            db.commit()
            rows, _ = recipes_repo.fetch_tvw_flavours(search="CAP Vanilla Custard", sort_by="used_in", sort_dir="desc")
            self.assertEqual([(row["id"], row["used_in_count"]) for row in rows], [(2, 1), (3, 0)])

        search = self.client.get("/api/stash/flavour-search", query_string={"q": "CAP Vanilla Custard"})
        self.assertEqual(search.status_code, 200)
        self.assertIn(2, [item["id"] for item in search.json["items"]])
        response = self.client.patch("/api/stash", json={"flavour_id": 2, "quantity_ml": 12})
        self.assertEqual(response.status_code, 200)
        detail = BeautifulSoup(self.client.get("/flavours/2").data, "html.parser")
        self.assertIn("1 recipe samples", detail.select_one(".flavour-quick-facts").get_text(" ", strip=True))
        self.assertEqual(detail.select_one("#stashSummaryQty").get_text(strip=True), "12.0 ml")

    def test_what_can_i_make_limits_rendering_and_keeps_batch_size_on_next_page(self):
        with fixtures.flask_app.app_context():
            db = get_db()
            db.execute("UPDATE recipes SET current_version_id=1 WHERE id=1")
            db.executemany(
                "INSERT INTO recipes (id, name, created_by, visibility) VALUES (?, ?, 1, 'private')",
                [(i, f"QA practice {i:02}") for i in range(2, 26)],
            )
            db.executemany(
                "INSERT INTO recipe_versions (id, recipe_id, version_number) VALUES (?, ?, '1')",
                [(i, i) for i in range(2, 26)],
            )
            db.execute("UPDATE recipes SET current_version_id=id WHERE id>=2")
            db.executemany(
                "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (?, 101, 2)",
                [(i,) for i in range(2, 26)],
            )
            db.commit()
        first = BeautifulSoup(self.client.get("/what-can-i-make?batch_ml=10").data, "html.parser")
        section = first.select_one("#missing_1")
        self.assertEqual(section.h2.select_one(".badge").get_text(strip=True), "25")
        self.assertEqual(len(section.select(".js-wcm-set-stash")), 10)
        next_page = section.find("a", string="Next")["href"]
        self.assertIn("batch_ml=10", next_page)
        second = BeautifulSoup(self.client.get(next_page).data, "html.parser").select_one("#missing_1")
        self.assertIn("Showing 11–20 of 25", second.get_text(" ", strip=True))
        self.assertNotIn("QA practice 02", second.get_text(" ", strip=True))
        self.assertIn("QA practice 12", second.get_text(" ", strip=True))
