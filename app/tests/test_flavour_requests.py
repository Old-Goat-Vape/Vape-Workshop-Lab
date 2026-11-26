import unittest

from bs4 import BeautifulSoup

from db import get_db
from models import recipes_repo
from tests import test_tvw_api as fixtures


class FlavourRequestTests(unittest.TestCase):
    setUp = fixtures.TVWApiTests.setUp
    tearDown = fixtures.TVWApiTests.tearDown
    member_path = "/flavours/request"
    queue_path = "/admin/flavours/requests"

    def login(self, user_id, username, role):
        with self.client.session_transaction() as session:
            session.clear()
            session.update(user_id=user_id, username=username, role=role)

    def token(self, path, field):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return BeautifulSoup(response.data, "html.parser").select_one(f'input[name="{field}"]')["value"]

    def submit(self, brand="Test", name="Lingonberry", **fields):
        form = {"brand": brand, "name": name, "request_token": self.token(self.member_path, "request_token")}
        form.update(fields)
        return self.client.post(self.member_path, data=form, follow_redirects=True)

    def review(self, request_id, action, **fields):
        path = f"{self.queue_path}/{request_id}"
        form = {"action": action, "review_token": self.token(path, "review_token")}
        form.update(fields)
        return self.client.post(path, data=form, follow_redirects=True)

    def test_member_requests_notify_admin_and_only_approval_changes_catalogue(self):
        self.assertIn(self.member_path.encode(), self.client.get("/flavours").data)
        self.assertIn(b'value="Lingonberry"', self.client.get(self.member_path + "?name=Lingonberry").data)
        self.assertIn(b"Flavour request sent", self.submit(product_url="https://example.com/lingonberry").data)
        self.assertIn(b"already awaiting review", self.submit(name="lingonberry").data)

        self.assertEqual(self.client.get(self.queue_path).status_code, 403)
        self.assertEqual(self.client.post(self.queue_path + "/1", data={"action": "add"}).status_code, 403)
        self.assertNotIn(b"flavourRequestNotice", self.client.get("/community").data)
        with fixtures.flask_app.app_context():
            self.assertEqual(recipes_repo.search_flavour_reference_for_builder(query="Lingonberry"), [])
            self.assertEqual(recipes_repo.fetch_tvw_flavours(search="Lingonberry")[1], 0)
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM flavour_requests").fetchone()[0], 1)

        self.login(2, "Old Goat", "Old Goat")
        self.assertIn(b"1 flavour request awaiting review", self.client.get("/community").data)
        self.assertIn(b"Lingonberry", self.client.get(self.queue_path).data)
        self.assertEqual(self.client.post(self.queue_path + "/1", data={"action": "add"}).status_code, 403)
        self.assertIn(b"No flavour requests awaiting review", self.review(1, "add").data)
        self.assertNotIn(b"flavourRequestNotice", self.client.get("/community").data)
        self.assertIn(b"Added to catalogue", self.client.get(self.queue_path + "/1").data)

        with fixtures.flask_app.app_context():
            db = get_db()
            library, total = recipes_repo.fetch_tvw_flavours(search="Lingonberry")
            self.assertEqual(total, 1)
            builder = recipes_repo.search_flavour_reference_for_builder(query="Lingonberry")
            self.assertEqual(library[0]["source_flavor_ref_id"], builder[0]["id"])
            self.assertEqual(db.execute("SELECT status, reviewed_by FROM flavour_requests").fetchone()[:], ("added", 2))
            self.assertIsNotNone(db.execute("SELECT id FROM tvw_admin_audit_logs WHERE action='flavour_request_added'").fetchone())

        self.login(1, "member1", "member")
        self.assertIn(b"Added to catalogue", self.client.get(self.member_path).data)

    def test_existing_flavour_can_be_matched_without_copying_it(self):
        self.assertIn(b"Flavour request sent", self.submit(name="Vanilla").data)
        self.login(2, "Old Goat", "Old Goat")
        self.assertIn(b"Choose an existing visible flavour", self.review(1, "match", flavour_id=999).data)
        self.assertIn(b"already in the catalogue", self.review(1, "add").data)
        self.assertIn(b"No flavour requests awaiting review", self.review(1, "match", flavour_id=1).data)
        with fixtures.flask_app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT status, flavour_id FROM flavour_requests").fetchone()[:], ("matched", 1))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM flavours").fetchone()[0], 1)

    def test_decline_and_reject_unsafe_product_links(self):
        self.assertEqual(self.client.post(self.member_path, data={"brand": "Test", "name": "x"}).status_code, 403)
        response = self.submit(product_url="javascript:alert(1)")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"valid HTTP or HTTPS product link", response.data)
        self.assertIn(b"Flavour request sent", self.submit(brand="OXVA", name="Replacement pods").data)
        self.login(2, "Old Goat", "Old Goat")
        self.assertIn(b"Only flavour concentrates", self.review(1, "add").data)
        self.assertIn(b"No flavour requests awaiting review", self.review(1, "decline").data)
        with fixtures.flask_app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT status FROM flavour_requests").fetchone()[0], "declined")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM flavours").fetchone()[0], 1)
