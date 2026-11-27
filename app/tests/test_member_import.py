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

    def upload(self, data, action, token=""):
        return self.client.post("/imports", data={
            "file": (io.BytesIO(data), "export.csv"), "action": action, "preview_token": token,
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
