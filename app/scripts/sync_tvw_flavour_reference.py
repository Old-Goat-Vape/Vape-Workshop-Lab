from __future__ import annotations

import argparse
import json
import os
import sys
import sqlite3
from datetime import UTC, datetime

# Allow running as a script from app/scripts.
APP_ROOT = os.path.dirname(os.path.dirname(__file__))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from config.canonical_library_brands import (
    canonical_library_brand,
    flavor_identity_match_key,
    normalize_flavor_identity_name,
    preferred_flavor_storage_name,
)
from config.flavor_name_aliases import FLAVOR_NAME_ALIASES


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _default_source_db() -> str:
    here = os.path.dirname(os.path.dirname(__file__))  # app/
    return os.path.abspath(os.path.join(here, "..", "..", "Old_Goat_Lab", "app", "old_goat_lab.db"))


def _default_target_db() -> str:
    here = os.path.dirname(os.path.dirname(__file__))  # app/
    return os.path.abspath(os.path.join(here, "tvw_calculator.db"))


def _dict_rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_sql: str) -> None:
    cols = [r["name"] for r in _dict_rows(conn, f"PRAGMA table_info({table})")]
    if column in cols:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_sql}")


def _find_or_create_flavour(conn: sqlite3.Connection, *, canonical_name: str, brand: str, category: str, profile: str | None) -> int:
    row = conn.execute(
        """
        SELECT id
        FROM flavours
        WHERE LOWER(COALESCE(canonical_name, '')) = LOWER(?)
          AND LOWER(COALESCE(brand, '')) = LOWER(?)
        LIMIT 1
        """,
        (canonical_name, brand),
    ).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO flavours (canonical_name, brand, category, profile, created_at, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (canonical_name, brand, category, profile),
    )
    return int(cur.lastrowid)


def sync_flavours(source_db: str, target_db: str, limit: int | None = None) -> dict:
    if not os.path.exists(source_db):
        raise FileNotFoundError(f"Source DB not found: {source_db}")
    if not os.path.exists(target_db):
        raise FileNotFoundError(f"Target DB not found: {target_db}")

    src = sqlite3.connect(source_db)
    dst = sqlite3.connect(target_db)
    src.row_factory = sqlite3.Row
    dst.row_factory = sqlite3.Row

    # Ensure new schema bits are present even if startup has not run yet.
    dst.executescript(
        """
        CREATE TABLE IF NOT EXISTS flavour_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flavour_id INTEGER NOT NULL,
            alias_raw TEXT NOT NULL,
            alias_normalized TEXT NOT NULL,
            source TEXT DEFAULT 'sync',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(flavour_id) REFERENCES flavours(id) ON DELETE CASCADE,
            UNIQUE(flavour_id, alias_normalized)
        );
        CREATE TABLE IF NOT EXISTS flavour_reference_meta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flavour_id INTEGER NOT NULL UNIQUE,
            source_name TEXT,
            source_table TEXT,
            source_record_id INTEGER,
            source_url TEXT,
            vc_url TEXT,
            reference_payload_json TEXT,
            last_synced_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(flavour_id) REFERENCES flavours(id) ON DELETE CASCADE
        );
        """
    )
    # Ensure added columns exist even if app startup migrations did not run yet.
    _ensure_column(dst, "flavours", "normalized_name", "TEXT")
    _ensure_column(dst, "flavours", "canonical_brand", "TEXT")
    _ensure_column(dst, "flavours", "profile", "TEXT")
    _ensure_column(dst, "flavours", "source", "TEXT")
    _ensure_column(dst, "flavours", "source_flavor_ref_id", "INTEGER")
    _ensure_column(dst, "flavours", "avg_usage", "REAL")
    _ensure_column(dst, "flavours", "default_percent", "REAL")
    _ensure_column(dst, "flavours", "created_at", "DATETIME")
    _ensure_column(dst, "flavours", "updated_at", "DATETIME")
    _ensure_column(dst, "flavor_reference", "vc_url", "TEXT")
    _ensure_column(dst, "flavor_reference", "description", "TEXT")
    _ensure_column(dst, "flavor_reference", "usage_notes", "TEXT")
    _ensure_column(dst, "flavor_reference", "resolved_profile", "TEXT")
    _ensure_column(dst, "flavor_reference", "created_at", "DATETIME")

    query = """
        SELECT
            id,
            brand,
            name,
            category,
            profiles,
            avg_usage,
            min_percent,
            max_percent,
            source,
            url,
            vc_url,
            description,
            usage_notes,
            resolved_profile,
            last_scraped
        FROM flavor_reference
        WHERE active = 1
        ORDER BY id ASC
    """
    rows = _dict_rows(src, query)
    if limit:
        rows = rows[: max(0, int(limit))]

    created_ref = 0
    updated_ref = 0
    created_flavours = 0
    updated_flavours = 0
    created_meta = 0
    updated_meta = 0
    created_aliases = 0
    now = _now_iso()

    for r in rows:
        brand_raw = str(r.get("brand") or "").strip()
        name_raw = str(r.get("name") or "").strip()
        if not brand_raw or not name_raw:
            continue
        canonical_brand = canonical_library_brand(brand_raw, name_raw)
        canonical_name = preferred_flavor_storage_name(name_raw)
        normalized_name = normalize_flavor_identity_name(name_raw)
        source_name = str(r.get("source") or "lab_sync")
        category = str(r.get("category") or "Flavor")
        profile = str(r.get("resolved_profile") or r.get("profiles") or "")
        avg_usage = r.get("avg_usage")
        min_percent = r.get("min_percent")
        max_percent = r.get("max_percent")
        default_percent = min_percent if min_percent is not None else avg_usage

        ref_row = dst.execute(
            "SELECT id FROM flavor_reference WHERE brand = ? AND name = ?",
            (canonical_brand, canonical_name),
        ).fetchone()
        if ref_row:
            ref_id = int(ref_row["id"])
            dst.execute(
                """
                UPDATE flavor_reference
                SET category = COALESCE(NULLIF(?, ''), category),
                    profiles = COALESCE(NULLIF(?, ''), profiles),
                    avg_usage = COALESCE(?, avg_usage),
                    min_percent = COALESCE(?, min_percent),
                    max_percent = COALESCE(?, max_percent),
                    source = COALESCE(NULLIF(?, ''), source),
                    url = COALESCE(NULLIF(?, ''), url),
                    vc_url = COALESCE(NULLIF(?, ''), vc_url),
                    description = COALESCE(NULLIF(?, ''), description),
                    usage_notes = COALESCE(NULLIF(?, ''), usage_notes),
                    resolved_profile = COALESCE(NULLIF(?, ''), resolved_profile),
                    last_scraped = COALESCE(NULLIF(?, ''), last_scraped),
                    active = 1
                WHERE id = ?
                """,
                (
                    category,
                    profile,
                    avg_usage,
                    min_percent,
                    max_percent,
                    source_name,
                    r.get("url"),
                    r.get("vc_url"),
                    r.get("description"),
                    r.get("usage_notes"),
                    profile,
                    r.get("last_scraped"),
                    ref_id,
                ),
            )
            updated_ref += 1
        else:
            cur = dst.execute(
                """
                INSERT INTO flavor_reference
                (brand, name, category, profiles, avg_usage, min_percent, max_percent, source, url, vc_url, description, usage_notes, resolved_profile, active, last_scraped)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    canonical_brand,
                    canonical_name,
                    category,
                    profile,
                    avg_usage,
                    min_percent,
                    max_percent,
                    source_name,
                    r.get("url"),
                    r.get("vc_url"),
                    r.get("description"),
                    r.get("usage_notes"),
                    profile,
                    r.get("last_scraped"),
                ),
            )
            ref_id = int(cur.lastrowid)
            created_ref += 1

        flavour_row = dst.execute(
            """
            SELECT id
            FROM flavours
            WHERE LOWER(COALESCE(canonical_name, '')) = LOWER(?)
              AND LOWER(COALESCE(brand, '')) = LOWER(?)
            LIMIT 1
            """,
            (canonical_name, canonical_brand),
        ).fetchone()
        if flavour_row:
            flavour_id = int(flavour_row["id"])
            dst.execute(
                """
                UPDATE flavours
                SET category = COALESCE(NULLIF(?, ''), category),
                    normalized_name = COALESCE(NULLIF(?, ''), normalized_name),
                    canonical_brand = COALESCE(NULLIF(?, ''), canonical_brand),
                    profile = COALESCE(NULLIF(?, ''), profile),
                    source = COALESCE(NULLIF(?, ''), source),
                    source_flavor_ref_id = ?,
                    avg_usage = COALESCE(?, avg_usage),
                    default_percent = COALESCE(?, default_percent),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    category,
                    normalized_name,
                    canonical_brand,
                    profile,
                    source_name,
                    ref_id,
                    avg_usage,
                    default_percent,
                    flavour_id,
                ),
            )
            updated_flavours += 1
        else:
            cur = dst.execute(
                """
                INSERT INTO flavours
                (canonical_name, brand, category, normalized_name, canonical_brand, profile, source, source_flavor_ref_id, avg_usage, default_percent, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    canonical_name,
                    canonical_brand,
                    category,
                    normalized_name,
                    canonical_brand,
                    profile,
                    source_name,
                    ref_id,
                    avg_usage,
                    default_percent,
                ),
            )
            flavour_id = int(cur.lastrowid)
            created_flavours += 1

        meta_payload = {
            "source_brand_raw": brand_raw,
            "source_name_raw": name_raw,
            "identity_key": flavor_identity_match_key(brand_raw, name_raw),
            "synced_at": now,
        }
        meta_row = dst.execute(
            "SELECT id FROM flavour_reference_meta WHERE flavour_id = ?",
            (flavour_id,),
        ).fetchone()
        if meta_row:
            dst.execute(
                """
                UPDATE flavour_reference_meta
                SET source_name = ?,
                    source_table = 'flavor_reference',
                    source_record_id = ?,
                    source_url = ?,
                    vc_url = ?,
                    reference_payload_json = ?,
                    last_synced_at = ?
                WHERE flavour_id = ?
                """,
                (
                    source_name,
                    int(r["id"]),
                    r.get("url"),
                    r.get("vc_url"),
                    json.dumps(meta_payload),
                    now,
                    flavour_id,
                ),
            )
            updated_meta += 1
        else:
            dst.execute(
                """
                INSERT INTO flavour_reference_meta
                (flavour_id, source_name, source_table, source_record_id, source_url, vc_url, reference_payload_json, last_synced_at)
                VALUES (?, ?, 'flavor_reference', ?, ?, ?, ?, ?)
                """,
                (
                    flavour_id,
                    source_name,
                    int(r["id"]),
                    r.get("url"),
                    r.get("vc_url"),
                    json.dumps(meta_payload),
                    now,
                ),
            )
            created_meta += 1

    # Seed alias table from curated config (non-destructive, repeatable).
    for alias in FLAVOR_NAME_ALIASES:
        alias_raw = str(alias.get("alias_raw") or "").strip()
        brand_canonical = str(alias.get("brand_canonical") or "").strip()
        target_name = str(alias.get("target_flavor_name") or "").strip()
        if not alias_raw or not target_name:
            continue
        target = dst.execute(
            """
            SELECT id
            FROM flavours
            WHERE LOWER(COALESCE(canonical_name, '')) = LOWER(?)
              AND LOWER(COALESCE(brand, '')) = LOWER(?)
            LIMIT 1
            """,
            (target_name, brand_canonical),
        ).fetchone()
        if not target:
            continue
        normalized_alias = normalize_flavor_identity_name(alias_raw)
        before = dst.total_changes
        dst.execute(
            """
            INSERT OR IGNORE INTO flavour_aliases (flavour_id, alias_raw, alias_normalized, source)
            VALUES (?, ?, ?, ?)
            """,
            (int(target["id"]), alias_raw, normalized_alias, "config_seed"),
        )
        if dst.total_changes > before:
            created_aliases += 1

    dst.commit()
    src.close()
    dst.close()
    return {
        "source_db": source_db,
        "target_db": target_db,
        "source_rows_read": len(rows),
        "flavor_reference_created": created_ref,
        "flavor_reference_updated": updated_ref,
        "flavours_created": created_flavours,
        "flavours_updated": updated_flavours,
        "meta_created": created_meta,
        "meta_updated": updated_meta,
        "aliases_created": created_aliases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Lab flavour reference catalog into TVW DB.")
    parser.add_argument("--source-db", default=_default_source_db(), help="Path to source Lab SQLite DB")
    parser.add_argument("--target-db", default=_default_target_db(), help="Path to target TVW SQLite DB")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit for test runs")
    args = parser.parse_args()
    result = sync_flavours(args.source_db, args.target_db, args.limit)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

