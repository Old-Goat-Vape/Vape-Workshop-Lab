"""Performance regression guards for guided-mix start → first bench render."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

from app import app as flask_app
from db import get_db
from migrations import run_startup_migrations
from services import guided_mix_session_service as gms


def _seed_12_flavour_recipe(db) -> None:
    db.execute(
        "INSERT INTO users (id, username, password_hash, role, status) VALUES (1, 'member1', 'x', 'member', 'Active')"
    )
    flavours = [
        ("FLV", "Apricot"),
        ("TFA", "Brown Sugar"),
        ("FLV", "Brulee"),
        ("CAP", "Vanilla Custard"),
        ("TFA", "Dragonfruit"),
        ("FA", "Cookie"),
        ("INW", "Biscuit"),
        ("CAP", "Super Sweet"),
        ("TFA", "Acetyl Pyrazine"),
        ("FLV", "Cream"),
        ("CAP", "Sugar Cookie"),
        ("TFA", "Marshmallow"),
    ]
    for i, (brand, name) in enumerate(flavours, start=101):
        db.execute(
            "INSERT INTO flavor_reference (id, brand, name, category, density, active) VALUES (?, ?, ?, 'Flavor', 1.036, 1)",
            (i, brand, name),
        )
    db.execute(
        "INSERT INTO recipes (id, name, status, visibility, moderation_status, active, created_by, slug, current_version_id) "
        "VALUES (1, 'Malva Bench', 'WIP', 'public', 'active', 1, 1, 'malva-bench', 1)"
    )
    db.execute(
        "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent, target_vg_pg_ratio) "
        "VALUES (1, 1, '1.0', 9.35, '70/30')"
    )
    pcts = [0.30, 1.00, 0.75, 1.50, 0.50, 0.80, 0.60, 0.40, 0.20, 1.00, 1.50, 0.80]
    for i, pct in enumerate(pcts):
        db.execute(
            "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (1, ?, ?)",
            (101 + i, pct),
        )
    db.commit()


class GuidedMixStartPerfTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "guided_start_perf.db")
        flask_app.config["TESTING"] = True
        flask_app.config["DATABASE"] = self.db_path
        flask_app.config["WTF_CSRF_ENABLED"] = False
        with flask_app.app_context():
            db = get_db()
            schema_path = os.path.join(os.path.dirname(__file__), "..", "schema.sql")
            with open(schema_path, "r", encoding="utf-8") as f:
                db.executescript(f.read())
            run_startup_migrations()
            _seed_12_flavour_recipe(db)
        self.client = flask_app.test_client()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "member1"
            sess["role"] = "member"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _form(self):
        return {
            "version_id": "1",
            "target_ml": "30",
            "target_nic_mg_ml": "3",
            "pg_ratio": "30",
            "vg_ratio": "70",
            "nic_base_mg_ml": "100",
            "nicotine_carrier": "PG",
        }

    def test_create_session_is_fast_and_minimal(self):
        with flask_app.app_context():
            db = get_db()
            sql_n = {"n": 0}

            def tracer(_statement: str) -> None:
                sql_n["n"] += 1

            db.set_trace_callback(tracer)
            t0 = time.perf_counter()
            sid, _vid, steps = gms.create_guided_mix_session(
                user_id=1,
                recipe_id=1,
                requested_version_id=1,
                target_ml=30,
                target_nic_mg_ml=3,
                nic_base_mg_ml=100,
                pg_ratio=30,
                vg_ratio=70,
                nicotine_carrier="PG",
            )
            create_ms = (time.perf_counter() - t0) * 1000
            db.set_trace_callback(None)
            row = db.execute("SELECT result_json FROM mix_sessions WHERE id = ?", (sid,)).fetchone()
            meta = json.loads(row["result_json"])
            result_bytes = len((row["result_json"] or "").encode("utf-8"))
            self.assertNotIn("steps", meta)
            self.assertNotIn("mix_summary", meta)
            self.assertNotIn("raw_event_log", meta)
            self.assertLess(result_bytes, 2500, f"result_json still heavy: {result_bytes} bytes")
            self.assertGreaterEqual(len(steps), 12)
            # CI-friendly ceiling; local target is far lower.
            self.assertLess(create_ms, 2000.0, f"create took {create_ms:.1f}ms")
            # Trace fires once per executemany row; assert we still only commit once.
            # Soft guard: avoid regressing to hundreds of incidental queries.
            self.assertLess(sql_n["n"], 120, f"too many SQL statements: {sql_n['n']}")

            t1 = time.perf_counter()
            bundle = gms.get_guided_session_bundle(session_id=sid, user_id=1, for_client=True)
            bundle_ms = (time.perf_counter() - t1) * 1000
            self.assertIsNotNone(bundle)
            self.assertIsNone(bundle.get("mix_summary"))
            self.assertNotIn("result_json", bundle)
            for step in bundle.get("steps") or []:
                self.assertNotIn("payload_json", step)
            self.assertLess(bundle_ms, 2000.0, f"bundle took {bundle_ms:.1f}ms")

    def test_http_post_redirect_get_under_ceiling(self):
        t0 = time.perf_counter()
        post = self.client.post(
            "/recipes/malva-bench/guided-mix/start",
            data=self._form(),
            follow_redirects=False,
        )
        post_ms = (time.perf_counter() - t0) * 1000
        self.assertEqual(post.status_code, 302)
        loc = post.headers.get("Location", "")
        self.assertIn("/guided-mix/session/", loc)
        t1 = time.perf_counter()
        get = self.client.get(loc)
        get_ms = (time.perf_counter() - t1) * 1000
        total_ms = (time.perf_counter() - t0) * 1000
        self.assertEqual(get.status_code, 200)
        self.assertNotIn(b"Start guided session", get.data)
        self.assertLess(post_ms, 2000.0, f"POST took {post_ms:.1f}ms")
        self.assertLess(get_ms, 3000.0, f"GET took {get_ms:.1f}ms")
        self.assertLess(total_ms, 4000.0, f"total transition took {total_ms:.1f}ms")

    def test_ten_consecutive_starts_without_busy_error(self):
        for i in range(10):
            post = self.client.post(
                "/recipes/malva-bench/guided-mix/start",
                data=self._form(),
                follow_redirects=False,
            )
            self.assertEqual(post.status_code, 302, f"start {i + 1} failed: {post.status_code}")
            loc = post.headers.get("Location", "")
            self.assertIn("/guided-mix/session/", loc)
            get = self.client.get(loc)
            self.assertEqual(get.status_code, 200, f"bench {i + 1} failed")
            self.assertNotIn(
                b"The database was busy while starting your mix",
                get.data,
                f"busy error on iteration {i + 1}",
            )

    def test_refresh_after_redirect_does_not_duplicate_session(self):
        post = self.client.post(
            "/recipes/malva-bench/guided-mix/start",
            data=self._form(),
            follow_redirects=False,
        )
        loc = post.headers["Location"]
        self.client.get(loc)
        self.client.get(loc)
        with flask_app.app_context():
            count = get_db().execute("SELECT COUNT(1) AS c FROM mix_sessions").fetchone()["c"]
        self.assertEqual(int(count), 1)


if __name__ == "__main__":
    unittest.main()
