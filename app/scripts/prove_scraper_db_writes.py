"""Prove BLCK + Clyrolinx write prices into the live app DB (limited pages)."""

from __future__ import annotations

import os
import sqlite3
import sys
import time

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

from database_path import resolve_database_path
from scrapers.blck_scraper import scrape_vc_flavors
from scrapers.clyrolinx_scraper import run_clyrolinx_scraper


def counts(db):
    return {
        "priced": db.execute(
            "SELECT COUNT(1) FROM flavor_reference WHERE COALESCE(price_per_ml,0)>0"
        ).fetchone()[0],
        "blck_priced": db.execute(
            "SELECT COUNT(1) FROM flavor_reference WHERE source='BLCK' AND COALESCE(price_per_ml,0)>0"
        ).fetchone()[0],
        "cly_priced": db.execute(
            "SELECT COUNT(1) FROM flavor_reference WHERE source='clyrolinx_scraper' AND COALESCE(price_per_ml,0)>0"
        ).fetchone()[0],
        "cly_last": db.execute(
            "SELECT MAX(last_scraped) FROM flavor_reference WHERE source='clyrolinx_scraper'"
        ).fetchone()[0],
        "blck_last": db.execute(
            "SELECT MAX(last_scraped) FROM flavor_reference WHERE source='BLCK'"
        ).fetchone()[0],
    }


def main() -> int:
    path, _info = resolve_database_path(APP_ROOT)
    print("DB", path, flush=True)
    db = sqlite3.connect(path, timeout=120)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=120000")
    before = counts(db)
    print("before", before, flush=True)

    t0 = time.perf_counter()
    blck = scrape_vc_flavors(db, limit_pages=1)
    print("BLCK", blck, "ms", round((time.perf_counter() - t0) * 1000), flush=True)

    t0 = time.perf_counter()
    added, skipped, updated = run_clyrolinx_scraper(db, max_pages=1, delay_s=0.0)
    print(
        "CLYRO",
        {"added": len(added), "skipped": skipped, "updated": updated},
        "ms",
        round((time.perf_counter() - t0) * 1000),
        flush=True,
    )

    after = counts(db)
    print("after", after, flush=True)
    print("delta priced", after["priced"] - before["priced"], flush=True)
    rows = db.execute(
        """
        SELECT brand, name, price_raw, price_per_ml, last_scraped
        FROM flavor_reference
        WHERE source='clyrolinx_scraper'
        ORDER BY last_scraped DESC
        LIMIT 3
        """
    ).fetchall()
    print("cly samples", [dict(r) for r in rows], flush=True)
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
