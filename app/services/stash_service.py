"""TVW stash: member `user_stash_items` vs Old Goat Lab bridge — helpers for API + stash page."""

from __future__ import annotations

from typing import Any

from db import get_db
from services import holdings_bridge_service


def upsert_user_stash_item(
    *,
    user_id: int,
    flavour_id: int,
    quantity_ml: float,
    is_favorite: bool,
    last_used_at: str,
) -> int:
    """One logical row per (user, flavour): dedupe legacy duplicates, then insert or update."""
    db = get_db()
    uid = int(user_id)
    fid = int(flavour_id)
    qty = float(quantity_ml)
    fav = 1 if is_favorite else 0
    rows = db.execute(
        "SELECT id FROM user_stash_items WHERE user_id = ? AND flavour_id = ? ORDER BY id ASC",
        (uid, fid),
    ).fetchall()
    if len(rows) > 1:
        for r in rows[1:]:
            db.execute("DELETE FROM user_stash_items WHERE id = ?", (int(r["id"]),))
    if rows:
        sid = int(rows[0]["id"])
        db.execute(
            """
            UPDATE user_stash_items
            SET quantity_ml = ?, is_favorite = ?, last_used_at = ?
            WHERE id = ?
            """,
            (qty, fav, last_used_at, sid),
        )
        db.commit()
        return sid
    cur = db.execute(
        """
        INSERT INTO user_stash_items (user_id, flavour_id, quantity_ml, is_favorite, last_used_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (uid, fid, qty, fav, last_used_at),
    )
    db.commit()
    return int(cur.lastrowid)


def delete_all_user_stash_for_flavour(*, user_id: int, flavour_id: int) -> int:
    db = get_db()
    cur = db.execute(
        "DELETE FROM user_stash_items WHERE user_id = ? AND flavour_id = ?",
        (int(user_id), int(flavour_id)),
    )
    db.commit()
    return int(cur.rowcount or 0)


def fetch_member_stash_aggregated(*, user_id: int) -> list[dict[str, Any]]:
    """Single query: one row per flavour with summed quantity (legacy duplicate-safe)."""
    db = get_db()
    rows = db.execute(
        """
        SELECT MIN(s.id) AS stash_item_id,
               s.flavour_id,
               SUM(s.quantity_ml) AS quantity_ml,
               MAX(s.is_favorite) AS is_favorite,
               MAX(s.last_used_at) AS last_used_at,
               MAX(f.canonical_name) AS canonical_name,
               MAX(f.brand) AS brand,
               MAX(f.source_flavor_ref_id) AS source_flavor_ref_id
        FROM user_stash_items s
        JOIN flavours f ON f.id = s.flavour_id
        WHERE s.user_id = ?
        GROUP BY s.flavour_id
        ORDER BY MAX(s.id) DESC
        """,
        (int(user_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def build_stash_page_rows(
    *,
    user_id: int | None,
    role: str | None,
    username: str | None,
    search: str = "",
    sort_by: str = "name",
    sort_dir: str = "asc",
) -> tuple[list[dict[str, Any]], str, bool]:
    """Returns (display_rows, stash_source_key, is_old_goat)."""
    search_l = (search or "").strip().lower()
    sb = (sort_by or "name").strip().lower()
    sd = "desc" if (sort_dir or "asc").strip().lower() == "desc" else "asc"
    is_old_goat = holdings_bridge_service.is_old_goat_user(user_id=user_id, role=role, username=username)

    rows: list[dict[str, Any]] = []
    if is_old_goat:
        source = "lab_shared_holdings"
        for it in holdings_bridge_service.list_old_goat_shared_holdings():
            rid = int(it["reference_id"])
            fid = it.get("flavour_id")
            fid_i = int(fid) if fid is not None else None
            display = (it.get("name") or "").strip() or "—"
            brand = (it.get("brand") or "").strip() or "—"
            rows.append(
                {
                    "stash_item_id": None,
                    "flavour_id": fid_i,
                    "reference_id": rid,
                    "display_name": display,
                    "brand": brand,
                    "quantity_ml": float(it.get("quantity_ml") or 0.0),
                    "unit": str(it.get("unit") or "ml"),
                    "is_favorite": False,
                    "tvw_mapped": bool(it.get("tvw_mapped", True)),
                    "lab_status": str(it.get("status") or ""),
                    "last_used_at": None,
                    "source": source,
                }
            )
    else:
        source = "user_stash_items"
        if not user_id:
            return [], source, False
        for r in fetch_member_stash_aggregated(user_id=int(user_id)):
            display = (r.get("canonical_name") or "").strip() or "—"
            brand = (r.get("brand") or "").strip() or "—"
            ref = r.get("source_flavor_ref_id")
            rows.append(
                {
                    "stash_item_id": int(r["stash_item_id"]) if r.get("stash_item_id") is not None else None,
                    "flavour_id": int(r["flavour_id"]),
                    "reference_id": int(ref) if ref is not None else None,
                    "display_name": display,
                    "brand": brand,
                    "quantity_ml": float(r.get("quantity_ml") or 0.0),
                    "unit": "ml",
                    "is_favorite": bool(int(r.get("is_favorite") or 0)),
                    "tvw_mapped": True,
                    "lab_status": None,
                    "last_used_at": r.get("last_used_at"),
                    "source": source,
                }
            )

    if search_l:
        rows = [
            r
            for r in rows
            if search_l in (r["display_name"] or "").lower()
            or search_l in (r["brand"] or "").lower()
            or (str(r.get("reference_id") or "") and search_l in str(r["reference_id"]))
        ]

    sort_keys = {
        "name": lambda r: (r["display_name"] or "").lower(),
        "brand": lambda r: (r["brand"] or "").lower(),
        "quantity": lambda r: float(r.get("quantity_ml") or 0.0),
        "reference": lambda r: int(r.get("reference_id") or 0),
    }
    key_fn = sort_keys.get(sb, sort_keys["name"])
    rev = sd == "desc"
    rows.sort(
        key=lambda r: (key_fn(r), (r["display_name"] or "").lower(), int(r.get("reference_id") or 0)),
        reverse=rev,
    )

    return rows, source, is_old_goat


def fetch_stash_quick_add_options(*, limit: int = 800) -> list[dict[str, Any]]:
    """Lightweight flavour picker rows for stash quick-add UX."""
    db = get_db()
    rows = db.execute(
        """
        SELECT id, canonical_name, brand, source_flavor_ref_id
        FROM flavours
        WHERE COALESCE(merged_into_flavour_id, 0) = 0
          AND COALESCE(is_hidden, 0) = 0
          AND COALESCE(is_invalid, 0) = 0
        ORDER BY brand COLLATE NOCASE ASC, canonical_name COLLATE NOCASE ASC
        LIMIT ?
        """,
        (max(50, min(int(limit or 800), 2000)),),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_stash_binding_for_flavour(
    *,
    user_id: int | None,
    role: str | None,
    username: str | None,
    flavour_id: int,
) -> dict[str, Any]:
    """Current stash state for one flavour id, respecting OG linked holdings split."""
    db = get_db()
    fid = int(flavour_id)
    is_old_goat = holdings_bridge_service.is_old_goat_user(user_id=user_id, role=role, username=username)
    if is_old_goat:
        frow = db.execute(
            "SELECT source_flavor_ref_id FROM flavours WHERE id = ? LIMIT 1",
            (fid,),
        ).fetchone()
        ref_id = int(frow["source_flavor_ref_id"]) if frow and frow["source_flavor_ref_id"] else None
        qty = 0.0
        if ref_id:
            qrow = db.execute(
                """
                SELECT COALESCE(SUM(current_stock), 0.0) AS qty
                FROM library_items
                WHERE reference_id = ? AND active = 1
                """,
                (int(ref_id),),
            ).fetchone()
            qty = float((qrow["qty"] if qrow else 0.0) or 0.0)
        return {
            "source": "lab_shared_holdings",
            "is_lab_linked": True,
            "flavour_id": fid,
            "reference_id": ref_id,
            "quantity_ml": float(qty),
            "is_favorite": False,
        }

    if not user_id:
        return {
            "source": "user_stash_items",
            "is_lab_linked": False,
            "flavour_id": fid,
            "reference_id": None,
            "quantity_ml": 0.0,
            "is_favorite": False,
        }
    row = db.execute(
        """
        SELECT COALESCE(SUM(quantity_ml), 0.0) AS quantity_ml,
               MAX(COALESCE(is_favorite, 0)) AS is_favorite
        FROM user_stash_items
        WHERE user_id = ? AND flavour_id = ?
        """,
        (int(user_id), fid),
    ).fetchone()
    frow = db.execute(
        "SELECT source_flavor_ref_id FROM flavours WHERE id = ? LIMIT 1",
        (fid,),
    ).fetchone()
    return {
        "source": "user_stash_items",
        "is_lab_linked": False,
        "flavour_id": fid,
        "reference_id": int(frow["source_flavor_ref_id"]) if frow and frow["source_flavor_ref_id"] else None,
        "quantity_ml": float((row["quantity_ml"] if row else 0.0) or 0.0),
        "is_favorite": bool(int((row["is_favorite"] if row else 0) or 0)),
    }
