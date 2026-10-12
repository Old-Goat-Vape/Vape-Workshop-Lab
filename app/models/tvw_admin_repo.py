from __future__ import annotations

import json
from typing import Any

from db import get_db


def fetch_admin_dashboard_counts() -> dict[str, int]:
    db = get_db()
    flagged_flavours = db.execute(
        """
        SELECT COUNT(1) AS c
        FROM flavours
        WHERE COALESCE(is_hidden, 0) = 1 OR COALESCE(is_invalid, 0) = 1 OR COALESCE(moderation_status, 'active') != 'active'
        """
    ).fetchone()
    hidden_recipes = db.execute(
        "SELECT COUNT(1) AS c FROM recipes WHERE COALESCE(is_hidden, 0) = 1 OR COALESCE(moderation_status, 'active') != 'active'"
    ).fetchone()
    pending_feedback = db.execute(
        "SELECT COUNT(1) AS c FROM recipe_feedback WHERE COALESCE(body, '') != ''"
    ).fetchone()
    flagged_users = db.execute(
        "SELECT COUNT(1) AS c FROM users WHERE COALESCE(is_suspended, 0) = 1 OR COALESCE(is_banned, 0) = 1 OR COALESCE(is_muted, 0) = 1"
    ).fetchone()
    return {
        "flagged_flavours": int(flagged_flavours["c"] or 0),
        "hidden_or_moderated_recipes": int(hidden_recipes["c"] or 0),
        "feedback_items": int(pending_feedback["c"] or 0),
        "flagged_users": int(flagged_users["c"] or 0),
    }


def list_admin_audit_logs(*, limit: int = 50) -> list[dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT l.id, l.actor_user_id, l.actor_role, l.action, l.target_type, l.target_id, l.reason, l.details_json, l.created_at,
               COALESCE(u.username, 'unknown') AS actor_username
        FROM tvw_admin_audit_logs l
        LEFT JOIN users u ON u.id = l.actor_user_id
        ORDER BY l.id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(r) for r in rows]


def insert_admin_audit_log(
    *,
    actor_user_id: int,
    actor_role: str,
    action: str,
    target_type: str,
    target_id: int | None,
    reason: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO tvw_admin_audit_logs
        (actor_user_id, actor_role, action, target_type, target_id, reason, details_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(actor_user_id),
            str(actor_role or ""),
            str(action),
            str(target_type),
            int(target_id) if target_id is not None else None,
            str(reason or ""),
            json.dumps(details or {}),
        ),
    )
    db.commit()


def list_flavour_moderation_rows(
    *,
    search: str = "",
    manufacturer: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    db = get_db()
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 50))
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
    if status == "hidden":
        where.append("COALESCE(f.is_hidden, 0) = 1")
    elif status == "invalid":
        where.append("COALESCE(f.is_invalid, 0) = 1")
    elif status == "active":
        where.append("COALESCE(f.is_hidden, 0) = 0 AND COALESCE(f.is_invalid, 0) = 0")

    rows = db.execute(
        f"""
        SELECT f.id, f.canonical_name, f.brand, f.category, f.source, f.source_flavor_ref_id,
               COALESCE(f.is_hidden, 0) AS is_hidden,
               COALESCE(f.is_invalid, 0) AS is_invalid,
               COALESCE(f.moderation_status, 'active') AS moderation_status,
               COALESCE(f.moderation_reason, '') AS moderation_reason,
               m.source_name, m.source_table, m.source_record_id, m.source_url, m.vc_url, m.last_synced_at
        FROM flavours f
        LEFT JOIN flavour_reference_meta m ON m.flavour_id = f.id
        WHERE {" AND ".join(where)}
        ORDER BY f.id DESC
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


def list_flavour_manufacturers(*, limit: int = 2000) -> list[str]:
    db = get_db()
    rows = db.execute(
        """
        SELECT DISTINCT TRIM(COALESCE(brand, '')) AS brand
        FROM flavours
        WHERE COALESCE(brand, '') != ''
        ORDER BY LOWER(brand) ASC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [str(r["brand"]) for r in rows if str(r["brand"]).strip()]


def list_flavour_identity_cleanup_rows(
    *,
    search: str = "",
    manufacturer: str = "",
    view: str = "",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """Catalogue rows for duplicate / suspicious / merged flavour identity review."""
    db = get_db()
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 50))
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

    view = (view or "").strip().lower()
    if view == "duplicates":
        where.append(
            """
            EXISTS (
              SELECT 1 FROM flavours f2
              WHERE LOWER(TRIM(COALESCE(f2.brand, ''))) = LOWER(TRIM(COALESCE(f.brand, '')))
                AND LOWER(TRIM(COALESCE(f2.canonical_name, ''))) = LOWER(TRIM(COALESCE(f.canonical_name, '')))
                AND f2.id != f.id
            )
            """
        )
    elif view == "suspicious":
        where.append(
            "(COALESCE(TRIM(f.canonical_name), '') = '' OR COALESCE(TRIM(f.brand), '') = '' "
            "OR LENGTH(COALESCE(TRIM(f.canonical_name), '')) < 2)"
        )
    elif view == "merged":
        where.append("COALESCE(f.merged_into_flavour_id, 0) != 0")
    elif view == "hidden":
        where.append("COALESCE(f.is_hidden, 0) = 1")
    elif view == "invalid":
        where.append("COALESCE(f.is_invalid, 0) = 1")
    elif view == "active":
        where.append(
            "COALESCE(f.is_hidden, 0) = 0 AND COALESCE(f.is_invalid, 0) = 0 "
            "AND COALESCE(f.merged_into_flavour_id, 0) = 0"
        )

    wsql = " AND ".join(where)
    dup_join = """
    LEFT JOIN (
      SELECT LOWER(TRIM(COALESCE(brand, ''))) AS b,
             LOWER(TRIM(COALESCE(canonical_name, ''))) AS n,
             COUNT(*) AS c
      FROM flavours
      GROUP BY 1, 2
    ) dg ON dg.b = LOWER(TRIM(COALESCE(f.brand, '')))
          AND dg.n = LOWER(TRIM(COALESCE(f.canonical_name, '')))
    """
    count_row = db.execute(
        f"SELECT COUNT(1) AS c FROM flavours f {dup_join} WHERE {wsql}",
        tuple(params),
    ).fetchone()
    total = int(count_row["c"] or 0) if count_row else 0
    rows = db.execute(
        f"""
        SELECT f.id, f.canonical_name, f.brand, f.category, f.source, f.source_flavor_ref_id,
               COALESCE(f.is_hidden, 0) AS is_hidden,
               COALESCE(f.is_invalid, 0) AS is_invalid,
               COALESCE(f.merged_into_flavour_id, 0) AS merged_into_flavour_id,
               COALESCE(f.moderation_status, 'active') AS moderation_status,
               COALESCE(f.moderation_reason, '') AS moderation_reason,
               COALESCE(dg.c, 1) AS dup_group_size,
               CASE
                 WHEN COALESCE(TRIM(f.canonical_name), '') = '' OR COALESCE(TRIM(f.brand), '') = ''
                      OR LENGTH(COALESCE(TRIM(f.canonical_name), '')) < 2 THEN 1
                 ELSE 0
               END AS suspicious
        FROM flavours f
        {dup_join}
        WHERE {wsql}
        ORDER BY dup_group_size DESC, LOWER(COALESCE(f.brand, '')), LOWER(COALESCE(f.canonical_name, '')), f.id
        LIMIT ? OFFSET ?
        """,
        (*params, page_size, offset),
    ).fetchall()
    return [dict(r) for r in rows], total


def fetch_flavour_identity_bundle(flavour_id: int) -> dict[str, Any] | None:
    db = get_db()
    row = db.execute(
        """
        SELECT f.id, f.canonical_name, f.brand, f.category, f.source, f.source_flavor_ref_id,
               COALESCE(f.is_hidden, 0) AS is_hidden,
               COALESCE(f.is_invalid, 0) AS is_invalid,
               COALESCE(f.moderation_status, 'active') AS moderation_status,
               COALESCE(f.moderation_reason, '') AS moderation_reason,
               COALESCE(f.merged_into_flavour_id, 0) AS merged_into_flavour_id,
               f.merged_at,
               mt.canonical_name AS merge_target_name,
               mt.brand AS merge_target_brand
        FROM flavours f
        LEFT JOIN flavours mt ON mt.id = f.merged_into_flavour_id
        WHERE f.id = ?
        """,
        (int(flavour_id),),
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    ref_id = out.get("source_flavor_ref_id")
    ri_count = 0
    if ref_id:
        rc = db.execute(
            "SELECT COUNT(1) AS c FROM recipe_items WHERE ingredient_id = ?",
            (int(ref_id),),
        ).fetchone()
        ri_count = int(rc["c"] or 0) if rc else 0
    out["recipe_item_count"] = ri_count
    sc = db.execute(
        "SELECT COUNT(1) AS c FROM user_stash_items WHERE flavour_id = ?",
        (int(flavour_id),),
    ).fetchone()
    out["stash_count"] = int(sc["c"] or 0) if sc else 0
    nc = db.execute(
        "SELECT COUNT(1) AS c FROM flavour_notes WHERE flavour_id = ?",
        (int(flavour_id),),
    ).fetchone()
    out["note_count"] = int(nc["c"] or 0) if nc else 0
    rvc = db.execute(
        "SELECT COUNT(1) AS c FROM flavour_reviews WHERE flavour_id = ?",
        (int(flavour_id),),
    ).fetchone()
    out["review_count"] = int(rvc["c"] or 0) if rvc else 0
    return out


def _assert_flavour_merge_pair(*, source: dict[str, Any], target: dict[str, Any]) -> None:
    sid = int(source["id"])
    tid = int(target["id"])
    if sid == tid:
        raise ValueError("Source and target must be different flavours.")
    if int(source.get("merged_into_flavour_id") or 0):
        raise ValueError("Source flavour is already merged into another record.")
    if int(target.get("merged_into_flavour_id") or 0):
        raise ValueError("Target flavour is merged into another record; pick the canonical target.")


def _source_target_ref_ids(*, source: dict[str, Any], target: dict[str, Any]) -> tuple[int, int]:
    _assert_flavour_merge_pair(source=source, target=target)
    rs = source.get("source_flavor_ref_id")
    rt = target.get("source_flavor_ref_id")
    if rs is None or rt is None:
        raise ValueError("Both flavours need a linked flavor_reference (source_flavor_ref_id) for a full merge.")
    return int(rs), int(rt)


def _repoint_recipe_items_merge_dup_versions(db, *, source_ref_id: int, target_ref_id: int) -> int:
    rows = db.execute(
        "SELECT id, version_id, amount_percent FROM recipe_items WHERE ingredient_id = ?",
        (int(source_ref_id),),
    ).fetchall()
    updated = 0
    for row in rows:
        rid = int(row["id"])
        vid = int(row["version_id"])
        amt = float(row["amount_percent"] or 0.0)
        other = db.execute(
            "SELECT id, amount_percent FROM recipe_items WHERE version_id = ? AND ingredient_id = ?",
            (vid, int(target_ref_id)),
        ).fetchone()
        if other:
            combined = float(other["amount_percent"] or 0.0) + amt
            db.execute(
                "UPDATE recipe_items SET amount_percent = ? WHERE id = ?",
                (combined, int(other["id"])),
            )
            db.execute("DELETE FROM recipe_items WHERE id = ?", (rid,))
        else:
            db.execute(
                "UPDATE recipe_items SET ingredient_id = ? WHERE id = ?",
                (int(target_ref_id), rid),
            )
        updated += 1
    return updated


def _merge_user_stash_flavours(db, *, source_flavour_id: int, target_flavour_id: int) -> int:
    moved = 0
    rows = db.execute(
        "SELECT id, user_id, quantity_ml, is_favorite, last_used_at FROM user_stash_items WHERE flavour_id = ?",
        (int(source_flavour_id),),
    ).fetchall()
    for row in rows:
        uid = int(row["user_id"])
        qty = float(row["quantity_ml"] or 0.0)
        existing = db.execute(
            """
            SELECT id, quantity_ml, is_favorite, last_used_at
            FROM user_stash_items
            WHERE user_id = ? AND flavour_id = ?
            """,
            (uid, int(target_flavour_id)),
        ).fetchone()
        if existing:
            new_qty = float(existing["quantity_ml"] or 0.0) + qty
            fav = max(int(existing["is_favorite"] or 0), int(row["is_favorite"] or 0))
            db.execute(
                """
                UPDATE user_stash_items
                SET quantity_ml = ?, is_favorite = ?, last_used_at = COALESCE(?, last_used_at)
                WHERE id = ?
                """,
                (new_qty, fav, row["last_used_at"], int(existing["id"])),
            )
            db.execute("DELETE FROM user_stash_items WHERE id = ?", (int(row["id"]),))
        else:
            db.execute(
                "UPDATE user_stash_items SET flavour_id = ? WHERE id = ?",
                (int(target_flavour_id), int(row["id"])),
            )
        moved += 1
    return moved


def set_flavour_merge_pointer_only(
    *,
    source_flavour_id: int,
    target_flavour_id: int,
    actor_user_id: int,
    actor_role: str,
    reason: str = "",
) -> dict[str, Any]:
    """Alias source → target without repointing recipe ingredients / stash (only UGC notes/reviews).

    Allowed only when no recipe_items use the source flavour_reference and no stash rows on source.
    """
    db = get_db()
    src_row = db.execute("SELECT * FROM flavours WHERE id = ?", (int(source_flavour_id),)).fetchone()
    tgt_row = db.execute("SELECT * FROM flavours WHERE id = ?", (int(target_flavour_id),)).fetchone()
    if not src_row or not tgt_row:
        raise ValueError("Source or target flavour not found.")
    source, target = dict(src_row), dict(tgt_row)
    _assert_flavour_merge_pair(source=source, target=target)
    rs = source.get("source_flavor_ref_id")

    if rs is not None:
        ri_n = db.execute(
            "SELECT COUNT(1) AS c FROM recipe_items WHERE ingredient_id = ?",
            (int(rs),),
        ).fetchone()
        if int(ri_n["c"] or 0) > 0:
            raise ValueError("This source still has recipe usage; use full merge instead of pointer-only.")
    st_n = db.execute(
        "SELECT COUNT(1) AS c FROM user_stash_items WHERE flavour_id = ?",
        (int(source_flavour_id),),
    ).fetchone()
    if int(st_n["c"] or 0) > 0:
        raise ValueError("This source still has stash rows; use full merge instead of pointer-only.")

    before_src = dict(source)
    db.execute(
        "UPDATE flavour_notes SET flavour_id = ? WHERE flavour_id = ?",
        (int(target_flavour_id), int(source_flavour_id)),
    )
    db.execute(
        "UPDATE flavour_reviews SET flavour_id = ? WHERE flavour_id = ?",
        (int(target_flavour_id), int(source_flavour_id)),
    )
    db.execute(
        """
        UPDATE flavours
        SET merged_into_flavour_id = ?,
            merged_at = CURRENT_TIMESTAMP,
            is_hidden = 1,
            moderation_status = 'merged',
            moderation_reason = COALESCE(?, moderation_reason),
            updated_by_user_id = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            int(target_flavour_id),
            (reason or "").strip() or None,
            int(actor_user_id),
            int(source_flavour_id),
        ),
    )
    after_src = db.execute("SELECT * FROM flavours WHERE id = ?", (int(source_flavour_id),)).fetchone()
    insert_admin_audit_log(
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        action="flavour_merge_pointer_only",
        target_type="flavour",
        target_id=int(source_flavour_id),
        reason=reason or "",
        details={
            "target_flavour_id": int(target_flavour_id),
            "before": before_src,
            "after": dict(after_src) if after_src else {},
        },
    )
    db.commit()
    return {"source_flavour_id": int(source_flavour_id), "target_flavour_id": int(target_flavour_id)}


def execute_flavour_merge_into(
    *,
    source_flavour_id: int,
    target_flavour_id: int,
    actor_user_id: int,
    actor_role: str,
    reason: str = "",
) -> dict[str, Any]:
    """Repoint recipe_items, stash, notes, and reviews; mark source merged (no flavour row delete)."""
    db = get_db()
    src_row = db.execute("SELECT * FROM flavours WHERE id = ?", (int(source_flavour_id),)).fetchone()
    tgt_row = db.execute("SELECT * FROM flavours WHERE id = ?", (int(target_flavour_id),)).fetchone()
    if not src_row or not tgt_row:
        raise ValueError("Source or target flavour not found.")
    source, target = dict(src_row), dict(tgt_row)
    rs, rt = _source_target_ref_ids(source=source, target=target)

    before_src = dict(source)
    ri_updated = _repoint_recipe_items_merge_dup_versions(db, source_ref_id=rs, target_ref_id=rt)
    stash_moved = _merge_user_stash_flavours(
        db, source_flavour_id=int(source_flavour_id), target_flavour_id=int(target_flavour_id)
    )
    db.execute(
        "UPDATE flavour_notes SET flavour_id = ? WHERE flavour_id = ?",
        (int(target_flavour_id), int(source_flavour_id)),
    )
    db.execute(
        "UPDATE flavour_reviews SET flavour_id = ? WHERE flavour_id = ?",
        (int(target_flavour_id), int(source_flavour_id)),
    )
    db.execute(
        """
        UPDATE flavours
        SET merged_into_flavour_id = ?,
            merged_at = CURRENT_TIMESTAMP,
            is_hidden = 1,
            moderation_status = 'merged',
            moderation_reason = COALESCE(?, moderation_reason),
            updated_by_user_id = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            int(target_flavour_id),
            (reason or "").strip() or None,
            int(actor_user_id),
            int(source_flavour_id),
        ),
    )
    after_src = db.execute("SELECT * FROM flavours WHERE id = ?", (int(source_flavour_id),)).fetchone()
    insert_admin_audit_log(
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        action="flavour_merge_execute",
        target_type="flavour",
        target_id=int(source_flavour_id),
        reason=reason or "",
        details={
            "target_flavour_id": int(target_flavour_id),
            "source_flavor_ref_id": rs,
            "target_flavor_ref_id": rt,
            "recipe_items_touched": ri_updated,
            "stash_rows_merged": stash_moved,
            "before": before_src,
            "after": dict(after_src) if after_src else {},
        },
    )
    db.commit()
    return {
        "source_flavour_id": int(source_flavour_id),
        "target_flavour_id": int(target_flavour_id),
        "recipe_items_touched": ri_updated,
        "stash_rows_merged": stash_moved,
    }


def update_flavour_moderation(
    *,
    flavour_id: int,
    actor_user_id: int,
    actor_role: str,
    is_hidden: int | None = None,
    is_invalid: int | None = None,
    moderation_status: str | None = None,
    moderation_reason: str | None = None,
    canonical_name: str | None = None,
    brand: str | None = None,
    category: str | None = None,
) -> None:
    db = get_db()
    row = db.execute("SELECT * FROM flavours WHERE id = ?", (int(flavour_id),)).fetchone()
    if not row:
        raise ValueError("Flavour not found.")
    current = dict(row)
    db.execute(
        """
        UPDATE flavours
        SET canonical_name = COALESCE(?, canonical_name),
            brand = COALESCE(?, brand),
            category = COALESCE(?, category),
            is_hidden = COALESCE(?, is_hidden),
            is_invalid = COALESCE(?, is_invalid),
            moderation_status = COALESCE(?, moderation_status),
            moderation_reason = COALESCE(?, moderation_reason),
            updated_by_user_id = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            canonical_name,
            brand,
            category,
            is_hidden,
            is_invalid,
            moderation_status,
            moderation_reason,
            int(actor_user_id),
            int(flavour_id),
        ),
    )
    updated = db.execute("SELECT * FROM flavours WHERE id = ?", (int(flavour_id),)).fetchone()
    insert_admin_audit_log(
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        action="flavour_moderation_update",
        target_type="flavour",
        target_id=int(flavour_id),
        reason=moderation_reason or "",
        details={"before": current, "after": dict(updated) if updated else {}},
    )
    db.commit()


def list_recipe_moderation_rows(
    *,
    search: str = "",
    visibility: str = "",
    status: str = "",
    moderation_status: str = "",
    hidden_filter: str = "",
    page: int = 1,
    page_size: int = 30,
) -> tuple[list[dict[str, Any]], int]:
    db = get_db()
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 30))
    offset = (page - 1) * page_size
    where = ["1=1"]
    params: list[Any] = []
    if search:
        where.append("(r.name LIKE ? OR COALESCE(u.username, '') LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like])
    if visibility:
        where.append("LOWER(COALESCE(r.visibility, 'private')) = ?")
        params.append(visibility.strip().lower())
    if status:
        where.append("LOWER(COALESCE(r.status, '')) = ?")
        params.append(status.strip().lower())
    if moderation_status:
        where.append("LOWER(COALESCE(r.moderation_status, 'active')) = ?")
        params.append(moderation_status.strip().lower())
    if hidden_filter == "hidden":
        where.append("COALESCE(r.is_hidden, 0) = 1")
    elif hidden_filter == "visible":
        where.append("COALESCE(r.is_hidden, 0) = 0")

    wsql = " AND ".join(where)
    rows = db.execute(
        f"""
        SELECT
            r.id,
            r.name,
            r.slug,
            r.status,
            COALESCE(r.visibility, 'private') AS visibility,
            COALESCE(r.is_hidden, 0) AS is_hidden,
            COALESCE(r.active, 1) AS active,
            COALESCE(r.moderation_status, 'active') AS moderation_status,
            COALESCE(r.moderation_reason, '') AS moderation_reason,
            r.current_version_id,
            r.updated_at,
            r.created_at,
            COALESCE(u.username, '') AS owner_username,
            rv.version_number AS current_version_number
        FROM recipes r
        LEFT JOIN users u ON u.id = r.created_by
        LEFT JOIN recipe_versions rv ON rv.id = r.current_version_id
        WHERE {wsql}
        ORDER BY COALESCE(r.updated_at, r.created_at) DESC, r.id DESC
        LIMIT ? OFFSET ?
        """,
        (*params, page_size, offset),
    ).fetchall()
    count_row = db.execute(
        f"SELECT COUNT(1) AS c FROM recipes r LEFT JOIN users u ON u.id = r.created_by WHERE {wsql}",
        tuple(params),
    ).fetchone()
    total = int(count_row["c"] or 0) if count_row else 0
    return [dict(r) for r in rows], total


def fetch_admin_recipe_bundle(recipe_id: int) -> dict[str, Any] | None:
    db = get_db()
    recipe = db.execute(
        """
        SELECT r.*, COALESCE(u.username, '') AS owner_username
        FROM recipes r
        LEFT JOIN users u ON u.id = r.created_by
        WHERE r.id = ?
        """,
        (int(recipe_id),),
    ).fetchone()
    if not recipe:
        return None
    rdict = dict(recipe)
    versions = db.execute(
        """
        SELECT id, recipe_id, version_number, target_vg_pg_ratio, total_flavor_percent, created_at, notes
        FROM recipe_versions
        WHERE recipe_id = ?
        ORDER BY id DESC
        """,
        (int(recipe_id),),
    ).fetchall()
    rdict["versions"] = [dict(v) for v in versions]
    cur_id = rdict.get("current_version_id")
    current = None
    if cur_id:
        current = db.execute(
            "SELECT * FROM recipe_versions WHERE id = ?",
            (int(cur_id),),
        ).fetchone()
    rdict["current_version_row"] = dict(current) if current else None
    return rdict


def update_recipe_moderation(
    *,
    recipe_id: int,
    actor_user_id: int,
    actor_role: str,
    action: str,
    reason: str = "",
    moderation_status: str | None = None,
) -> None:
    db = get_db()
    row = db.execute("SELECT * FROM recipes WHERE id = ?", (int(recipe_id),)).fetchone()
    if not row:
        raise ValueError("Recipe not found.")
    before = dict(row)
    action = (action or "").strip().lower()

    set_hidden: int | None = None
    set_active: int | None = None
    set_mod_status: str | None = None
    set_reason = (reason or "").strip() or None

    if action == "hide":
        set_hidden = 1
        set_mod_status = "hidden"
    elif action == "unhide":
        set_hidden = 0
        prev_mod = str(before.get("moderation_status") or "active").strip().lower()
        if prev_mod == "hidden":
            set_mod_status = "active"
    elif action == "archive":
        set_active = 0
        set_mod_status = "archived"
    elif action == "restore":
        set_active = 1
        set_hidden = 0
        set_mod_status = "active"
    elif action == "set_moderation":
        set_mod_status = (moderation_status or "").strip().lower()
        if not set_mod_status:
            raise ValueError("moderation_status is required.")
        ms = set_mod_status
        if ms == "archived":
            set_active = 0
            set_hidden = 0
        elif ms == "hidden":
            set_hidden = 1
            set_active = 1
        elif ms in ("active", "review", "flagged", "removed"):
            set_hidden = 0
            set_active = 1
    else:
        raise ValueError("Unknown recipe moderation action.")

    db.execute(
        """
        UPDATE recipes
        SET is_hidden = COALESCE(?, is_hidden),
            active = COALESCE(?, active),
            moderation_status = COALESCE(?, moderation_status),
            moderation_reason = COALESCE(?, moderation_reason),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            set_hidden,
            set_active,
            set_mod_status,
            set_reason,
            int(recipe_id),
        ),
    )
    after_row = db.execute("SELECT * FROM recipes WHERE id = ?", (int(recipe_id),)).fetchone()
    after = dict(after_row) if after_row else {}
    insert_admin_audit_log(
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        action=f"recipe_moderation_{action}",
        target_type="recipe",
        target_id=int(recipe_id),
        reason=reason or "",
        details={"before": before, "after": after, "action": action},
    )
    db.commit()


_CONTENT_TABLE = {
    "note": "flavour_notes",
    "review": "flavour_reviews",
    "feedback": "recipe_feedback",
}

_CONTENT_AUDIT_TYPE = {
    "note": "flavour_note",
    "review": "flavour_review",
    "feedback": "recipe_feedback",
}


def list_community_content_queue(
    *,
    content_type: str,
    search: str = "",
    moderation_status: str = "",
    page: int = 1,
    page_size: int = 35,
) -> tuple[list[dict[str, Any]], int]:
    db = get_db()
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 35))
    offset = (page - 1) * page_size
    ct = (content_type or "feedback").strip().lower()
    if ct not in _CONTENT_TABLE:
        ct = "feedback"

    alias = "fn" if ct == "note" else ("fr" if ct == "review" else "rf")
    where = ["1=1"]
    params: list[Any] = []
    if search:
        like = f"%{search}%"
        if ct == "note":
            where.append("(fn.note LIKE ? OR COALESCE(u.username,'') LIKE ? OR COALESCE(f.canonical_name,'') LIKE ?)")
            params.extend([like, like, like])
        elif ct == "review":
            where.append(
                "(fr.review LIKE ? OR COALESCE(u.username,'') LIKE ? OR COALESCE(f.canonical_name,'') LIKE ?)"
            )
            params.extend([like, like, like])
        else:
            where.append(
                "(rf.body LIKE ? OR COALESCE(u.username,'') LIKE ? OR COALESCE(r.name,'') LIKE ?)"
            )
            params.extend([like, like, like])
    if moderation_status:
        where.append(f"LOWER(COALESCE({alias}.moderation_status,'active')) = ?")
        params.append(moderation_status.strip().lower())

    wsql = " AND ".join(where)
    if ct == "note":
        count_row = db.execute(
            f"""
            SELECT COUNT(1) AS c
            FROM flavour_notes fn
            LEFT JOIN users u ON u.id = fn.author_user_id
            JOIN flavours f ON f.id = fn.flavour_id
            WHERE {wsql}
            """,
            tuple(params),
        ).fetchone()
        total = int(count_row["c"] or 0) if count_row else 0
        rows = db.execute(
            f"""
            SELECT
                'note' AS content_kind,
                fn.id AS content_id,
                fn.created_at,
                COALESCE(fn.is_hidden, 0) AS is_hidden,
                COALESCE(fn.moderation_status, 'active') AS moderation_status,
                COALESCE(fn.moderation_reason, '') AS moderation_reason,
                fn.note AS body,
                '' AS extra,
                COALESCE(u.username, '') AS author_username,
                f.id AS context_id,
                f.canonical_name AS context_title,
                'flavour' AS context_type
            FROM flavour_notes fn
            LEFT JOIN users u ON u.id = fn.author_user_id
            JOIN flavours f ON f.id = fn.flavour_id
            WHERE {wsql}
            ORDER BY fn.id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, page_size, offset),
        ).fetchall()
    elif ct == "review":
        count_row = db.execute(
            f"""
            SELECT COUNT(1) AS c
            FROM flavour_reviews fr
            LEFT JOIN users u ON u.id = fr.author_user_id
            JOIN flavours f ON f.id = fr.flavour_id
            WHERE {wsql}
            """,
            tuple(params),
        ).fetchone()
        total = int(count_row["c"] or 0) if count_row else 0
        rows = db.execute(
            f"""
            SELECT
                'review' AS content_kind,
                fr.id AS content_id,
                fr.created_at,
                COALESCE(fr.is_hidden, 0) AS is_hidden,
                COALESCE(fr.moderation_status, 'active') AS moderation_status,
                COALESCE(fr.moderation_reason, '') AS moderation_reason,
                COALESCE(fr.review, '') AS body,
                printf('rating %.1f steep %d', fr.rating, COALESCE(fr.steep_days, 0)) AS extra,
                COALESCE(u.username, '') AS author_username,
                f.id AS context_id,
                f.canonical_name AS context_title,
                'flavour' AS context_type
            FROM flavour_reviews fr
            LEFT JOIN users u ON u.id = fr.author_user_id
            JOIN flavours f ON f.id = fr.flavour_id
            WHERE {wsql}
            ORDER BY fr.id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, page_size, offset),
        ).fetchall()
    else:
        count_row = db.execute(
            f"""
            SELECT COUNT(1) AS c
            FROM recipe_feedback rf
            LEFT JOIN users u ON u.id = rf.author_user_id
            JOIN recipes r ON r.id = rf.recipe_id
            WHERE {wsql}
            """,
            tuple(params),
        ).fetchone()
        total = int(count_row["c"] or 0) if count_row else 0
        rows = db.execute(
            f"""
            SELECT
                'feedback' AS content_kind,
                rf.id AS content_id,
                rf.created_at,
                COALESCE(rf.is_hidden, 0) AS is_hidden,
                COALESCE(rf.moderation_status, 'active') AS moderation_status,
                COALESCE(rf.moderation_reason, '') AS moderation_reason,
                COALESCE(rf.body, '') AS body,
                printf('score %.1f v%s', rf.score_overall, COALESCE(rf.recipe_version_id, 0)) AS extra,
                COALESCE(u.username, '') AS author_username,
                r.id AS context_id,
                r.name AS context_title,
                'recipe' AS context_type
            FROM recipe_feedback rf
            LEFT JOIN users u ON u.id = rf.author_user_id
            JOIN recipes r ON r.id = rf.recipe_id
            WHERE {wsql}
            ORDER BY rf.id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, page_size, offset),
        ).fetchall()

    return [dict(r) for r in rows], total


def update_community_content_moderation(
    *,
    kind: str,
    content_id: int,
    actor_user_id: int,
    actor_role: str,
    action: str,
    reason: str = "",
) -> None:
    k = (kind or "").strip().lower()
    if k not in _CONTENT_TABLE:
        raise ValueError("Invalid content kind.")
    table = _CONTENT_TABLE[k]
    audit_type = _CONTENT_AUDIT_TYPE[k]
    db = get_db()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (int(content_id),)).fetchone()
    if not row:
        raise ValueError("Content not found.")
    before = dict(row)
    action = (action or "").strip().lower()
    set_hidden: int | None = None
    set_mod: str | None = None
    set_reason = (reason or "").strip() or None

    if action == "hide":
        set_hidden = 1
        set_mod = "hidden"
    elif action == "unhide":
        set_hidden = 0
        set_mod = "active"
    elif action == "mark_spam":
        set_hidden = 1
        set_mod = "spam"
    elif action == "mark_review":
        set_mod = "review"
        set_hidden = 0
    elif action == "restore_active":
        set_hidden = 0
        set_mod = "active"
    else:
        raise ValueError("Unknown moderation action.")

    db.execute(
        f"""
        UPDATE {table}
        SET is_hidden = COALESCE(?, is_hidden),
            moderation_status = COALESCE(?, moderation_status),
            moderation_reason = COALESCE(?, moderation_reason)
        WHERE id = ?
        """,
        (set_hidden, set_mod, set_reason, int(content_id)),
    )
    after_row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (int(content_id),)).fetchone()
    after = dict(after_row) if after_row else {}
    insert_admin_audit_log(
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        action=f"ugc_moderation_{action}",
        target_type=audit_type,
        target_id=int(content_id),
        reason=reason or "",
        details={"before": before, "after": after, "kind": k, "action": action},
    )
    db.commit()


def actor_outranks_target_user(actor_role: str, target_role: str) -> bool:
    from utils.auth import ROLES_HIERARCHY

    a = ROLES_HIERARCHY.get(str(actor_role or "").strip(), 0)
    t = ROLES_HIERARCHY.get(str(target_role or "").strip(), 0)
    return a > t


def assert_actor_can_moderate_user(*, actor_id: int, actor_role: str, target: dict[str, Any]) -> None:
    tid = int(target.get("id") or 0)
    if tid == int(actor_id):
        raise ValueError("You cannot moderate your own account through this console.")
    if not actor_outranks_target_user(actor_role, str(target.get("role") or "")):
        raise ValueError("Insufficient authority to moderate this user.")


def list_distinct_user_roles() -> list[str]:
    db = get_db()
    rows = db.execute(
        """
        SELECT DISTINCT TRIM(role) AS r
        FROM users
        WHERE COALESCE(TRIM(role), '') != ''
        ORDER BY r COLLATE NOCASE
        """
    ).fetchall()
    return [str(r["r"]) for r in rows if r["r"]]


def list_user_moderation_rows(
    *,
    search: str = "",
    role_filter: str = "",
    moderation_status: str = "",
    suspended: str = "",
    banned: str = "",
    muted: str = "",
    page: int = 1,
    page_size: int = 35,
) -> tuple[list[dict[str, Any]], int]:
    db = get_db()
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 35))
    offset = (page - 1) * page_size
    where = ["1=1"]
    params: list[Any] = []
    if search:
        where.append(
            "(u.username LIKE ? OR COALESCE(u.email, '') LIKE ?)"
        )
        like = f"%{search}%"
        params.extend([like, like])
    if role_filter:
        where.append("LOWER(TRIM(COALESCE(u.role, ''))) = ?")
        params.append(role_filter.strip().lower())
    if moderation_status:
        where.append("LOWER(TRIM(COALESCE(u.moderation_status, 'active'))) = ?")
        params.append(moderation_status.strip().lower())
    if suspended == "yes":
        where.append("COALESCE(u.is_suspended, 0) = 1")
    elif suspended == "no":
        where.append("COALESCE(u.is_suspended, 0) = 0")
    if banned == "yes":
        where.append("COALESCE(u.is_banned, 0) = 1")
    elif banned == "no":
        where.append("COALESCE(u.is_banned, 0) = 0")
    if muted == "yes":
        where.append("COALESCE(u.is_muted, 0) = 1")
    elif muted == "no":
        where.append("COALESCE(u.is_muted, 0) = 0")

    wsql = " AND ".join(where)
    count_row = db.execute(
        f"SELECT COUNT(1) AS c FROM users u WHERE {wsql}",
        tuple(params),
    ).fetchone()
    total = int(count_row["c"] or 0) if count_row else 0
    rows = db.execute(
        f"""
        SELECT u.id, u.username, u.email, u.role, u.status, u.created_at,
               COALESCE(u.is_suspended, 0) AS is_suspended,
               COALESCE(u.is_banned, 0) AS is_banned,
               COALESCE(u.is_muted, 0) AS is_muted,
               COALESCE(u.moderation_status, 'active') AS moderation_status,
               COALESCE(u.moderation_note, '') AS moderation_note
        FROM users u
        WHERE {wsql}
        ORDER BY u.id DESC
        LIMIT ? OFFSET ?
        """,
        (*params, page_size, offset),
    ).fetchall()
    return [dict(r) for r in rows], total


def fetch_admin_user_bundle(user_id: int) -> dict[str, Any] | None:
    db = get_db()
    row = db.execute(
        """
        SELECT u.id, u.username, u.email, u.role, u.status, u.created_at,
               COALESCE(u.is_suspended, 0) AS is_suspended,
               COALESCE(u.is_banned, 0) AS is_banned,
               COALESCE(u.is_muted, 0) AS is_muted,
               COALESCE(u.moderation_status, 'active') AS moderation_status,
               COALESCE(u.moderation_note, '') AS moderation_note
        FROM users u
        WHERE u.id = ?
        """,
        (int(user_id),),
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    out["recipe_count"] = int(
        db.execute(
            "SELECT COUNT(1) AS c FROM recipes WHERE created_by = ?",
            (int(user_id),),
        ).fetchone()["c"]
        or 0
    )
    out["note_count"] = int(
        db.execute(
            "SELECT COUNT(1) AS c FROM flavour_notes WHERE author_user_id = ?",
            (int(user_id),),
        ).fetchone()["c"]
        or 0
    )
    out["review_count"] = int(
        db.execute(
            "SELECT COUNT(1) AS c FROM flavour_reviews WHERE author_user_id = ?",
            (int(user_id),),
        ).fetchone()["c"]
        or 0
    )
    out["feedback_count"] = int(
        db.execute(
            "SELECT COUNT(1) AS c FROM recipe_feedback WHERE author_user_id = ?",
            (int(user_id),),
        ).fetchone()["c"]
        or 0
    )
    out["recent_audits"] = list_admin_audit_logs_for_user_target(target_user_id=int(user_id), limit=25)
    return out


# Canonical role names assignable via TVW admin (must exist in utils.auth.ROLES_HIERARCHY).
TVW_ASSIGNABLE_ROLE_ORDER: tuple[str, ...] = (
    "Kid",
    "Hand",
    "member",
    "Mixer",
    "Ram",
    "moderator",
    "admin",
    "Foreman",
    "Master",
    "Old Goat",
)


def tvw_role_level(role: str | None) -> int:
    from utils.auth import ROLES_HIERARCHY

    return int(ROLES_HIERARCHY.get(str(role or "").strip(), 0))


def actor_may_assign_tv_role(*, actor_role: str, new_role: str) -> bool:
    """Whether actor_role may set a user to new_role (ignores target user)."""
    from utils.auth import ROLES_HIERARCHY

    ar = str(actor_role or "").strip()
    nr = str(new_role or "").strip()
    if nr not in TVW_ASSIGNABLE_ROLE_ORDER or nr not in ROLES_HIERARCHY:
        return False
    actor_lv = tvw_role_level(ar)
    new_lv = tvw_role_level(nr)
    if actor_lv < ROLES_HIERARCHY.get("admin", 3):
        return False

    if ar == "Old Goat":
        return True

    # Only Old Goat may grant the admin role (Master / admin / Foreman cannot).
    if nr == "admin":
        return False

    if nr == "Old Goat":
        return False
    if nr == "Master":
        return False

    if ar == "Master":
        return new_lv <= ROLES_HIERARCHY["admin"]

    if actor_lv >= ROLES_HIERARCHY["admin"]:
        return new_lv < ROLES_HIERARCHY["admin"]

    return False


def list_assignable_roles_for_actor(actor_role: str | None) -> list[str]:
    out = [r for r in TVW_ASSIGNABLE_ROLE_ORDER if actor_may_assign_tv_role(actor_role=str(actor_role or ""), new_role=r)]
    return out


def count_users_with_role_exact(*, role_name: str) -> int:
    db = get_db()
    row = db.execute(
        "SELECT COUNT(1) AS c FROM users WHERE TRIM(role) = ?",
        (str(role_name or "").strip(),),
    ).fetchone()
    return int(row["c"] or 0) if row else 0


def assert_actor_can_change_user_role(
    *,
    actor_user_id: int,
    actor_role: str,
    target_user_id: int,
    target_role: str,
    new_role: str,
) -> None:
    if int(actor_user_id) == int(target_user_id):
        raise ValueError("You cannot change your own role.")
    tr = str(target_role or "").strip()
    ar_actor = str(actor_role or "").strip()
    nr = str(new_role or "").strip()
    if tr == "Old Goat" and ar_actor != "Old Goat":
        raise ValueError("Only Old Goat can change the Old Goat account role.")
    tr_lv = tvw_role_level(tr)
    if tr and tr_lv == 0 and tr not in TVW_ASSIGNABLE_ROLE_ORDER:
        if ar_actor != "Old Goat":
            raise ValueError("Only Old Goat can reassign users with non-standard roles.")
    if nr not in TVW_ASSIGNABLE_ROLE_ORDER:
        raise ValueError("Invalid role selection.")
    if tr == "Old Goat" and nr != "Old Goat":
        if count_users_with_role_exact(role_name="Old Goat") <= 1:
            raise ValueError("Cannot remove or replace the last remaining Old Goat role.")
    if not actor_may_assign_tv_role(actor_role=ar_actor, new_role=nr):
        raise ValueError("You are not allowed to assign this role.")


def update_user_role_with_audit(
    *,
    target_user_id: int,
    new_role: str,
    actor_user_id: int,
    actor_role: str,
    reason: str,
) -> None:
    from models import users_repo

    reason_t = (reason or "").strip()
    if not reason_t:
        raise ValueError("A reason is required for role changes.")

    db = get_db()
    target = users_repo.fetch_user_moderation_row(int(target_user_id))
    if not target:
        raise ValueError("User not found.")

    old_role = str(target.get("role") or "").strip()
    nr = str(new_role or "").strip()

    assert_actor_can_change_user_role(
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        target_user_id=target_user_id,
        target_role=old_role,
        new_role=nr,
    )

    if nr == old_role:
        raise ValueError("The user already has this role.")

    before = dict(target)
    db.execute("UPDATE users SET role = ? WHERE id = ?", (nr, int(target_user_id)))
    after_row = users_repo.fetch_user_moderation_row(int(target_user_id))
    after = dict(after_row) if after_row else {}

    insert_admin_audit_log(
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        action="user_role_change",
        target_type="user",
        target_id=int(target_user_id),
        reason=reason_t,
        details={
            "target_username": target.get("username"),
            "old_role": old_role,
            "new_role": nr,
            "before": before,
            "after": after,
        },
    )
    db.commit()


def list_admin_audit_logs_for_user_target(*, target_user_id: int, limit: int = 25) -> list[dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT l.id, l.actor_user_id, l.actor_role, l.action, l.target_type, l.target_id, l.reason, l.details_json, l.created_at,
               COALESCE(u.username, 'unknown') AS actor_username
        FROM tvw_admin_audit_logs l
        LEFT JOIN users u ON u.id = l.actor_user_id
        WHERE l.target_type = 'user' AND l.target_id = ?
        ORDER BY l.id DESC
        LIMIT ?
        """,
        (int(target_user_id), max(1, int(limit))),
    ).fetchall()
    return [dict(r) for r in rows]


def update_user_moderation(
    *,
    target_user_id: int,
    actor_user_id: int,
    actor_role: str,
    action: str,
    reason: str = "",
    moderation_status: str | None = None,
    moderation_note: str | None = None,
) -> None:
    from models import users_repo

    db = get_db()
    target = users_repo.fetch_user_moderation_row(target_user_id)
    if not target:
        raise ValueError("User not found.")
    assert_actor_can_moderate_user(actor_id=actor_user_id, actor_role=actor_role, target=target)
    before = dict(target)
    action = (action or "").strip().lower()

    set_banned: int | None = None
    set_suspended: int | None = None
    set_muted: int | None = None
    set_mod: str | None = None
    set_note: str | None = None

    if action == "ban":
        set_banned = 1
        set_mod = "banned"
        set_suspended = 0
    elif action == "unban":
        set_banned = 0
        set_mod = "active"
    elif action == "suspend":
        set_suspended = 1
        set_mod = "suspended"
    elif action == "unsuspend":
        set_suspended = 0
        if str(before.get("moderation_status") or "").strip().lower() == "suspended":
            set_mod = "active"
    elif action == "mute":
        set_muted = 1
    elif action == "unmute":
        set_muted = 0
    elif action == "set_moderation_status":
        set_mod = (moderation_status or "").strip().lower()
        if not set_mod:
            raise ValueError("moderation_status is required.")
        if set_mod == "banned":
            set_banned = 1
            set_suspended = 0
        elif set_mod == "suspended":
            set_suspended = 1
            set_banned = 0
        elif set_mod in ("active", "review", "flagged"):
            set_banned = 0
            set_suspended = 0
    elif action == "set_moderation_note":
        if moderation_note is None:
            raise ValueError("moderation_note is required.")
        set_note = str(moderation_note).strip() or None
    else:
        raise ValueError("Unknown user moderation action.")

    db.execute(
        """
        UPDATE users
        SET is_banned = COALESCE(?, is_banned),
            is_suspended = COALESCE(?, is_suspended),
            is_muted = COALESCE(?, is_muted),
            moderation_status = COALESCE(?, moderation_status),
            moderation_note = COALESCE(?, moderation_note)
        WHERE id = ?
        """,
        (set_banned, set_suspended, set_muted, set_mod, set_note, int(target_user_id)),
    )
    after_row = users_repo.fetch_user_moderation_row(target_user_id)
    after = dict(after_row) if after_row else {}
    insert_admin_audit_log(
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        action=f"user_moderation_{action}",
        target_type="user",
        target_id=int(target_user_id),
        reason=reason or "",
        details={"before": before, "after": after, "action": action},
    )
    db.commit()
