import csv
import io
import unittest

from bs4 import BeautifulSoup

from db import get_db
from services import member_import_service as imports
from tests import test_recipe_builder_create as base


def vc_export(*rows, delimiter=";"):
    output = io.StringIO(newline="")
    csv.writer(output).writerow(imports.VC_HEADER)
    writer = csv.writer(output, delimiter=delimiter)
    for source_id, name, flavours, total in rows:
        writer.writerow([
            source_id, name, "2026-01-01 10:00:00", flavours, total, "<p>Note</p>", "2 weeks",
            "", "8", "<p>First</p><p>Second</p>", "2", "", "", '["custard","fruity"]',
        ])
    return output.getvalue().encode()


class MemberImportTests(unittest.TestCase):
    setUp = base.RecipeBuilderCreateTests.setUp
    tearDown = base.RecipeBuilderCreateTests.tearDown
    _login = base.RecipeBuilderCreateTests._login

    def upload(self, data, action, token="", **quantities):
        return self.client.post("/imports", data={
            "file": (io.BytesIO(data), "export.csv"), "action": action, "preview_token": token,
            **quantities,
        })

    def token(self, response):
        self.assertEqual(response.status_code, 200)
        return BeautifulSoup(response.data, "html.parser").select_one("[name=preview_token]")["value"]

    def test_mixed_delimiter_recipe_import_is_private_and_repeatable(self):
        data = vc_export(
            (42, 'My "Custard"', "1, Vanilla, Test, 2\n2, Cream, Test, 1", 3),
            (43, "Needs review", "3, Unknown, Test, 2", 2),
        )
        self.assertEqual(self.upload(data, "preview").status_code, 302)
        self._login(1, "member1", "member")
        self.assertEqual(self.upload(data, "apply").status_code, 400)
        preview = self.upload(data, "preview")
        self.assertIn(b"Catalogue match needed for Test", preview.data)
        self.assertEqual(preview.headers["Cache-Control"], "private, no-store")
        with base.flask_app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM recipes").fetchone()[0], 0)
        result = self.upload(data, "apply", self.token(preview))
        self.assertEqual(result.status_code, 302)
        with base.flask_app.app_context():
            db = get_db()
            recipe = db.execute("SELECT * FROM recipes").fetchone()
            self.assertEqual((recipe["name"], recipe["visibility"], recipe["status"], recipe["created_by"]),
                             ('My "Custard"', "private", "WIP", 1))
            version = db.execute("SELECT * FROM recipe_versions WHERE id=?", (recipe["current_version_id"],)).fetchone()
            self.assertEqual((version["notes"], version["total_flavor_percent"], version["default_nic_mg_ml"]), ("Note", 3, 0))
            self.assertEqual([tuple(r) for r in db.execute("SELECT ingredient_id, amount_percent FROM recipe_items ORDER BY id")],
                             [(101, 2), (102, 1)])
            story = db.execute("SELECT description,steep_days,profiles_json FROM recipe_stories").fetchone()
            self.assertEqual(tuple(story), ("First\nSecond", 14, '["Custard", "Fruit"]'))
        self.assertIn(b"Already imported", self.upload(data, "preview").data)
        self._login(2, "member2", "member")
        private_detail = self.client.get(f"/recipes/{recipe['slug']}", follow_redirects=True)
        self.assertIn(b"This recipe is unavailable.", private_detail.data)
        self.assertNotIn(b"First\nSecond", private_detail.data)
        self.assertIn(b"1 ready to import", self.upload(data, "preview").data)

    def test_comma_delimited_recipes_support_comma_in_flavour_name(self):
        data = vc_export((42, "Recipe", "1, Vanilla, Dark, Test, 2", 2), delimiter=",")
        kind, rows = imports.read_export(data)
        self.assertEqual(kind, "recipes")
        self.assertEqual(rows[0]["ingredients"], [("Test", "Vanilla, Dark", 2)])
        _, rows = imports.read_export(vc_export((42, "Recipe", "1, Vanilla, Test, 2", 5)))
        self.assertIn("disagree", rows[0]["issue"])

    def test_member_stash_conflicts_tampering_and_lab_holdings(self):
        with base.flask_app.app_context():
            db = get_db()
            db.execute("INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(1,'Test','Vanilla','Flavor',101)")
            db.execute("INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(2,'Test','Cream','Flavor',102)")
            db.execute("INSERT INTO user_stash_items(user_id,flavour_id,quantity_ml,is_favorite) VALUES(1,1,9,1)")
            db.commit()
        data = b"brand,flavour,quantity_ml\nTest,Vanilla,2\nTest,Cream,4.5\nTest,Unknown,1\n"
        self._login(1, "member1", "member")
        response = self.upload(data, "preview")
        self.assertIn(b"Saved quantity kept", response.data)
        self.assertEqual(self.upload(data.replace(b"4.5", b"5"), "apply", self.token(response)).status_code, 409)
        token = self.token(self.upload(data, "preview"))
        self.assertEqual(self.upload(data, "apply", token).status_code, 302)
        with base.flask_app.app_context():
            db = get_db()
            self.assertEqual([tuple(r) for r in db.execute("SELECT flavour_id,quantity_ml,is_favorite FROM user_stash_items ORDER BY id")],
                             [(1, 9, 1), (2, 4.5, 0)])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM library_items").fetchone()[0], 0)
        self._login(3, "Old Goat", "Old Goat")
        response = self.upload(data, "preview")
        self.assertIn(b"shared Lab holdings", response.data)
        self.assertEqual(self.upload(data, "apply", self.token(response)).status_code, 302)
        with base.flask_app.app_context():
            stock = get_db().execute("SELECT reference_id,current_stock FROM library_items ORDER BY reference_id")
            self.assertEqual([tuple(row) for row in stock], [(101, 2), (102, 4.5)])

    def test_sd_stash_imports_unknown_and_measured_quantities(self):
        self._login(1, "member1", "member")
        with base.flask_app.app_context():
            db = get_db()
            db.executemany(
                "INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(?,'Test',?,'Flavor',?)",
                [(1, "Vanilla", 101), (2, "Cream", 102), (3, "Fruit", None), (4, "Chocolate", None)],
            )
            db.commit()
        data = (
            b'Manufacturer,Flavor,"On Hand (ml)",Cost/Bottle,Cost/ml,Location\n'
            b'Test,Vanilla,,12,3,Box\nTest,Cream,7.5,,,\nTest,Fruit,,,,\n'
            b'Test,Chocolate,,,,\n,Brandless,,,,\nTest,Unknown,,,,\n'
        )
        preview = self.upload(data, "preview")
        self.assertIn("1 ready to import · 3 will import with unknown quantities · 2 to skip", preview.text)
        self.assertIn("Manufacturer and flavour name needed", preview.text)
        self.assertIn("Catalogue match needed", preview.text)
        token = self.token(preview)
        invalid = self.upload(data, "apply", token, quantity_0="3.25", quantity_2="-1")
        self.assertEqual(invalid.status_code, 400)
        fields = BeautifulSoup(invalid.data, "html.parser")
        self.assertEqual(fields.select_one('[name="quantity_0"]')["value"], "3.25")
        self.assertEqual(fields.select_one('[name="quantity_2"]')["aria-invalid"], "true")
        with base.flask_app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM user_stash_items").fetchone()[0], 0)
        result = self.upload(data, "apply", token, quantity_0="3.25", quantity_1="99", quantity_2="0", quantity_4="5")
        self.assertEqual(result.status_code, 302)
        with base.flask_app.app_context():
            rows = get_db().execute("SELECT flavour_id,quantity_ml FROM user_stash_items ORDER BY flavour_id")
            self.assertEqual([tuple(row) for row in rows], [(1, 3.25), (2, 7.5), (3, 0), (4, None)])
        self.assertIn('placeholder="Unknown"', self.client.get("/stash").text)
        favorite = self.client.patch("/api/stash", json={"flavour_id": 4, "is_favorite": True})
        self.assertEqual(favorite.status_code, 200)
        with base.flask_app.app_context():
            self.assertIsNone(get_db().execute("SELECT quantity_ml FROM user_stash_items WHERE flavour_id=4").fetchone()[0])
        repeat = self.upload(data, "preview")
        self.assertIn("0 ready to import · 6 to skip", repeat.text)

    def test_sd_stash_imports_unknown_amount_into_linked_lab_without_counting_it_as_stock(self):
        from models import inventory_repo
        from services.what_can_i_make_service import fetch_stash_reference_qty_ml

        self._login(3, "Old Goat", "Old Goat")
        with base.flask_app.app_context():
            db = get_db()
            db.execute("INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(1,'Test','Vanilla','Flavor',101)")
            db.commit()
        data = b'Manufacturer,Flavor,"On Hand (ml)",Cost/Bottle,Cost/ml,Location\nTest,Vanilla,,,,\n'
        token = self.token(self.upload(data, "preview"))
        result = self.upload(data, "apply", token)
        self.assertEqual(result.status_code, 302)
        page = self.client.get("/stash").text
        self.assertIn("Imported 1 stash items. Quantity unknown for 1.", page)
        self.assertIn('placeholder="Unknown"', page)
        with base.flask_app.app_context():
            db = get_db()
            inventory_repo.mark_empty_if_below(reference_id=101)
            row = db.execute("SELECT current_stock,status FROM library_items WHERE reference_id=101").fetchone()
            self.assertEqual(tuple(row), (0, "Quantity unknown"))
            self.assertEqual(fetch_stash_reference_qty_ml(user_id=3, role="Old Goat", username="Old Goat")[0], {})
            db.commit()
        save = self.client.patch("/api/stash", json={"flavour_id": 1, "quantity_ml": 8})
        self.assertEqual(save.status_code, 200)
        self.assertNotIn('placeholder="Unknown"', self.client.get("/stash").text)
        with base.flask_app.app_context():
            row = get_db().execute("SELECT current_stock,status FROM library_items WHERE reference_id=101").fetchone()
            self.assertEqual(tuple(row), (8, "In Use"))

    def test_unmatched_stash_rows_queue_once_and_admin_match_enables_reimport(self):
        from services import flavour_request_service

        self._login(1, "member1", "member")
        data = (
            b'brand,flavour,quantity_ml\n'
            b'Test,Alternate Vanilla,3.5\nTest,Alternate Vanilla,3.5\n,No Brand,4\n'
        )
        preview = self.upload(data, "preview")
        self.assertIn(b"Confirm to send 1 unmatched flavour to admins", preview.data)
        self.assertIn(b"Send flavour requests", preview.data)
        with base.flask_app.app_context():
            self.assertEqual(flavour_request_service.pending_count(), 0)
        result = self.upload(data, "apply", self.token(preview))
        self.assertEqual(result.status_code, 302)
        repeat = self.upload(data, "preview")
        self.assertNotIn(b"Send flavour requests", repeat.data)
        with base.flask_app.app_context():
            db = get_db()
            requests = flavour_request_service.pending_requests(1, 10)
            self.assertEqual(len(requests), 1)
            self.assertEqual((requests[0]["brand"], requests[0]["name"], requests[0]["username"]),
                             ("Test", "Alternate Vanilla", "member1"))
            db.execute("INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(1,'Test','Vanilla','Flavor',101)")
            db.commit()
            flavour_request_service.review(requests[0]["id"], 3, "Old Goat", "match", 1)
        single = b'brand,flavour,quantity_ml\nTest,Alternate Vanilla,3.5\n'
        self.assertEqual(self.upload(single, "apply", self.token(self.upload(single, "preview"))).status_code, 302)
        with base.flask_app.app_context():
            row = get_db().execute("SELECT flavour_id,quantity_ml FROM user_stash_items").fetchone()
            self.assertEqual(tuple(row), (1, 3.5))
        self.assertIn(b"Saved quantity kept", self.upload(single, "preview").data)

    def test_recipe_import_deduplicates_unmatched_ingredients_across_formulas(self):
        self._login(1, "member1", "member")
        data = vc_export(
            (42, "First recipe", "1, Unknown, Test, 2", 2),
            (43, "Second recipe", "1, Unknown, Test, 3", 3),
        )
        preview = self.upload(data, "preview")
        self.assertIn(b"Confirm to send 1 unmatched flavour to admins", preview.data)
        self.assertEqual(self.upload(data, "apply", self.token(preview)).status_code, 302)
        with base.flask_app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recipes").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM flavour_requests WHERE status='pending'").fetchone()[0], 1)

    def test_stale_preview_does_not_overwrite_stash(self):
        self._login(1, "member1", "member")
        with base.flask_app.app_context():
            db = get_db()
            db.execute("INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(1,'Test','Vanilla','Flavor',101)")
            db.commit()
        data = b"brand,flavour,quantity_ml\nTest,Vanilla,2\n"
        token = self.token(self.upload(data, "preview"))
        with base.flask_app.app_context():
            db = get_db()
            db.execute("INSERT INTO user_stash_items(user_id,flavour_id,quantity_ml) VALUES(1,1,9)")
            db.commit()
        self.assertEqual(self.upload(data, "apply", token).status_code, 409)
        with base.flask_app.app_context():
            self.assertEqual(get_db().execute("SELECT quantity_ml FROM user_stash_items").fetchone()[0], 9)
