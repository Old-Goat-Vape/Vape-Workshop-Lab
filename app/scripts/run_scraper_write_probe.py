"""Run limited scraper writes and report DB before/after counts."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from datetime import datetime

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

os.environ.setdefault("TVW_ENABLE_LAB_MODULES", "1")
os.environ.setdefault("FLASK_USE_RELOADER", "0")
os.environ.setdefault("OGL_ENABLE_MONTHLY_SCHEDULER", "0")
os.environ.setdefault("RUN_STARTUP_MONTHLY_CATCHUP", "0")


def _counts(db):
    return {
        "flavor_rows": db.execute("SELECT COUNT(1) AS c FROM flavor_reference").fetchone()[0],
        "priced": db.execute(
            "SELECT COUNT(1) AS c FROM flavor_reference WHERE COALESCE(price_per_ml,0) > 0"
        ).fetchone()[0],
        "blck": db.execute(
            "SELECT COUNT(1) AS c FROM flavor_reference WHERE source='BLCK'"
        ).fetchone()[0],
        "clyrolinx": db.execute(
            "SELECT COUNT(1) AS c FROM flavor_reference WHERE source='clyrolinx_scraper'"
        ).fetchone()[0],
        "vc": db.execute(
            "SELECT COUNT(1) AS c FROM flavor_reference WHERE source='VC'"
        ).fetchone()[0],
    }


def run_isolated() -> None:
    """Fresh temp DB — proves scrapers can parse + write."""
    from scrapers.blck_scraper import scrape_vc_flavors
    from scrapers.clyrolinx_scraper import run_clyrolinx_scraper
    import sqlite3

    path = os.path.join(tempfile.mkdtemp(), "scraper_probe.db")
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
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
            usage_notes TEXT,
            product_url TEXT,
            price_raw REAL,
            unit_size_ml REAL,
            UNIQUE(brand, name)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER,
            action TEXT,
            target_table TEXT,
            target_id INTEGER,
            details TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.commit()

    print("=== Isolated BLCK (2 pages) ===")
    t0 = time.perf_counter()
    try:
        res = scrape_vc_flavors(db, limit_pages=2)
        print("result", res, "ms", round((time.perf_counter() - t0) * 1000))
        print("counts", _counts(db))
    except Exception:
        traceback.print_exc()

    print("=== Isolated Clyrolinx (2 pages, delay 0) ===")
    t0 = time.perf_counter()
    try:
        # temporarily monkeypatch delay by calling with delay_s=0
        added, skipped, updated = run_clyrolinx_scraper(db, max_pages=2, delay_s=0.0)
        print("result", {"added": len(added), "skipped": skipped, "updated": updated}, "ms", round((time.perf_counter() - t0) * 1000))
        print("counts", _counts(db))
    except Exception:
        traceback.print_exc()
    db.close()


def run_live_limited() -> None:
    """Against configured app DB — limited pages."""
    from app import app
    from db import get_db
    from scrapers.blck_scraper import scrape_vc_flavors
    from scrapers.clyrolinx_scraper import run_clyrolinx_scraper

    with app.app_context():
        db = get_db()
        print("Live DB:", app.config.get("DATABASE"))
        before = _counts(db)
        print("before", before)

        print("=== Live BLCK (1 page) ===")
        t0 = time.perf_counter()
        try:
            res = scrape_vc_flavors(db, limit_pages=1)
            print("result", res, "ms", round((time.perf_counter() - t0) * 1000))
        except Exception:
            traceback.print_exc()

        print("=== Live Clyrolinx (1 page, delay 0) ===")
        t0 = time.perf_counter()
        try:
            added, skipped, updated = run_clyrolinx_scraper(db, max_pages=1, delay_s=0.0)
            print("result", {"added": len(added), "skipped": skipped, "updated": updated}, "ms", round((time.perf_counter() - t0) * 1000))
        except Exception:
            traceback.print_exc()

        after = _counts(db)
        print("after", after)
        print("delta priced", after["priced"] - before["priced"])

        try:
            rows = db.execute(
                """
                SELECT scraper_name, status, started_at, completed_at, substr(summary_json,1,200) AS summary
                FROM scraper_runs
                ORDER BY id DESC LIMIT 8
                """
            ).fetchall()
            print("recent scraper_runs:")
            for r in rows:
                print(dict(r))
        except Exception as exc:
            print("scraper_runs query failed:", exc)


if __name__ == "__main__":
    print(datetime.now().isoformat(timespec="seconds"))
    run_isolated()
    print()
    run_live_limited()
