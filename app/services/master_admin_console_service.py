from __future__ import annotations

import json
import re
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from db import get_db, sqlite_retry
from werkzeug.security import generate_password_hash
from scrapers.clyrolinx_scraper import run_clyrolinx_scraper
from scrapers.blck_scraper import scrape_vc_flavors
from scrapers.vc_flavours_scraper import run_vc_scraper
from scrapers.vc_recipes_dtl_scraper import (
    run_vc_dtl_scraper,
    backfill_all_imported_flavour_identity_matches,
    fix_recipe_flavor_links_safe,
    resync_imported_recipe_display_names,
)
from config.canonical_library_brands import canonical_library_brand
from services.flavor_canonical_repair_service import run_canonical_library_repair

ACTIVE_SCRAPERS = ("blck", "clyrolinx", "vc_flavours", "vc_dtl")

# Soft ceiling for a run left in status=started before it is treated as stale.
# Generous enough for polite Clyrolinx delays and VC enrichment; short enough to clear zombies.
SCRAPER_STALE_AFTER_MINUTES: Dict[str, int] = {
    "blck": 45,
    "clyrolinx": 90,
    "vc_flavours": 90,
    "vc_dtl": 24 * 60,
    "all": 24 * 60,
}
SCRAPER_STALE_DEFAULT_MINUTES = 120

_SOURCE_SQL = {
    "blck": "source = 'BLCK'",
    "clyrolinx": "source = 'clyrolinx_scraper'",
    "vc_flavours": "source = 'VC'",
    "vc_dtl": "1=0",
}

_SOURCE_NOTES = {
    "blck": "Commercial price upsert (Shopify products.json)",
    "clyrolinx": "Commercial price upsert (WooCommerce Store API)",
    "vc_flavours": "Enrichment only — does not write commercial prices",
    "vc_dtl": "Recipe reference scrape — not a shop price source",
}

# Master admin maintenance scrapers (Identity & Repair). Kept as sets so new aliases stay in one place.
SCRAPER_CANONICAL_LIBRARY_PREVIEW = frozenset(
    {
        "canonical_library_repair_preview",
        "canonicalize_flavor_brands_preview",
    }
)
SCRAPER_CANONICAL_LIBRARY_APPLY = frozenset(
    {
        "canonical_library_repair_apply",
        "canonicalize_flavor_brands_apply",
    }
)


def _normalize_scraper_token(scraper_name: str) -> str:
    """Lowercase + hyphen/dash variants for URL or pasted names."""
    s = (scraper_name or "").strip().lower()
    s = s.replace("-", "_")
    s = s.replace("\u2013", "_").replace("\u2014", "_")
    return s


SCHEDULED_RUN_TYPES = {"scheduled_monthly", "auto_startup_catchup"}
_scheduler_lock = threading.Lock()
_scheduler_thread: Optional[threading.Thread] = None
_scheduler_stop_event: Optional[threading.Event] = None
_scheduler_state: Dict[str, Any] = {
    "scheduler_enabled": True,
    "scheduler_process_active": False,
    "monthly_job_registered": False,
    "monthly_job_enabled": True,
    "monthly_last_run": "",
    "monthly_next_run": "",
    "monthly_last_result": "",
    "monthly_last_error": "",
    "monthly_dispatch_ok": False,
    "last_health_check_at": "",
}


def _norm_name(value: str) -> str:
    s = (value or "").strip().lower()
    s = re.sub(r"[^\w\s()%/+&-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _ensure_flavor_aliases_table() -> None:
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS flavor_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias_raw TEXT NOT NULL,
            alias_normalized TEXT NOT NULL,
            brand_canonical TEXT,
            target_flavor_id INTEGER NOT NULL,
            notes TEXT,
            confidence_override REAL,
            source TEXT,
            created_at DATETIME,
            created_by INTEGER
        )
        """
    )


def _log_admin_action(*, actor_id: int, action: str, details: str = "", target_id: int = 0) -> None:
    db = get_db()
    # audit_logs.actor_id FK -> users(id). Scraper/scheduler paths use 0; no user id 0 exists.
    aid: Optional[int] = int(actor_id) if int(actor_id) > 0 else None
    db.execute(
        """
        INSERT INTO audit_logs (actor_id, action, target_table, target_id, details, timestamp)
        VALUES (?, ?, 'admin_console', ?, ?, ?)
        """,
        (aid, action, int(target_id), details, datetime.now()),
    )


def _parse_summary(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


def _row_to_run(row) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    d = dict(row)
    summary = _parse_summary(d.get("summary_json"))
    d["summary"] = summary
    d["run_id"] = int(d["id"])
    d["added"] = int(summary.get("added") or 0)
    d["updated"] = int(summary.get("updated") or 0)
    d["skipped"] = int(summary.get("skipped") or 0)
    d["enriched"] = int(summary.get("enriched") or 0)
    fetched = summary.get("fetched")
    if fetched is None:
        fetched = d["added"] + d["updated"] + d["skipped"] + d["enriched"]
    d["parsed"] = int(fetched or 0)
    d["error_message"] = ""
    if str(d.get("status") or "").lower() in {"error", "abandoned", "stale"}:
        err = summary.get("message") or summary.get("errors") or d.get("status") or ""
        if isinstance(err, list) and err:
            err = err[0]
        d["error_message"] = str(err)[:500]
    duration_s = None
    if d.get("started_at") and d.get("completed_at"):
        try:
            started = datetime.fromisoformat(str(d["started_at"]))
            completed = datetime.fromisoformat(str(d["completed_at"]))
            duration_s = round((completed - started).total_seconds(), 2)
        except Exception:
            duration_s = None
    d["duration_s"] = duration_s
    return d


def _stale_threshold_minutes(scraper_name: str) -> int:
    return int(SCRAPER_STALE_AFTER_MINUTES.get(scraper_name, SCRAPER_STALE_DEFAULT_MINUTES))


def abandon_stale_scraper_runs(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Mark long-running status=started rows as stale (not currently running).

    Does not touch runs younger than the per-source threshold.
    """
    db = get_db()
    stamp = now or datetime.now()
    stamp_s = stamp.isoformat(sep=" ", timespec="seconds")
    abandoned: List[Dict[str, Any]] = []

    rows = db.execute(
        """
        SELECT id, scraper_name, started_at, status
        FROM scraper_runs
        WHERE status = 'started' AND completed_at IS NULL
        """
    ).fetchall()
    for row in rows:
        name = str(row["scraper_name"] or "")
        try:
            started = datetime.fromisoformat(str(row["started_at"]))
        except Exception:
            started = stamp - timedelta(days=365)
        age_min = (stamp - started).total_seconds() / 60.0
        threshold = _stale_threshold_minutes(name)
        if age_min < threshold:
            continue
        reason = (
            f"Marked stale after {int(age_min)} minutes without completion "
            f"(threshold={threshold}m for {name or 'unknown'})"
        )
        summary = {
            "status": "stale",
            "message": reason,
            "scraper": name,
            "run_id": int(row["id"]),
        }
        db.execute(
            """
            UPDATE scraper_runs
            SET status = 'stale',
                completed_at = ?,
                summary_json = ?
            WHERE id = ? AND status = 'started' AND completed_at IS NULL
            """,
            (stamp_s, json.dumps(summary), int(row["id"])),
        )
        abandoned.append({"run_id": int(row["id"]), "scraper": name, "reason": reason})
    if abandoned:
        db.commit()
    return {"abandoned_count": len(abandoned), "abandoned": abandoned}


def _start_scraper_run(*, scraper_name: str, run_type: str, run_key: str = "") -> int:
    db = get_db()
    # Clear age-stale zombies first, then supersede any remaining in-progress row for this source.
    abandon_stale_scraper_runs()
    now_s = datetime.now().isoformat(sep=" ", timespec="seconds")
    db.execute(
        """
        UPDATE scraper_runs
        SET status = 'abandoned',
            completed_at = COALESCE(completed_at, ?),
            summary_json = COALESCE(NULLIF(summary_json, ''), ?)
        WHERE scraper_name = ?
          AND status = 'started'
          AND completed_at IS NULL
        """,
        (
            now_s,
            json.dumps(
                {
                    "status": "abandoned",
                    "message": "Superseded by a newer run before completion",
                    "scraper": scraper_name,
                }
            ),
            scraper_name,
        ),
    )
    cur = db.execute(
        """
        INSERT INTO scraper_runs (scraper_name, run_type, run_key, started_at, status)
        VALUES (?, ?, ?, ?, 'started')
        """,
        (scraper_name, run_type, run_key, now_s),
    )
    db.commit()
    return int(cur.lastrowid)


def _finish_scraper_run(*, run_id: int, status: str, summary: Dict[str, Any]) -> None:
    """Persist scraper run outcome. Commits here so error paths are not left without a commit."""
    payload = dict(summary or {})
    payload.setdefault("run_id", int(run_id))
    payload.setdefault("status", status)
    completed_s = datetime.now().isoformat(sep=" ", timespec="seconds")

    def _write() -> None:
        db = get_db()
        db.execute(
            """
            UPDATE scraper_runs
            SET completed_at = ?, status = ?, summary_json = ?
            WHERE id = ?
            """,
            (completed_s, status, json.dumps(payload), int(run_id)),
        )
        db.commit()

    sqlite_retry(_write, attempts=15, base_delay_s=0.05)


def list_scraper_last_runs() -> Dict[str, Dict[str, Any]]:
    """Latest run row per active scraper (same source of truth as health.latest_run)."""
    abandon_stale_scraper_runs()
    db = get_db()
    out: Dict[str, Dict[str, Any]] = {}
    for name in [*ACTIVE_SCRAPERS, "all"]:
        row = db.execute(
            """
            SELECT id, scraper_name, run_type, run_key, started_at, completed_at, status, summary_json
            FROM scraper_runs
            WHERE scraper_name = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (name,),
        ).fetchone()
        out[name] = _row_to_run(row) or {}
    return out


def _data_freshness(db, scraper_name: str) -> Dict[str, Any]:
    source_filter = _SOURCE_SQL.get(scraper_name, "1=0")
    if source_filter == "1=0":
        return {
            "products_in_db": 0,
            "priced_in_db": 0,
            "newest_last_scraped": None,
            "oldest_active_priced_scraped": None,
        }
    product_count = int(
        db.execute(f"SELECT COUNT(1) AS c FROM flavor_reference WHERE {source_filter}").fetchone()["c"]
    )
    priced_count = int(
        db.execute(
            f"SELECT COUNT(1) AS c FROM flavor_reference WHERE {source_filter} AND COALESCE(price_per_ml,0) > 0"
        ).fetchone()["c"]
    )
    newest = db.execute(
        f"SELECT MAX(last_scraped) AS v FROM flavor_reference WHERE {source_filter}"
    ).fetchone()["v"]
    oldest_priced = db.execute(
        f"""
        SELECT MIN(last_scraped) AS v
        FROM flavor_reference
        WHERE {source_filter}
          AND COALESCE(active,1)=1
          AND COALESCE(price_per_ml,0) > 0
          AND COALESCE(last_scraped,'') <> ''
        """
    ).fetchone()["v"]
    return {
        "products_in_db": product_count,
        "priced_in_db": priced_count,
        "newest_last_scraped": newest,
        "oldest_active_priced_scraped": oldest_priced,
    }


def _visual_state(latest: Optional[Dict[str, Any]]) -> str:
    if not latest:
        return "never"
    st = str(latest.get("status") or "").lower()
    if st == "started":
        return "running"
    if st in {"stale", "abandoned"}:
        return "stale"
    if st in {"error", "failed"}:
        return "failed"
    if st in {"success", "partial"}:
        return "success"
    return "stale"


def scraper_health_report() -> Dict[str, Any]:
    """Per-source health snapshot. Latest run is authoritative; failures are historical only."""
    stale = abandon_stale_scraper_runs()
    db = get_db()
    sources = list(ACTIVE_SCRAPERS)
    report: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "lab_modules_enabled": True,
        "stale_cleanup": stale,
        "sources": {},
    }
    priced = db.execute(
        "SELECT COUNT(1) AS c FROM flavor_reference WHERE COALESCE(price_per_ml, 0) > 0"
    ).fetchone()
    report["priced_flavor_rows"] = int(priced["c"] if priced else 0)

    for name in sources:
        latest_row = db.execute(
            """
            SELECT id, scraper_name, run_type, run_key, started_at, completed_at, status, summary_json
            FROM scraper_runs
            WHERE scraper_name = ?
            ORDER BY id DESC LIMIT 1
            """,
            (name,),
        ).fetchone()
        last_ok_row = db.execute(
            """
            SELECT id, scraper_name, run_type, run_key, started_at, completed_at, status, summary_json
            FROM scraper_runs
            WHERE scraper_name = ? AND status IN ('success', 'partial')
            ORDER BY id DESC LIMIT 1
            """,
            (name,),
        ).fetchone()
        last_fail_row = db.execute(
            """
            SELECT id, scraper_name, run_type, run_key, started_at, completed_at, status, summary_json
            FROM scraper_runs
            WHERE scraper_name = ? AND status IN ('error', 'failed')
            ORDER BY id DESC LIMIT 1
            """,
            (name,),
        ).fetchone()
        if not last_fail_row:
            last_fail_row = db.execute(
                """
                SELECT id, scraper_name, run_type, run_key, started_at, completed_at, status, summary_json
                FROM scraper_runs
                WHERE scraper_name = ? AND status IN ('stale', 'abandoned')
                ORDER BY id DESC LIMIT 1
                """,
                (name,),
            ).fetchone()

        latest = _row_to_run(latest_row)
        last_ok = _row_to_run(last_ok_row)
        last_fail = _row_to_run(last_fail_row)
        freshness = _data_freshness(db, name)
        visual = _visual_state(latest)

        # Current error only if the latest run itself failed; otherwise historical only.
        current_error = ""
        historical_error = ""
        if latest and visual in {"failed", "stale"}:
            current_error = latest.get("error_message") or ""
        if last_fail and (not latest or int(last_fail["run_id"]) != int(latest.get("run_id") or 0)):
            historical_error = last_fail.get("error_message") or ""
        elif last_fail and visual in {"failed", "stale"}:
            historical_error = ""  # already shown as current

        report["sources"][name] = {
            "visual_state": visual,
            "notes": _SOURCE_NOTES.get(name, ""),
            "latest_run": {
                "run_id": (latest or {}).get("run_id"),
                "status": (latest or {}).get("status"),
                "started_at": (latest or {}).get("started_at"),
                "completed_at": (latest or {}).get("completed_at"),
                "duration_s": (latest or {}).get("duration_s"),
                "added": (latest or {}).get("added", 0),
                "updated": (latest or {}).get("updated", 0),
                "skipped": (latest or {}).get("skipped", 0),
                "parsed": (latest or {}).get("parsed", 0),
                "error_message": current_error,
            }
            if latest
            else None,
            "last_success": {
                "run_id": last_ok.get("run_id"),
                "completed_at": last_ok.get("completed_at"),
                "added": last_ok.get("added", 0),
                "updated": last_ok.get("updated", 0),
                "parsed": last_ok.get("parsed", 0),
            }
            if last_ok
            else None,
            "last_failure": {
                "run_id": last_fail.get("run_id"),
                "status": last_fail.get("status"),
                "completed_at": last_fail.get("completed_at") or last_fail.get("started_at"),
                "error_message": last_fail.get("error_message") or "",
                "is_current": bool(latest and last_fail and latest.get("run_id") == last_fail.get("run_id")),
            }
            if last_fail
            else None,
            "historical_last_error": historical_error,
            # Back-compat keys used by older templates/scripts (latest-run scoped).
            "last_attempted_at": (latest or {}).get("started_at"),
            "last_attempt_status": (latest or {}).get("status"),
            "last_successful_at": (last_ok or {}).get("completed_at"),
            "last_error": current_error or None,
            "last_run_products_parsed": (latest or {}).get("parsed", 0),
            "last_run_added": (latest or {}).get("added", 0),
            "last_run_updated": (latest or {}).get("updated", 0),
            "last_run_duration_s": (latest or {}).get("duration_s"),
            **freshness,
        }
    return report


def verify_latest_scraper_run(scraper_name: str) -> Dict[str, Any]:
    """Validate integrity of the latest recorded run without re-scraping."""
    name = _normalize_scraper_token(scraper_name)
    abandon_stale_scraper_runs()
    db = get_db()
    latest = _row_to_run(
        db.execute(
            """
            SELECT id, scraper_name, run_type, run_key, started_at, completed_at, status, summary_json
            FROM scraper_runs
            WHERE scraper_name = ?
            ORDER BY id DESC LIMIT 1
            """,
            (name,),
        ).fetchone()
    )
    freshness = _data_freshness(db, name)
    checks: List[Dict[str, Any]] = []

    def _check(ok: bool, code: str, detail: str) -> None:
        checks.append({"ok": bool(ok), "code": code, "detail": detail})

    if not latest:
        _check(False, "no_runs", f"No scraper_runs rows for {name}")
        return {
            "status": "error",
            "scraper": name,
            "checks": checks,
            "freshness": freshness,
        }

    st = str(latest.get("status") or "").lower()
    _check(st != "started", "not_stuck_started", f"latest status={st}")
    if st in {"success", "partial"}:
        _check(bool(latest.get("completed_at")), "has_completed_at", str(latest.get("completed_at")))
        summary_run_id = latest.get("summary", {}).get("run_id")
        if summary_run_id is not None:
            _check(
                int(summary_run_id) == int(latest.get("run_id") or 0),
                "run_id_consistent",
                f"row_id={latest.get('run_id')} summary.run_id={summary_run_id}",
            )
        else:
            _check(True, "run_id_consistent", "legacy summary without run_id (row id used)")
        if name in {"blck", "clyrolinx"} and int(latest.get("updated") or 0) + int(latest.get("added") or 0) > 0:
            newest = freshness.get("newest_last_scraped")
            started_day = str(latest.get("started_at") or "")[:10]
            newest_day = str(newest or "")[:10]
            _check(
                bool(newest) and newest_day >= started_day,
                "db_timestamps_from_run_window",
                f"newest_last_scraped={newest} started={latest.get('started_at')}",
            )
    elif st in {"error", "failed"}:
        _check(bool(latest.get("error_message")), "failure_has_message", latest.get("error_message") or "")
    elif st in {"stale", "abandoned"}:
        _check(True, "marked_terminal", f"latest status={st}")

    open_started = db.execute(
        """
        SELECT COUNT(1) AS c FROM scraper_runs
        WHERE scraper_name = ? AND status = 'started' AND completed_at IS NULL
        """,
        (name,),
    ).fetchone()["c"]
    _check(int(open_started) == 0 or st == "started", "no_extra_started_rows", f"open_started={open_started}")

    ok_all = all(c["ok"] for c in checks)
    return {
        "status": "success" if ok_all else "error",
        "scraper": name,
        "latest_run": {
            "run_id": latest.get("run_id"),
            "status": latest.get("status"),
            "started_at": latest.get("started_at"),
            "completed_at": latest.get("completed_at"),
            "added": latest.get("added"),
            "updated": latest.get("updated"),
            "parsed": latest.get("parsed"),
            "duration_s": latest.get("duration_s"),
            "error_message": latest.get("error_message"),
        },
        "checks": checks,
        "freshness": freshness,
    }


def list_vc_dtl_benchmark_stats() -> List[Dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT source_type, device_type, profile_normalized, recipe_count,
               avg_total_flavour_pct, median_total_flavour_pct, avg_flavour_count,
               avg_pct_per_flavour, sweetener_usage_rate, cooling_usage_rate,
               analytics_version, updated_at
        FROM recipe_reference_benchmark_stats
        WHERE source_type = 'vc_dtl' AND device_type = 'DTL'
        ORDER BY recipe_count DESC, profile_normalized ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_unresolved_imported_flavour_metrics() -> Dict[str, Any]:
    db = get_db()
    _ensure_flavor_aliases_table()
    t = db.execute(
        """
        SELECT
          COUNT(1) AS total_rows,
          SUM(CASE WHEN COALESCE(rf.matched_flavor_id,0) > 0 THEN 1 ELSE 0 END) AS matched_rows,
          SUM(CASE WHEN COALESCE(rf.matched_flavor_id,0)=0 OR COALESCE(rf.needs_review,0)=1 THEN 1 ELSE 0 END) AS unresolved_rows
        FROM recipe_reference_flavours rf
        JOIN recipe_references rr ON rr.id = rf.recipe_reference_id
        WHERE rr.source='vc' AND rr.source_type='vc_dtl'
        """
    ).fetchone()
    g = db.execute(
        """
        SELECT COUNT(1) AS c FROM (
          SELECT COALESCE(rf.brand_canonical,''), COALESCE(rf.flavour_name_normalized,'')
          FROM recipe_reference_flavours rf
          JOIN recipe_references rr ON rr.id = rf.recipe_reference_id
          WHERE rr.source='vc' AND rr.source_type='vc_dtl'
            AND (COALESCE(rf.matched_flavor_id,0)=0 OR COALESCE(rf.needs_review,0)=1)
          GROUP BY COALESCE(rf.brand_canonical,''), COALESCE(rf.flavour_name_normalized,'')
        ) x
        """
    ).fetchone()
    recipe_linkability = db.execute(
        """
        SELECT
          SUM(CASE WHEN unresolved_cnt = 0 THEN 1 ELSE 0 END) AS fully_linkable_recipes,
          SUM(CASE WHEN unresolved_cnt > 0 THEN 1 ELSE 0 END) AS recipes_with_unresolved
        FROM (
          SELECT rr.id,
                 SUM(CASE WHEN COALESCE(rf.matched_flavor_id,0)=0 OR COALESCE(rf.needs_review,0)=1 THEN 1 ELSE 0 END) AS unresolved_cnt
          FROM recipe_references rr
          LEFT JOIN recipe_reference_flavours rf ON rf.recipe_reference_id = rr.id
          WHERE rr.source='vc' AND rr.source_type='vc_dtl'
          GROUP BY rr.id
        ) t
        """
    ).fetchone()
    total = int(t["total_rows"] or 0) if t else 0
    matched = int(t["matched_rows"] or 0) if t else 0
    unresolved = int(t["unresolved_rows"] or 0) if t else 0
    return {
        "total_imported_flavour_rows": total,
        "total_matched_rows": matched,
        "total_unresolved_rows": unresolved,
        "unresolved_grouped_entries": int(g["c"] or 0) if g else 0,
        "match_rate_percent": round((100.0 * matched / total), 2) if total else 0.0,
        "fully_linkable_imported_recipes": int(recipe_linkability["fully_linkable_recipes"] or 0) if recipe_linkability else 0,
        "imported_recipes_with_unresolved_rows": int(recipe_linkability["recipes_with_unresolved"] or 0) if recipe_linkability else 0,
    }


def list_unresolved_imported_flavour_queue(*, brand: str = "", min_occurrence: int = 1, search: str = "", limit: int = 200) -> List[Dict[str, Any]]:
    db = get_db()
    _ensure_flavor_aliases_table()
    params: List[Any] = []
    where = [
        "rr.source='vc'",
        "rr.source_type='vc_dtl'",
        "(COALESCE(rf.matched_flavor_id,0)=0 OR COALESCE(rf.needs_review,0)=1)",
    ]
    if brand:
        where.append("COALESCE(rf.brand_canonical,'') = ?")
        params.append(canonical_library_brand(brand, ""))
    if search:
        where.append("(COALESCE(rf.flavour_name_raw,'') LIKE ? OR COALESCE(rf.flavour_name_normalized,'') LIKE ? OR COALESCE(rf.brand_canonical,'') LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    query = f"""
        SELECT
          COALESCE(rf.brand_canonical,'') AS brand_canonical,
          COALESCE(rf.flavour_name_normalized,'') AS flavour_name_normalized,
          COUNT(1) AS occurrence_count,
          MIN(COALESCE(rf.flavour_name_raw,'')) AS sample_raw_name,
          MIN(COALESCE(rr.source_recipe_id,'')) AS sample_source_recipe_id,
          MIN(COALESCE(rr.title,'')) AS sample_recipe_title,
          MAX(COALESCE(rr.scraped_at,'')) AS latest_seen_at
        FROM recipe_reference_flavours rf
        JOIN recipe_references rr ON rr.id = rf.recipe_reference_id
        WHERE {' AND '.join(where)}
        GROUP BY COALESCE(rf.brand_canonical,''), COALESCE(rf.flavour_name_normalized,'')
        HAVING COUNT(1) >= ?
        ORDER BY occurrence_count DESC, brand_canonical ASC, flavour_name_normalized ASC
        LIMIT ?
    """
    params.extend([int(min_occurrence), int(limit)])
    rows = db.execute(query, params).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        alias_exists = db.execute(
            """
            SELECT 1 FROM flavor_aliases
            WHERE alias_normalized = ? AND COALESCE(brand_canonical,'') = ?
            LIMIT 1
            """,
            (str(r["flavour_name_normalized"] or ""), str(r["brand_canonical"] or "")),
        ).fetchone()
        out.append(
            {
                "brand_canonical": str(r["brand_canonical"] or ""),
                "flavour_name_normalized": str(r["flavour_name_normalized"] or ""),
                "occurrence_count": int(r["occurrence_count"] or 0),
                "sample_raw_names": [str(r["sample_raw_name"] or "")],
                "sample_source_recipe_id": str(r["sample_source_recipe_id"] or ""),
                "sample_recipe_title": str(r["sample_recipe_title"] or ""),
                "latest_seen_at": str(r["latest_seen_at"] or ""),
                "alias_exists": bool(alias_exists),
            }
        )
    return out


def list_identity_candidates(*, brand_canonical: str, flavour_name_normalized: str, limit: int = 5) -> List[Dict[str, Any]]:
    db = get_db()
    b = canonical_library_brand(brand_canonical or "", "")
    n = _norm_name(flavour_name_normalized or "")
    if not n:
        return []
    like = f"%{n.split(' ')[0]}%"
    rows = db.execute(
        """
        SELECT id, brand, name
        FROM flavor_reference
        WHERE active = 1
          AND (LOWER(name) LIKE ? OR LOWER(brand) LIKE ?)
        ORDER BY
          CASE WHEN LOWER(brand) = LOWER(?) THEN 0 ELSE 1 END,
          CASE WHEN LOWER(name) = LOWER(?) THEN 0 ELSE 1 END,
          LENGTH(name) ASC
        LIMIT ?
        """,
        (like, f"%{(b or '').lower()}%", b, n, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        name_norm = _norm_name(str(r["name"] or ""))
        conf = 0.55
        if name_norm == n:
            conf = 0.88
        elif n in name_norm or name_norm in n:
            conf = 0.72
        if canonical_library_brand(str(r["brand"] or ""), str(r["name"] or "")) == b:
            conf += 0.08
        out.append(
            {
                "candidate_flavor_id": int(r["id"]),
                "candidate_internal_flavor_name": str(r["name"] or ""),
                "candidate_brand": str(r["brand"] or ""),
                "confidence_hint": round(min(0.99, conf), 2),
            }
        )
    return out


def create_flavor_alias_from_admin_queue(*, actor_id: int, alias_raw: str, brand_canonical: str, target_flavor_name: str, notes: str = "", confidence_override: float = 0.9) -> Tuple[Dict[str, Any], int]:
    db = get_db()
    _ensure_flavor_aliases_table()
    alias_raw = (alias_raw or "").strip()
    brand_canonical = canonical_library_brand(brand_canonical or "", "")
    alias_norm = _norm_name(alias_raw)
    if not alias_raw or not alias_norm:
        return {"status": "error", "message": "Alias is required."}, 400
    target = db.execute(
        "SELECT id, brand, name FROM flavor_reference WHERE active = 1 AND LOWER(name)=LOWER(?) LIMIT 1",
        (target_flavor_name.strip(),),
    ).fetchone()
    if not target:
        return {"status": "error", "message": "Target flavor not found."}, 404
    existing = db.execute(
        "SELECT id, target_flavor_id FROM flavor_aliases WHERE alias_normalized = ? AND COALESCE(brand_canonical,'') = ? LIMIT 1",
        (alias_norm, brand_canonical),
    ).fetchone()
    if existing and int(existing["target_flavor_id"]) != int(target["id"]):
        return {"status": "error", "message": "Alias already maps to another target. Use force overwrite workflow."}, 409
    if existing:
        db.execute(
            "UPDATE flavor_aliases SET target_flavor_id=?, notes=?, confidence_override=?, source='admin_queue', created_at=?, created_by=? WHERE id=?",
            (int(target["id"]), notes, float(confidence_override), datetime.now(), int(actor_id or 0), int(existing["id"])),
        )
    else:
        db.execute(
            """
            INSERT INTO flavor_aliases
            (alias_raw, alias_normalized, brand_canonical, target_flavor_id, notes, confidence_override, source, created_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, 'admin_queue', ?, ?)
            """,
            (alias_raw, alias_norm, brand_canonical, int(target["id"]), notes, float(confidence_override), datetime.now(), int(actor_id or 0)),
        )
    db.commit()
    return {
        "status": "success",
        "alias_raw": alias_raw,
        "alias_normalized": alias_norm,
        "brand_canonical": brand_canonical,
        "target_flavor_id": int(target["id"]),
        "target_flavor_name": str(target["name"] or ""),
    }, 200


def _run_single_scraper(name: str, *, actor_id: int, run_type: str, run_key: str = "") -> Tuple[Dict[str, Any], int]:
    name = _normalize_scraper_token(name)
    db = get_db()
    run_id = _start_scraper_run(scraper_name=name, run_type=run_type, run_key=run_key)

    def _ok(result: Dict[str, Any], http: int = 200) -> Tuple[Dict[str, Any], int]:
        result = dict(result)
        result["run_id"] = int(run_id)
        result.setdefault("status", "success")
        _finish_scraper_run(run_id=run_id, status=str(result.get("status") or "success"), summary=result)
        db.commit()
        return result, http

    try:
        if name == "clyrolinx":
            added, skipped, updated = run_clyrolinx_scraper(db, max_pages=200, delay_s=10.0)
            result = {
                "status": "success",
                "scraper": "clyrolinx",
                "added": len(added),
                "updated": updated,
                "skipped": skipped,
            }
            _log_admin_action(
                actor_id=actor_id,
                action="SCRAPER_RUN",
                details=f"clyrolinx run_id={run_id} added={len(added)} updated={updated} skipped={skipped}",
            )
            return _ok(result)

        if name == "blck":
            res = scrape_vc_flavors(db, limit_pages=30)
            result = {"status": "success", "scraper": "blck", **res}
            _log_admin_action(
                actor_id=actor_id,
                action="SCRAPER_RUN",
                details=f"blck run_id={run_id} added={res.get('added')} updated={res.get('updated')}",
            )
            return _ok(result)

        if name in {"vc", "vc_flavours", "vc_flavors"}:
            res = run_vc_scraper(db, delay_s=0.5, commit_batch=200)
            result = {"status": "success", "scraper": "vc_flavours", **res}
            _log_admin_action(
                actor_id=actor_id,
                action="SCRAPER_RUN",
                details=(
                    f"vc_flavours run_id={run_id} added={res.get('added')} "
                    f"enriched={res.get('enriched')} skipped={res.get('skipped')} errors={res.get('errors')}"
                ),
            )
            return _ok(result)

        if name in {
            "vc_dtl",
            "vc_recipes_dtl",
            "vc_recipes",
            "vc_dtl_public_all",
            "vc_dtl_public_resume",
            "vc_dtl_public_reset",
            "vc_dtl_user_2078",
            "vc_dtl_user_11",
            "vc_dtl_user_385",
            "vc_dtl_user_resume_2078",
            "vc_dtl_user_resume_11",
            "vc_dtl_user_resume_385",
            "vc_dtl_user_reset_2078",
            "vc_dtl_user_reset_11",
            "vc_dtl_user_reset_385",
        }:
            # Separate VC recipe reference scraper for DTL benchmark analytics.
            mode = "vc_public_all"
            source_user_id = ""
            resume = True
            reset_checkpoint = False
            if "public_reset" in name:
                reset_checkpoint = True
            if "_user_" in name:
                mode = "vc_user_profile"
                m = re.search(r"(2078|11|385)", name)
                source_user_id = (m.group(1) if m else "")
                if "user_reset_" in name:
                    reset_checkpoint = True
            if name == "vc_dtl_public_all":
                resume = False
            res = run_vc_dtl_scraper(
                db,
                mode=mode,
                source_user_id=source_user_id,
                resume=resume,
                reset_checkpoint=reset_checkpoint,
            )
            result = {"scraper": "vc_dtl", **res}
            status_value = str(result.get("status") or "").lower()
            if status_value in {"success", "partial", "no_new_work"}:
                run_status = "success"
            else:
                run_status = "error"
            result["status"] = run_status
            _log_admin_action(
                actor_id=actor_id,
                action="SCRAPER_RUN",
                details=(
                    f"vc_dtl run_id={run_id} stored={res.get('stored_recipes')} "
                    f"eligible={res.get('benchmark_eligible')} broken={res.get('broken_or_incomplete')}"
                ),
            )
            return _ok(result, http=200 if run_status == "success" else 500)

        if name in {"vc_flavour_identity_backfill", "vc_flavour_identity_force", "vc_flavour_identity_unresolved"}:
            force = name == "vc_flavour_identity_force"
            unresolved_only = name == "vc_flavour_identity_unresolved"
            res = backfill_all_imported_flavour_identity_matches(
                db,
                force_rematch=force,
                unresolved_only=unresolved_only,
                chunk_size=250,
            )
            result = {"status": "success", "scraper": "vc_flavour_identity_backfill", **res}
            return _ok(result)

        if name in {"vc_imported_display_resync", "vc_identity_display_resync"}:
            res = resync_imported_recipe_display_names(db)
            result = {"status": "success", "scraper": "vc_imported_display_resync", **res}
            return _ok(result)

        if name in {"fix_recipe_flavor_links"}:
            res = fix_recipe_flavor_links_safe(db, chunk_size=250, dry_run=False)
            result = {"status": "success", "scraper": "fix_recipe_flavor_links", **res}
            return _ok(result)

        if name in SCRAPER_CANONICAL_LIBRARY_PREVIEW:
            res = run_canonical_library_repair(db, dry_run=True)
            result = {"status": "success", "scraper": name, **res}
            return _ok(result)

        if name in SCRAPER_CANONICAL_LIBRARY_APPLY:
            res = run_canonical_library_repair(db, dry_run=False)
            result = {"status": "success", "scraper": name, **res}
            return _ok(result)

        result = {"status": "error", "message": f"Unknown scraper: {name}", "run_id": int(run_id), "scraper": name}
        _finish_scraper_run(run_id=run_id, status="error", summary=result)
        db.commit()
        return result, 400
    except Exception as exc:
        db.rollback()
        # write error record in a separate transaction scope
        msg = str(exc)
        if isinstance(exc, UnicodeEncodeError):
            msg = (
                "Console encoding blocked scraper logging (emoji/non-ASCII print). "
                f"{type(exc).__name__}: {exc}"
            )
        err = {
            "status": "error",
            "message": f"{name}: {msg}",
            "errors": [f"{type(exc).__name__}: {msg}"],
            "scraper": name,
            "run_id": int(run_id),
        }
        _finish_scraper_run(run_id=run_id, status="error", summary=err)
        _log_admin_action(actor_id=actor_id, action="SCRAPER_ERROR", details=f"{name} run_id={run_id}: {msg}")
        db.commit()
        return err, 500


def render_user_overview(*, role_filter: str = "", search: str = "") -> List[Dict[str, Any]]:
    db = get_db()
    params: List[Any] = []
    query = """
        SELECT
            u.id,
            u.username,
            COALESCE(u.email, '') AS email,
            u.role,
            u.status,
            u.last_login,
            (SELECT COUNT(*) FROM mix_history mh WHERE mh.user_id = u.id) AS mixes_completed,
            (SELECT COUNT(*) FROM audit_logs al WHERE al.actor_id = u.id AND (
                al.action LIKE '%MISTAKE%' OR al.action LIKE '%ERROR%' OR al.action LIKE '%RECONCILE%'
            )) AS mistakes
        FROM users u
        WHERE 1=1
    """
    if role_filter:
        query += " AND u.role = ?"
        params.append(role_filter)
    if search:
        query += " AND (LOWER(u.username) LIKE LOWER(?) OR LOWER(COALESCE(u.email,'')) LIKE LOWER(?))"
        like = f"%{search.strip()}%"
        params.extend([like, like])
    query += " ORDER BY u.id ASC"
    rows = db.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def reset_user_password(*, actor_id: int, user_id: int, new_password: str) -> None:
    if not new_password or len(new_password.strip()) < 4:
        raise ValueError("Password must be at least 4 characters.")
    db = get_db()
    target = db.execute("SELECT id, username FROM users WHERE id = ?", (int(user_id),)).fetchone()
    if not target:
        raise ValueError("User not found.")
    pw_hash = generate_password_hash(new_password.strip())
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, int(user_id)))
    _log_admin_action(
        actor_id=actor_id,
        action="USER_PASSWORD_RESET",
        target_id=int(user_id),
        details=f"username={target['username']}",
    )


def get_console_summary() -> Dict[str, int]:
    db = get_db()
    total_users = int((db.execute("SELECT COUNT(*) AS c FROM users").fetchone() or {"c": 0})["c"])
    active_users = int((db.execute("SELECT COUNT(*) AS c FROM users WHERE COALESCE(status,'Active') = 'Active'").fetchone() or {"c": 0})["c"])
    low_stock = int(
        (
            db.execute(
                """
                SELECT COUNT(*) AS c
                FROM library_items
                WHERE active = 1
                  AND COALESCE(status, 'In Use') = 'In Use'
                  AND COALESCE(current_stock, 0) <= COALESCE(low_stock_threshold, 10)
                """
            ).fetchone()
            or {"c": 0}
        )["c"]
    )
    recent_scraper_runs = int(
        (
            db.execute(
                """
                SELECT COUNT(*) AS c
                FROM scraper_runs
                WHERE started_at >= datetime('now', '-24 hours')
                """
            ).fetchone()
            or {"c": 0}
        )["c"]
    )
    return {
        "total_users": total_users,
        "active_users": active_users,
        "low_stock_items": low_stock,
        "recent_scraper_runs": recent_scraper_runs,
    }


def update_user_account(*, actor_id: int, user_id: int, role: Optional[str], status: Optional[str]) -> None:
    db = get_db()
    if role is not None:
        db.execute("UPDATE users SET role = ? WHERE id = ?", (role, int(user_id)))
    if status is not None:
        db.execute("UPDATE users SET status = ? WHERE id = ?", (status, int(user_id)))
    _log_admin_action(
        actor_id=actor_id,
        action="MASTER_USER_UPDATE",
        target_id=int(user_id),
        details=f"role={role or ''} status={status or ''}".strip(),
    )


def render_audit_logs(
    *,
    user_id: str = "",
    action: str = "",
    start: str = "",
    end: str = "",
    limit: int = 200,
) -> List[Dict[str, Any]]:
    db = get_db()
    params: List[Any] = []
    query = """
        SELECT
            al.id,
            al.timestamp,
            COALESCE(u.username, 'SYSTEM') AS username,
            al.action,
            COALESCE(al.target_table, '') AS target_table,
            COALESCE(al.target_id, 0) AS target_id,
            COALESCE(al.details, '') AS details
        FROM audit_logs al
        LEFT JOIN users u ON u.id = al.actor_id
        WHERE 1=1
    """
    if user_id:
        query += " AND al.actor_id = ?"
        params.append(int(user_id))
    if action:
        query += " AND al.action LIKE ?"
        params.append(f"%{action.strip()}%")
    if start:
        query += " AND al.timestamp >= ?"
        params.append(start)
    if end:
        query += " AND al.timestamp <= ?"
        params.append(end)
    query += " ORDER BY al.id DESC LIMIT ?"
    params.append(int(limit))
    rows = db.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def render_shopping_list(
    *,
    thresholds: Dict[str, float],
    default_threshold_ml: float,
) -> List[Dict[str, Any]]:
    """Returns current low-stock list, grouped by supplier (brand).

    Source of truth for low stock:
    - active inventory rows only
    - status = In Use only
    - item-specific threshold (library_items.low_stock_threshold) when present
    - otherwise category threshold fallback
    """
    db = get_db()

    # Support both schema names: quantity_ml (spec) and current_stock (current app)
    cols = {r["name"] for r in db.execute("PRAGMA table_info(library_items)").fetchall()}
    qty_col = "quantity_ml" if "quantity_ml" in cols else "current_stock"

    rows = db.execute(
        f"""
        SELECT
            l.id AS library_item_id,
            l.reference_id,
            l.{qty_col} AS quantity_ml,
            COALESCE(l.low_stock_threshold, 0.0) AS item_low_stock_threshold,
            COALESCE(l.status, 'In Use') AS stock_status,
            r.name,
            r.brand,
            r.category,
            COALESCE(r.profiles, '') AS profiles,
            COALESCE(r.product_url, r.url) AS product_url,
            COALESCE(r.price_raw, 0.0) AS price_raw,
            COALESCE(r.price_per_ml, 0.0) AS price_per_ml,
            COALESCE(r.vc_url, '') AS vc_url,
            COALESCE(r.needs_review, 0) AS needs_review
        FROM library_items l
        JOIN flavor_reference r ON r.id = l.reference_id
        WHERE l.active = 1
          AND COALESCE(l.status, 'In Use') = 'In Use'
        """,
    ).fetchall()

    seen: set[int] = set()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        ref_id = int(d["reference_id"])
        if ref_id in seen:
            continue
        seen.add(ref_id)
        cat = str(d.get("category") or "Flavor")
        item_thresh = float(d.get("item_low_stock_threshold") or 0.0)
        thresh = item_thresh if item_thresh > 0 else float(thresholds.get(cat, default_threshold_ml))
        qty = float(d.get("quantity_ml") or 0.0)
        # Keep boundary behavior aligned with dashboard depletion count.
        # If stock is exactly at threshold, it is still considered critical.
        if qty <= thresh:
            d["target_quantity_ml"] = thresh
            d["threshold_used_ml"] = thresh
            d["supplier"] = d.get("brand") or ""
            out.append(d)

    out.sort(key=lambda x: (str(x.get("supplier") or ""), float(x.get("quantity_ml") or 0.0)))
    sample = out[:3]
    sample_keys = list(sample[0].keys()) if sample else []
    print(f"[low-stock] rows_scanned={len(rows)} low_stock_found={len(out)} sample_count={len(sample)} sample_keys={sample_keys}")
    if sample:
        print(f"[low-stock] sample_items={sample}")
    print(f"[low-stock] return_type={type(out).__name__}")
    return out


def log_shopping_list_run(
    *,
    actor_id: int,
    thresholds: Dict[str, float],
    items: List[Dict[str, Any]],
) -> int:
    db = get_db()
    cur = db.execute(
        "INSERT INTO shopping_list_runs (actor_id, thresholds_json) VALUES (?, ?)",
        (int(actor_id), json.dumps(thresholds)),
    )
    run_id = int(cur.lastrowid)
    for it in items:
        db.execute(
            """
            INSERT INTO shopping_list_run_items
            (run_id, reference_id, current_qty_ml, target_qty_ml, supplier, product_url, price_raw)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                int(it["reference_id"]),
                float(it.get("quantity_ml") or 0.0),
                float(it.get("target_quantity_ml") or 0.0),
                str(it.get("supplier") or ""),
                str(it.get("product_url") or ""),
                float(it.get("price_raw") or 0.0),
            ),
        )
    _log_admin_action(actor_id=actor_id, action="SHOPPING_LIST_RUN_SAVED", details=f"run_id={run_id}")
    return run_id


def list_shopping_list_runs(*, limit: int = 30) -> List[Dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT r.id, r.created_at, COALESCE(u.username,'SYSTEM') AS username,
               (SELECT COUNT(*) FROM shopping_list_run_items i WHERE i.run_id = r.id) AS item_count
        FROM shopping_list_runs r
        LEFT JOIN users u ON u.id = r.actor_id
        ORDER BY r.id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def trigger_scraper(scraper_name: str, *, actor_id: int) -> Tuple[Dict[str, Any], int]:
    """Trigger scrapers synchronously (UI remains async via fetch)."""
    name = _normalize_scraper_token(scraper_name)
    if name == "manual":
        return {"status": "error", "message": "Manual import not implemented yet."}, 400
    return _run_single_scraper(name, actor_id=actor_id, run_type="manual")


def get_scheduled_scraper_plan(run_type: str = "manual") -> List[str]:
    """Single source of truth for run-all composition across manual and monthly flows."""
    rt = str(run_type or "manual").strip().lower()
    # VC monthly/manual should use resumable production-safe backfill trigger.
    base_plan = ["blck", "clyrolinx", "vc_flavours", "vc_dtl_public_resume"]
    if rt in SCHEDULED_RUN_TYPES:
        return list(base_plan)
    return list(base_plan)


def preview_monthly_scraper_plan() -> Dict[str, Any]:
    monthly_plan = get_scheduled_scraper_plan("scheduled_monthly")
    vc_trigger = next((s for s in monthly_plan if s.startswith("vc_dtl")), "")
    return {
        "status": "success",
        "run_type": "scheduled_monthly",
        "monthly_scraper_plan": monthly_plan,
        "vc_monthly_inclusion": bool(vc_trigger),
        "vc_monthly_trigger": vc_trigger,
    }


def run_all_scrapers(*, actor_id: int, run_type: str = "manual", run_key: str = "") -> Tuple[Dict[str, Any], int]:
    db = get_db()
    parent_run_id = _start_scraper_run(scraper_name="all", run_type=run_type, run_key=run_key)
    results: Dict[str, Any] = {}
    aggregate = {"added": 0, "updated": 0, "enriched": 0, "skipped": 0, "errors": 0}
    plan = get_scheduled_scraper_plan(run_type)
    monthly_scrapers_started: List[str] = []
    monthly_scrapers_completed: List[str] = []
    monthly_scrapers_failed: List[str] = []
    monthly_scrapers_skipped: List[str] = []
    status_code = 200
    try:
        _log_admin_action(
            actor_id=actor_id,
            action="SCRAPER_RUN_ALL_PLAN",
            details=f"run_type={run_type} plan={json.dumps(plan)}",
        )
        for scraper in plan:
            monthly_scrapers_started.append(scraper)
            body, code = _run_single_scraper(scraper, actor_id=actor_id, run_type=run_type, run_key=run_key)
            results[scraper] = body
            status_code = max(status_code, code)
            aggregate["added"] += int(body.get("added") or 0)
            aggregate["updated"] += int(body.get("updated") or 0)
            aggregate["enriched"] += int(body.get("enriched") or 0)
            aggregate["skipped"] += int(body.get("skipped") or 0)
            aggregate["errors"] += int(body.get("errors") or 0)
            st = str(body.get("status") or "").lower()
            if st in {"skipped", "no_new_work"}:
                monthly_scrapers_skipped.append(scraper)
            elif code >= 400:
                monthly_scrapers_failed.append(scraper)
                aggregate["errors"] += 1
            else:
                monthly_scrapers_completed.append(scraper)
        out = {
            "status": "success" if aggregate["errors"] == 0 else "partial",
            "scraper": "all",
            "summary": aggregate,
            "results": results,
            "monthly_scraper_plan": plan,
            "monthly_scrapers_started": monthly_scrapers_started,
            "monthly_scrapers_completed": monthly_scrapers_completed,
            "monthly_scrapers_failed": monthly_scrapers_failed,
            "monthly_scrapers_skipped": monthly_scrapers_skipped,
        }
        _finish_scraper_run(run_id=parent_run_id, status=out["status"], summary=out)
        _log_admin_action(
            actor_id=actor_id,
            action="SCRAPER_RUN_ALL",
            details=f"type={run_type} plan={json.dumps(plan)} {json.dumps(aggregate)}",
        )
        db.commit()
        return out, 200 if out["status"] == "success" else 207
    except Exception as exc:
        db.rollback()
        err = {"status": "error", "scraper": "all", "message": str(exc), "summary": aggregate, "results": results}
        _finish_scraper_run(run_id=parent_run_id, status="error", summary=err)
        _log_admin_action(actor_id=actor_id, action="SCRAPER_ERROR", details=f"all: {exc}")
        db.commit()
        return err, 500


def run_monthly_scrapers_if_due() -> Dict[str, Any]:
    db = get_db()
    now = datetime.now()
    run_key = f"{now.year:04d}-{now.month:02d}"
    existing = db.execute(
        """
        SELECT id
        FROM scraper_runs
        WHERE scraper_name = 'all'
          AND run_key = ?
          AND run_type IN ('scheduled_monthly', 'auto_startup_catchup')
          AND status IN ('success', 'partial')
        ORDER BY id DESC
        LIMIT 1
        """,
        (run_key,),
    ).fetchone()
    if existing:
        return {"status": "skipped", "reason": "already_ran", "run_key": run_key}
    run_type = "scheduled_monthly" if now.day == 1 else "auto_startup_catchup"
    body, status = run_all_scrapers(actor_id=0, run_type=run_type, run_key=run_key)
    return {"status_code": status, "run_type": run_type, "run_key": run_key, **body}


def _compute_next_monthly_run(now: Optional[datetime] = None) -> datetime:
    base = now or datetime.now()
    if base.month == 12:
        return datetime(base.year + 1, 1, 1, 0, 5, 0)
    return datetime(base.year, base.month + 1, 1, 0, 5, 0)


def _scheduler_loop(app, tick_seconds: int, run_on_start: bool) -> None:
    global _scheduler_state
    first_cycle = True
    while _scheduler_stop_event and not _scheduler_stop_event.is_set():
        if first_cycle and not run_on_start:
            first_cycle = False
            time.sleep(max(30, int(tick_seconds)))
            continue
        first_cycle = False
        with app.app_context():
            now = datetime.now()
            with _scheduler_lock:
                _scheduler_state["scheduler_process_active"] = True
                _scheduler_state["monthly_job_registered"] = True
                _scheduler_state["monthly_next_run"] = _compute_next_monthly_run(now).strftime("%Y-%m-%d %H:%M:%S")
                _scheduler_state["last_health_check_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
            try:
                res = run_monthly_scrapers_if_due()
                with _scheduler_lock:
                    _scheduler_state["monthly_last_result"] = str(res.get("status") or "")
                    _scheduler_state["monthly_dispatch_ok"] = str(res.get("status") or "") in {
                        "success",
                        "partial",
                        "no_new_work",
                        "skipped",
                    }
                    if str(res.get("status") or "") in {"success", "partial", "no_new_work"}:
                        _scheduler_state["monthly_last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    _scheduler_state["monthly_last_error"] = ""
            except Exception as exc:
                with _scheduler_lock:
                    _scheduler_state["monthly_dispatch_ok"] = False
                    _scheduler_state["monthly_last_error"] = f"{type(exc).__name__}: {exc}"
        time.sleep(max(30, int(tick_seconds)))


def start_scheduler(app, *, tick_seconds: int = 900, run_on_start: bool = False) -> Dict[str, Any]:
    global _scheduler_thread, _scheduler_stop_event, _scheduler_state
    with _scheduler_lock:
        monthly_enabled = str(os.environ.get("OGL_ENABLE_MONTHLY_SCHEDULER", "1")).strip().lower() not in {
            "0",
            "false",
            "off",
            "no",
        }
        _scheduler_state["scheduler_enabled"] = monthly_enabled
        _scheduler_state["monthly_job_enabled"] = monthly_enabled
        _scheduler_state["monthly_next_run"] = _compute_next_monthly_run().strftime("%Y-%m-%d %H:%M:%S")
        if not monthly_enabled:
            _scheduler_state["scheduler_process_active"] = False
            _scheduler_state["monthly_job_registered"] = False
            return get_scheduler_status()
        if _scheduler_thread and _scheduler_thread.is_alive():
            return get_scheduler_status()
        _scheduler_stop_event = threading.Event()
        _scheduler_thread = threading.Thread(
            target=_scheduler_loop,
            args=(app, int(tick_seconds), bool(run_on_start)),
            name="old-goat-monthly-scheduler",
            daemon=True,
        )
        _scheduler_thread.start()
        _scheduler_state["scheduler_process_active"] = True
        _scheduler_state["monthly_job_registered"] = True
        _scheduler_state["monthly_last_error"] = ""
    return get_scheduler_status()


def get_scheduler_status() -> Dict[str, Any]:
    with _scheduler_lock:
        snap = dict(_scheduler_state)
    db = get_db()
    last = db.execute(
        """
        SELECT completed_at, status, run_type, summary_json
        FROM scraper_runs
        WHERE scraper_name = 'all'
          AND run_type IN ('scheduled_monthly', 'auto_startup_catchup')
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    if last:
        snap["monthly_last_run"] = snap.get("monthly_last_run") or str(last["completed_at"] or "")
        snap["monthly_last_result"] = snap.get("monthly_last_result") or str(last["status"] or "")
        snap["monthly_last_run_type"] = str(last["run_type"] or "")
    else:
        snap["monthly_last_run_type"] = ""
    monthly_plan = get_scheduled_scraper_plan("scheduled_monthly")
    vc_trigger = next((s for s in monthly_plan if s.startswith("vc_dtl")), "")
    snap["monthly_scraper_plan"] = monthly_plan
    snap["vc_monthly_inclusion"] = bool(vc_trigger)
    snap["vc_monthly_trigger"] = vc_trigger
    return snap


def check_scheduler_health() -> Dict[str, Any]:
    status = get_scheduler_status()
    issues: List[str] = []
    if not status.get("monthly_job_enabled"):
        issues.append("Monthly scheduler disabled by config")
    if status.get("monthly_job_enabled") and not status.get("monthly_job_registered"):
        issues.append("Monthly scheduler not registered")
    if status.get("monthly_job_registered") and not status.get("scheduler_process_active"):
        issues.append("Scheduler process not active")
    if status.get("monthly_job_registered") and not status.get("monthly_next_run"):
        issues.append("Monthly trigger has no next run")
    if status.get("monthly_job_registered") and not status.get("monthly_last_run"):
        issues.append("Monthly scheduler registered but never fired")
    if status.get("monthly_last_error"):
        issues.append("Monthly scheduler fired but dispatch failed")
    status["issues"] = issues
    status["health"] = "ok" if not issues else ("warning" if len(issues) <= 2 else "error")
    return status


def start_stock_take_run(*, actor_id: int, notes: str = "") -> int:
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO stock_take_runs (created_by, started_at, status, notes)
        VALUES (?, ?, 'open', ?)
        """,
        (int(actor_id), datetime.now(), (notes or "").strip()),
    )
    run_id = int(cur.lastrowid)
    db.execute(
        """
        INSERT INTO stock_take_items
        (stock_take_run_id, library_item_id, reference_id, expected_stock, actual_stock, variance, action_taken, notes)
        SELECT ?, l.id, l.reference_id, COALESCE(l.current_stock, 0.0), NULL, 0.0, 'skipped', ''
        FROM library_items l
        WHERE l.active = 1
        """,
        (run_id,),
    )
    _log_admin_action(actor_id=actor_id, action="STOCK_TAKE_STARTED", target_id=run_id, details=f"items_seeded_for_run={run_id}")
    db.commit()
    return run_id


def get_latest_open_stock_take_run() -> Optional[Dict[str, Any]]:
    db = get_db()
    row = db.execute(
        """
        SELECT r.id, r.created_by, r.started_at, r.status, COALESCE(u.username,'SYSTEM') AS username
        FROM stock_take_runs r
        LEFT JOIN users u ON u.id = r.created_by
        WHERE r.status = 'open'
        ORDER BY r.id DESC
        LIMIT 1
        """
    ).fetchone()
    return dict(row) if row else None


def get_stock_take_run(run_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    row = db.execute(
        """
        SELECT r.*, COALESCE(u.username,'SYSTEM') AS username
        FROM stock_take_runs r
        LEFT JOIN users u ON u.id = r.created_by
        WHERE r.id = ?
        """,
        (int(run_id),),
    ).fetchone()
    return dict(row) if row else None


def list_stock_take_runs(*, limit: int = 30) -> List[Dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT r.id, r.started_at, r.completed_at, r.status, COALESCE(u.username,'SYSTEM') AS username,
               (SELECT COUNT(*) FROM stock_take_items i WHERE i.stock_take_run_id = r.id) AS item_count,
               (SELECT COUNT(*) FROM stock_take_items i WHERE i.stock_take_run_id = r.id AND ABS(COALESCE(i.variance,0.0)) > 0.0001) AS adjusted_count
        FROM stock_take_runs r
        LEFT JOIN users u ON u.id = r.created_by
        ORDER BY r.id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def get_stock_take_items(
    *,
    run_id: int,
    search: str = "",
    category: str = "",
    uncounted_only: bool = False,
    variances_only: bool = False,
) -> List[Dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT
            i.id,
            i.stock_take_run_id,
            i.library_item_id,
            i.reference_id,
            COALESCE(i.expected_stock, 0.0) AS expected_stock,
            i.actual_stock,
            COALESCE(i.variance, 0.0) AS variance,
            COALESCE(i.action_taken, 'skipped') AS action_taken,
            COALESCE(i.notes, '') AS notes,
            COALESCE(r.name, '') AS name,
            COALESCE(r.brand, '') AS brand,
            COALESCE(r.category, '') AS category,
            COALESCE(r.profiles, '') AS profiles,
            COALESCE(r.price_per_ml, 0.0) AS price_per_ml
        FROM stock_take_items i
        JOIN library_items l ON l.id = i.library_item_id
        JOIN flavor_reference r ON r.id = i.reference_id
        WHERE i.stock_take_run_id = ?
          AND l.active = 1
        ORDER BY r.brand ASC, r.name ASC
        """,
        (int(run_id),),
    ).fetchall()
    out = [dict(r) for r in rows]
    if search:
        q = search.strip().lower()
        out = [x for x in out if q in str(x.get("name", "")).lower() or q in str(x.get("brand", "")).lower()]
    if category:
        out = [x for x in out if str(x.get("category") or "") == category]
    if uncounted_only:
        out = [x for x in out if x.get("actual_stock") is None]
    if variances_only:
        out = [x for x in out if abs(float(x.get("variance") or 0.0)) > 0.0001]
    return out


def save_stock_take_progress(*, run_id: int, item_updates: List[Dict[str, Any]], actor_id: int) -> Dict[str, int]:
    db = get_db()
    run = get_stock_take_run(int(run_id))
    if not run:
        raise ValueError("Stock take run not found.")
    if str(run.get("status")) != "open":
        raise ValueError("Stock take run is not open.")
    saved = 0
    for u in item_updates:
        item_id = int(u.get("id") or 0)
        if not item_id:
            continue
        exp_row = db.execute("SELECT expected_stock FROM stock_take_items WHERE id = ? AND stock_take_run_id = ?", (item_id, int(run_id))).fetchone()
        if not exp_row:
            continue
        expected = float(exp_row["expected_stock"] or 0.0)
        actual_raw = u.get("actual_stock")
        actual_val = None if actual_raw in (None, "", "None") else float(actual_raw)
        action_taken = str(u.get("action_taken") or "skipped")
        notes = str(u.get("notes") or "").strip()
        if action_taken == "marked_empty":
            actual_val = 0.0
        variance = 0.0 if actual_val is None else float(actual_val - expected)
        db.execute(
            """
            UPDATE stock_take_items
            SET actual_stock = ?, variance = ?, action_taken = ?, notes = ?
            WHERE id = ? AND stock_take_run_id = ?
            """,
            (actual_val, variance, action_taken, notes, item_id, int(run_id)),
        )
        saved += 1
    _log_admin_action(actor_id=actor_id, action="STOCK_TAKE_PROGRESS_SAVED", target_id=int(run_id), details=f"saved_items={saved}")
    db.commit()
    return {"saved_items": saved}


def finalize_stock_take(*, run_id: int, actor_id: int) -> Dict[str, Any]:
    db = get_db()
    run = get_stock_take_run(int(run_id))
    if not run:
        raise ValueError("Stock take run not found.")
    if str(run.get("status")) != "open":
        raise ValueError("Stock take run is not open.")

    rows = db.execute(
        """
        SELECT id, library_item_id, reference_id, expected_stock, actual_stock, variance
        FROM stock_take_items
        WHERE stock_take_run_id = ?
        """,
        (int(run_id),),
    ).fetchall()

    total = 0
    adjusted = 0
    unchanged = 0
    for r in rows:
        d = dict(r)
        total += 1
        expected = float(d.get("expected_stock") or 0.0)
        actual = d.get("actual_stock")
        final_actual = expected if actual is None else float(actual)
        variance = float(final_actual - expected)
        action_taken = "unchanged" if abs(variance) <= 0.0001 else ("marked_empty" if final_actual <= 0 else "adjusted")
        db.execute(
            "UPDATE stock_take_items SET actual_stock = ?, variance = ?, action_taken = ? WHERE id = ?",
            (final_actual, variance, action_taken, int(d["id"])),
        )
        if abs(variance) <= 0.0001:
            unchanged += 1
            continue
        adjusted += 1
        new_status = "Empty" if final_actual <= 0 else "In Use"
        db.execute(
            "UPDATE library_items SET current_stock = ?, status = ?, updated_at = ? WHERE id = ?",
            (final_actual, new_status, datetime.now(), int(d["library_item_id"])),
        )
        _log_admin_action(
            actor_id=actor_id,
            action="STOCK_TAKE_ITEM_ADJUSTED",
            target_id=int(d["library_item_id"]),
            details=f"run_id={run_id} expected={expected} actual={final_actual} variance={variance}",
        )

    db.execute(
        "UPDATE stock_take_runs SET status = 'completed', completed_at = ? WHERE id = ?",
        (datetime.now(), int(run_id)),
    )
    _log_admin_action(
        actor_id=actor_id,
        action="STOCK_TAKE_COMPLETED",
        target_id=int(run_id),
        details=f"total={total} adjusted={adjusted} unchanged={unchanged}",
    )
    db.commit()
    return {"run_id": int(run_id), "total_items": total, "adjusted_items": adjusted, "unchanged_items": unchanged}


def build_stock_take_report(*, run_id: int) -> Dict[str, Any]:
    run = get_stock_take_run(int(run_id))
    if not run:
        raise ValueError("Stock take run not found.")
    items = get_stock_take_items(run_id=int(run_id))
    total = len(items)
    adjusted = len([i for i in items if abs(float(i.get("variance") or 0.0)) > 0.0001])
    unchanged = total - adjusted
    shortage_total = sum([float(i.get("variance") or 0.0) for i in items if float(i.get("variance") or 0.0) < 0])
    excess_total = sum([float(i.get("variance") or 0.0) for i in items if float(i.get("variance") or 0.0) > 0])
    return {
        "run": run,
        "items": items,
        "summary": {
            "total_items": total,
            "adjusted_items": adjusted,
            "unchanged_items": unchanged,
            "shortage_total": shortage_total,
            "excess_total": excess_total,
        },
    }


def list_production_batches(*, status: str = "", decant_filter: str = "") -> List[Dict[str, Any]]:
    db = get_db()
    params: List[Any] = []
    query = """
        SELECT pb.*,
               COALESCE(u.username, 'SYSTEM') AS mixed_by_name,
               (SELECT COUNT(*)
                  FROM decant_runs dr
                 WHERE dr.production_batch_id = pb.id
                   AND dr.status = 'completed') AS completed_decant_runs,
               (SELECT COALESCE(SUM(di.quantity_produced), 0)
                  FROM decant_runs dr
                  JOIN decant_items di ON di.decant_run_id = dr.id
                 WHERE dr.production_batch_id = pb.id
                   AND dr.status = 'completed') AS total_bottles_produced,
               (SELECT COALESCE(SUM(di.total_volume_used_ml), 0.0)
                  FROM decant_runs dr
                  JOIN decant_items di ON di.decant_run_id = dr.id
                 WHERE dr.production_batch_id = pb.id
                   AND dr.status = 'completed') AS total_output_ml,
               (SELECT COALESCE(SUM(dr.wastage_ml), 0.0)
                  FROM decant_runs dr
                 WHERE dr.production_batch_id = pb.id
                   AND dr.status = 'completed') AS total_wastage_ml
        FROM production_batches pb
        LEFT JOIN users u ON u.id = pb.mixed_by
        WHERE 1=1
    """
    if status:
        query += " AND pb.batch_status = ?"
        params.append(status)
    query += " ORDER BY pb.id DESC"
    rows = db.execute(query, params).fetchall()
    out = [dict(r) for r in rows]
    for r in out:
        completed = int(r.get("completed_decant_runs") or 0)
        remaining = float(r.get("remaining_volume_ml") or 0.0)
        if completed == 0:
            state = "not_decanted"
        elif remaining <= 0.01 or str(r.get("batch_status")) == "depleted":
            state = "fully_decanted"
        else:
            state = "partially_decanted"
        r["decant_state"] = state
    if decant_filter:
        out = [r for r in out if r.get("decant_state") == decant_filter]
    return out


def list_finished_goods(*, search: str = "") -> List[Dict[str, Any]]:
    db = get_db()
    params: List[Any] = []
    query = """
        SELECT fg.*, COALESCE(pb.batch_name, '') AS batch_name
        FROM finished_goods_inventory fg
        LEFT JOIN production_batches pb ON pb.id = fg.production_batch_id
        WHERE fg.active = 1
    """
    if search:
        query += " AND LOWER(fg.product_name) LIKE LOWER(?)"
        params.append(f"%{search.strip()}%")
    query += " ORDER BY fg.product_name ASC, fg.bottle_size_ml ASC"
    rows = db.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def create_production_batch(
    *,
    actor_id: int,
    batch_name: str,
    total_volume_ml: float,
    recipe_id: Optional[int] = None,
    notes: str = "",
) -> int:
    if not batch_name.strip():
        raise ValueError("Batch name is required.")
    if float(total_volume_ml or 0) <= 0:
        raise ValueError("Total volume must be greater than zero.")
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO production_batches
        (recipe_id, batch_name, total_volume_ml, remaining_volume_ml, batch_status, mixed_by, mixed_at, notes)
        VALUES (?, ?, ?, ?, 'open', ?, ?, ?)
        """,
        (
            int(recipe_id) if recipe_id else None,
            batch_name.strip(),
            float(total_volume_ml),
            float(total_volume_ml),
            int(actor_id),
            datetime.now(),
            (notes or "").strip(),
        ),
    )
    batch_id = int(cur.lastrowid)
    _log_admin_action(
        actor_id=actor_id,
        action="PRODUCTION_BATCH_CREATED",
        target_id=batch_id,
        details=f"name={batch_name.strip()} total_volume_ml={float(total_volume_ml)}",
    )
    db.commit()
    return batch_id


def get_production_batch(batch_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    row = db.execute(
        """
        SELECT pb.*, COALESCE(u.username, 'SYSTEM') AS mixed_by_name
        FROM production_batches pb
        LEFT JOIN users u ON u.id = pb.mixed_by
        WHERE pb.id = ?
        """,
        (int(batch_id),),
    ).fetchone()
    return dict(row) if row else None


def start_decant_run(*, batch_id: int, actor_id: int, notes: str = "") -> int:
    db = get_db()
    batch = get_production_batch(int(batch_id))
    if not batch:
        raise ValueError("Production batch not found.")
    if str(batch.get("batch_status")) in {"depleted", "archived"}:
        raise ValueError("Batch is not available for decanting.")
    open_row = db.execute(
        """
        SELECT id FROM decant_runs
        WHERE production_batch_id = ? AND status = 'open'
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(batch_id),),
    ).fetchone()
    if open_row:
        return int(open_row["id"])
    cur = db.execute(
        """
        INSERT INTO decant_runs (production_batch_id, created_by, started_at, notes, wastage_ml, status)
        VALUES (?, ?, ?, ?, 0.0, 'open')
        """,
        (int(batch_id), int(actor_id), datetime.now(), (notes or "").strip()),
    )
    run_id = int(cur.lastrowid)
    _log_admin_action(
        actor_id=actor_id,
        action="DECANT_RUN_STARTED",
        target_id=run_id,
        details=f"batch_id={batch_id}",
    )
    db.commit()
    return run_id


def get_decant_run(run_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    row = db.execute(
        """
        SELECT dr.*, pb.batch_name, pb.total_volume_ml, pb.remaining_volume_ml, pb.recipe_id,
               COALESCE(u.username, 'SYSTEM') AS created_by_name
        FROM decant_runs dr
        JOIN production_batches pb ON pb.id = dr.production_batch_id
        LEFT JOIN users u ON u.id = dr.created_by
        WHERE dr.id = ?
        """,
        (int(run_id),),
    ).fetchone()
    return dict(row) if row else None


def list_decant_items(*, run_id: int) -> List[Dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT id, decant_run_id, bottle_size_ml, quantity_produced, total_volume_used_ml, COALESCE(label_name, '') AS label_name
        FROM decant_items
        WHERE decant_run_id = ?
        ORDER BY id ASC
        """,
        (int(run_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def save_decant_draft(
    *,
    run_id: int,
    lines: List[Dict[str, Any]],
    wastage_ml: float,
    notes: str,
    actor_id: int,
) -> Dict[str, Any]:
    db = get_db()
    run = get_decant_run(int(run_id))
    if not run:
        raise ValueError("Decant run not found.")
    if str(run.get("status")) != "open":
        raise ValueError("Decant run is not open.")

    db.execute("DELETE FROM decant_items WHERE decant_run_id = ?", (int(run_id),))
    line_count = 0
    total_used = 0.0
    for line in lines:
        bottle_size = float(line.get("bottle_size_ml") or 0.0)
        qty = int(line.get("quantity_produced") or 0)
        if bottle_size <= 0 or qty <= 0:
            continue
        label = str(line.get("label_name") or "").strip()
        used_ml = float(bottle_size * qty)
        total_used += used_ml
        db.execute(
            """
            INSERT INTO decant_items (decant_run_id, bottle_size_ml, quantity_produced, total_volume_used_ml, label_name)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(run_id), bottle_size, qty, used_ml, label),
        )
        line_count += 1

    db.execute(
        "UPDATE decant_runs SET wastage_ml = ?, notes = ? WHERE id = ?",
        (float(wastage_ml or 0.0), (notes or "").strip(), int(run_id)),
    )
    _log_admin_action(
        actor_id=actor_id,
        action="DECANT_RUN_DRAFT_SAVED",
        target_id=int(run_id),
        details=f"line_count={line_count} total_used_ml={total_used} wastage_ml={float(wastage_ml or 0.0)}",
    )
    db.commit()
    return {"line_count": line_count, "total_used_ml": total_used, "wastage_ml": float(wastage_ml or 0.0)}


def finalize_decant_run(*, run_id: int, actor_id: int) -> Dict[str, Any]:
    db = get_db()
    run = get_decant_run(int(run_id))
    if not run:
        raise ValueError("Decant run not found.")
    if str(run.get("status")) != "open":
        raise ValueError("Decant run is not open.")
    items = list_decant_items(run_id=int(run_id))
    if not items:
        raise ValueError("Cannot finalize without produced bottle lines.")

    total_output_ml = sum([float(i.get("total_volume_used_ml") or 0.0) for i in items])
    wastage = float(run.get("wastage_ml") or 0.0)
    total_deduct = float(total_output_ml + wastage)
    remaining_before = float(run.get("remaining_volume_ml") or 0.0)
    if total_deduct > remaining_before + 1e-9:
        raise ValueError(f"Over-decant prevented. Required {total_deduct:.2f}ml but only {remaining_before:.2f}ml remains.")

    remaining_after = max(0.0, remaining_before - total_deduct)
    new_status = "depleted" if remaining_after <= 0.01 else "open"
    db.execute(
        "UPDATE production_batches SET remaining_volume_ml = ?, batch_status = ? WHERE id = ?",
        (remaining_after, new_status, int(run["production_batch_id"])),
    )

    batch_name = str(run.get("batch_name") or f"Batch #{run.get('production_batch_id')}")
    recipe_id = run.get("recipe_id")
    finished_updates = 0
    for i in items:
        bottle_size = float(i.get("bottle_size_ml") or 0.0)
        qty = int(i.get("quantity_produced") or 0)
        if bottle_size <= 0 or qty <= 0:
            continue
        product_name = str(i.get("label_name") or "").strip() or batch_name
        existing = db.execute(
            """
            SELECT id, quantity_on_hand
            FROM finished_goods_inventory
            WHERE active = 1
              AND COALESCE(production_batch_id, 0) = COALESCE(?, 0)
              AND COALESCE(recipe_id, 0) = COALESCE(?, 0)
              AND product_name = ?
              AND bottle_size_ml = ?
            LIMIT 1
            """,
            (int(run["production_batch_id"]), int(recipe_id) if recipe_id else None, product_name, bottle_size),
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE finished_goods_inventory SET quantity_on_hand = quantity_on_hand + ? WHERE id = ?",
                (qty, int(existing["id"])),
            )
        else:
            db.execute(
                """
                INSERT INTO finished_goods_inventory
                (production_batch_id, recipe_id, product_name, bottle_size_ml, quantity_on_hand, active)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (int(run["production_batch_id"]), int(recipe_id) if recipe_id else None, product_name, bottle_size, qty),
            )
        finished_updates += 1

    db.execute(
        "UPDATE decant_runs SET status = 'completed', completed_at = ? WHERE id = ?",
        (datetime.now(), int(run_id)),
    )
    _log_admin_action(
        actor_id=actor_id,
        action="DECANT_RUN_COMPLETED",
        target_id=int(run_id),
        details=f"batch_id={run['production_batch_id']} output_ml={total_output_ml} wastage_ml={wastage} deducted_ml={total_deduct} remaining_after={remaining_after}",
    )
    _log_admin_action(
        actor_id=actor_id,
        action="FINISHED_GOODS_UPDATED",
        target_id=int(run["production_batch_id"]),
        details=f"run_id={run_id} line_items={len(items)} finished_updates={finished_updates}",
    )
    db.commit()
    return {
        "run_id": int(run_id),
        "batch_id": int(run["production_batch_id"]),
        "total_output_ml": total_output_ml,
        "wastage_ml": wastage,
        "total_deducted_ml": total_deduct,
        "remaining_after_ml": remaining_after,
        "finished_updates": finished_updates,
    }


def build_production_batch_report(*, batch_id: int) -> Dict[str, Any]:
    db = get_db()
    batch = get_production_batch(int(batch_id))
    if not batch:
        raise ValueError("Batch not found.")
    runs = db.execute(
        """
        SELECT dr.id, dr.started_at, dr.completed_at, dr.status, dr.wastage_ml, COALESCE(u.username,'SYSTEM') AS created_by_name
        FROM decant_runs dr
        LEFT JOIN users u ON u.id = dr.created_by
        WHERE dr.production_batch_id = ?
        ORDER BY dr.id DESC
        """,
        (int(batch_id),),
    ).fetchall()
    run_list = [dict(r) for r in runs]
    details = db.execute(
        """
        SELECT dr.id AS decant_run_id, di.bottle_size_ml, di.quantity_produced, di.total_volume_used_ml, COALESCE(di.label_name,'') AS label_name
        FROM decant_runs dr
        JOIN decant_items di ON di.decant_run_id = dr.id
        WHERE dr.production_batch_id = ?
        ORDER BY dr.id DESC, di.id ASC
        """,
        (int(batch_id),),
    ).fetchall()
    detail_list = [dict(r) for r in details]
    total_bottles = sum([int(d.get("quantity_produced") or 0) for d in detail_list])
    total_output_ml = sum([float(d.get("total_volume_used_ml") or 0.0) for d in detail_list])
    total_wastage = sum([float(r.get("wastage_ml") or 0.0) for r in run_list if str(r.get("status")) == "completed"])
    return {
        "batch": batch,
        "decant_runs": run_list,
        "decant_items": detail_list,
        "summary": {
            "total_bottles_produced": total_bottles,
            "total_output_ml": total_output_ml,
            "total_wastage_ml": total_wastage,
        },
    }

