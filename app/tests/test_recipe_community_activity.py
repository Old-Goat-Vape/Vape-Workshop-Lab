import unittest

from db import get_db
from tests import test_tvw_api as base


class RecipeCommunityActivityTests(unittest.TestCase):
    setUp = base.TVWApiTests.setUp
    tearDown = base.TVWApiTests.tearDown

    def test_likes_are_idempotent_and_separate_from_favourites(self):
        for expected in (1, 1, 0, 0):
            response = self.client.post("/api/recipes/1/like", json={"liked": bool(expected)})
            self.assertEqual(response.status_code, 200)
            result = response.get_json()
            self.assertEqual(result["like_count"], expected)
            self.assertEqual(result["is_liked"], bool(expected))
            self.assertFalse(result["is_favorited"])

        self.assertEqual(self.client.post("/api/recipes/1/like", json={"liked": "false"}).status_code, 400)

    def test_private_recipe_has_no_community_activity(self):
        with base.flask_app.app_context():
            db = get_db()
            db.execute("UPDATE recipes SET visibility = 'private' WHERE id = 1")
            db.commit()

        self.assertEqual(self.client.post("/api/recipes/1/like", json={"liked": True}).status_code, 404)
        response = self.client.get("/api/recipes/1/engagement")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("like_count", response.get_json())
        self.assertNotIn("times_mixed", response.get_json())

    def test_times_mixed_only_counts_confirmed_personal_batches(self):
        with base.flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent) VALUES (2, 1, '2', 5)"
            )
            db.executemany(
                """
                INSERT INTO mix_sessions (id, user_id, recipe_version_id, mode, completed_at, abandoned_at)
                VALUES (?, 1, ?, ?, ?, ?)
                """,
                [
                    (1, 1, "quick", None, None),
                    (2, 1, "quick", None, None),
                    (3, 2, "guided", "2026-11-13", None),
                    (4, 1, "guided", None, None),
                    (5, 1, "guided", "2026-11-13", "2026-11-14"),
                    (6, 1, "guided", "2026-11-13", None),
                ],
            )
            db.execute("INSERT INTO batch_steeping (session_id, mixed_at) VALUES (1, '2026-11-13')")
            db.execute("INSERT INTO mix_session_steps (mix_session_id, step_no, step_type) VALUES (6, 1, 'pour')")
            db.commit()

        response = self.client.get("/api/recipes/1/engagement")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["times_mixed"], 2)

    def test_comment_requires_a_version_from_this_recipe_when_specified(self):
        with base.flask_app.app_context():
            db = get_db()
            db.execute("INSERT INTO recipes (id, name, created_by) VALUES (2, 'Other recipe', 1)")
            db.execute(
                "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent) VALUES (2, 2, '1', 5)"
            )
            db.commit()

        endpoint = "/api/recipes/1/feedback"
        for version in (2, "bad"):
            response = self.client.post(endpoint, json={"recipe_version_id": version, "body": "Test comment"})
            self.assertEqual(response.status_code, 400)
        response = self.client.post(endpoint, json={"recipe_version_id": 1, "body": "Test comment"})
        self.assertEqual(response.status_code, 201)
        comments = self.client.get(endpoint + "?recipe_version_id=1").get_json()["items"]
        self.assertEqual([comment["body"] for comment in comments], ["Test comment"])
