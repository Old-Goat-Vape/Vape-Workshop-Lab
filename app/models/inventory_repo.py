from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from db import get_db


def fetch_reference_basic(reference_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    row = db.execute(
        "SELECT id, name, brand, category, price_per_ml, density FROM flavor_reference WHERE id = ?",
        (int(reference_id),),
    ).fetchone()
    return dict(row) if row else None


def fetch_reference_density(reference_id: int) -> Optional[float]:
    db = get_db()
    row = db.execute("SELECT density FROM flavor_reference WHERE id = ?", (int(reference_id),)).fetchone()
    if not row:
        return None
    return float(row["density"] or 0) if "density" in row.keys() else None


def find_active_library_item_by_reference(reference_id: int) -> Optional[int]:
    db = get_db()
    row = db.execute(
        "SELECT id FROM library_items WHERE reference_id = ? AND active = 1 LIMIT 1",
        (int(reference_id),),
    ).fetchone()
    return int(row["id"]) if row else None


def insert_library_item(
    *,
    reference_id: int,
    current_stock: float,
    unit: str = "ml",
    low_stock_threshold: Optional[float] = None,
    status: Optional[str] = None,
    cost_per_unit: Optional[float] = None,
) -> int:
    db = get_db()

    cols = ["reference_id", "current_stock", "unit", "active"]
    vals: List[Any] = [int(reference_id), float(current_stock), unit, 1]
    if low_stock_threshold is not None:
        cols.append("low_stock_threshold")
        vals.append(float(low_stock_threshold))
    if status is not None:
        cols.append("status")
        vals.append(status)
    if cost_per_unit is not None:
        cols.append("cost_per_unit")
        vals.append(float(cost_per_unit))

    placeholders = ", ".join(["?"] * len(cols))
    col_sql = ", ".join(cols)
    cur = db.execute(f"INSERT INTO library_items ({col_sql}) VALUES ({placeholders})", tuple(vals))
    return int(cur.lastrowid)


def update_library_stock_delta_ml(*, reference_id: int, delta_ml: float) -> None:
    db = get_db()
    db.execute(
        """
        UPDATE library_items
        SET current_stock = current_stock + ?
        WHERE reference_id = ?
          AND status = 'In Use'
          AND active = 1
        """,
        (float(delta_ml), int(reference_id)),
    )


def update_library_stock_set(*, item_id: int, new_stock: float, unit: str) -> None:
    db = get_db()
    db.execute(
        "UPDATE library_items SET current_stock = ?, unit = ?, active = 1 WHERE id = ?",
        (float(new_stock), unit, int(item_id)),
    )


def mark_empty_if_below(*, reference_id: int, threshold: float = 0.05) -> None:
    db = get_db()
    db.execute(
        """
        UPDATE library_items
        SET status = 'Empty', current_stock = 0
        WHERE current_stock <= ?
          AND reference_id = ?
          AND active = 1
        """,
        (float(threshold), int(reference_id)),
    )


def select_low_stock(*, threshold_ml: float) -> List[Dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT r.name, r.brand, l.current_stock
        FROM library_items l
        JOIN flavor_reference r ON l.reference_id = r.id
        WHERE l.current_stock < ?
          AND l.status = 'In Use'
          AND l.active = 1
        """,
        (float(threshold_ml),),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_library_cost_per_g(reference_id: int) -> float:
    db = get_db()
    try:
        row = db.execute(
            "SELECT cost_per_g FROM library_items WHERE reference_id = ?",
            (int(reference_id),),
        ).fetchone()
        return float(row["cost_per_g"] or 0.0) if row else 0.0
    except Exception:
        return 0.0


def select_reference_library_dump() -> List[Dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT
            r.id,
            r.name,
            r.brand,
            r.price_per_ml,
            COALESCE(r.avg_usage, 0.0) AS avg_usage,
            COALESCE(fra.recipe_count, 0) AS recipe_count,
            fra.avg_pct AS recipe_avg_usage,
            COALESCE(fra.avg_pct, 1.0) AS active_avg_usage,
            COALESCE(fra.avg_pct, 1.0) AS avg_percent,
            (SELECT id FROM library_items WHERE reference_id = r.id AND active=1 LIMIT 1) as in_stock
        FROM flavor_reference r
        LEFT JOIN flavor_recipe_analytics fra
          ON fra.reference_id = r.id
        """,
    ).fetchall()
    return [dict(r) for r in rows]


def search_reference_with_in_stock(*, query: str, limit: int) -> List[Dict[str, Any]]:
    db = get_db()
    search_term = f"%{(query or '').strip()}%"
    rows = db.execute(
        """
        SELECT r.*,
               COALESCE(r.category, 'Flavor') AS display_category,
               COALESCE(fra.avg_pct, 1.0) AS active_avg_usage,
               fra.avg_pct AS recipe_avg_usage,
               COALESCE(fra.recipe_count, 0) AS recipe_count,
               (SELECT id FROM library_items WHERE reference_id = r.id AND active=1 LIMIT 1) AS in_stock_id
        FROM flavor_reference r
        LEFT JOIN flavor_recipe_analytics fra
          ON fra.reference_id = r.id
        WHERE LOWER(r.name) LIKE LOWER(?)
           OR LOWER(r.brand) LIKE LOWER(?)
        ORDER BY r.brand ASC, r.name ASC
        LIMIT ?
        """,
        (search_term, search_term, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def select_library_index_rows(
    *,
    view_type: str,
    current_cat: str,
    search_query: str,
    include_nicotine: bool,
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[Dict[str, Any]], int]:
    db = get_db()
    params: List[Any] = []
    where_clauses: List[str] = []
    page = max(1, int(page or 1))
    page_size = max(1, min(200, int(page_size or 50)))
    offset = (page - 1) * page_size

    if view_type == "reference":
        select_sql = (
            "SELECT r.id, r.name, r.brand, r.category, r.profiles, r.source, "
            "COALESCE(r.price_per_ml, 0.0) AS price_per_ml, "
            "COALESCE(r.avg_usage, 0.0) AS avg_usage, "
            "COALESCE(fra.avg_pct, 1.0) AS active_avg_usage, "
            "fra.avg_pct AS recipe_avg_usage, "
            "COALESCE(fra.recipe_count, 0) AS recipe_count, "
            "COALESCE(r.rating, 0.0) AS rating, "
            "COALESCE(r.url, '') AS url, "
            "COALESCE(r.vc_url, '') AS vc_url, "
            "COALESCE(r.resolved_profile, '') AS resolved_profile, "
            "COALESCE(r.keyword_primary, '') AS keyword_primary, "
            "COALESCE(r.sweetness_bias, 0) AS sweetness_bias, "
            "COALESCE(r.brightness_bias, 0) AS brightness_bias, "
            "COALESCE(r.harshness_bias, 0) AS harshness_bias, "
            "COALESCE(r.coil_risk, 0) AS coil_risk, "
            "COALESCE(r.confidence, '') AS confidence, "
            "COALESCE(r.override_reason, '') AS override_reason, "
            "COALESCE(r.needs_review, 0) AS needs_review, "
            "(SELECT id FROM library_items WHERE reference_id = r.id AND active=1 LIMIT 1) as in_stock_id "
            "FROM flavor_reference r "
            "LEFT JOIN flavor_recipe_analytics fra ON fra.reference_id = r.id"
        )
        base_where = "r.active = 1"
    else:
        select_sql = (
            "SELECT l.id, l.reference_id, l.current_stock, l.unit, l.low_stock_threshold, l.status, l.active, "
            "r.name, r.brand, r.category, r.profiles, r.url, r.product_url, "
            "r.vc_url, r.source, r.avg_usage, r.rating, r.price_per_ml, r.price_10ml, "
            "COALESCE(fra.avg_pct, 1.0) AS active_avg_usage, "
            "fra.avg_pct AS recipe_avg_usage, "
            "COALESCE(fra.recipe_count, 0) AS recipe_count, "
            "COALESCE(r.needs_review, 0) AS needs_review, "
            "l.id as item_id "
            "FROM library_items l "
            "JOIN flavor_reference r ON l.reference_id = r.id "
            "LEFT JOIN flavor_recipe_analytics fra ON fra.reference_id = r.id"
        )
        base_where = "l.active = 1"

    where_clauses.append(base_where)

    if not include_nicotine:
        where_clauses.append("r.category != 'Nicotine'")

    if search_query:
        where_clauses.append("(r.name LIKE ? OR r.brand LIKE ? OR r.profiles LIKE ?)")
        like = f"%{search_query}%"
        params.extend([like, like, like])
    else:
        where_clauses.append("r.category = ?")
        params.append(current_cat)

    where_sql = " WHERE " + " AND ".join(where_clauses)
    count_sql = (
        "SELECT COUNT(*) AS c FROM flavor_reference r"
        if view_type == "reference"
        else "SELECT COUNT(*) AS c FROM library_items l JOIN flavor_reference r ON l.reference_id = r.id"
    ) + where_sql
    total_row = db.execute(count_sql, params).fetchone()
    total_count = int(total_row["c"] if total_row else 0)

    query = select_sql + where_sql + " ORDER BY r.brand ASC, r.name ASC LIMIT ? OFFSET ?"
    rows = db.execute(query, [*params, page_size, offset]).fetchall()
    return [dict(r) for r in rows], total_count


def select_flavor_detail(*, reference_id: int, include_inventory: bool) -> Optional[Dict[str, Any]]:
    db = get_db()
    row = db.execute(
        """
        SELECT
            r.id,
            r.name,
            r.brand,
            COALESCE(r.category, '') AS category,
            COALESCE(r.profiles, '') AS profiles,
            COALESCE(r.source, '') AS source,
            COALESCE(r.price_per_ml, 0.0) AS price_per_ml,
            COALESCE(r.price_10ml, 0.0) AS price_10ml,
            COALESCE(r.avg_usage, 0.0) AS avg_usage,
            fra.avg_pct AS recipe_avg_usage,
            COALESCE(fra.recipe_count, 0) AS recipe_count,
            COALESCE(fra.avg_pct, 1.0) AS active_avg_usage,
            COALESCE(r.rating, 0.0) AS rating,
            COALESCE(r.url, '') AS url,
            COALESCE(r.vc_url, '') AS vc_url,
            COALESCE(r.description, '') AS description,
            COALESCE(r.usage_notes, '') AS usage_notes,
            COALESCE(r.active, 1) AS active,
            (
                SELECT id
                FROM library_items li2
                WHERE li2.reference_id = r.id
                  AND li2.active = 1
                ORDER BY li2.id DESC
                LIMIT 1
            ) AS in_stock_id,
            (
                SELECT COALESCE(li2.current_stock, 0.0)
                FROM library_items li2
                WHERE li2.reference_id = r.id
                  AND li2.active = 1
                ORDER BY li2.id DESC
                LIMIT 1
            ) AS current_stock,
            (
                SELECT COALESCE(li2.unit, 'ml')
                FROM library_items li2
                WHERE li2.reference_id = r.id
                  AND li2.active = 1
                ORDER BY li2.id DESC
                LIMIT 1
            ) AS stock_unit,
            (
                SELECT COALESCE(li2.status, '')
                FROM library_items li2
                WHERE li2.reference_id = r.id
                  AND li2.active = 1
                ORDER BY li2.id DESC
                LIMIT 1
            ) AS stock_status
        FROM flavor_reference r
        LEFT JOIN flavor_recipe_analytics fra
          ON fra.reference_id = r.id
        WHERE r.id = ?
          AND r.active = 1
        """,
        (int(reference_id),),
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    out["in_stock"] = bool(out.get("in_stock_id"))
    if not include_inventory:
        out["current_stock"] = None
        out["stock_unit"] = ""
        out["stock_status"] = ""
    return out


def update_reference_category(reference_id: int, category: str) -> None:
    db = get_db()
    db.execute("UPDATE flavor_reference SET category = ? WHERE id = ?", (category, int(reference_id)))


def select_active_references_for_reclassify() -> List[Dict[str, Any]]:
    db = get_db()
    rows = db.execute("SELECT id, name, brand, category FROM flavor_reference WHERE active = 1").fetchall()
    return [dict(r) for r in rows]


def fetch_library_item_with_category(item_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    row = db.execute(
        """
        SELECT l.id, r.category, l.current_stock, r.name
        FROM library_items l JOIN flavor_reference r ON l.reference_id = r.id
        WHERE l.id = ?
        """,
        (int(item_id),),
    ).fetchone()
    return dict(row) if row else None


def fetch_reference_id_by_brand_name(*, brand: str, name: str) -> Optional[int]:
    db = get_db()
    row = db.execute(
        "SELECT id FROM flavor_reference WHERE brand = ? AND name = ?",
        (brand, name),
    ).fetchone()
    return int(row["id"]) if row else None


def insert_reference(*, brand: str, name: str, category: str) -> int:
    from config.canonical_library_brands import canonical_library_brand, preferred_flavor_storage_name

    db = get_db()
    canon_b = canonical_library_brand(brand or "", name or "")
    storage_n = preferred_flavor_storage_name(name or "") or (name or "").strip()
    cur = db.execute(
        "INSERT INTO flavor_reference (brand, name, category, active) VALUES (?, ?, ?, 1)",
        (canon_b, storage_n, category),
    )
    return int(cur.lastrowid)


def fetch_library_item_reference_id(item_id: int) -> Optional[int]:
    db = get_db()
    row = db.execute("SELECT reference_id FROM library_items WHERE id = ?", (int(item_id),)).fetchone()
    if not row:
        return None
    # Row may be tuple-like depending on row_factory usage
    return int(row[0]) if 0 in range(len(row)) else int(row["reference_id"])


def update_reference_fields(*, reference_id: int, name: str, brand: str, category: str) -> None:
    db = get_db()
    db.execute(
        "UPDATE flavor_reference SET name = ?, brand = ?, category = ? WHERE id = ?",
        (name, brand, category, int(reference_id)),
    )


def update_library_item_fields(*, item_id: int, current_stock: float, unit: str) -> None:
    db = get_db()
    db.execute(
        "UPDATE library_items SET current_stock = ?, unit = ?, active = 1 WHERE id = ?",
        (float(current_stock), unit, int(item_id)),
    )


def update_library_item_cost_per_g(*, item_id: int, cost_per_g: float) -> None:
    db = get_db()
    db.execute("UPDATE library_items SET cost_per_g = ? WHERE id = ?", (cost_per_g, int(item_id)))


def select_edit_item_view(item_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    row = db.execute(
        """
        SELECT l.*, r.name, r.brand, r.category
        FROM library_items l
        JOIN flavor_reference r ON l.reference_id = r.id
        WHERE l.id = ?
        """,
        (int(item_id),),
    ).fetchone()
    return dict(row) if row else None


def merge_items(*, source_id: int, target_id: int) -> Tuple[int, int, float]:
    """Returns (source_ref_id, target_ref_id, source_stock)."""
    db = get_db()
    source = db.execute("SELECT current_stock, reference_id FROM library_items WHERE id = ?", (int(source_id),)).fetchone()
    target = db.execute("SELECT current_stock, reference_id FROM library_items WHERE id = ?", (int(target_id),)).fetchone()
    if not source or not target:
        raise ValueError("Source or target not found.")

    source_stock = float(source["current_stock"])
    source_ref = int(source["reference_id"])
    target_ref = int(target["reference_id"])

    db.execute("UPDATE library_items SET current_stock = current_stock + ? WHERE id = ?", (source_stock, int(target_id)))
    db.execute("UPDATE recipe_items SET ingredient_id = ? WHERE ingredient_id = ?", (target_ref, source_ref))
    db.execute("UPDATE library_items SET active = 0 WHERE id = ?", (int(source_id),))

    return source_ref, target_ref, source_stock


def archive_item(item_id: int) -> None:
    db = get_db()
    db.execute("UPDATE library_items SET active = 0 WHERE id = ?", (int(item_id),))

