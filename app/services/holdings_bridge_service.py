"""Bridge Old Goat's TVW "stash" API to Lab physical holdings (`library_items`).

Source of truth (phase 1):
  - `flavor_reference` is the canonical catalog row (identity).
  - `library_items` rows keyed by `reference_id` are the shelf/stock truth for Lab + OG stash.
  - `flavours.source_flavor_ref_id` maps TVW community flavour ids → that canonical reference.

Old Goat's GET/POST /api/stash reads and writes `library_items` (not `user_stash_items`), so Lab mix
deductions (`finalize_inventory_deduction` / `inventory_repo`) and TVW stash stay aligned.

Community users: unchanged — their stash stays in `user_stash_items`.

Future: optional `user_stash_items` shadow for OG favorites, multi-user holdings, or cross-db sync.
"""

from __future__ import annotations

from typing import Any

from db import get_db


def is_old_goat_user(*, user_id: int | None, role: str | None, username: str | None) -> bool:
    if str(role or "").strip() == "Old Goat":
        return True
    if str(username or "").strip().lower() == "old goat":
        return True
    if not user_id:
        return False
    db = get_db()
    row = db.execute(
        "SELECT id FROM users WHERE id = ? AND (role = 'Old Goat' OR LOWER(username) = 'old goat')",
        (int(user_id),),
    ).fetchone()
    return bool(row)


def _flavour_to_reference_id(flavour_id: int) -> int | None:
    db = get_db()
    row = db.execute(
        "SELECT source_flavor_ref_id FROM flavours WHERE id = ? LIMIT 1",
        (int(flavour_id),),
    ).fetchone()
    if not row:
        return None
    ref_id = row["source_flavor_ref_id"]
    return int(ref_id) if ref_id else None


def _reference_to_flavour_id(reference_id: int) -> int | None:
    db = get_db()
    row = db.execute(
        "SELECT id FROM flavours WHERE source_flavor_ref_id = ? ORDER BY id ASC LIMIT 1",
        (int(reference_id),),
    ).fetchone()
    return int(row["id"]) if row else None


def list_old_goat_shared_holdings() -> list[dict[str, Any]]:
    """One entry per canonical `reference_id`, aggregating duplicate active `library_items` rows."""
    db = get_db()
    rows = db.execute(
        """
        SELECT
            MAX(li.id) AS library_item_id,
            li.reference_id,
            SUM(li.current_stock) AS current_stock,
            CASE WHEN SUM(li.current_stock) > 0.05 THEN 'In Use' ELSE 'Empty' END AS status,
            MAX(COALESCE(li.unit, 'ml')) AS unit,
            MAX(fr.brand) AS brand,
            MAX(fr.name) AS name
        FROM library_items li
        JOIN flavor_reference fr ON fr.id = li.reference_id
        WHERE li.active = 1
        GROUP BY li.reference_id
        ORDER BY MAX(li.id) DESC
        """
    ).fetchall()
    items: list[dict[str, Any]] = []
    for r in rows:
        ref_rid = int(r["reference_id"])
        flavour_id = _reference_to_flavour_id(ref_rid)
        items.append(
            {
                "id": int(r["library_item_id"]),
                "user_id": None,
                "flavour_id": flavour_id,
                "quantity_ml": float(r["current_stock"] or 0.0),
                "is_favorite": 0,
                "last_used_at": None,
                "source": "lab_shared_holdings",
                "reference_id": ref_rid,
                "brand": r["brand"],
                "name": r["name"],
                "status": r["status"],
                "unit": r["unit"],
                **({"tvw_mapped": True} if flavour_id else {"tvw_mapped": False}),
            }
        )
    return items


def _apply_absolute_stock_for_reference(*, reference_id: int, quantity_ml: float) -> dict[str, Any]:
    """Shared implementation for Lab `library_items` upsert by reference id."""
    db = get_db()
    ref_id = int(reference_id)
    qty = float(quantity_ml)
    status = "Empty" if qty <= 0.05 else "In Use"
    rows = db.execute(
        "SELECT id FROM library_items WHERE reference_id = ? AND active = 1 ORDER BY id ASC",
        (int(ref_id),),
    ).fetchall()
    if not rows:
        cur = db.execute(
            """
            INSERT INTO library_items (reference_id, current_stock, unit, low_stock_threshold, status, active)
            VALUES (?, ?, 'ml', 10.0, ?, 1)
            """,
            (int(ref_id), qty, status),
        )
        item_id = int(cur.lastrowid)
    else:
        keep = int(rows[0]["id"])
        db.execute(
            """
            UPDATE library_items
            SET current_stock = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (qty, status, keep),
        )
        for r in rows[1:]:
            db.execute(
                "UPDATE library_items SET active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (int(r["id"]),),
            )
        item_id = keep
    db.commit()
    return {"library_item_id": item_id, "reference_id": int(ref_id), "quantity_ml": qty}


def upsert_old_goat_shared_holding(*, flavour_id: int, quantity_ml: float) -> dict[str, Any]:
    """Set absolute stock (ml) on Lab holdings for this flavour's canonical reference; merge duplicate rows."""
    ref_id = _flavour_to_reference_id(int(flavour_id))
    if not ref_id:
        raise ValueError("Flavour is not mapped to a canonical reference.")
    return _apply_absolute_stock_for_reference(reference_id=int(ref_id), quantity_ml=quantity_ml)


def upsert_old_goat_shared_holding_by_reference_id(*, reference_id: int, quantity_ml: float) -> dict[str, Any]:
    """Lab stock update when only `flavor_reference.id` is known (e.g. catalogue row without a TVW flavour)."""
    db = get_db()
    ref_id = int(reference_id)
    row = db.execute("SELECT 1 FROM flavor_reference WHERE id = ? LIMIT 1", (ref_id,)).fetchone()
    if not row:
        raise ValueError("Unknown flavour reference.")
    return _apply_absolute_stock_for_reference(reference_id=ref_id, quantity_ml=quantity_ml)


def remove_old_goat_shared_holding(*, flavour_id: int) -> dict[str, Any]:
    """Archive all active Lab holdings for this TVW flavour's canonical reference (stash removed on both sides)."""
    db = get_db()
    ref_id = _flavour_to_reference_id(int(flavour_id))
    if not ref_id:
        raise ValueError("Flavour is not mapped to a canonical reference.")
    db.execute(
        """
        UPDATE library_items
        SET active = 0,
            current_stock = 0,
            status = 'Empty',
            updated_at = CURRENT_TIMESTAMP
        WHERE reference_id = ? AND active = 1
        """,
        (int(ref_id),),
    )
    db.commit()
    return {"reference_id": int(ref_id), "archived": True}


def remove_old_goat_shared_holding_by_reference_id(*, reference_id: int) -> dict[str, Any]:
    """Archive Lab holdings by canonical reference id (unmapped catalogue rows)."""
    db = get_db()
    ref_id = int(reference_id)
    row = db.execute("SELECT 1 FROM flavor_reference WHERE id = ? LIMIT 1", (ref_id,)).fetchone()
    if not row:
        raise ValueError("Unknown flavour reference.")
    db.execute(
        """
        UPDATE library_items
        SET active = 0,
            current_stock = 0,
            status = 'Empty',
            updated_at = CURRENT_TIMESTAMP
        WHERE reference_id = ? AND active = 1
        """,
        (ref_id,),
    )
    db.commit()
    return {"reference_id": ref_id, "archived": True}

