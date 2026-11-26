import unittest
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from db import get_db
from migrations import run_startup_migrations
from services import batch_steeping_service as steep
from services import member_notification_service as notifications
from services import recipe_sharing_service as sharing
from tests import test_tvw_api as fixtures


class MemberNotificationTests(unittest.TestCase):
    setUp = fixtures.TVWApiTests.setUp
    tearDown = fixtures.TVWApiTests.tearDown

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session.clear()
            session.update(
                user_id=user_id,
                username="member1" if user_id == 1 else "Old Goat",
                role="member" if user_id == 1 else "Old Goat",
            )

    def inbox(self):
        response = self.client.get("/notifications")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        return BeautifulSoup(response.data, "html.parser")

    def open_item(self, page, kind):
        form = page.select_one(f'form:has(input[name="kind"][value="{kind}"])')
        fields = {field["name"]: field["value"] for field in form.select('input[type="hidden"]')}
        return self.client.post("/notifications", data={"action": "open", **fields})

    def test_private_recipe_share_read_revoke_and_reshare(self):
        with fixtures.flask_app.app_context():
            db = get_db()
            db.execute("UPDATE recipes SET created_by=2, visibility='private' WHERE id=1")
            db.commit()
            sharing.change(1, 2, 1, "view")

        page = self.inbox()
        self.assertIn("Old Goat shared Recipe1 with you.", page.get_text(" ", strip=True))
        self.assertEqual(page.select_one(".tvw-header-inbox")["aria-label"], "Notifications, 1 unread")
        self.assertEqual(self.client.post("/notifications", data={"action": "read_all"}).status_code, 403)
        response = self.open_item(page, "recipe_shared")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.location, "/recipes/1")
        self.assertEqual(self.client.get(response.location).status_code, 200)
        self.assertEqual(self.inbox().select_one(".tvw-header-inbox")["aria-label"], "Notifications")

        with fixtures.flask_app.app_context():
            sharing.change(1, 2, 1, "view")
            self.assertEqual(notifications.unread_count(1), 0)
            sharing.change(1, 2, 1, "revoke")
        self.assertIn("You have no notifications yet.", self.inbox().get_text())
        with fixtures.flask_app.app_context():
            sharing.change(1, 2, 1, "view")
            self.assertEqual(notifications.unread_count(1), 1)
        self.login(2)
        self.assertIn("You have no notifications yet.", self.inbox().get_text())

    def test_opted_in_batch_lifecycle_and_updated_plan(self):
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with fixtures.flask_app.app_context():
            db = get_db()
            db.execute("INSERT INTO mix_sessions(id,user_id,recipe_version_id,mode) VALUES(40,1,1,'quick')")
            db.commit()
            steep.save(40, 1, 14, True)
            self.assertEqual(notifications.unread_count(1), 0)
            steep.confirm_quick(40, 1, start)
            self.assertEqual(notifications.unread_count(1), 1)
            self.assertEqual(notifications.unread_count(2), 0)

        page = self.inbox()
        self.assertIn("Recipe1 reached its planned steep time.", page.get_text(" ", strip=True))
        self.assertEqual(self.open_item(page, "steeping_ready").location, "/my-mixes/40/steeping")
        self.assertEqual(self.inbox().select_one(".tvw-header-inbox")["aria-label"], "Notifications")

        with fixtures.flask_app.app_context():
            steep.save(40, 1, 14, True)
            self.assertEqual(notifications.unread_count(1), 0)
            steep.save(40, 1, 15, True)
            self.assertEqual(notifications.unread_count(1), 1)
            steep.acknowledge(40, 1)
            self.assertEqual(notifications.unread_count(1), 0)
            steep.save(40, 1, 15, False)
        self.assertIn("You have no notifications yet.", self.inbox().get_text())

        with fixtures.flask_app.app_context():
            steep.save(40, 1, 15, True)
            self.assertEqual(notifications.unread_count(1), 1)
            db = get_db()
            db.execute("UPDATE mix_sessions SET abandoned_at=CURRENT_TIMESTAMP WHERE id=40")
            db.commit()
            self.assertEqual(notifications.unread_count(1), 0)

    def test_mark_all_is_private_and_legacy_batch_schema_is_upgraded(self):
        with fixtures.flask_app.app_context():
            db = get_db()
            db.execute("ALTER TABLE batch_steeping DROP COLUMN reminder_revision")
            run_startup_migrations()
            run_startup_migrations()
            self.assertIn("reminder_revision", [row["name"] for row in db.execute("PRAGMA table_info(batch_steeping)")])
            db.executemany(
                "INSERT INTO mix_sessions(id,user_id,recipe_version_id,mode) VALUES(?,?,1,'quick')",
                [(41, 1), (42, 2)],
            )
            db.executemany(
                "INSERT INTO batch_steeping(session_id,steep_days,mixed_at,reminder_enabled) VALUES(?,1,'2020-01-01',1)",
                [(41,), (42,)],
            )
            db.commit()

        owner_page = self.inbox()
        other_key = owner_page.select_one('[name="event_key"]')["value"]
        self.login(2)
        page = self.inbox()
        token = page.select_one('[name="notification_token"]')["value"]
        response = self.client.post("/notifications", data={
            "action": "open", "kind": "steeping_ready", "event_key": other_key, "notification_token": token,
        })
        self.assertEqual(response.location, "/notifications")
        response = self.client.post("/notifications", data={"action": "read_all", "notification_token": token})
        self.assertEqual(response.status_code, 303)
        self.assertIsNone(self.inbox().select_one('button[value="read_all"]'))
        with fixtures.flask_app.app_context():
            self.assertEqual(notifications.unread_count(1), 1)
            self.assertEqual(notifications.unread_count(2), 0)
