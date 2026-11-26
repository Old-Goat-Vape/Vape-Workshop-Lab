import unittest

from bs4 import BeautifulSoup

from db import get_db
from services import recipe_sharing_service
from tests import test_recipe_builder_create as fixtures


class RecipeOwnerControlsTests(unittest.TestCase):
    setUp = fixtures.RecipeBuilderCreateTests.setUp
    tearDown = fixtures.RecipeBuilderCreateTests.tearDown
    _login = fixtures.RecipeBuilderCreateTests._login

    def create_public_help_recipe(self):
        self._login(1, "member1", "member")
        response = self.client.post("/recipes/save", data=fixtures._valid_create_form(status="help"))
        self.assertEqual(response.status_code, 302)
        with fixtures.flask_app.app_context():
            return dict(get_db().execute("SELECT * FROM recipes ORDER BY id DESC LIMIT 1").fetchone())

    def token(self, manage):
        response = self.client.get(manage)
        self.assertEqual(response.status_code, 200)
        return BeautifulSoup(response.data, "html.parser").select_one("input[name=manage_token]")["value"]

    def test_owner_can_make_public_help_recipe_private_without_flavours_or_new_version(self):
        recipe = self.create_public_help_recipe()
        rid, version = recipe["id"], recipe["current_version_id"]
        manage = f"/recipes/{rid}/manage"
        detail = f"/recipes/{recipe['slug']}"
        with fixtures.flask_app.app_context():
            db = get_db()
            db.execute("DELETE FROM recipe_items WHERE version_id = ?", (version,))
            db.commit()
            recipe_sharing_service.change(rid, 1, 3, "view")

        self.assertIn(manage.encode(), self.client.get(detail).data)
        self.assertIn(manage.encode(), self.client.get("/recipes?mine=1").data)
        self.assertEqual(self.client.post(manage, data={"action": "private"}).status_code, 403)
        response = self.client.post(manage, data={"action": "private", "manage_token": self.token(manage)}, follow_redirects=True)
        self.assertIn(b"Recipe is now private.", response.data)

        with fixtures.flask_app.app_context():
            db = get_db()
            saved = db.execute("SELECT visibility, status, current_version_id FROM recipes WHERE id = ?", (rid,)).fetchone()
            self.assertEqual(tuple(saved), ("private", "WIP", version))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recipe_versions WHERE recipe_id = ?", (rid,)).fetchone()[0], 1)
            self.assertTrue(recipe_sharing_service.access(rid, 3))

        self._login(2, "member2", "member")
        self.assertEqual(self.client.get(detail).status_code, 302)
        self.assertNotIn(recipe["name"].encode(), self.client.get("/recipes").data)
        self.assertNotIn(recipe["name"].encode(), self.client.get("/community").data)

    def test_private_choice_in_editor_closes_help_request(self):
        recipe = self.create_public_help_recipe()
        self.assertEqual(recipe["visibility"], "public")
        response = self.client.post("/recipes/save", data=fixtures._valid_create_form(
            recipe_id=str(recipe["id"]), status="help", visibility="private",
        ))
        self.assertEqual(response.status_code, 302)
        with fixtures.flask_app.app_context():
            row = get_db().execute("SELECT visibility, status FROM recipes WHERE id = ?", (recipe["id"],)).fetchone()
            self.assertEqual(tuple(row), ("private", "WIP"))

    def test_delete_requires_owner_and_removes_access_but_keeps_sessions(self):
        recipe = self.create_public_help_recipe()
        rid, version = recipe["id"], recipe["current_version_id"]
        manage = f"/recipes/{rid}/manage"
        detail = f"/recipes/{recipe['slug']}"
        with fixtures.flask_app.app_context():
            db = get_db()
            recipe_sharing_service.change(rid, 1, 2, "edit")
            db.execute("INSERT INTO mix_sessions (id, recipe_version_id, user_id, mode) VALUES (42, ?, 1, 'quick')", (version,))
            db.commit()

        token = self.token(manage + "?action=delete")
        for uid, name, role in ((2, "member2", "member"), (3, "Old Goat", "Old Goat")):
            self._login(uid, name, role)
            self.assertEqual(self.client.get(manage).status_code, 404)
            self.assertEqual(self.client.post(manage, data={"action": "delete", "manage_token": token}).status_code, 404)

        self._login(1, "member1", "member")
        token = self.token(manage + "?action=delete")
        response = self.client.post(manage, data={"action": "delete", "manage_token": token}, follow_redirects=True)
        self.assertIn(b"Recipe removed from the library", response.data)
        self.assertNotIn(recipe["name"].encode(), response.data)
        self.assertEqual(self.client.post(manage, data={"action": "delete", "manage_token": token}).status_code, 404)
        self.assertEqual(self.client.get(f"/recipes/{rid}/sharing").status_code, 404)
        history = self.client.get("/my-mixes").data
        self.assertIn(recipe["name"].encode(), history)
        self.assertNotIn(b"Mix again", history)

        for uid, name, role in ((1, "member1", "member"), (2, "member2", "member"), (3, "Old Goat", "Old Goat")):
            self._login(uid, name, role)
            self.assertEqual(self.client.get(detail).status_code, 302)
            self.assertEqual(self.client.get(f"/api/recipes/{rid}/feedback").status_code, 404)

        with fixtures.flask_app.app_context():
            db = get_db()
            state = db.execute("SELECT visibility, active, moderation_status FROM recipes WHERE id = ?", (rid,)).fetchone()
            self.assertEqual(tuple(state), ("private", 0, "archived"))
            self.assertEqual(db.execute("SELECT recipe_version_id FROM mix_sessions WHERE id = 42").fetchone()[0], version)
