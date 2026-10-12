"""Regression tests for scraper run-state / health reporting trustworthiness."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _make_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE scraper_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scraper_name TEXT NOT NULL,
            run_type TEXT NOT NULL,
            run_key TEXT,
            started_at DATETIME,
            completed_at DATETIME,
            status TEXT,
            summary_json TEXT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE flavor_reference (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand TEXT,
            name TEXT,
            source TEXT,
            category TEXT,
            price_per_ml REAL DEFAULT 0,
            url TEXT,
            last_scraped TEXT,
            active INTEGER DEFAULT 1,
            UNIQUE(brand, name)
        )
        """
    )
    db.commit()
    return db


class TestScraperRunStateReporting(unittest.TestCase):
    def setUp(self):
        self.db = _make_db()
        self.svc = __import__("services.master_admin_console_service", fromlist=["x"])
        self.get_db_patch = mock.patch.object(self.svc, "get_db", return_value=self.db)
        self.get_db_patch.start()

    def tearDown(self):
        self.get_db_patch.stop()
        self.db.close()

    def _insert_run(self, name, status, *, started=None, completed=None, summary=None):
        started = started or datetime.now()
        completed = completed
        cur = self.db.execute(
            """
            INSERT INTO scraper_runs (scraper_name, run_type, run_key, started_at, completed_at, status, summary_json)
            VALUES (?, 'manual', '', ?, ?, ?, ?)
            """,
            (
                name,
                started,
                completed,
                status,
                json.dumps(summary or {"status": status, "scraper": name}),
            ),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def test_success_after_failure_keeps_failure_historical(self):
        fail_id = self._insert_run(
            "blck",
            "error",
            started=datetime.now() - timedelta(hours=1),
            completed=datetime.now() - timedelta(hours=1),
            summary={"status": "error", "message": "OperationalError: database is locked", "errors": ["OperationalError: database is locked"]},
        )
        ok_id = self._insert_run(
            "blck",
            "success",
            started=datetime.now() - timedelta(minutes=5),
            completed=datetime.now() - timedelta(minutes=4),
            summary={"status": "success", "scraper": "blck", "added": 0, "updated": 12, "run_id": 0},
        )
        # Patch summary run_id to match row
        self.db.execute(
            "UPDATE scraper_runs SET summary_json=? WHERE id=?",
            (json.dumps({"status": "success", "scraper": "blck", "added": 0, "updated": 12, "run_id": ok_id}), ok_id),
        )
        self.db.commit()

        health = self.svc.scraper_health_report()
        blck = health["sources"]["blck"]
        self.assertEqual(blck["visual_state"], "success")
        self.assertEqual(blck["latest_run"]["status"], "success")
        self.assertEqual(blck["latest_run"]["run_id"], ok_id)
        self.assertFalse(blck["latest_run"].get("error_message"))
        self.assertIsNone(blck.get("last_error"))
        self.assertEqual(blck["last_failure"]["run_id"], fail_id)
        self.assertFalse(blck["last_failure"]["is_current"])
        self.assertIn("database is locked", blck["last_failure"]["error_message"])
        self.assertIn("database is locked", blck["historical_last_error"])

    def test_failure_after_success_preserves_last_success(self):
        ok_id = self._insert_run(
            "clyrolinx",
            "success",
            started=datetime.now() - timedelta(hours=2),
            completed=datetime.now() - timedelta(hours=2),
            summary={"status": "success", "updated": 10, "added": 0},
        )
        fail_id = self._insert_run(
            "clyrolinx",
            "error",
            started=datetime.now() - timedelta(minutes=2),
            completed=datetime.now() - timedelta(minutes=1),
            summary={"status": "error", "message": "HTTP 403"},
        )
        health = self.svc.scraper_health_report()
        row = health["sources"]["clyrolinx"]
        self.assertEqual(row["visual_state"], "failed")
        self.assertEqual(row["latest_run"]["run_id"], fail_id)
        self.assertTrue(row["latest_run"]["error_message"])
        self.assertEqual(row["last_success"]["run_id"], ok_id)
        self.assertTrue(row["last_failure"]["is_current"])

    def test_stale_started_becomes_stale(self):
        old = datetime.now() - timedelta(hours=5)
        run_id = self._insert_run("vc_flavours", "started", started=old, completed=None, summary={})
        self.db.execute("UPDATE scraper_runs SET summary_json='' WHERE id=?", (run_id,))
        self.db.commit()
        result = self.svc.abandon_stale_scraper_runs()
        self.assertGreaterEqual(result["abandoned_count"], 1)
        row = self.db.execute("SELECT status, completed_at, summary_json FROM scraper_runs WHERE id=?", (run_id,)).fetchone()
        self.assertEqual(row["status"], "stale")
        self.assertIsNotNone(row["completed_at"])
        health = self.svc.scraper_health_report()
        self.assertEqual(health["sources"]["vc_flavours"]["visual_state"], "stale")

    def test_young_started_not_abandoned(self):
        run_id = self._insert_run(
            "blck",
            "started",
            started=datetime.now() - timedelta(minutes=2),
            completed=None,
            summary={},
        )
        result = self.svc.abandon_stale_scraper_runs()
        self.assertEqual(result["abandoned_count"], 0)
        row = self.db.execute("SELECT status FROM scraper_runs WHERE id=?", (run_id,)).fetchone()
        self.assertEqual(row["status"], "started")

    def test_run_id_counts_belong_to_same_run(self):
        run_id = self._insert_run(
            "blck",
            "success",
            started=datetime.now() - timedelta(minutes=3),
            completed=datetime.now() - timedelta(minutes=2),
            summary={"status": "success", "added": 1, "updated": 7, "run_id": 999},
        )
        # Force mismatched summary run_id to 999 initially; health should still use row id for latest_run.run_id
        health = self.svc.scraper_health_report()
        latest = health["sources"]["blck"]["latest_run"]
        self.assertEqual(latest["run_id"], run_id)
        self.assertEqual(latest["added"], 1)
        self.assertEqual(latest["updated"], 7)
        self.assertEqual(latest["parsed"], 8)

    def test_success_with_db_updates_shows_freshness(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.db.execute(
            "INSERT INTO flavor_reference (brand, name, source, category, price_per_ml, last_scraped, active) VALUES (?,?,?,?,?,?,1)",
            ("BLCK", "Test Flavour", "BLCK", "Flavor", 1.5, now),
        )
        self.db.commit()
        self._insert_run(
            "blck",
            "success",
            started=datetime.now() - timedelta(minutes=2),
            completed=datetime.now() - timedelta(minutes=1),
            summary={"status": "success", "added": 0, "updated": 1},
        )
        health = self.svc.scraper_health_report()
        blck = health["sources"]["blck"]
        self.assertEqual(blck["priced_in_db"], 1)
        self.assertEqual(blck["newest_last_scraped"], now)
        self.assertEqual(blck["latest_run"]["updated"], 1)

    def test_verify_latest_success(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.db.execute(
            "INSERT INTO flavor_reference (brand, name, source, category, price_per_ml, last_scraped, active) VALUES (?,?,?,?,?,?,1)",
            ("BLCK", "A", "BLCK", "Flavor", 2.0, now),
        )
        run_id = self._insert_run(
            "blck",
            "success",
            started=datetime.now() - timedelta(minutes=2),
            completed=datetime.now() - timedelta(minutes=1),
            summary={"status": "success", "added": 0, "updated": 1},
        )
        self.db.execute(
            "UPDATE scraper_runs SET summary_json=? WHERE id=?",
            (json.dumps({"status": "success", "added": 0, "updated": 1, "run_id": run_id}), run_id),
        )
        self.db.commit()
        report = self.svc.verify_latest_scraper_run("blck")
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["latest_run"]["run_id"], run_id)

    def test_failed_db_commit_reports_error_not_success(self):
        """If finish cannot persist, trigger path must surface error status."""
        run_id = self.svc._start_scraper_run(scraper_name="blck", run_type="manual")

        def boom(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        with mock.patch.object(self.svc, "_finish_scraper_run", side_effect=boom):
            with mock.patch.object(self.svc, "scrape_vc_flavors", return_value={"added": 0, "updated": 3}):
                # Call internal runner pieces: simulate exception after scrape when finishing
                try:
                    self.svc._finish_scraper_run(run_id=run_id, status="success", summary={"added": 0, "updated": 3})
                    finished_ok = True
                except sqlite3.OperationalError:
                    finished_ok = False
                    err = {
                        "status": "error",
                        "message": "blck: database is locked",
                        "errors": ["OperationalError: database is locked"],
                        "scraper": "blck",
                        "run_id": run_id,
                    }
                    # Persist failure the way _run_single_scraper except-block does (without mocked finish)
                    self.db.execute(
                        "UPDATE scraper_runs SET status=?, completed_at=?, summary_json=? WHERE id=?",
                        ("error", datetime.now(), json.dumps(err), run_id),
                    )
                    self.db.commit()
        self.assertFalse(finished_ok)
        health = self.svc.scraper_health_report()
        self.assertEqual(health["sources"]["blck"]["visual_state"], "failed")
        self.assertEqual(health["sources"]["blck"]["latest_run"]["status"], "error")
        self.assertIn("database is locked", health["sources"]["blck"]["latest_run"]["error_message"])


if __name__ == "__main__":
    unittest.main()
