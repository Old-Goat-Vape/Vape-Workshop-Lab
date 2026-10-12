"""Tests for local database path resolution and legacy migration."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import database_path as dp


class DatabasePathTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "app"
        self.root.mkdir()
        self.data_dir = Path(self.temp_dir.name) / "localdata"
        self.env = mock.patch.dict(
            os.environ,
            {"TVW_LOCAL_DATA_DIR": str(self.data_dir), "DATABASE_PATH": ""},
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp_dir.cleanup()

    def _seed_legacy_db(self) -> Path:
        legacy = dp.legacy_database_path(self.root)
        conn = sqlite3.connect(str(legacy))
        conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY, label TEXT)")
        conn.execute("INSERT INTO probe (label) VALUES ('legacy')")
        conn.commit()
        conn.close()
        return legacy

    def test_uses_local_data_dir_when_no_legacy(self):
        path, info = dp.resolve_database_path(self.root)
        self.assertEqual(path, str((self.data_dir / dp.DATABASE_FILENAME).resolve()))
        self.assertEqual(info["source"], "local_data_dir")
        self.assertFalse(info.get("migrated"))

    def test_migrates_legacy_on_first_run(self):
        legacy = self._seed_legacy_db()
        path, info = dp.resolve_database_path(self.root)
        self.assertTrue(info.get("migrated"))
        self.assertEqual(info.get("migration_from"), str(legacy.resolve()))
        self.assertTrue(Path(path).is_file())
        conn = sqlite3.connect(path)
        row = conn.execute("SELECT label FROM probe").fetchone()
        conn.close()
        self.assertEqual(row[0], "legacy")
        marker = self.data_dir / dp.MIGRATION_MARKER
        self.assertTrue(marker.is_file())

    def test_env_override_wins(self):
        custom = self.data_dir / "custom.db"
        with mock.patch.dict(os.environ, {"DATABASE_PATH": str(custom)}):
            path, info = dp.resolve_database_path(self.root)
        self.assertEqual(path, str(custom.resolve()))
        self.assertEqual(info["source"], "env")

    def test_instance_lock_detects_conflict(self):
        lock_dir = self.data_dir
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / dp.DEV_LOCK_FILENAME
        lock_path.write_text("99999 1\n", encoding="utf-8")
        with mock.patch.object(dp, "_process_alive", return_value=True):
            info = dp.register_dev_instance_lock(lock_dir)
        self.assertEqual(info["conflict_pid"], 99999)


if __name__ == "__main__":
    unittest.main()
