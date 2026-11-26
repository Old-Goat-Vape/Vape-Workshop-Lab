import unittest

from bs4 import BeautifulSoup

from db import get_db
from models import recipes_repo, users_repo
from tests import test_tvw_api as base


class MixerStatsTests(unittest.TestCase):
    setUp = base.TVWApiTests.setUp
    tearDown = base.TVWApiTests.tearDown

    def test_personal_activity_and_progression_preserve_account_role(self):
        with base.flask_app.app_context():
            db = get_db()
            db.execute("UPDATE recipes SET visibility = 'private' WHERE id = 1")
            db.execute("INSERT INTO recipes (name, active, created_by) VALUES ('Archived', 0, 1)")
            db.execute("INSERT INTO recipes (name, active, created_by) VALUES ('Imported', 1, 1)")
            db.execute(
                """
                INSERT INTO recipe_references (source, source_type, device_type, source_recipe_id, recipe_id)
                SELECT 'Test', 'recipe', 'DTL', '1', id FROM recipes WHERE name = 'Imported'
                """
            )
            db.executemany(
                """
                INSERT INTO mix_sessions (id, user_id, recipe_version_id, mode, completed_at, abandoned_at, production_job_id)
                VALUES (?, ?, 1, ?, ?, ?, ?)
                """,
                [
                    (1, 1, "quick", None, None, None),
                    (2, 1, "quick", None, None, None),
                    (3, 1, "guided", "2026-11-24", None, None),
                    (4, 1, "guided", "2026-11-24", None, None),
                    (5, 1, "guided", "2026-11-24", "2026-11-25", None),
                    (6, 1, "guided", "2026-11-24", None, 1),
                    (7, 2, "guided", "2026-11-24", None, None),
                    (8, 1, "guided", "2026-11-24", None, None),
                ],
            )
            db.execute("INSERT INTO batch_steeping (session_id, mixed_at) VALUES (1, '2026-11-24')")
            db.execute("INSERT INTO mix_session_steps (mix_session_id, step_no, step_type) VALUES (8, 1, 'pour')")
            db.commit()
            self.assertEqual(
                recipes_repo.fetch_mixer_activity_counts(1),
                {"quick_mixes": 1, "guided_mixes": 2, "recipes_created": 1},
            )

        response = self.client.get("/my-mixer-stats?user_id=2")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        page = BeautifulSoup(response.data, "html.parser")
        badges = page.select('#mixerBadges .badge')
        self.assertEqual([badge.get_text(strip=True) for badge in badges], ["Kid", "Apprentice"])
        self.assertEqual(badges[-1]["aria-current"], "true")
        self.assertIn("Next badge · Bench Regular", page.get_text(" ", strip=True))
        self.assertEqual(page.select_one("#mixerMixProgress")["value"], "3")
        self.assertEqual(page.select_one("#mixerMixProgress")["max"], "10")
        with base.flask_app.app_context():
            self.assertEqual(users_repo.fetch_user_role(1), "member")

    def test_new_mixer_gets_kid_badge_and_signed_out_requests_redirect(self):
        response = self.client.get("/my-mixer-stats")
        page = BeautifulSoup(response.data, "html.parser")
        badges = page.select('#mixerBadges .badge')
        self.assertEqual([badge.get_text(strip=True) for badge in badges], ["Kid"])
        self.assertIn("Next badge · Apprentice", page.get_text(" ", strip=True))
        self.assertEqual(page.select_one("#mixerMixProgress")["value"], "0")
        with self.client.session_transaction() as session:
            session.clear()
        response = self.client.get("/my-mixer-stats")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_mixer_badge_is_visible_without_activity_totals(self):
        with base.flask_app.app_context():
            db = get_db()
            db.execute("UPDATE recipes SET current_version_id = 1 WHERE id = 1")
            db.executemany(
                "INSERT INTO mix_sessions (user_id, recipe_version_id, mode, completed_at) VALUES (1, 1, 'guided', ?)",
                [("2026-11-24",)] * 3,
            )
            db.execute(
                "INSERT INTO recipe_feedback (recipe_id, recipe_version_id, author_user_id, feedback_type, body) "
                "VALUES (1, 1, 1, 'structured', 'Public feedback')"
            )
            db.commit()
        with self.client.session_transaction() as session:
            session.clear()
            session.update(user_id=2, username="Old Goat", role="Old Goat")

        for path in ("/profile/member1", "/recipes/1", "/recipes", "/community"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            page = BeautifulSoup(response.data, "html.parser")
            self.assertIn("Apprentice", [badge.get_text(strip=True) for badge in page.select(".tvw-mixer-badge")], path)
            self.assertIsNone(page.select_one("#mixerMixProgress"), path)

        feedback = self.client.get("/api/recipes/1/feedback").get_json()["items"][0]
        self.assertEqual(feedback["mixer_badge"], "Apprentice")
        self.assertNotIn("quick_mixes", feedback)
        self.assertNotIn("recipes_created", feedback)

    def test_recipe_awards_are_admin_recorded_and_publicly_displayed(self):
        with base.flask_app.app_context():
            db = get_db()
            db.execute("UPDATE recipes SET current_version_id = 1 WHERE id = 1")
            db.execute(
                "INSERT INTO recipes (id, name, status, visibility, active, created_by) "
                "VALUES (2, 'Second recipe', 'WIP', 'public', 1, 1)"
            )
            db.execute(
                "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent) VALUES (2, 2, '1', 5)"
            )
            db.execute("UPDATE recipes SET current_version_id = 2 WHERE id = 2")
            db.commit()

        response = self.client.get("/recipes/1")
        self.assertNotIn(b"Record a recipe award", response.data)
        self.assertEqual(self.client.post("/recipes/1/awards", data={"period": "2026"}).status_code, 403)

        with self.client.session_transaction() as session:
            session.clear()
            session.update(user_id=2, username="Old Goat", role="Old Goat")
        page = BeautifulSoup(self.client.get("/recipes/1").data, "html.parser")
        token = page.select_one("input[name=award_token]")["value"]
        self.assertEqual(self.client.post("/recipes/1/awards", data={"period": "2026"}).status_code, 403)

        def record(recipe_id, period):
            return self.client.post(
                f"/recipes/{recipe_id}/awards",
                data={"period": period, "award_token": token},
                follow_redirects=True,
            )

        self.assertIn(b"Recipe award recorded", record(1, "2026-11").data)
        self.assertIn(b"Recipe award recorded", record(1, "2026").data)
        self.assertIn(b"Recipe award recorded", record(1, "2026-11").data)
        self.assertIn(b"already has a winning recipe", record(2, "2026-11").data)
        self.assertIn(b"Enter a valid award period", record(1, "2026-13").data)

        with self.client.session_transaction() as session:
            session.clear()
            session.update(user_id=1, username="member1", role="member")
        awards = {"Recipe of the Month · November 2026", "Recipe of the Year · 2026"}
        for path in ("/recipes/1", "/recipes", "/profile/member1", "/community"):
            response = self.client.get(path)
            page = BeautifulSoup(response.data, "html.parser")
            self.assertEqual({badge.get_text(strip=True) for badge in page.select(".tvw-recipe-award")}, awards, path)

        with base.flask_app.app_context():
            db = get_db()
            db.execute("UPDATE recipes SET visibility = 'private' WHERE id = 1")
            db.commit()
        page = BeautifulSoup(self.client.get("/recipes/1").data, "html.parser")
        self.assertIsNone(page.select_one(".tvw-recipe-award"))
        with self.client.session_transaction() as session:
            session.clear()
            session.update(user_id=2, username="Old Goat", role="Old Goat")
            session["recipe_award_token"] = token
        self.assertIn(b"Only published public recipes", record(1, "2027").data)
