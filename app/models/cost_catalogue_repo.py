from __future__ import annotations

from typing import Any, Dict, List, Optional

from db import get_db


def ensure_cost_override_tables() -> None:
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS recipe_cost_ingredient_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            version_id INTEGER NOT NULL,
            ingredient_id INTEGER NOT NULL,
            catalogue_reference_id INTEGER,
            manual_price_per_ml REAL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(recipe_id, version_id, ingredient_id),
            FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE,
            FOREIGN KEY(version_id) REFERENCES recipe_versions(id) ON DELETE CASCADE,
            FOREIGN KEY(ingredient_id) REFERENCES flavor_reference(id),
            FOREIGN KEY(catalogue_reference_id) REFERENCES flavor_reference(id)
        );
        CREATE INDEX IF NOT EXISTS idx_recipe_cost_overrides_lookup
            ON recipe_cost_ingredient_overrides(recipe_id, version_id, ingredient_id);
        """
    )


def fetch_all_catalogue_for_costing() -> List[Dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT id, brand, name, COALESCE(price_per_ml, 0.0) AS price_per_ml,
               COALESCE(category, 'Flavor') AS category,
               COALESCE(is_nicotine, 0) AS is_nicotine,
               COALESCE(active, 1) AS active
        FROM flavor_reference
        WHERE COALESCE(active, 1) = 1
        ORDER BY brand COLLATE NOCASE ASC, name COLLATE NOCASE ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def search_catalogue_for_costing(*, query: str, limit: int = 50) -> List[Dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return fetch_all_catalogue_for_costing()[:limit]
    like = f"%{q}%"
    db = get_db()
    rows = db.execute(
        """
        SELECT id, brand, name, COALESCE(price_per_ml, 0.0) AS price_per_ml,
               COALESCE(category, 'Flavor') AS category,
               COALESCE(is_nicotine, 0) AS is_nicotine,
               COALESCE(active, 1) AS active
        FROM flavor_reference
        WHERE COALESCE(active, 1) = 1
          AND (LOWER(COALESCE(brand, '')) LIKE LOWER(?)
               OR LOWER(COALESCE(name, '')) LIKE LOWER(?)
               OR LOWER(COALESCE(brand, '') || ' ' || COALESCE(name, '')) LIKE LOWER(?))
        ORDER BY
          CASE WHEN COALESCE(price_per_ml, 0) > 0 THEN 0 ELSE 1 END,
          brand COLLATE NOCASE ASC,
          name COLLATE NOCASE ASC
        LIMIT ?
        """,
        (like, like, like, max(1, min(int(limit or 50), 200))),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_cost_overrides_for_version(*, recipe_id: int, version_id: int) -> Dict[int, Dict[str, Any]]:
    ensure_cost_override_tables()
    db = get_db()
    rows = db.execute(
        """
        SELECT ingredient_id, catalogue_reference_id, manual_price_per_ml
        FROM recipe_cost_ingredient_overrides
        WHERE recipe_id = ? AND version_id = ?
        """,
        (int(recipe_id), int(version_id)),
    ).fetchall()
    out: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        iid = int(r["ingredient_id"])
        out[iid] = {
            "catalogue_reference_id": int(r["catalogue_reference_id"]) if r["catalogue_reference_id"] is not None else None,
            "manual_price_per_ml": float(r["manual_price_per_ml"]) if r["manual_price_per_ml"] is not None else None,
        }
    return out


def upsert_catalogue_override(
    *,
    recipe_id: int,
    version_id: int,
    ingredient_id: int,
    catalogue_reference_id: int,
) -> None:
    ensure_cost_override_tables()
    db = get_db()
    db.execute(
        """
        INSERT INTO recipe_cost_ingredient_overrides
            (recipe_id, version_id, ingredient_id, catalogue_reference_id, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(recipe_id, version_id, ingredient_id) DO UPDATE SET
            catalogue_reference_id = excluded.catalogue_reference_id,
            updated_at = CURRENT_TIMESTAMP
        """,
        (int(recipe_id), int(version_id), int(ingredient_id), int(catalogue_reference_id)),
    )
    db.commit()


def upsert_manual_price_override(
    *,
    recipe_id: int,
    version_id: int,
    ingredient_id: int,
    manual_price_per_ml: Optional[float],
) -> None:
    ensure_cost_override_tables()
    db = get_db()
    if manual_price_per_ml is None:
        db.execute(
            """
            UPDATE recipe_cost_ingredient_overrides
            SET manual_price_per_ml = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE recipe_id = ? AND version_id = ? AND ingredient_id = ?
            """,
            (int(recipe_id), int(version_id), int(ingredient_id)),
        )
        db.commit()
        return
    db.execute(
        """
        INSERT INTO recipe_cost_ingredient_overrides
            (recipe_id, version_id, ingredient_id, manual_price_per_ml, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(recipe_id, version_id, ingredient_id) DO UPDATE SET
            manual_price_per_ml = excluded.manual_price_per_ml,
            updated_at = CURRENT_TIMESTAMP
        """,
        (int(recipe_id), int(version_id), int(ingredient_id), float(manual_price_per_ml)),
    )
    db.commit()


def clear_manual_price_override(*, recipe_id: int, version_id: int, ingredient_id: int) -> None:
    upsert_manual_price_override(
        recipe_id=recipe_id,
        version_id=version_id,
        ingredient_id=ingredient_id,
        manual_price_per_ml=None,
    )


def fetch_catalogue_row(catalogue_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    row = db.execute(
        """
        SELECT id, brand, name, COALESCE(price_per_ml, 0.0) AS price_per_ml,
               COALESCE(category, 'Flavor') AS category,
               COALESCE(is_nicotine, 0) AS is_nicotine,
               COALESCE(active, 1) AS active
        FROM flavor_reference WHERE id = ?
        """,
        (int(catalogue_id),),
    ).fetchone()
    return dict(row) if row else None
