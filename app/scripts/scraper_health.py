"""CLI scraper health dump (no secrets)."""

from __future__ import annotations

import json
import os
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

os.environ.setdefault("TVW_ENABLE_LAB_MODULES", "1")
os.environ.setdefault("FLASK_USE_RELOADER", "0")
os.environ.setdefault("OGL_ENABLE_MONTHLY_SCHEDULER", "0")
os.environ.setdefault("RUN_STARTUP_MONTHLY_CATCHUP", "0")


def main() -> int:
    # Prefer direct DB health without taking the Flask instance lock when possible.
    import sqlite3
    from database_path import resolve_database_path

    # Import service helpers only if app can load; otherwise print raw run table.
    try:
        # Avoid instance lock contention: query scraper_runs + flavor_reference directly.
        path, _info = resolve_database_path(APP_ROOT)
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        print(json.dumps({"database": path}, indent=2))
        from services import master_admin_console_service as svc

        # Monkey-patch get_db for offline report
        svc.get_db = lambda: db  # type: ignore
        report = svc.scraper_health_report()
        print(json.dumps(report, indent=2, default=str))
        return 0
    except Exception as exc:
        print(f"health_failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
