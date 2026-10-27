from __future__ import annotations

from typing import Any, Dict, Optional

from db import get_db


def _table_has_column(table: str, column: str) -> bool:
    db = get_db()
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(r["name"]) == column for r in rows)


def fetch_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    db = get_db()
    cols = (
        "id, username, password_hash, role, status, "
        "COALESCE(is_banned, 0) AS is_banned, "
        "COALESCE(is_suspended, 0) AS is_suspended, "
        "COALESCE(moderation_status, 'active') AS moderation_status"
    )
    if _table_has_column("users", "must_change_password"):
        cols += ", COALESCE(must_change_password, 0) AS must_change_password"
    if _table_has_column("users", "password_changed_at"):
        cols += ", password_changed_at"
    row = db.execute(
        f"""
        SELECT {cols}
        FROM users
        WHERE username = ? COLLATE NOCASE
        """,
        (username,),
    ).fetchone()
    return dict(row) if row else None


def fetch_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    cols = "id, username, role, status, password_hash"
    if _table_has_column("users", "must_change_password"):
        cols += ", COALESCE(must_change_password, 0) AS must_change_password"
    if _table_has_column("users", "password_changed_at"):
        cols += ", password_changed_at"
    row = db.execute(
        f"SELECT {cols} FROM users WHERE id = ?",
        (int(user_id),),
    ).fetchone()
    return dict(row) if row else None


def fetch_user_moderation_row(user_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    cols = (
        "id, username, role, status, email, created_at, "
        "COALESCE(is_banned, 0) AS is_banned, "
        "COALESCE(is_suspended, 0) AS is_suspended, "
        "COALESCE(is_muted, 0) AS is_muted, "
        "COALESCE(moderation_status, 'active') AS moderation_status, "
        "moderation_note"
    )
    if _table_has_column("users", "must_change_password"):
        cols += ", COALESCE(must_change_password, 0) AS must_change_password"
    row = db.execute(
        f"""
        SELECT {cols}
        FROM users
        WHERE id = ?
        """,
        (int(user_id),),
    ).fetchone()
    return dict(row) if row else None


def tvw_account_access_violation(user_id: int) -> Optional[str]:
    """If user must be blocked from TVW app use, return violation code or None."""
    row = fetch_user_moderation_row(user_id)
    if not row:
        return None
    if str(row.get("status") or "").strip().lower() == "disabled":
        return "disabled"
    if int(row.get("is_banned") or 0):
        return "banned"
    mod = str(row.get("moderation_status") or "active").strip().lower()
    if mod == "banned":
        return "banned"
    if int(row.get("is_suspended") or 0):
        return "suspended"
    if mod == "suspended":
        return "suspended"
    return None


def user_is_muted(user_id: int) -> bool:
    row = fetch_user_moderation_row(user_id)
    if not row:
        return False
    return int(row.get("is_muted") or 0) == 1


def list_users_with_mix_stats() -> list[Dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT u.id, u.username, u.role, u.status, u.last_login,
               (SELECT COUNT(*) FROM mix_history WHERE user_id = u.id) as total_mixes,
               (SELECT SUM(total_volume_actual) FROM mix_history WHERE user_id = u.id) as total_volume
        FROM users u
        ORDER BY
            CASE role
                WHEN 'Master' THEN 1
                WHEN 'Old Goat' THEN 2
                WHEN 'Foreman' THEN 3
                WHEN 'Ram' THEN 4
                WHEN 'Mixer' THEN 5
                WHEN 'Hand' THEN 6
                WHEN 'Kid' THEN 7
            END, username ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def update_user_password_hash(
    *,
    user_id: int,
    password_hash: str,
    must_change_password: bool | None = None,
) -> None:
    db = get_db()
    if _table_has_column("users", "password_changed_at") and _table_has_column(
        "users", "must_change_password"
    ):
        force = 1 if must_change_password else (0 if must_change_password is False else None)
        if force is None:
            db.execute(
                """
                UPDATE users
                SET password_hash = ?, password_changed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (password_hash, int(user_id)),
            )
        else:
            db.execute(
                """
                UPDATE users
                SET password_hash = ?,
                    password_changed_at = CURRENT_TIMESTAMP,
                    must_change_password = ?
                WHERE id = ?
                """,
                (password_hash, int(force), int(user_id)),
            )
    else:
        db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, int(user_id)),
        )


def clear_must_change_password(user_id: int) -> None:
    if not _table_has_column("users", "must_change_password"):
        return
    db = get_db()
    db.execute(
        "UPDATE users SET must_change_password = 0 WHERE id = ?",
        (int(user_id),),
    )


def update_user_role_status(*, user_id: int, role: str, status: str) -> None:
    db = get_db()
    db.execute("UPDATE users SET role = ?, status = ? WHERE id = ?", (role, status, int(user_id)))


def insert_user(*, username: str, password_hash: str, role: str) -> int:
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO users (username, password_hash, role, status)
        VALUES (?, ?, ?, 'Active')
        """,
        (username, password_hash, role),
    )
    return int(cur.lastrowid)


def insert_audit_log(*, actor_id: int, action: str, target_id: int, details: str, timestamp) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO audit_logs (actor_id, action, target_id, details, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (int(actor_id), action, int(target_id), details, timestamp),
    )


def fetch_user_role(user_id: int) -> Optional[str]:
    db = get_db()
    row = db.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["role"] if row else None


def update_last_login(user_id: int) -> None:
    db = get_db()
    db.execute("UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
    db.commit()


def password_signature(password_hash: str | None) -> str:
    """Stable short token derived from the stored hash (not reversible to the password)."""
    return str(password_hash or "")[:32]


def username_exists(username: str) -> bool:
    return fetch_user_by_username(username) is not None
