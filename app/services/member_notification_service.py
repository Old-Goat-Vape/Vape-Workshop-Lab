"""Account inbox backed by active shares, opted-in steeping plans and read receipts."""
from db import get_db
from models.recipes_repo import tvw_recipe_analytics_predicate_sql
from services.batch_steeping_service import DUE_AT_SQL, RECORDED_BATCH_SQL

SCHEMA = """
CREATE TABLE IF NOT EXISTS member_notification_reads (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    event_key TEXT NOT NULL,
    read_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, kind, event_key)
);
CREATE INDEX IF NOT EXISTS idx_recipe_access_audit_notification
    ON recipe_access_audit(user_id, recipe_id, id DESC);
"""

# Keep events tied to their source. A revoked share or a disabled reminder
# drops out without a background job, and receipts identify one occurrence.
_EVENTS = f"""
WITH events AS (
    SELECT 'recipe_shared' AS kind,
           ra.recipe_id || ':' || COALESCE((
               SELECT a.id FROM recipe_access_audit a
               WHERE a.user_id = ra.user_id AND a.recipe_id = ra.recipe_id
                 AND a.action IN ('view', 'edit')
               ORDER BY a.id DESC LIMIT 1
           ), 0) AS event_key,
           ra.recipe_id AS target_id,
           r.name AS title, u.username AS actor,
           datetime(ra.granted_at) AS occurred_at, 0 AS acknowledged
    FROM recipe_access ra
    JOIN recipes r ON r.id = ra.recipe_id
    LEFT JOIN users u ON u.id = ra.granted_by
    WHERE ra.user_id = :user_id AND ra.revoked_at IS NULL
      AND {tvw_recipe_analytics_predicate_sql('r')}

    UNION ALL

    SELECT 'steeping_ready',
           b.session_id || ':' || b.reminder_revision || ':' || {DUE_AT_SQL},
           b.session_id, r.name, NULL, {DUE_AT_SQL},
           COALESCE(datetime(b.acknowledged_at) >= {DUE_AT_SQL}, 0)
    FROM batch_steeping b
    JOIN mix_sessions ms ON ms.id = b.session_id
    JOIN recipe_versions rv ON rv.id = ms.recipe_version_id
    JOIN recipes r ON r.id = rv.recipe_id
    WHERE ms.user_id = :user_id AND {RECORDED_BATCH_SQL}
      AND b.reminder_enabled = 1 AND {DUE_AT_SQL} <= datetime('now')
)
"""
_READ_JOIN = """
LEFT JOIN member_notification_reads receipt ON receipt.user_id = :user_id
    AND receipt.kind = e.kind AND receipt.event_key = e.event_key
"""
_UNREAD = "e.acknowledged = 0 AND receipt.read_at IS NULL"


def unread_count(user_id):
    return get_db().execute(
        _EVENTS + f"SELECT COUNT(*) FROM events e {_READ_JOIN} WHERE {_UNREAD}",
        {"user_id": user_id},
    ).fetchone()[0]


def list_notifications(user_id, *, page, page_size):
    return get_db().execute(
        _EVENTS + f"""
        SELECT e.*, ({_UNREAD}) AS unread
        FROM events e {_READ_JOIN}
        ORDER BY occurred_at DESC, kind, target_id DESC
        LIMIT :limit OFFSET :offset
        """,
        {"user_id": user_id, "limit": page_size + 1, "offset": (page - 1) * page_size},
    ).fetchall()


def open_notification(user_id, kind, event_key):
    db = get_db()
    with db:
        item = db.execute(
            _EVENTS + "SELECT * FROM events WHERE kind = :kind AND event_key = :event_key",
            {"user_id": user_id, "kind": kind, "event_key": event_key},
        ).fetchone()
        if item:
            db.execute(
                "INSERT OR IGNORE INTO member_notification_reads (user_id, kind, event_key) VALUES (?, ?, ?)",
                (user_id, kind, event_key),
            )
        return item


def mark_all_read(user_id):
    db = get_db()
    with db:
        db.execute(
            _EVENTS + f"""
            INSERT OR IGNORE INTO member_notification_reads (user_id, kind, event_key)
            SELECT :user_id, e.kind, e.event_key FROM events e {_READ_JOIN} WHERE {_UNREAD}
            """,
            {"user_id": user_id},
        )
