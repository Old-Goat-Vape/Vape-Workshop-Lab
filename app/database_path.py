"""Resolve SQLite database path — local app data, away from cloud sync folders."""

from __future__ import annotations

import atexit
import json
import os
import sqlite3
import time
from pathlib import Path

DATABASE_FILENAME = "tvw_calculator.db"
MIGRATION_MARKER = "database_migration.json"
DEV_LOCK_FILENAME = "tvw_dev_instance.lock"


def _path_in_onedrive(path: str | Path) -> bool:
    normalized = os.path.normcase(os.path.abspath(str(path)))
    return "onedrive" in normalized


def default_local_data_dir() -> Path:
    override = os.environ.get("TVW_LOCAL_DATA_DIR", "").strip()
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "TheVapeWorkshopCalculator"
    return Path.home() / ".local" / "share" / "the-vape-workshop-calculator"


def legacy_database_path(app_root: str | Path) -> Path:
    return Path(app_root) / DATABASE_FILENAME


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def migrate_legacy_database(legacy_path: Path, local_path: Path) -> None:
    """Copy legacy DB to local data dir using SQLite backup API (safe with WAL)."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = local_path.with_suffix(".db.migrating")
    if temp_path.exists():
        temp_path.unlink()

    src = sqlite3.connect(str(legacy_path), timeout=30.0)
    try:
        try:
            src.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except sqlite3.OperationalError:
            # Another process may hold a read lock; backup still works with WAL.
            pass
        dst = sqlite3.connect(str(temp_path), timeout=30.0)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()

    if local_path.exists():
        backup_name = local_path.with_suffix(f".db.pre-migrate-{int(time.time())}")
        local_path.replace(backup_name)
    temp_path.replace(local_path)

    marker = local_path.parent / MIGRATION_MARKER
    marker.write_text(
        json.dumps(
            {
                "migrated_at_unix": time.time(),
                "migrated_from": str(legacy_path.resolve()),
                "migrated_to": str(local_path.resolve()),
                "method": "sqlite_backup",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def resolve_database_path(app_root: str | Path) -> tuple[str, dict]:
    """Return (absolute_db_path, info_dict)."""
    app_root = Path(app_root)
    env_path = os.environ.get("DATABASE_PATH", "").strip()
    if env_path:
        resolved = str(Path(env_path).resolve())
        return resolved, {
            "source": "env",
            "path": resolved,
            "in_onedrive": _path_in_onedrive(resolved),
        }

    data_dir = default_local_data_dir()
    local_db = data_dir / DATABASE_FILENAME
    legacy_db = legacy_database_path(app_root)
    info: dict = {
        "source": "local_data_dir",
        "path": str(local_db.resolve()),
        "local_data_dir": str(data_dir.resolve()),
        "legacy_path": str(legacy_db.resolve()) if legacy_db.is_file() else None,
        "migrated": False,
        "in_onedrive": False,
    }

    if local_db.is_file():
        return str(local_db.resolve()), info

    if legacy_db.is_file():
        migrate_legacy_database(legacy_db, local_db)
        info["migrated"] = True
        info["migration_from"] = str(legacy_db.resolve())
        return str(local_db.resolve()), info

    data_dir.mkdir(parents=True, exist_ok=True)
    return str(local_db.resolve()), info


def data_dir_for_database(db_path: str | Path) -> Path:
    return Path(db_path).resolve().parent


def register_dev_instance_lock(data_dir: str | Path) -> dict:
    """Record this process in a dev lock file; warn if another live instance exists."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / DEV_LOCK_FILENAME
    pid = os.getpid()
    ppid = os.getppid()
    conflict_pid: int | None = None

    if lock_path.is_file():
        try:
            first_token = lock_path.read_text(encoding="utf-8").strip().split()[0]
            other_pid = int(first_token)
            if other_pid != pid and _process_alive(other_pid):
                conflict_pid = other_pid
        except (ValueError, OSError, IndexError):
            pass

    lock_path.write_text(f"{pid} {ppid}\n", encoding="utf-8")

    def _cleanup() -> None:
        try:
            if lock_path.is_file():
                current = lock_path.read_text(encoding="utf-8").strip().split()
                if current and int(current[0]) == pid:
                    lock_path.unlink()
        except (OSError, ValueError):
            pass

    atexit.register(_cleanup)
    return {
        "lock_path": str(lock_path.resolve()),
        "pid": pid,
        "ppid": ppid,
        "conflict_pid": conflict_pid,
    }
