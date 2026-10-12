from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from db import get_db


def _existing_columns(table: str) -> set[str]:
    db = get_db()
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def insert_mix_history(
    *,
    batch_number: Optional[str],
    recipe_id: int,
    version_id: int,
    user_id: int,
    supervisor_id: Optional[int],
    total_volume_actual: float,
    style: Optional[str] = None,
    session_token: Optional[str] = None,
    ready_for_sale: Optional[int] = None,
    safety_json: Optional[str] = None,
    total_cost: Optional[float] = None,
    vessel_size: Optional[float] = None,
    mix_mode: Optional[str] = None,
    source_recipe_mode: Optional[str] = None,
    target_mix_mode: Optional[str] = None,
    formula_origin: Optional[str] = None,
    formula_snapshot_json: Optional[str] = None,
    stock_override_used: Optional[int] = None,
    stock_override_reason: Optional[str] = None,
    stock_override_detail_json: Optional[str] = None,
) -> int:
    db = get_db()
    cols_exist = _existing_columns("mix_history")

    cols = [
        "batch_number",
        "recipe_id",
        "version_id",
        "user_id",
        "supervisor_id",
        "total_volume_actual",
    ]
    vals: List[Any] = [batch_number, recipe_id, version_id, user_id, supervisor_id, total_volume_actual]

    # These columns exist in some DBs; keep insertion flexible.
    if style is not None and "style" in cols_exist:
        cols.append("style")
        vals.append(style)
    if session_token is not None and "session_token" in cols_exist:
        cols.append("session_token")
        vals.append(session_token)
    if ready_for_sale is not None and "ready_for_sale" in cols_exist:
        cols.append("ready_for_sale")
        vals.append(ready_for_sale)
    if safety_json is not None and "safety_json" in cols_exist:
        cols.append("safety_json")
        vals.append(safety_json)
    if total_cost is not None and "total_cost" in cols_exist:
        cols.append("total_cost")
        vals.append(total_cost)
    if vessel_size is not None and "vessel_size" in cols_exist:
        cols.append("vessel_size")
        vals.append(vessel_size)
    if mix_mode is not None and "mix_mode" in cols_exist:
        cols.append("mix_mode")
        vals.append(mix_mode)
    if source_recipe_mode is not None and "source_recipe_mode" in cols_exist:
        cols.append("source_recipe_mode")
        vals.append(source_recipe_mode)
    if target_mix_mode is not None and "target_mix_mode" in cols_exist:
        cols.append("target_mix_mode")
        vals.append(target_mix_mode)
    if formula_origin is not None and "formula_origin" in cols_exist:
        cols.append("formula_origin")
        vals.append(formula_origin)
    if formula_snapshot_json is not None and "formula_snapshot_json" in cols_exist:
        cols.append("formula_snapshot_json")
        vals.append(formula_snapshot_json)
    if stock_override_used is not None and "stock_override_used" in cols_exist:
        cols.append("stock_override_used")
        vals.append(stock_override_used)
    if stock_override_reason is not None and "stock_override_reason" in cols_exist:
        cols.append("stock_override_reason")
        vals.append(stock_override_reason)
    if stock_override_detail_json is not None and "stock_override_detail_json" in cols_exist:
        cols.append("stock_override_detail_json")
        vals.append(stock_override_detail_json)

    placeholders = ", ".join(["?"] * len(cols))
    col_sql = ", ".join(cols)

    cur = db.execute(
        f"INSERT INTO mix_history ({col_sql}) VALUES ({placeholders})",
        tuple(vals),
    )
    return int(cur.lastrowid)


def insert_batch_ingredient(*, batch_id: int, reference_id: int, actual_grams: float) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO batch_ingredients (batch_id, reference_id, actual_grams)
        VALUES (?, ?, ?)
        """,
        (batch_id, reference_id, actual_grams),
    )


def insert_mix_history_item(
    *,
    batch_id: int,
    ingredient_id: int,
    measured_grams: float,
    target_grams: Optional[float],
    cost_at_time: float,
    delta_grams: Optional[float] = None,
) -> None:
    db = get_db()
    # Some legacy DBs may not have this table; migrations create it, but keep safety net.
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS mix_history_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            ingredient_id INTEGER NOT NULL,
            measured_grams REAL NOT NULL,
            target_grams REAL,
            delta_grams REAL,
            cost_at_time REAL DEFAULT 0.0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cols_exist = _existing_columns("mix_history_items")
    if "delta_grams" in cols_exist:
        db.execute(
            """
            INSERT INTO mix_history_items (batch_id, ingredient_id, measured_grams, target_grams, delta_grams, cost_at_time)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (batch_id, ingredient_id, measured_grams, target_grams, delta_grams, cost_at_time),
        )
    else:
        db.execute(
            """
            INSERT INTO mix_history_items (batch_id, ingredient_id, measured_grams, target_grams, cost_at_time)
            VALUES (?, ?, ?, ?, ?)
            """,
            (batch_id, ingredient_id, measured_grams, target_grams, cost_at_time),
        )


def list_mix_history() -> List[Dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT
            mh.*,
            mh.total_volume_actual as batch_size,
            COALESCE(mh.total_cost, 0.0) as total_cost,
            CASE WHEN mh.total_volume_actual > 0 THEN COALESCE(mh.total_cost, 0.0) / mh.total_volume_actual ELSE 0 END as cost_per_ml,
            (
                SELECT GROUP_CONCAT(
                    COALESCE(
                        CASE
                            WHEN mhi.ingredient_id = 0 THEN 'PG'
                            WHEN mhi.ingredient_id = -1 THEN 'VG'
                            ELSE (fr.brand || ' ' || fr.name)
                        END,
                        'Unknown'
                    )
                    || ' ' || printf('%.2fg', mhi.measured_grams),
                    ' | '
                )
                FROM mix_history_items mhi
                LEFT JOIN flavor_reference fr ON fr.id = mhi.ingredient_id
                WHERE mhi.batch_id = mh.id
            ) as ingredient_breakdown,
            r.name as recipe_name,
            rv.version_number,
            u.username,
            s.username as supervisor_name
        FROM mix_history mh
        JOIN recipes r ON mh.recipe_id = r.id
        JOIN recipe_versions rv ON mh.version_id = rv.id
        JOIN users u ON mh.user_id = u.id
        LEFT JOIN users s ON mh.supervisor_id = s.id
        ORDER BY mh.created_at DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]

