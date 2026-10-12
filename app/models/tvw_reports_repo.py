from __future__ import annotations

from typing import Any

from db import get_db
from models import recipes_repo
from models.tvw_admin_repo import insert_admin_audit_log

REPORT_TARGET_TYPES = frozenset({"recipe", "note", "review", "feedback"})
REPORT_REASONS = frozenset(
    {
        "spam",
        "abusive",
        "duplicate",
        "wrong_category",
        "misleading",
        "not_flavour",
        "other",
    }
)
REPORT_STATUSES = frozenset({"open", "reviewed", "dismissed", "escalated"})


def _validate_report_target(
    *,
    db,
    target_type: str,
    target_id: int,
    reporter_user_id: int,
    viewer_is_old_goat: bool,
) -> None:
    tid = int(target_id)
    r = str(target_type).strip().lower()
    uid = int(reporter_user_id)

    if r == "recipe":
        row = db.execute("SELECT id, name, created_by FROM recipes WHERE id = ?", (tid,)).fetchone()
        if not row:
            raise ValueError("Recipe not found.")
        rec = dict(row)
        if not recipes_repo.can_view_recipe_in_tvw_community(
            rec, viewer_user_id=uid, viewer_is_old_goat=viewer_is_old_goat
        ):
            raise ValueError("You cannot report this recipe.")
        if int(rec.get("created_by") or 0) == uid:
            raise ValueError("You cannot report your own recipe.")
        return

    vis_note = recipes_repo.tvw_community_content_visible_sql("fn")
    vis_rev = recipes_repo.tvw_community_content_visible_sql("fr")
    vis_fb = recipes_repo.tvw_community_content_visible_sql("rf")

    if r == "note":
        row = db.execute(
            f"SELECT id, author_user_id FROM flavour_notes fn WHERE fn.id = ? AND {vis_note}",
            (tid,),
        ).fetchone()
        if not row:
            raise ValueError("Note not found or not visible.")
        if int(row["author_user_id"] or 0) == uid:
            raise ValueError("You cannot report your own note.")
        return

    if r == "review":
        row = db.execute(
            f"SELECT id, author_user_id FROM flavour_reviews fr WHERE fr.id = ? AND {vis_rev}",
            (tid,),
        ).fetchone()
        if not row:
            raise ValueError("Review not found or not visible.")
        if int(row["author_user_id"] or 0) == uid:
            raise ValueError("You cannot report your own review.")
        return

    if r == "feedback":
        row = db.execute(
            f"""
            SELECT rf.id, rf.author_user_id, rf.recipe_id
            FROM recipe_feedback rf
            JOIN recipes r ON r.id = rf.recipe_id
            WHERE rf.id = ? AND {vis_fb}
            """,
            (tid,),
        ).fetchone()
        if not row:
            raise ValueError("Feedback not found or not visible.")
        if int(row["author_user_id"] or 0) == uid:
            raise ValueError("You cannot report your own feedback.")
        rid = int(row["recipe_id"])
        rrow = db.execute("SELECT id, name, created_by FROM recipes WHERE id = ?", (rid,)).fetchone()
        if not rrow or not recipes_repo.can_view_recipe_in_tvw_community(
            dict(rrow), viewer_user_id=uid, viewer_is_old_goat=viewer_is_old_goat
        ):
            raise ValueError("You cannot report this feedback.")
        return

    raise ValueError("Invalid report target type.")


def insert_content_report(
    *,
    reporter_user_id: int,
    target_type: str,
    target_id: int,
    reason: str,
    detail: str | None = None,
    viewer_is_old_goat: bool = False,
) -> int:
    tt = str(target_type or "").strip().lower()
    rs = str(reason or "").strip().lower().replace("-", "_")
    if tt not in REPORT_TARGET_TYPES:
        raise ValueError("Invalid target type.")
    if rs not in REPORT_REASONS:
        raise ValueError("Invalid report reason.")
    db = get_db()
    _validate_report_target(
        db=db,
        target_type=tt,
        target_id=int(target_id),
        reporter_user_id=int(reporter_user_id),
        viewer_is_old_goat=bool(viewer_is_old_goat),
    )
    det = (detail or "").strip() or None
    cur = db.execute(
        """
        INSERT INTO tvw_content_reports
        (reporter_user_id, target_type, target_id, reason, detail, status)
        VALUES (?, ?, ?, ?, ?, 'open')
        """,
        (int(reporter_user_id), tt, int(target_id), rs, det),
    )
    db.commit()
    return int(cur.lastrowid)


def list_content_reports(
    *,
    target_type: str = "",
    status: str = "",
    search: str = "",
    page: int = 1,
    page_size: int = 35,
) -> tuple[list[dict[str, Any]], int]:
    db = get_db()
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 35))
    offset = (page - 1) * page_size
    where = ["1=1"]
    params: list[Any] = []

    if target_type:
        where.append("r.target_type = ?")
        params.append(target_type.strip().lower())
    if status:
        where.append("LOWER(TRIM(r.status)) = ?")
        params.append(status.strip().lower())
    if search:
        like = f"%{search}%"
        where.append(
            """(
            CAST(r.target_id AS TEXT) LIKE ?
            OR CAST(r.id AS TEXT) LIKE ?
            OR COALESCE(ru.username, '') LIKE ?
            OR COALESCE(r.detail, '') LIKE ?
            OR COALESCE(
              CASE r.target_type
                WHEN 'recipe' THEN (SELECT name FROM recipes WHERE id = r.target_id)
                WHEN 'note' THEN (SELECT substr(note, 1, 120) FROM flavour_notes WHERE id = r.target_id)
                WHEN 'review' THEN (SELECT substr(review, 1, 120) FROM flavour_reviews WHERE id = r.target_id)
                WHEN 'feedback' THEN (SELECT substr(body, 1, 120) FROM recipe_feedback WHERE id = r.target_id)
              END, ''
            ) LIKE ?
            )"""
        )
        params.extend([like, like, like, like, like])

    wsql = " AND ".join(where)
    total_row = db.execute(
        f"SELECT COUNT(1) AS c FROM tvw_content_reports r LEFT JOIN users ru ON ru.id = r.reporter_user_id WHERE {wsql}",
        tuple(params),
    ).fetchone()
    total = int(total_row["c"] or 0) if total_row else 0

    rows = db.execute(
        f"""
        SELECT
          r.id, r.reporter_user_id, r.target_type, r.target_id, r.reason, r.detail, r.status,
          r.created_at, r.updated_at, r.reviewed_by_user_id, r.reviewed_at,
          COALESCE(ru.username, 'unknown') AS reporter_username,
          COALESCE(rb.username, '') AS reviewed_by_username,
          CASE r.target_type
            WHEN 'recipe' THEN COALESCE((SELECT name FROM recipes WHERE id = r.target_id), '(missing)')
            WHEN 'note' THEN COALESCE(
              'Note #' || r.target_id || ' · ' || substr((SELECT note FROM flavour_notes WHERE id = r.target_id), 1, 100),
              '(missing)'
            )
            WHEN 'review' THEN COALESCE(
              'Review #' || r.target_id || ' · ' || substr((SELECT review FROM flavour_reviews WHERE id = r.target_id), 1, 100),
              '(missing)'
            )
            WHEN 'feedback' THEN COALESCE(
              'Feedback #' || r.target_id || ' · ' || substr((SELECT body FROM recipe_feedback WHERE id = r.target_id), 1, 100),
              '(missing)'
            )
            ELSE '(unknown)'
          END AS target_summary,
          CASE r.target_type
            WHEN 'recipe' THEN (SELECT slug FROM recipes WHERE id = r.target_id)
            ELSE NULL
          END AS target_recipe_slug,
          CASE r.target_type
            WHEN 'note' THEN (SELECT flavour_id FROM flavour_notes WHERE id = r.target_id)
            WHEN 'review' THEN (SELECT flavour_id FROM flavour_reviews WHERE id = r.target_id)
            ELSE NULL
          END AS target_flavour_id
        FROM tvw_content_reports r
        LEFT JOIN users ru ON ru.id = r.reporter_user_id
        LEFT JOIN users rb ON rb.id = r.reviewed_by_user_id
        WHERE {wsql}
        ORDER BY r.id DESC
        LIMIT ? OFFSET ?
        """,
        (*params, page_size, offset),
    ).fetchall()
    return [dict(x) for x in rows], total


def fetch_report_row(report_id: int) -> dict[str, Any] | None:
    db = get_db()
    row = db.execute("SELECT * FROM tvw_content_reports WHERE id = ?", (int(report_id),)).fetchone()
    return dict(row) if row else None


def update_report_moderation(
    *,
    report_id: int,
    action: str,
    actor_user_id: int,
    actor_role: str,
    note: str = "",
) -> None:
    act = str(action or "").strip().lower()
    if act == "mark_reviewed":
        new_status = "reviewed"
    elif act == "dismiss":
        new_status = "dismissed"
    elif act == "escalate":
        new_status = "escalated"
    elif act == "reopen":
        new_status = "open"
    else:
        raise ValueError("Unknown report action.")

    db = get_db()
    before = fetch_report_row(int(report_id))
    if not before:
        raise ValueError("Report not found.")

    if act == "reopen":
        db.execute(
            """
            UPDATE tvw_content_reports
            SET status = 'open',
                updated_at = CURRENT_TIMESTAMP,
                reviewed_by_user_id = NULL,
                reviewed_at = NULL
            WHERE id = ?
            """,
            (int(report_id),),
        )
    else:
        db.execute(
            """
            UPDATE tvw_content_reports
            SET status = ?,
                updated_at = CURRENT_TIMESTAMP,
                reviewed_by_user_id = ?,
                reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_status, int(actor_user_id), int(report_id)),
        )

    after = db.execute("SELECT * FROM tvw_content_reports WHERE id = ?", (int(report_id),)).fetchone()
    insert_admin_audit_log(
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        action=f"report_{act}",
        target_type="report",
        target_id=int(report_id),
        reason=(note or "").strip(),
        details={
            "before": before,
            "after": dict(after) if after else {},
            "moderator_note": (note or "").strip(),
        },
    )
    db.commit()

