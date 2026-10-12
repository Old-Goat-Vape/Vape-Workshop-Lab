from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from db import get_db


def tvw_recipe_public_list_predicate_sql(alias: str = "r") -> str:
    """SQL fragment: recipe rows visible to non-owners in community lists/feeds."""
    a = alias
    return (
        f"COALESCE({a}.active, 1) = 1 AND COALESCE({a}.is_hidden, 0) = 0 AND "
        f"LOWER(COALESCE({a}.moderation_status, 'active')) NOT IN ('hidden', 'archived', 'removed') AND "
        f"LOWER(COALESCE({a}.visibility, 'private')) = 'public'"
    )


def recipe_dict_visible_in_public_lists(recipe: dict | None) -> bool:
    if not recipe:
        return False
    if int(recipe.get("active") or 1) != 1:
        return False
    if int(recipe.get("is_hidden") or 0) == 1:
        return False
    mod = str(recipe.get("moderation_status") or "active").strip().lower()
    if mod in ("hidden", "archived", "removed"):
        return False
    vis = str(recipe.get("visibility") or "private").strip().lower()
    if vis != "public":
        return False
    return True


def tvw_community_content_visible_sql(alias: str) -> str:
    """UGC (notes/reviews/feedback) visible in community: not hidden and moderation_status active."""
    a = alias
    return (
        f"(COALESCE({a}.is_hidden, 0) = 0 AND "
        f"LOWER(TRIM(COALESCE({a}.moderation_status, 'active'))) = 'active')"
    )


def can_view_recipe_in_tvw_community(
    recipe: dict | None,
    *,
    viewer_user_id: int | None,
    viewer_is_old_goat: bool,
) -> bool:
    """Community detail/API access: Old Goat or owner bypasses public-only gate."""
    if not recipe:
        return False
    if viewer_is_old_goat:
        return True
    owner_id = recipe.get("created_by")
    if viewer_user_id is not None and owner_id is not None and int(viewer_user_id) == int(owner_id):
        return True
    return recipe_dict_visible_in_public_lists(recipe)


def _table_has_column(table: str, column: str) -> bool:
    db = get_db()
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def backfill_recipe_library_types_if_needed(*, active_only: bool = True) -> Dict[str, Any]:
    """Backfill recipes.library_type with an idempotent heuristic.

    Rule:
      - created_by from the Old Goat user -> personal
      - otherwise -> reference
    """
    db = get_db()
    if not _table_has_column("recipes", "library_type"):
        return {"status": "skipped", "reason": "library_type column missing"}

    where_active = " AND active = 1" if active_only else ""
    null_count = db.execute(f"SELECT COUNT(1) AS c FROM recipes WHERE library_type IS NULL{where_active}").fetchone()
    nulls = int(null_count["c"] or 0)
    if nulls <= 0:
        return {"status": "skipped", "reason": "no_null_rows"}

    row = db.execute(
        """
        SELECT id
        FROM users
        WHERE role = 'Old Goat'
           OR LOWER(username) = 'old goat'
        ORDER BY id ASC
        LIMIT 1
        """
    ).fetchone()
    old_goat_id = int(row["id"]) if row and row["id"] is not None else None

    if old_goat_id is None:
        db.execute(
            f"""
            UPDATE recipes
            SET library_type = 'reference'
            WHERE library_type IS NULL{where_active}
            """
        )
        db.commit()
        return {"status": "backfilled", "updated": nulls, "old_goat_id": None}

    db.execute(
        f"""
        UPDATE recipes
        SET library_type = CASE
            WHEN created_by = ? AND COALESCE(status,'') != 'Reference' THEN 'personal'
            ELSE 'reference'
        END
        WHERE library_type IS NULL{where_active}
        """,
        (old_goat_id,),
    )
    db.commit()
    return {"status": "backfilled", "updated": nulls, "old_goat_id": old_goat_id}


def fetch_recipes_library_page(
    *,
    include_dev_archived: bool,
    include_advanced: bool,
    status_filter: str,
    search: str,
    library_scope: str,
    exclude_reference: bool,
    current_user_id: Optional[int],
    page: int,
    page_size: int,
    allow_nicotine: bool,
    show_cost: bool,
) -> Tuple[List[Dict[str, Any]], int]:
    """Fetch one paginated library page with latest version + nicotine + optional cost.

    library_scope:
      - all: no ownership filter (respect exclude_reference when True)
      - mine: created_by = current_user_id
      - reference: reference library_type only (Old Goat; exclude_reference should be False)
      - shared: other users' non-reference recipes (Old Goat)
    """
    db = get_db()
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 24))
    offset = (page - 1) * page_size

    library_scope = (library_scope or "all").strip().lower()
    if library_scope not in {"all", "mine", "reference", "shared"}:
        library_scope = "all"

    has_lib_type = _table_has_column("recipes", "library_type")

    where = ["r.active = 1"]
    params: List[Any] = []

    if not include_dev_archived:
        where.append("r.status NOT IN ('Archived', 'Development')")
    if not include_advanced:
        where.append("r.status != 'Advanced'")
    if status_filter:
        where.append("r.status = ?")
        params.append(status_filter)
    if search:
        where.append(
            """
            (
              r.name LIKE ?
              OR COALESCE(r.recipe_code, '') LIKE ?
              OR CAST(r.id AS TEXT) = ?
              OR EXISTS (
                SELECT 1 FROM recipe_tags rt
                WHERE rt.recipe_id = r.id AND LOWER(rt.tag) LIKE ?
              )
              OR EXISTS (
                SELECT 1 FROM users u2
                WHERE u2.id = r.created_by AND LOWER(COALESCE(u2.username, '')) LIKE ?
              )
            )
            """
        )
        like = f"%{search}%"
        params.extend([like, like, search.strip(), like.lower(), like.lower()])

    if exclude_reference and has_lib_type:
        # Hide system/reference imports, but always list rows the current user created
        # (backfill may tag some non–OG rows as reference).
        if current_user_id is not None:
            where.append(
                "(COALESCE(r.library_type, 'reference') != 'reference' OR r.created_by = ?)"
            )
            params.append(int(current_user_id))
        else:
            where.append("COALESCE(r.library_type, 'reference') != 'reference'")

    if has_lib_type and library_scope == "reference":
        where.append("COALESCE(r.library_type, 'reference') = 'reference'")
    elif library_scope == "mine" and current_user_id is not None:
        where.append("r.created_by = ?")
        params.append(int(current_user_id))
    elif library_scope == "shared" and current_user_id is not None:
        where.append("(r.created_by IS NOT NULL AND r.created_by != ?)")
        params.append(int(current_user_id))
        if has_lib_type:
            where.append("COALESCE(r.library_type, 'reference') != 'reference'")

    # Latest versions per recipe (by id; insertion order).
    base_with = """
    WITH latest AS (
      SELECT recipe_id, MAX(id) AS latest_version_id
      FROM recipe_versions
      GROUP BY recipe_id
    )
    """

    # Nicotine visibility: allow_nicotine determines whether we filter out nicotine recipes.
    if not allow_nicotine:
        where.append(
            """
            NOT EXISTS (
              SELECT 1
              FROM recipe_items ri
              JOIN flavor_reference fr ON fr.id = ri.ingredient_id
              WHERE ri.version_id = rv.id
                AND fr.is_nicotine = 1
            )
            """
        )

    cost_sql = ""
    if show_cost:
        cost_sql = """
          ,(
            SELECT COALESCE(SUM(fr2.price_per_ml * (ri2.amount_percent / 100.0)), 0.0)
            FROM recipe_items ri2
            JOIN flavor_reference fr2 ON fr2.id = ri2.ingredient_id
            WHERE ri2.version_id = rv.id
          ) AS cost_per_ml
        """
    else:
        cost_sql = """
          ,NULL AS cost_per_ml
        """

    sort_uid = int(current_user_id) if current_user_id is not None else -1

    order_by = f"""
        ORDER BY
          CASE WHEN COALESCE(r.library_type, 'reference') = 'reference' THEN 1 ELSE 0 END,
          CASE r.status WHEN 'Production' THEN 0 WHEN 'Experimental' THEN 1 ELSE 2 END,
          CASE WHEN r.created_by IS NOT NULL AND r.created_by = ? THEN 0 ELSE 1 END,
          r.name COLLATE NOCASE ASC
    """

    main_sql = (
        base_with
        + f"""
        SELECT
          r.id,
          r.name,
          r.recipe_code,
          r.status,
          r.image_path,
          r.created_by,
          COALESCE(r.visibility, 'private') AS visibility,
          COALESCE(r.library_type, 'reference') AS library_type,
          u.username AS owner_username,
          rv.id AS latest_version_id,
          rv.version_number AS latest_version_number,
          rv.total_flavor_percent,
          EXISTS (
            SELECT 1
            FROM recipe_items ri3
            JOIN flavor_reference fr3 ON fr3.id = ri3.ingredient_id
            WHERE ri3.version_id = rv.id
              AND fr3.is_nicotine = 1
            LIMIT 1
          ) AS has_nicotine
          {cost_sql}
        FROM recipes r
        LEFT JOIN users u ON u.id = r.created_by
        JOIN latest ON latest.recipe_id = r.id
        JOIN recipe_versions rv ON rv.id = latest.latest_version_id
        WHERE {' AND '.join(where)}
        {order_by}
        LIMIT ? OFFSET ?
        """
    )

    rows = db.execute(main_sql, (*params, sort_uid, page_size, offset)).fetchall()

    count_sql = (
        base_with
        + f"""
        SELECT COUNT(1) AS total_count
        FROM recipes r
        JOIN latest ON latest.recipe_id = r.id
        JOIN recipe_versions rv ON rv.id = latest.latest_version_id
        WHERE {' AND '.join(where)}
        """
    )
    total_row = db.execute(count_sql, params).fetchone()
    total_count = int(total_row["total_count"] or 0) if total_row else 0
    return [dict(r) for r in rows], total_count

@contextmanager
def transaction():
    db = get_db()
    try:
        db.execute("BEGIN")
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise

def fetch_recipes_filtered(
    *,
    include_dev_archived: bool,
    include_advanced: bool,
    status_filter: str,
    search: str,
) -> List[Dict[str, Any]]:
    db = get_db()

    query = "SELECT * FROM recipes WHERE active = 1"
    params: List[Any] = []

    if not include_dev_archived:
        query += " AND status NOT IN ('Archived', 'Development')"
    if not include_advanced:
        query += " AND status != 'Advanced'"

    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    if search:
        query += " AND (name LIKE ? OR recipe_code LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like])

    rows = db.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def fetch_latest_version(recipe_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    row = db.execute(
        """
        SELECT id, version_number, target_vg_pg_ratio, total_flavor_percent
        FROM recipe_versions
        WHERE recipe_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (recipe_id,),
    ).fetchone()
    return dict(row) if row else None


def fetch_version(version_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    row = db.execute(
        """
        SELECT id, recipe_id, version_number, target_vg_pg_ratio, total_flavor_percent, created_at
        FROM recipe_versions
        WHERE id = ?
        LIMIT 1
        """,
        (version_id,),
    ).fetchone()
    return dict(row) if row else None


def resolve_recipe_version_id_for_mix(*, recipe_id: int, requested_version_id: int | None) -> int:
    """Pick a version for mixing: explicit request, else recipe current, else latest."""
    if requested_version_id:
        version = fetch_version(int(requested_version_id))
        if not version or int(version.get("recipe_id") or 0) != int(recipe_id):
            raise ValueError("recipe_version_id does not belong to recipe.")
        return int(version["id"])
    current = fetch_current_version(recipe_id)
    if current:
        return int(current["id"])
    latest = fetch_latest_version(recipe_id)
    if latest:
        return int(latest["id"])
    raise ValueError("No recipe version found for recipe.")


def fetch_current_version(recipe_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    row = db.execute(
        """
        SELECT rv.id, rv.recipe_id, rv.version_number, rv.target_vg_pg_ratio, rv.total_flavor_percent, rv.created_at
        FROM recipes r
        JOIN recipe_versions rv ON rv.id = r.current_version_id
        WHERE r.id = ?
        LIMIT 1
        """,
        (recipe_id,),
    ).fetchone()
    return dict(row) if row else None


def set_current_version(*, recipe_id: int, version_id: int) -> None:
    db = get_db()
    db.execute(
        "UPDATE recipes SET current_version_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (version_id, recipe_id),
    )


def version_has_nicotine(version_id: int) -> bool:
    db = get_db()
    row = db.execute(
        """
        SELECT 1
        FROM recipe_items ri
        JOIN flavor_reference fr ON ri.ingredient_id = fr.id
        WHERE ri.version_id = ? AND fr.is_nicotine = 1
        LIMIT 1
        """,
        (version_id,),
    ).fetchone()
    return bool(row)


def compute_cost_per_ml(version_id: int) -> float:
    db = get_db()
    row = db.execute(
        """
        SELECT SUM(fr.price_per_ml * (ri.amount_percent / 100.0)) AS cost_ml
        FROM recipe_items ri
        JOIN flavor_reference fr ON ri.ingredient_id = fr.id
        WHERE ri.version_id = ?
        """,
        (version_id,),
    ).fetchone()
    if not row:
        return 0.0
    return float(row["cost_ml"] or 0.0)


def fetch_recipe(recipe_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    row = db.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    return dict(row) if row else None


def fetch_recipe_by_slug_or_id(slug: str) -> Optional[Dict[str, Any]]:
    db = get_db()
    raw = str(slug or "").strip()
    recipe_id = None
    if raw.isdigit():
        recipe_id = int(raw)
    elif "-" in raw and raw.rsplit("-", 1)[-1].isdigit():
        recipe_id = int(raw.rsplit("-", 1)[-1])

    if recipe_id is not None:
        row = db.execute(
            """
            SELECT r.*, u.username AS owner_username
            FROM recipes r
            LEFT JOIN users u ON u.id = r.created_by
            WHERE r.id = ?
            LIMIT 1
            """,
            (recipe_id,),
        ).fetchone()
        return dict(row) if row else None

    row = db.execute(
        """
        SELECT r.*, u.username AS owner_username
        FROM recipes r
        LEFT JOIN users u ON u.id = r.created_by
        WHERE r.slug = ?
        LIMIT 1
        """,
        (raw,),
    ).fetchone()
    return dict(row) if row else None


def fetch_recipe_version_notes(*, version_id: int) -> str:
    """Return trimmed version notes for discovery display (empty string when absent)."""
    db = get_db()
    row = db.execute(
        "SELECT notes FROM recipe_versions WHERE id = ? LIMIT 1",
        (int(version_id),),
    ).fetchone()
    if not row or row["notes"] is None:
        return ""
    return str(row["notes"]).strip()


def fetch_recipe_versions(recipe_id: int) -> List[Dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT id, recipe_id, version_number, target_vg_pg_ratio, total_flavor_percent, notes, created_at
        FROM recipe_versions
        WHERE recipe_id = ?
        ORDER BY id DESC
        """,
        (recipe_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_recipe_collab_snapshot(recipe_id: int) -> dict[str, Any]:
    """Latest status transition + most recent explicit HELP request (for community recipe UX)."""
    db = get_db()
    rid = int(recipe_id)
    last = db.execute(
        """
        SELECT id, recipe_version_id, from_status, to_status, reason, created_at
        FROM recipe_status_updates
        WHERE recipe_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (rid,),
    ).fetchone()
    last_help = db.execute(
        """
        SELECT id, recipe_version_id, reason, created_at
        FROM recipe_status_updates
        WHERE recipe_id = ? AND UPPER(TRIM(COALESCE(to_status, ''))) = 'HELP'
        ORDER BY id DESC
        LIMIT 1
        """,
        (rid,),
    ).fetchone()
    return {
        "last_status_change": dict(last) if last else None,
        "latest_help_request": dict(last_help) if last_help else None,
    }


def fetch_recipe_tags(recipe_id: int) -> List[str]:
    db = get_db()
    rows = db.execute(
        "SELECT tag FROM recipe_tags WHERE recipe_id = ? ORDER BY tag COLLATE NOCASE ASC",
        (recipe_id,),
    ).fetchall()
    return [str(r["tag"]) for r in rows if r["tag"]]


def replace_recipe_tags(*, recipe_id: int, tags: Sequence[str]) -> None:
    db = get_db()
    db.execute("DELETE FROM recipe_tags WHERE recipe_id = ?", (recipe_id,))
    clean = []
    seen = set()
    for tag in tags:
        t = str(tag or "").strip().lower()
        if not t or t in seen:
            continue
        seen.add(t)
        clean.append(t[:40])
    for t in clean:
        db.execute("INSERT INTO recipe_tags (recipe_id, tag) VALUES (?, ?)", (recipe_id, t))


def fetch_flavour_reference_options(*, limit: int = 500) -> List[Dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT id, brand, name, COALESCE(density, 1.036) AS density
        FROM flavor_reference
        WHERE active = 1
        ORDER BY brand COLLATE NOCASE ASC, name COLLATE NOCASE ASC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(r) for r in rows]


def search_flavour_reference_for_builder(*, query: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Ranked case-insensitive search across the full active flavour_reference catalog.

    Also matches TVW catalogue rows (``flavours.canonical_name``) so autocomplete is not
    limited to whatever appears on page 1 of the flavour library browser.
    """
    q = (query or "").strip()
    if not q:
        return []
    q_lower = q.lower()
    like_prefix = f"{q_lower}%"
    like_contains = f"%{q_lower}%"
    lim = max(1, min(int(limit or 100), 250))
    db = get_db()
    rows = db.execute(
        """
        SELECT
            hits.id,
            hits.brand,
            hits.name,
            hits.density,
            hits.display_name,
            hits.catalogue_name
        FROM (
            SELECT
                fr.id AS id,
                COALESCE(fr.brand, '') AS brand,
                COALESCE(fr.name, '') AS name,
                COALESCE(fr.density, 1.036) AS density,
                (COALESCE(fr.brand, '') || ' - ' || COALESCE(fr.name, '')) AS display_name,
                MIN(NULLIF(TRIM(COALESCE(fv.canonical_name, '')), '')) AS catalogue_name
            FROM flavor_reference fr
            LEFT JOIN flavours fv
              ON fv.source_flavor_ref_id = fr.id
             AND COALESCE(fv.merged_into_flavour_id, 0) = 0
            WHERE COALESCE(fr.active, 1) = 1
              AND (
                lower(COALESCE(fr.name, '')) LIKE ?
                OR lower(COALESCE(fr.brand, '')) LIKE ?
                OR lower(COALESCE(fr.brand, '') || ' - ' || COALESCE(fr.name, '')) LIKE ?
                OR lower(COALESCE(fr.name, '') || ' (' || COALESCE(fr.brand, '') || ')') LIKE ?
                OR lower(COALESCE(fr.brand, '') || ' ' || COALESCE(fr.name, '')) LIKE ?
                OR lower(COALESCE(fv.canonical_name, '')) LIKE ?
                OR lower(COALESCE(fv.brand, '')) LIKE ?
                OR lower(COALESCE(fv.canonical_name, '') || ' (' || COALESCE(fv.brand, '') || ')') LIKE ?
                OR lower(COALESCE(fv.brand, '') || ' ' || COALESCE(fv.canonical_name, '')) LIKE ?
              )
            GROUP BY fr.id
        ) AS hits
        ORDER BY
          CASE
            WHEN lower(trim(hits.name)) = ? THEN 0
            WHEN lower(trim(COALESCE(hits.catalogue_name, ''))) = ? THEN 0
            WHEN lower(hits.name) LIKE ? THEN 1
            WHEN lower(COALESCE(hits.catalogue_name, '')) LIKE ? THEN 1
            WHEN lower(hits.display_name) LIKE ? THEN 2
            WHEN lower(COALESCE(hits.catalogue_name, '') || ' (' || hits.brand || ')') LIKE ? THEN 2
            WHEN lower(hits.name) LIKE ? THEN 3
            WHEN lower(hits.display_name) LIKE ? THEN 4
            WHEN lower(hits.brand) LIKE ? THEN 4
            ELSE 5
          END,
          hits.brand COLLATE NOCASE ASC,
          hits.name COLLATE NOCASE ASC
        LIMIT ?
        """,
        (
            like_contains,
            like_contains,
            like_contains,
            like_contains,
            like_contains,
            like_contains,
            like_contains,
            like_contains,
            like_contains,
            q_lower,
            q_lower,
            like_prefix,
            like_prefix,
            like_prefix,
            like_prefix,
            like_contains,
            like_contains,
            like_contains,
            lim,
        ),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        brand = str(d.get("brand") or "Unknown")
        name = str(d.get("name") or f"Flavor #{d.get('id')}")
        ref_id = int(d["id"])
        d["display_label"] = f"{brand} - {name} (#{ref_id})"
        out.append(d)
    return out


def fetch_flavour_reference_map(reference_ids: Sequence[int]) -> Dict[int, Dict[str, Any]]:
    ids = [int(x) for x in reference_ids if int(x or 0) > 0]
    if not ids:
        return {}
    db = get_db()
    ph = ",".join("?" * len(ids))
    rows = db.execute(
        f"""
        SELECT id, brand, name, COALESCE(density, 1.036) AS density
        FROM flavor_reference
        WHERE id IN ({ph}) AND active = 1
        """,
        tuple(ids),
    ).fetchall()
    return {int(r["id"]): dict(r) for r in rows}


def fetch_tvw_flavours(
    *,
    search: str = "",
    manufacturer: str = "",
    sort_by: str = "name",
    sort_dir: str = "asc",
    page: int = 1,
    page_size: int = 24,
) -> tuple[list[dict], int]:
    """Paginated TVW flavour catalog for discovery (excludes merged targets).

    Metrics (no N+1):
      - ``used_in_count``: distinct community-visible recipes (same rules as recipe library)
        using this flavour's ``source_flavor_ref_id`` in any version.
      - ``avg_rating``: mean of visible flavour_reviews only; NULL if none.
      - ``avg_usage_percent``: ``avg_usage`` else ``default_percent`` from import/catalog;
        NULL if neither set (UI shows —).
    """
    db = get_db()
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 24)))
    offset = (page - 1) * page_size
    where = ["1=1"]
    params: list[Any] = []
    if search:
        where.append("(f.canonical_name LIKE ? OR COALESCE(f.brand, '') LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like])
    if manufacturer:
        where.append("LOWER(COALESCE(f.brand, '')) = ?")
        params.append(manufacturer.strip().lower())
    where.append("COALESCE(f.merged_into_flavour_id, 0) = 0")

    sort_map = {
        "name": "LOWER(COALESCE(f.canonical_name, ''))",
        "manufacturer": "LOWER(COALESCE(f.brand, ''))",
        "used_in": "used_in_count",
        "rating": "avg_rating",
        "avg_usage": "avg_usage_percent",
    }
    sort_key = (sort_by or "name").strip().lower()
    sort_expr = sort_map.get(sort_key, sort_map["name"])
    sort_order = "DESC" if (sort_dir or "").strip().lower() == "desc" else "ASC"

    pub = tvw_recipe_public_list_predicate_sql("r")

    rows = db.execute(
        f"""
        SELECT
            f.id,
            f.canonical_name,
            f.brand,
            f.category,
            CASE
                WHEN f.avg_usage IS NOT NULL THEN f.avg_usage
                WHEN f.default_percent IS NOT NULL THEN f.default_percent
                ELSE NULL
            END AS avg_usage_percent,
            rv.avg_rating AS avg_rating,
            COALESCE(ru.used_in_count, 0) AS used_in_count
        FROM flavours f
        LEFT JOIN (
            SELECT flavour_id, AVG(rating) AS avg_rating
            FROM flavour_reviews fr
            WHERE COALESCE(fr.is_hidden, 0) = 0
              AND LOWER(TRIM(COALESCE(fr.moderation_status, 'active'))) = 'active'
            GROUP BY flavour_id
        ) rv ON rv.flavour_id = f.id
        LEFT JOIN (
            SELECT f2.id AS flavour_id, COUNT(DISTINCT r.id) AS used_in_count
            FROM flavours f2
            JOIN recipe_items ri ON ri.ingredient_id = f2.source_flavor_ref_id
            JOIN recipe_versions v ON v.id = ri.version_id
            JOIN recipes r ON r.id = v.recipe_id
            WHERE COALESCE(f2.merged_into_flavour_id, 0) = 0
              AND COALESCE(f2.source_flavor_ref_id, 0) != 0
              AND {pub}
            GROUP BY f2.id
        ) ru ON ru.flavour_id = f.id
        WHERE {" AND ".join(where)}
        ORDER BY {sort_expr} {sort_order}, f.id ASC
        LIMIT ? OFFSET ?
        """,
        (*params, page_size, offset),
    ).fetchall()
    count = db.execute(
        f"SELECT COUNT(1) AS c FROM flavours f WHERE {' AND '.join(where)}",
        tuple(params),
    ).fetchone()
    total = int(count["c"] or 0) if count else 0
    return [dict(r) for r in rows], total


def fetch_tvw_flavour_manufacturers(*, limit: int = 1000) -> list[str]:
    db = get_db()
    rows = db.execute(
        """
        SELECT DISTINCT TRIM(COALESCE(brand, '')) AS brand
        FROM flavours
        WHERE COALESCE(brand, '') != ''
          AND COALESCE(merged_into_flavour_id, 0) = 0
        ORDER BY LOWER(brand) ASC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [str(r["brand"]) for r in rows if str(r["brand"]).strip()]


def resolve_tvw_flavour_id(flavour_id: int, *, max_hops: int = 12) -> int:
    """Follow merged_into_flavour_id chain to the canonical catalogue id."""
    db = get_db()
    cur = int(flavour_id)
    for _ in range(max(1, int(max_hops))):
        row = db.execute(
            "SELECT merged_into_flavour_id FROM flavours WHERE id = ?",
            (cur,),
        ).fetchone()
        if not row:
            return cur
        nxt = row["merged_into_flavour_id"]
        if not nxt:
            return cur
        cur = int(nxt)
    return cur


def fetch_tvw_flavour(flavour_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    row = db.execute(
        """
        SELECT
            id,
            canonical_name,
            brand,
            category,
            source_flavor_ref_id,
            avg_usage,
            default_percent
        FROM flavours
        WHERE id = ? AND COALESCE(merged_into_flavour_id, 0) = 0
        LIMIT 1
        """,
        (flavour_id,),
    ).fetchone()
    return dict(row) if row else None


def attach_tvw_flavour_usage_display(flavour: dict) -> dict:
    """Annotate flavour dict for UI: catalogue average vs default % fallback (no network averages)."""
    if flavour.get("avg_usage") is not None:
        flavour["usage_display_kind"] = "avg_usage"
        flavour["usage_display_value"] = float(flavour["avg_usage"])
    elif flavour.get("default_percent") is not None:
        flavour["usage_display_kind"] = "default_percent"
        flavour["usage_display_value"] = float(flavour["default_percent"])
    else:
        flavour["usage_display_kind"] = "none"
        flavour["usage_display_value"] = None
    return flavour


def tvw_flavour_ingredient_ref_ids(flavour: Dict[str, Any]) -> list[int]:
    """Canonical recipe_items.ingredient_id values (flavor_reference.id) for this catalogue row."""
    db = get_db()
    ref = flavour.get("source_flavor_ref_id")
    if ref is not None and int(ref) != 0:
        return [int(ref)]
    rows = db.execute(
        """
        SELECT id
        FROM flavor_reference
        WHERE LOWER(COALESCE(TRIM(name), '')) = LOWER(COALESCE(TRIM(?), ''))
          AND LOWER(COALESCE(TRIM(brand), '')) = LOWER(COALESCE(TRIM(?), ''))
          AND COALESCE(active, 1) = 1
        """,
        (flavour.get("canonical_name"), flavour.get("brand")),
    ).fetchall()
    return [int(r["id"]) for r in rows]


def fetch_tvw_flavour_community_counts(flavour_id: int) -> dict[str, int]:
    """Visible notes/reviews counts (same rules as list queries) in one round-trip."""
    db = get_db()
    v_notes = tvw_community_content_visible_sql("fn")
    v_rev = tvw_community_content_visible_sql("fr")
    row = db.execute(
        f"""
        SELECT
            (
                SELECT COUNT(1)
                FROM flavour_notes fn
                WHERE fn.flavour_id = ? AND {v_notes}
            ) AS notes_visible,
            (
                SELECT COUNT(1)
                FROM flavour_reviews fr
                WHERE fr.flavour_id = ? AND {v_rev}
            ) AS reviews_visible
        """,
        (flavour_id, flavour_id),
    ).fetchone()
    return {
        "notes_visible": int(row["notes_visible"] or 0) if row else 0,
        "reviews_visible": int(row["reviews_visible"] or 0) if row else 0,
    }


def count_tvw_public_current_recipes_using_refs(ref_ids: Sequence[int]) -> int:
    """Distinct active recipes (community list rules) whose *current* version contains one of the refs."""
    if not ref_ids:
        return 0
    db = get_db()
    pub = tvw_recipe_public_list_predicate_sql("r")
    ph = ",".join("?" * len(ref_ids))
    row = db.execute(
        f"""
        SELECT COUNT(DISTINCT r.id) AS c
        FROM recipes r
        JOIN recipe_versions rv ON rv.id = r.current_version_id
        JOIN recipe_items ri ON ri.version_id = rv.id
        WHERE r.current_version_id IS NOT NULL
          AND ri.ingredient_id IN ({ph})
          AND {pub}
        """,
        tuple(ref_ids),
    ).fetchone()
    return int(row["c"] or 0) if row else 0


def fetch_tvw_public_current_recipes_using_refs(
    ref_ids: Sequence[int],
    *,
    offset: int = 0,
    limit: int = 15,
) -> list[dict]:
    if not ref_ids:
        return []
    db = get_db()
    pub = tvw_recipe_public_list_predicate_sql("r")
    ph = ",".join("?" * len(ref_ids))
    lim = max(1, min(50, int(limit)))
    off = max(0, int(offset))
    rows = db.execute(
        f"""
        SELECT DISTINCT
            r.id,
            r.name,
            r.slug,
            r.status,
            r.visibility,
            r.updated_at,
            rv.version_number AS current_version_number
        FROM recipes r
        JOIN recipe_versions rv ON rv.id = r.current_version_id
        JOIN recipe_items ri ON ri.version_id = rv.id
        WHERE r.current_version_id IS NOT NULL
          AND ri.ingredient_id IN ({ph})
          AND {pub}
        ORDER BY r.updated_at DESC, r.id DESC
        LIMIT ? OFFSET ?
        """,
        (*ref_ids, lim, off),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_tvw_flavour_notes(flavour_id: int, *, viewer_user_id: int | None = None) -> list[dict]:
    db = get_db()
    vis = tvw_community_content_visible_sql("fn")
    if viewer_user_id is not None:
        where_sql = f"fn.flavour_id = ? AND ({vis} OR fn.author_user_id = ?)"
        args: tuple[Any, ...] = (flavour_id, int(viewer_user_id))
    else:
        where_sql = f"fn.flavour_id = ? AND {vis}"
        args = (flavour_id,)
    rows = db.execute(
        f"""
        SELECT
            fn.id, fn.flavour_id, fn.author_user_id, fn.note, fn.created_at,
            COALESCE(fn.is_hidden, 0) AS is_hidden,
            COALESCE(fn.moderation_status, 'active') AS moderation_status,
            COALESCE(fn.moderation_reason, '') AS moderation_reason,
            COALESCE(u.username, 'unknown') AS author_username
        FROM flavour_notes fn
        LEFT JOIN users u ON u.id = fn.author_user_id
        WHERE {where_sql}
        ORDER BY fn.id DESC
        """,
        args,
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        mine = viewer_user_id is not None and int(d.get("author_user_id") or 0) == int(viewer_user_id)
        d["owner_moderation_visible"] = bool(mine)
        d["public_visible"] = bool(
            int(d.get("is_hidden") or 0) == 0
            and str(d.get("moderation_status") or "active").strip().lower() == "active"
        )
        out.append(d)
    return out


def fetch_tvw_flavour_reviews(flavour_id: int, *, viewer_user_id: int | None = None) -> list[dict]:
    db = get_db()
    vis = tvw_community_content_visible_sql("fr")
    if viewer_user_id is not None:
        where_sql = f"fr.flavour_id = ? AND ({vis} OR fr.author_user_id = ?)"
        args: tuple[Any, ...] = (flavour_id, int(viewer_user_id))
    else:
        where_sql = f"fr.flavour_id = ? AND {vis}"
        args = (flavour_id,)
    rows = db.execute(
        f"""
        SELECT
            fr.id, fr.flavour_id, fr.author_user_id, fr.rating, fr.review, fr.steep_days, fr.created_at,
            COALESCE(fr.is_hidden, 0) AS is_hidden,
            COALESCE(fr.moderation_status, 'active') AS moderation_status,
            COALESCE(fr.moderation_reason, '') AS moderation_reason,
            COALESCE(u.username, 'unknown') AS author_username
        FROM flavour_reviews fr
        LEFT JOIN users u ON u.id = fr.author_user_id
        WHERE {where_sql}
        ORDER BY fr.id DESC
        """,
        args,
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        mine = viewer_user_id is not None and int(d.get("author_user_id") or 0) == int(viewer_user_id)
        d["owner_moderation_visible"] = bool(mine)
        d["public_visible"] = bool(
            int(d.get("is_hidden") or 0) == 0
            and str(d.get("moderation_status") or "active").strip().lower() == "active"
        )
        out.append(d)
    return out


def build_tvw_library_where(
    *,
    search: str = "",
    status: str = "",
    visibility: str = "",
    tag: str = "",
    favorites_user_id: int | None = None,
    owner_user_id: int | None = None,
    viewer_user_id: int | None = None,
    viewer_is_old_goat: bool = False,
    exclude_recipe_ids: Sequence[int] | None = None,
    public_discovery_only: bool = False,
    min_rating_count: int = 0,
    min_favorite_count: int = 0,
) -> tuple[list[str], list[Any]]:
    """Shared WHERE clause + params for recipe library / discovery (community list rules)."""
    where: list[str] = []
    params: list[Any] = []
    public_gate = tvw_recipe_public_list_predicate_sql("r")
    if viewer_is_old_goat:
        where.append("1=1")
    elif public_discovery_only:
        where.append(public_gate)
    else:
        if viewer_user_id is not None:
            where.append(f"(({public_gate}) OR r.created_by = ?)")
            params.append(int(viewer_user_id))
        else:
            where.append(public_gate)
    if search:
        where.append("(r.name LIKE ? OR COALESCE(r.recipe_code,'') LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like])
    if status:
        where.append("LOWER(COALESCE(r.status,'')) = ?")
        params.append(status.strip().lower())
    if visibility:
        where.append("LOWER(COALESCE(r.visibility,'private')) = ?")
        params.append(visibility.strip().lower())
    if tag:
        where.append(
            "EXISTS (SELECT 1 FROM recipe_tags rt WHERE rt.recipe_id = r.id AND LOWER(rt.tag) = ?)"
        )
        params.append(tag.strip().lower())
    if favorites_user_id is not None:
        where.append(
            "EXISTS (SELECT 1 FROM recipe_favorites fav WHERE fav.recipe_id = r.id AND fav.user_id = ?)"
        )
        params.append(int(favorites_user_id))
    if owner_user_id is not None:
        where.append("r.created_by = ?")
        params.append(int(owner_user_id))
    if exclude_recipe_ids:
        ids = sorted({int(x) for x in exclude_recipe_ids if x is not None})
        if ids:
            placeholders = ", ".join("?" for _ in ids)
            where.append(f"r.id NOT IN ({placeholders})")
            params.extend(ids)
    if int(min_rating_count or 0) > 0:
        where.append(
            "(SELECT COUNT(1) FROM recipe_ratings rr WHERE rr.recipe_id = r.id) >= ?"
        )
        params.append(int(min_rating_count))
    if int(min_favorite_count or 0) > 0:
        where.append(
            "(SELECT COUNT(1) FROM recipe_favorites rf WHERE rf.recipe_id = r.id) >= ?"
        )
        params.append(int(min_favorite_count))
    return where, params


def fetch_tvw_library_count(*, where: list[str], params: list[Any]) -> int:
    db = get_db()
    count_row = db.execute(
        f"SELECT COUNT(1) AS c FROM recipes r WHERE {' AND '.join(where)}",
        tuple(params),
    ).fetchone()
    return int(count_row["c"] or 0) if count_row else 0


def _tvw_library_select_sql(*, viewer_user_id: int | None) -> tuple[str, list[Any]]:
    """Leading SELECT columns; bind `viewer_user_id` favorite subquery before WHERE params."""
    lead: list[Any] = []
    if viewer_user_id is not None:
        fav_expr = (
            "(SELECT COUNT(1) FROM recipe_favorites rf WHERE rf.recipe_id = r.id AND rf.user_id = ?) "
            "AS is_favorited"
        )
        lead.append(int(viewer_user_id))
    else:
        fav_expr = "0 AS is_favorited"
    sql = f"""
    SELECT r.id,
           r.name,
           r.slug,
           r.status,
           COALESCE(r.visibility, 'private') AS visibility,
           r.current_version_id,
           r.created_at,
           r.updated_at,
           COALESCE(u.username, 'Unknown') AS owner_username,
           rv.version_number AS current_version_number,
           rv.total_flavor_percent AS current_total_flavour_pct,
           COALESCE(GROUP_CONCAT(rt.tag, ', '), '') AS tags_csv,
           (SELECT AVG(rr.rating) FROM recipe_ratings rr WHERE rr.recipe_id = r.id) AS avg_recipe_rating,
           (SELECT COUNT(1) FROM recipe_ratings rr WHERE rr.recipe_id = r.id) AS recipe_rating_count,
           (SELECT COUNT(1) FROM recipe_favorites rf WHERE rf.recipe_id = r.id) AS recipe_favorite_count,
           {fav_expr}
    """
    return sql, lead


def _tvw_library_from_sql() -> str:
    return """
    FROM recipes r
    LEFT JOIN users u ON u.id = r.created_by
    LEFT JOIN recipe_versions rv ON rv.id = r.current_version_id
    LEFT JOIN recipe_tags rt ON rt.recipe_id = r.id
    """


def tvw_library_order_clause(*, sort_by: str = "updated", sort_dir: str = "desc") -> str:
    sb = (sort_by or "updated").strip().lower()
    sd = (sort_dir or "desc").strip().lower()
    desc = sd != "asc"
    if sb == "rating":
        if desc:
            return (
                "ORDER BY COALESCE((SELECT AVG(rr.rating) FROM recipe_ratings rr WHERE rr.recipe_id = r.id), 0) DESC, "
                "COALESCE((SELECT COUNT(1) FROM recipe_ratings rr WHERE rr.recipe_id = r.id), 0) DESC, "
                "r.updated_at DESC, r.id DESC"
            )
        return (
            "ORDER BY COALESCE((SELECT AVG(rr.rating) FROM recipe_ratings rr WHERE rr.recipe_id = r.id), 0) ASC, "
            "COALESCE((SELECT COUNT(1) FROM recipe_ratings rr WHERE rr.recipe_id = r.id), 0) ASC, "
            "r.updated_at ASC, r.id ASC"
        )
    if sb == "favorites":
        if desc:
            return (
                "ORDER BY COALESCE((SELECT COUNT(1) FROM recipe_favorites rf WHERE rf.recipe_id = r.id), 0) DESC, "
                "COALESCE((SELECT AVG(rr.rating) FROM recipe_ratings rr WHERE rr.recipe_id = r.id), 0) DESC, "
                "r.updated_at DESC, r.id DESC"
            )
        return (
            "ORDER BY COALESCE((SELECT COUNT(1) FROM recipe_favorites rf WHERE rf.recipe_id = r.id), 0) ASC, "
            "COALESCE((SELECT AVG(rr.rating) FROM recipe_ratings rr WHERE rr.recipe_id = r.id), 0) ASC, "
            "r.updated_at ASC, r.id ASC"
        )
    if sb == "newest":
        if desc:
            return "ORDER BY COALESCE(r.created_at, r.updated_at) DESC, r.id DESC"
        return "ORDER BY COALESCE(r.created_at, r.updated_at) ASC, r.id ASC"
    if sb == "created":
        if desc:
            return "ORDER BY (r.created_at IS NULL), r.created_at DESC, r.id DESC"
        return "ORDER BY (r.created_at IS NULL), r.created_at ASC, r.id ASC"
    if sb == "name":
        if desc:
            return "ORDER BY r.name COLLATE NOCASE DESC, r.id DESC"
        return "ORDER BY r.name COLLATE NOCASE ASC, r.id ASC"
    if desc:
        return "ORDER BY r.updated_at DESC, r.id DESC"
    return "ORDER BY r.updated_at ASC, r.id ASC"


def fetch_tvw_library_rows_for_stash_ranking(
    *,
    where: list[str],
    params: list[Any],
    limit: int,
    viewer_user_id: int | None = None,
) -> list[dict]:
    """Recent-first pool for in-memory stash sort (cap enforced by caller)."""
    db = get_db()
    lim = max(1, min(int(limit or 1), 5000))
    order = "ORDER BY r.updated_at DESC, r.id DESC"
    sel, lead = _tvw_library_select_sql(viewer_user_id=viewer_user_id)
    sql = sel + _tvw_library_from_sql() + f" WHERE {' AND '.join(where)} GROUP BY r.id {order} LIMIT ?"
    rows = db.execute(sql, (*lead, *params, lim)).fetchall()
    return [dict(r) for r in rows]


def fetch_tvw_library(
    *,
    search: str = "",
    status: str = "",
    visibility: str = "",
    tag: str = "",
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "updated",
    sort_dir: str = "desc",
    viewer_user_id: int | None = None,
    viewer_is_old_goat: bool = False,
    favorites_user_id: int | None = None,
    owner_user_id: int | None = None,
    exclude_recipe_ids: Sequence[int] | None = None,
    public_discovery_only: bool = False,
    min_rating_count: int = 0,
    min_favorite_count: int = 0,
) -> tuple[list[dict], int]:
    db = get_db()
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 20))
    offset = (page - 1) * page_size
    where, params = build_tvw_library_where(
        search=search,
        status=status,
        visibility=visibility,
        tag=tag,
        favorites_user_id=favorites_user_id,
        owner_user_id=owner_user_id,
        viewer_user_id=viewer_user_id,
        viewer_is_old_goat=viewer_is_old_goat,
        exclude_recipe_ids=exclude_recipe_ids,
        public_discovery_only=public_discovery_only,
        min_rating_count=min_rating_count,
        min_favorite_count=min_favorite_count,
    )
    order = tvw_library_order_clause(sort_by=sort_by, sort_dir=sort_dir)
    sel, lead = _tvw_library_select_sql(viewer_user_id=viewer_user_id)
    sql = sel + _tvw_library_from_sql() + f" WHERE {' AND '.join(where)} GROUP BY r.id {order} LIMIT ? OFFSET ?"
    rows = db.execute(sql, (*lead, *params, page_size, offset)).fetchall()
    total = fetch_tvw_library_count(where=where, params=params)
    return [dict(r) for r in rows], total


def fetch_recipe_rating_aggregate(*, recipe_id: int) -> dict[str, Any]:
    db = get_db()
    row = db.execute(
        "SELECT AVG(rating) AS avg_rating, COUNT(1) AS c FROM recipe_ratings WHERE recipe_id = ?",
        (int(recipe_id),),
    ).fetchone()
    avg = row["avg_rating"] if row and row["avg_rating"] is not None else None
    return {
        "avg_rating": float(avg) if avg is not None else None,
        "rating_count": int(row["c"] or 0) if row else 0,
    }


def is_recipe_favorited_by(*, user_id: int, recipe_id: int) -> bool:
    db = get_db()
    row = db.execute(
        "SELECT 1 AS ok FROM recipe_favorites WHERE user_id = ? AND recipe_id = ? LIMIT 1",
        (int(user_id), int(recipe_id)),
    ).fetchone()
    return bool(row)


def fetch_user_recipe_star_rating(*, user_id: int, recipe_id: int) -> int | None:
    db = get_db()
    row = db.execute(
        "SELECT rating FROM recipe_ratings WHERE user_id = ? AND recipe_id = ? LIMIT 1",
        (int(user_id), int(recipe_id)),
    ).fetchone()
    if not row or row["rating"] is None:
        return None
    return int(round(float(row["rating"])))


def fetch_recipe_engagement_for_viewer(*, recipe_id: int, viewer_user_id: int | None) -> dict[str, Any]:
    agg = fetch_recipe_rating_aggregate(recipe_id=int(recipe_id))
    out: dict[str, Any] = {
        "avg_rating": agg["avg_rating"],
        "rating_count": agg["rating_count"],
        "is_favorited": False,
        "my_rating": None,
    }
    if viewer_user_id is not None:
        uid = int(viewer_user_id)
        out["is_favorited"] = is_recipe_favorited_by(user_id=uid, recipe_id=int(recipe_id))
        out["my_rating"] = fetch_user_recipe_star_rating(user_id=uid, recipe_id=int(recipe_id))
    return out


def set_recipe_favorite(*, user_id: int, recipe_id: int, favorited: bool) -> None:
    db = get_db()
    uid, rid = int(user_id), int(recipe_id)
    if favorited:
        db.execute(
            "INSERT OR IGNORE INTO recipe_favorites (user_id, recipe_id) VALUES (?, ?)",
            (uid, rid),
        )
    else:
        db.execute("DELETE FROM recipe_favorites WHERE user_id = ? AND recipe_id = ?", (uid, rid))
    db.commit()


def upsert_recipe_star_rating(*, user_id: int, recipe_id: int, rating: int) -> None:
    """Persist integer 1–5; one row per user per recipe (upsert)."""
    db = get_db()
    db.execute(
        """
        INSERT INTO recipe_ratings (user_id, recipe_id, rating, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(user_id, recipe_id) DO UPDATE SET
            rating = excluded.rating,
            updated_at = excluded.updated_at
        """,
        (int(user_id), int(recipe_id), int(rating)),
    )
    db.commit()


def fetch_recipe_name(recipe_id: int) -> Optional[str]:
    db = get_db()
    row = db.execute("SELECT name FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    return row["name"] if row else None


def fetch_version_items_with_stock(version_id: int) -> List[Dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT fr.name,
               fr.brand,
               fr.id AS flavor_id,
               fr.is_nicotine,
               fr.density,
               COALESCE(fr.category, 'Flavor') AS category,
               COALESCE(fr.resolved_profile, '') AS resolved_profile,
                COALESCE(fr.resolved_role, '') AS resolved_role,
                COALESCE(fr.resolved_potency, '') AS resolved_potency,
                COALESCE(fr.resolved_volatility, '') AS resolved_volatility,
                COALESCE(fr.resolved_body_weight, '') AS resolved_body_weight,
                COALESCE(fr.resolved_pod_suitability, '') AS resolved_pod_suitability,
                COALESCE(fr.sweetness_bias, 0) AS sweetness_bias,
                COALESCE(fr.brightness_bias, 0) AS brightness_bias,
                COALESCE(fr.harshness_bias, 0) AS harshness_bias,
                COALESCE(fr.coil_risk, 0) AS coil_risk,
                COALESCE(fr.needs_review, 0) AS needs_review,
               ri.amount_percent,
               li.current_stock
        FROM recipe_items ri
        JOIN flavor_reference fr ON ri.ingredient_id = fr.id
        LEFT JOIN library_items li ON fr.id = li.reference_id AND li.active = 1
        WHERE ri.version_id = ?
        ORDER BY ri.id ASC
        """,
        (version_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_amount_percent_for_version_item(*, version_id: int, ingredient_id: int) -> Optional[float]:
    db = get_db()
    row = db.execute(
        "SELECT amount_percent FROM recipe_items WHERE version_id = ? AND ingredient_id = ?",
        (version_id, ingredient_id),
    ).fetchone()
    return float(row["amount_percent"]) if row else None


def fetch_version_primary_nicotine_reference_id(version_id: int) -> Optional[int]:
    """First nicotine flavour_reference id on this recipe version (for synthetic queue id -2)."""
    db = get_db()
    row = db.execute(
        """
        SELECT fr.id AS flavor_id
        FROM recipe_items ri
        JOIN flavor_reference fr ON ri.ingredient_id = fr.id
        WHERE ri.version_id = ?
          AND COALESCE(fr.is_nicotine, 0) = 1
        ORDER BY ri.id ASC
        LIMIT 1
        """,
        (int(version_id),),
    ).fetchone()
    if not row or row["flavor_id"] is None:
        return None
    return int(row["flavor_id"])


def fetch_version_items_basic(version_id: int) -> List[Dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT ri.ingredient_id, ri.amount_percent, fr.name, fr.is_nicotine
        FROM recipe_items ri
        JOIN flavor_reference fr ON ri.ingredient_id = fr.id
        WHERE ri.version_id = ?
        """,
        (version_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_recipe_status(recipe_id: int) -> Optional[str]:
    db = get_db()
    row = db.execute("SELECT status FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    return row["status"] if row else None


def update_recipe(*, recipe_id: int, name: str, recipe_code: str, status: str) -> None:
    db = get_db()
    db.execute(
        "UPDATE recipes SET name = ?, recipe_code = ?, status = ? WHERE id = ?",
        (name, recipe_code, status, recipe_id),
    )


def update_recipe_image(*, recipe_id: int, image_path: str | None) -> None:
    db = get_db()
    db.execute(
        "UPDATE recipes SET image_path = ? WHERE id = ?",
        (image_path, recipe_id),
    )


def insert_recipe(*, name: str, recipe_code: str, status: str, created_by: int) -> int:
    """Insert a recipe row.

    Empty ``recipe_code`` is stored as NULL. SQLite UNIQUE treats NULL as distinct,
    so Workshop creates that omit a code no longer collide on ``''``.
    """
    db = get_db()
    code = str(recipe_code or "").strip() or None
    cur = db.execute(
        "INSERT INTO recipes (name, recipe_code, status, created_by) VALUES (?, ?, ?, ?)",
        (name, code, status, created_by),
    )
    return int(cur.lastrowid)


def insert_recipe_version(
    *,
    recipe_id: int,
    version_number: str,
    target_vg_pg_ratio: str,
    total_flavor_percent: float,
    notes: str | None = None,
) -> int:
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO recipe_versions (recipe_id, version_number, target_vg_pg_ratio, total_flavor_percent, notes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (recipe_id, version_number, target_vg_pg_ratio, total_flavor_percent, notes),
    )
    return int(cur.lastrowid)


def update_recipe_community_fields(
    *,
    recipe_id: int,
    visibility: str,
    status: str,
    slug: str | None,
    library_type: str | None = None,
) -> None:
    db = get_db()
    if library_type is not None and _table_has_column("recipes", "library_type"):
        db.execute(
            """
            UPDATE recipes
            SET visibility = ?, status = ?, slug = COALESCE(?, slug),
                library_type = COALESCE(library_type, ?),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (visibility, status, slug, library_type, recipe_id),
        )
    else:
        db.execute(
            """
            UPDATE recipes
            SET visibility = ?, status = ?, slug = COALESCE(?, slug), updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (visibility, status, slug, recipe_id),
        )


def insert_recipe_items(*, version_id: int, items: Iterable[Tuple[int, float]]) -> None:
    db = get_db()
    for ingredient_id, percent in items:
        db.execute(
            "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (?, ?, ?)",
            (version_id, ingredient_id, percent),
        )


def fetch_last_version_number(recipe_id: int) -> Optional[str]:
    db = get_db()
    row = db.execute(
        "SELECT version_number FROM recipe_versions WHERE recipe_id = ? ORDER BY id DESC LIMIT 1",
        (recipe_id,),
    ).fetchone()
    return row["version_number"] if row else None


def insert_audit_log(
    *,
    actor_id: int,
    action: str,
    target_id: int,
    details: str,
    timestamp: datetime,
) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO audit_logs (actor_id, action, target_id, details, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (actor_id, action, target_id, details, timestamp),
    )


def fetch_version_items_for_editor(version_id: int) -> List[Dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT ri.amount_percent AS percent,
               fr.name,
               fr.brand,
               fr.id AS ingredient_id
        FROM recipe_items ri
        JOIN flavor_reference fr ON ri.ingredient_id = fr.id
        WHERE ri.version_id = ?
        """,
        (version_id,),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        # Safety clamp for legacy bad imports: editor should never receive negative percentages.
        d["percent"] = abs(float(d.get("percent") or 0.0))
        out.append(d)
    return out


def fetch_version_items_with_pricing(version_id: int) -> List[Dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT ri.amount_percent,
               fr.id AS ingredient_id,
               fr.name,
               fr.brand,
               fr.price_per_ml,
               COALESCE(fr.is_nicotine, 0) AS is_nicotine,
               COALESCE(fr.category, 'Flavor') AS category
        FROM recipe_items ri
        JOIN flavor_reference fr ON ri.ingredient_id = fr.id
        WHERE ri.version_id = ?
        ORDER BY ri.id ASC
        """,
        (version_id,),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["amount_percent"] = abs(float(d.get("amount_percent") or 0.0))
        out.append(d)
    return out


def fetch_version_ingredients_for_view(
    version_id: int,
    *,
    recipe_id: Optional[int] = None,
    recipe_code: str = "",
) -> List[Dict[str, Any]]:
    db = get_db()
    # Imported VC recipes should render with identity-resolution metadata so operators can
    # see both the algorithm-facing match and the original imported flavour text.
    if recipe_id:
        rr_row = None
        try:
            rr_row = db.execute(
                """
                SELECT id
                FROM recipe_references
                WHERE source = 'vc'
                  AND source_type = 'vc_dtl'
                  AND recipe_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(recipe_id),),
            ).fetchone()
        except Exception:
            rr_row = None

        if not rr_row:
            code = str(recipe_code or "").strip()
            if code.upper().startswith("VCREF-"):
                src_id = code.split("-", 1)[1].strip()
                if src_id:
                    rr_row = db.execute(
                        """
                        SELECT id
                        FROM recipe_references
                        WHERE source = 'vc'
                          AND source_type = 'vc_dtl'
                          AND source_recipe_id = ?
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (src_id,),
                    ).fetchone()

        if rr_row:
            rows = db.execute(
                """
                SELECT
                    rf.percentage AS amount_percent,
                    COALESCE(rf.matched_flavor_name, '') AS matched_flavor_name,
                    rf.matched_flavor_id,
                    rf.match_method,
                    rf.match_confidence,
                    COALESCE(rf.needs_review, 0) AS needs_review,
                    COALESCE(rf.brand_raw, '') AS brand_raw,
                    COALESCE(rf.flavour_name_raw, '') AS flavour_name_raw,
                    COALESCE(rf.brand_canonical, '') AS brand_canonical,
                    COALESCE(rf.flavour_name_normalized, '') AS flavour_name_normalized,
                    COALESCE(fr.brand, '') AS matched_brand,
                    COALESCE(fr.name, '') AS matched_name_from_ref,
                    COALESCE(fr.is_nicotine, 0) AS is_nicotine,
                    COALESCE(fr.price_per_ml, 0.0) AS price_per_ml,
                    COALESCE(li.current_stock, 0.0) AS stock_g
                FROM recipe_reference_flavours rf
                LEFT JOIN flavor_reference fr ON fr.id = rf.matched_flavor_id
                LEFT JOIN library_items li ON li.reference_id = rf.matched_flavor_id
                WHERE rf.recipe_reference_id = ?
                ORDER BY rf.position ASC, rf.id ASC
                """,
                (int(rr_row["id"]),),
            ).fetchall()
            imported_out: List[Dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                d["amount_percent"] = abs(float(d.get("amount_percent") or 0.0))
                d["matched_flavor_id"] = int(d["matched_flavor_id"]) if d.get("matched_flavor_id") else None
                d["match_confidence"] = (
                    float(d["match_confidence"]) if d.get("match_confidence") is not None else None
                )
                d["needs_review"] = 1 if int(d.get("needs_review") or 0) == 1 else 0
                d["is_imported_identity_row"] = True
                d["is_matched_identity"] = bool(d.get("matched_flavor_id"))
                d["ingredient_id"] = d["matched_flavor_id"]
                # Keep compatibility with existing template fields.
                d["brand"] = d.get("matched_brand") or d.get("brand_raw") or ""
                d["name"] = (
                    d.get("matched_flavor_name")
                    or d.get("matched_name_from_ref")
                    or d.get("flavour_name_raw")
                    or ""
                )
                imported_out.append(d)
            if imported_out:
                return imported_out

    rows = db.execute(
        """
        SELECT ri.*,
               fr.name,
               fr.brand,
               fr.is_nicotine,
               fr.price_per_ml,
               COALESCE(li.current_stock, 0) AS stock_g
        FROM recipe_items ri
        JOIN flavor_reference fr ON ri.ingredient_id = fr.id
        LEFT JOIN library_items li ON fr.id = li.reference_id
        WHERE ri.version_id = ?
        """,
        (version_id,),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["amount_percent"] = abs(float(d.get("amount_percent") or 0.0))
        d["is_imported_identity_row"] = False
        d["is_matched_identity"] = False
        d["matched_flavor_id"] = None
        d["matched_flavor_name"] = ""
        d["match_method"] = ""
        d["match_confidence"] = None
        d["needs_review"] = 0
        d["brand_raw"] = ""
        d["flavour_name_raw"] = ""
        d["brand_canonical"] = ""
        d["flavour_name_normalized"] = ""
        out.append(d)
    return out

