"""Canonical BLCK purchase variants for Lab base/nicotine costing."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from config.blck_canonical_purchases import CANONICAL_PURCHASES, purchases_by_role
from db import get_db
from scrapers.blck_variant_normalize import variant_matches_criteria


def ensure_canonical_rows(db=None) -> None:
    db = db or get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for spec in CANONICAL_PURCHASES:
        db.execute(
            """
            INSERT INTO costing_canonical_variants (role, label, source, source_product_id, source_variant_id, status, updated_at)
            VALUES (?, ?, 'BLCK', ?, ?, 'pending', ?)
            ON CONFLICT(role) DO UPDATE SET
                label = excluded.label,
                updated_at = excluded.updated_at
            """,
            (
                spec["role"],
                spec["label"],
                str(spec.get("preferred_source_product_id") or ""),
                str(spec.get("preferred_source_variant_id") or ""),
                now,
            ),
        )
    db.commit()


def _find_variant_row(db, *, source_variant_id: str) -> Optional[Dict[str, Any]]:
    row = db.execute(
        """
        SELECT * FROM blck_product_variants
        WHERE source = 'BLCK' AND source_variant_id = ?
        LIMIT 1
        """,
        (str(source_variant_id),),
    ).fetchone()
    return dict(row) if row else None


def _fallback_match(db, spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    match = spec.get("match") or {}
    rows = db.execute(
        """
        SELECT * FROM blck_product_variants
        WHERE source = 'BLCK'
        ORDER BY id ASC
        """
    ).fetchall()
    title_needles = [str(x).lower() for x in (match.get("product_title_contains") or [])]
    for row in rows:
        d = dict(row)
        title = str(d.get("product_title") or "").lower()
        if title_needles and not any(n in title for n in title_needles):
            continue
        fields = {
            "pack_volume_ml": d.get("pack_volume_ml"),
            "nicotine_strength_mg_ml": d.get("nicotine_strength_mg_ml"),
            "carrier": d.get("carrier"),
            "nicotine_type": d.get("nicotine_type"),
        }
        if variant_matches_criteria(
            fields,
            pack_volume_ml=match.get("pack_volume_ml"),
            nicotine_strength_mg_ml=match.get("nicotine_strength_mg_ml"),
            carrier=match.get("carrier"),
            nicotine_type=match.get("nicotine_type"),
        ):
            return d
    return None


def sync_canonical_bindings(db=None) -> Dict[str, Any]:
    """Attach canonical roles to scraped Shopify variant ids; flag unavailable."""
    db = db or get_db()
    ensure_canonical_rows(db)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report: Dict[str, Any] = {"bound": [], "unavailable": []}

    for spec in CANONICAL_PURCHASES:
        role = str(spec["role"])
        preferred = str(spec.get("preferred_source_variant_id") or "")
        row = _find_variant_row(db, source_variant_id=preferred) if preferred else None
        if not row:
            row = _fallback_match(db, spec)
        if not row:
            db.execute(
                """
                UPDATE costing_canonical_variants
                SET status = 'unavailable',
                    unavailable_reason = ?,
                    updated_at = ?
                WHERE role = ?
                """,
                ("Configured costing product unavailable — variant not found in latest BLCK scrape", now, role),
            )
            report["unavailable"].append(role)
            continue

        available = int(row.get("available") or 0) == 1
        if not available:
            db.execute(
                """
                UPDATE costing_canonical_variants
                SET source_product_id = ?,
                    source_variant_id = ?,
                    status = 'unavailable',
                    unavailable_reason = ?,
                    updated_at = ?
                WHERE role = ?
                """,
                (
                    str(row.get("source_product_id") or ""),
                    str(row.get("source_variant_id") or ""),
                    "Configured costing product unavailable — Shopify variant marked unavailable",
                    now,
                    role,
                ),
            )
            report["unavailable"].append(role)
            continue

        db.execute(
            """
            UPDATE costing_canonical_variants
            SET source_product_id = ?,
                source_variant_id = ?,
                status = 'active',
                unavailable_reason = NULL,
                updated_at = ?
            WHERE role = ?
            """,
            (
                str(row.get("source_product_id") or ""),
                str(row.get("source_variant_id") or ""),
                now,
                role,
            ),
        )
        report["bound"].append(
            {
                "role": role,
                "source_variant_id": str(row.get("source_variant_id")),
                "pack_price": row.get("pack_price"),
                "price_per_ml": row.get("price_per_ml"),
            }
        )

    db.commit()
    return report


def get_canonical_costing_snapshot(db=None) -> Dict[str, Any]:
    """Live prices for configured purchase variants (no hard-coded Rand values)."""
    db = db or get_db()
    ensure_canonical_rows(db)
    out: Dict[str, Any] = {"roles": {}, "unavailable": [], "generated_at": datetime.now().isoformat(timespec="seconds")}
    by_role = purchases_by_role()
    rows = db.execute("SELECT * FROM costing_canonical_variants").fetchall()
    for crow in rows:
        c = dict(crow)
        role = str(c.get("role") or "")
        spec = by_role.get(role) or {}
        vid = str(c.get("source_variant_id") or "")
        variant = _find_variant_row(db, source_variant_id=vid) if vid else None
        status = str(c.get("status") or "pending")
        unavailable = status != "active" or not variant or int((variant or {}).get("available") or 0) != 1
        if unavailable and role not in out["unavailable"]:
            out["unavailable"].append(role)
        entry = {
            "role": role,
            "label": c.get("label") or spec.get("label") or role,
            "status": "unavailable" if unavailable else "active",
            "unavailable_reason": c.get("unavailable_reason")
            if unavailable
            else None,
            "source": "BLCK",
            "source_product_id": (variant or {}).get("source_product_id") or c.get("source_product_id"),
            "source_variant_id": (variant or {}).get("source_variant_id") or vid,
            "product_title": (variant or {}).get("product_title"),
            "variant_title": (variant or {}).get("variant_title"),
            "option1": (variant or {}).get("option1"),
            "option2": (variant or {}).get("option2"),
            "option3": (variant or {}).get("option3"),
            "pack_volume_ml": (variant or {}).get("pack_volume_ml"),
            "nicotine_strength_mg_ml": (variant or {}).get("nicotine_strength_mg_ml"),
            "carrier": (variant or {}).get("carrier"),
            "nicotine_type": (variant or {}).get("nicotine_type"),
            "pack_price": (variant or {}).get("pack_price"),
            "price_per_ml": (variant or {}).get("price_per_ml"),
            "product_url": (variant or {}).get("product_url"),
            "last_scraped": (variant or {}).get("last_scraped"),
            "available": int((variant or {}).get("available") or 0) == 1 if variant else False,
        }
        out["roles"][role] = entry
    return out


def resolve_nicotine_price_per_ml(*, kind: str, db=None) -> Optional[Dict[str, Any]]:
    snap = get_canonical_costing_snapshot(db=db)
    role = "salt_nic" if kind == "salt" else "freebase_nic"
    entry = snap["roles"].get(role)
    if not entry or entry.get("status") != "active":
        return None
    ppm = entry.get("price_per_ml")
    try:
        if ppm is None or float(ppm) <= 0:
            return None
    except (TypeError, ValueError):
        return None
    return entry


def compute_base_liquid_breakdown(
    *,
    effective_batch_ml: float,
    flavour_percent_total: float,
    nicotine_percent: float = 0.0,
    vg_ratio: float = 70.0,
    pg_ratio: float = 30.0,
    db=None,
) -> Dict[str, Any]:
    """Split remaining non-flavour volume across VG/PG using canonical BLCK prices."""
    snap = get_canonical_costing_snapshot(db=db)
    vg = snap["roles"].get("vg") or {}
    pg = snap["roles"].get("pg") or {}
    warnings: List[str] = []
    for role, entry in (("vg", vg), ("pg", pg)):
        if entry.get("status") != "active":
            warnings.append(
                f"Configured costing product unavailable ({role}): "
                f"{entry.get('unavailable_reason') or 'missing variant'}"
            )

    batch = max(0.0, float(effective_batch_ml or 0.0))
    flav_pct = max(0.0, float(flavour_percent_total or 0.0))
    nic_pct = max(0.0, float(nicotine_percent or 0.0))
    # Remainder after flavour concentrates (nicotine usually counted in remainder / separate).
    base_pct = max(0.0, 100.0 - flav_pct)
    # If nicotine is already an ingredient %, do not double-count it in PG/VG.
    pg_vg_pct = max(0.0, base_pct - nic_pct)
    ratio_sum = max(0.01, float(vg_ratio or 0.0) + float(pg_ratio or 0.0))
    vg_share = float(vg_ratio or 0.0) / ratio_sum
    pg_share = float(pg_ratio or 0.0) / ratio_sum
    vg_ml = batch * (pg_vg_pct / 100.0) * vg_share
    pg_ml = batch * (pg_vg_pct / 100.0) * pg_share

    def _cost(entry: Dict[str, Any], ml: float) -> float:
        if entry.get("status") != "active":
            return 0.0
        try:
            return float(entry.get("price_per_ml") or 0.0) * ml
        except (TypeError, ValueError):
            return 0.0

    vg_cost = _cost(vg, vg_ml)
    pg_cost = _cost(pg, pg_ml)
    total = vg_cost + pg_cost
    blended = (total / batch) if batch > 0 else 0.0

    return {
        "vg": {**vg, "recipe_ml": round(vg_ml, 4), "line_cost": round(vg_cost, 4)},
        "pg": {**pg, "recipe_ml": round(pg_ml, 4), "line_cost": round(pg_cost, 4)},
        "total_vg_cost": round(vg_cost, 4),
        "total_pg_cost": round(pg_cost, 4),
        "total_base_cost": round(total, 4),
        "blended_base_cost_per_ml": round(blended, 6),
        "warnings": warnings,
        "snapshot": snap,
    }
