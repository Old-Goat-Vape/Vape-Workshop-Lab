"""Verify scraper reporting integrity + sample commercial price freshness."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime

import requests

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

os.environ.setdefault("TVW_ENABLE_LAB_MODULES", "1")
os.environ.setdefault("FLASK_USE_RELOADER", "0")
os.environ.setdefault("OGL_ENABLE_MONTHLY_SCHEDULER", "0")
os.environ.setdefault("RUN_STARTUP_MONTHLY_CATCHUP", "0")


def _process_report() -> dict:
    try:
        import psutil  # type: ignore

        apps = []
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or [])
                if "app.py" in cmd and "python" in (p.info.get("name") or "").lower():
                    apps.append({"pid": p.info["pid"], "cmd": cmd[:160]})
            except Exception:
                pass
        return {"app_py_count": len(apps), "apps": apps}
    except Exception:
        # Fallback: count via tasklist-ish is environment specific; report unknown.
        return {"app_py_count": None, "apps": [], "note": "psutil unavailable"}


def _counts(db, source_sql: str) -> dict:
    return {
        "products": db.execute(f"SELECT COUNT(1) FROM flavor_reference WHERE {source_sql}").fetchone()[0],
        "priced": db.execute(
            f"SELECT COUNT(1) FROM flavor_reference WHERE {source_sql} AND COALESCE(price_per_ml,0)>0"
        ).fetchone()[0],
        "newest": db.execute(f"SELECT MAX(last_scraped) FROM flavor_reference WHERE {source_sql}").fetchone()[0],
        "oldest_priced": db.execute(
            f"""
            SELECT MIN(last_scraped) FROM flavor_reference
            WHERE {source_sql} AND COALESCE(active,1)=1 AND COALESCE(price_per_ml,0)>0
              AND COALESCE(last_scraped,'')<>''
            """
        ).fetchone()[0],
    }


def main() -> int:
    from database_path import resolve_database_path
    from scrapers.blck_scraper import scrape_vc_flavors
    from scrapers.clyrolinx_scraper import run_clyrolinx_scraper, parse_flavor_data
    from services import master_admin_console_service as svc

    path, _ = resolve_database_path(APP_ROOT)
    print("DB", path, flush=True)
    print("PROCESSES", json.dumps(_process_report(), indent=2), flush=True)

    db = sqlite3.connect(path, timeout=120)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=120000")
    svc.get_db = lambda: db  # type: ignore

    stale = svc.abandon_stale_scraper_runs()
    print("STALE_CLEANUP", json.dumps(stale, indent=2, default=str), flush=True)

    # --- BLCK limited full-ish verification (3 pages) ---
    before_b = _counts(db, "source='BLCK'")
    run_id_b = svc._start_scraper_run(scraper_name="blck", run_type="manual_verify")
    t0 = time.perf_counter()
    try:
        res_b = scrape_vc_flavors(db, limit_pages=3)
        result_b = {"status": "success", "scraper": "blck", "run_id": run_id_b, **res_b}
        svc._finish_scraper_run(run_id=run_id_b, status="success", summary=result_b)
    except Exception as exc:
        result_b = {"status": "error", "scraper": "blck", "run_id": run_id_b, "message": str(exc)}
        svc._finish_scraper_run(run_id=run_id_b, status="error", summary=result_b)
    ms_b = round((time.perf_counter() - t0) * 1000)
    after_b = _counts(db, "source='BLCK'")
    print("BLCK_RUN", json.dumps({"result": result_b, "ms": ms_b, "before": before_b, "after": after_b}, indent=2, default=str), flush=True)

    # Sample 5 BLCK vs live JSON
    live_blck = requests.get("https://blck.co.za/products.json?limit=5&page=1", timeout=20).json().get("products") or []
    samples_b = []
    for p in live_blck[:5]:
        title = p.get("title") or ""
        vendor = p.get("vendor") or ""
        handle = p.get("handle") or ""
        price = float((p.get("variants") or [{}])[0].get("price") or 0)
        clean = title.replace(vendor, "").strip()
        row = db.execute(
            "SELECT brand, name, price_per_ml, last_scraped, url FROM flavor_reference WHERE source='BLCK' AND name=? LIMIT 1",
            (clean,),
        ).fetchone()
        samples_b.append(
            {
                "live_title": title,
                "live_price": price,
                "stored": dict(row) if row else None,
                "match_name": clean,
            }
        )
    print("BLCK_SAMPLES", json.dumps(samples_b, indent=2, default=str), flush=True)

    # --- Clyrolinx ---
    before_c = _counts(db, "source='clyrolinx_scraper'")
    run_id_c = svc._start_scraper_run(scraper_name="clyrolinx", run_type="manual_verify")
    t0 = time.perf_counter()
    try:
        added, skipped, updated = run_clyrolinx_scraper(db, max_pages=2, delay_s=0.0)
        result_c = {
            "status": "success",
            "scraper": "clyrolinx",
            "run_id": run_id_c,
            "added": len(added),
            "skipped": skipped,
            "updated": updated,
        }
        svc._finish_scraper_run(run_id=run_id_c, status="success", summary=result_c)
    except Exception as exc:
        result_c = {"status": "error", "scraper": "clyrolinx", "run_id": run_id_c, "message": str(exc)}
        svc._finish_scraper_run(run_id=run_id_c, status="error", summary=result_c)
    ms_c = round((time.perf_counter() - t0) * 1000)
    after_c = _counts(db, "source='clyrolinx_scraper'")
    print("CLYRO_RUN", json.dumps({"result": result_c, "ms": ms_c, "before": before_c, "after": after_c}, indent=2, default=str), flush=True)

    live_c = requests.get("https://clyrolinx.co.za/wp-json/wc/store/products?per_page=20&page=1", timeout=30).json()
    samples_c = []
    for p in live_c if isinstance(live_c, list) else []:
        flav = parse_flavor_data(p)
        if not flav:
            continue
        row = db.execute(
            "SELECT brand, name, price_raw, price_per_ml, last_scraped, product_url FROM flavor_reference WHERE source='clyrolinx_scraper' AND name=? LIMIT 1",
            (flav.name,),
        ).fetchone()
        samples_c.append(
            {
                "live_name": flav.name,
                "live_price_raw": flav.price_raw,
                "live_unit_ml": flav.unit_size_ml,
                "stored": dict(row) if row else None,
            }
        )
        if len(samples_c) >= 5:
            break
    print("CLYRO_SAMPLES", json.dumps(samples_c, indent=2, default=str), flush=True)

    health = svc.scraper_health_report()
    print("HEALTH", json.dumps({k: health["sources"].get(k) for k in ("blck", "clyrolinx", "vc_flavours")}, indent=2, default=str), flush=True)
    print("VERIFY_BLCK", json.dumps(svc.verify_latest_scraper_run("blck"), indent=2, default=str), flush=True)
    print("VERIFY_CLYRO", json.dumps(svc.verify_latest_scraper_run("clyrolinx"), indent=2, default=str), flush=True)
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
