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
