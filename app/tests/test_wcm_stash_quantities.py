import unittest

from bs4 import BeautifulSoup

from db import get_db
from tests import test_tvw_api as fixtures


class WhatCanIMakeStashTests(unittest.TestCase):
    setUp = fixtures.TVWApiTests.setUp
    tearDown = fixtures.TVWApiTests.tearDown

    def test_stash_quantity_and_batch_size_determine_which_group_is_displayed(self):
        with fixtures.flask_app.app_context():
            db = get_db()
            db.execute("UPDATE recipes SET current_version_id = 1 WHERE id = 1")
            db.commit()

        def page(batch_ml):
            response = self.client.get("/what-can-i-make", query_string={"batch_ml": batch_ml})
            self.assertEqual(response.status_code, 200)
            return BeautifulSoup(response.data, "html.parser")

        def section_for_recipe(document):
            link = document.find("a", string="Recipe1")
            self.assertIsNotNone(link)
            return link.find_parent("section")

        missing = section_for_recipe(page(60))
        self.assertIn("Missing 1 flavour", missing.h2.get_text(" ", strip=True))
        field = missing.select_one('form[data-flavour-id="1"] input[name="quantity_ml"]')
        self.assertIsNotNone(field)
        self.assertFalse(field.get("value"))

        self.assertEqual(self.client.post("/api/stash", json={
            "flavour_id": 1, "quantity_ml": 0.01, "is_favorite": True,
        }).status_code, 201)
        self.assertEqual(self.client.patch("/api/stash", json={
            "flavour_id": 1, "quantity_ml": 2.75,
        }).status_code, 200)
        with fixtures.flask_app.app_context():
            db = get_db()
            stash = db.execute(
                "SELECT quantity_ml, is_favorite FROM user_stash_items WHERE user_id=1 AND flavour_id=1"
            ).fetchone()
            self.assertEqual(tuple(stash), (2.75, 1))

        short = section_for_recipe(page(60))
        self.assertIn("Not enough for this batch", short.h2.get_text(" ", strip=True))
        self.assertIn("3.00", short.get_text(" ", strip=True))
        self.assertIn("2.75", short.get_text(" ", strip=True))
        self.assertIn("Can make but low stock", section_for_recipe(page(50)).h2.get_text(" ", strip=True))
        self.assertIn("Can make now", section_for_recipe(page(30)).h2.get_text(" ", strip=True))
