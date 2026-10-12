from __future__ import annotations

from functools import wraps
from typing import Callable, TypeVar

from flask import flash, jsonify, redirect, request, session, url_for

F = TypeVar("F", bound=Callable[..., object])


# Old Goat is the top-level role. This hierarchy is the single source of truth
# for permission checks and keeps role-gated features scalable over time.
ROLES_HIERARCHY = {
    "member": 1,
    "moderator": 2,
    "admin": 3,
    "Master": 4,
    "Old Goat": 5,
    # Legacy roles kept for compatibility with transitional routes/templates.
    "Kid": 1,
    "Hand": 1,
    "Mixer": 2,
    "Ram": 2,
    "Foreman": 3,
}

# Backward-compatible alias used by existing imports.
ROLE_HIERARCHY = ROLES_HIERARCHY


def has_min_role(user_role: str | None, required_role: str) -> bool:
    return ROLES_HIERARCHY.get(str(user_role or "").strip(), 0) >= ROLES_HIERARCHY.get(required_role, 0)


def can_access_lab_bridge(role: str | None) -> bool:
    return has_min_role(role, "admin")


def can_access_tvw_admin(role: str | None) -> bool:
    return has_min_role(role, "admin")


def is_old_goat_role(role: str | None) -> bool:
    return str(role or "").strip() == "Old Goat"


def can_perform_destructive_tvw_admin_actions(role: str | None) -> bool:
    return has_min_role(role, "Master")


# Production Jobs capabilities (Lab manufacturing projects — not Workshop primary UI).
CAP_PRODUCTION_JOBS_VIEW = "lab.production_jobs.view"
CAP_PRODUCTION_JOBS_PRODUCE = "lab.production_jobs.produce"
CAP_PRODUCTION_JOBS_MANAGE = "lab.production_jobs.manage"


def _user_has_capability(user_id: int | None, capability: str) -> bool:
    if user_id is None:
        return False
    try:
        from db import get_db

        row = get_db().execute(
            "SELECT 1 AS ok FROM user_capabilities WHERE user_id = ? AND capability = ? LIMIT 1",
            (int(user_id), str(capability)),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def production_jobs_access_level(role: str | None, user_id: int | None = None) -> str | None:
    """Return 'manage' | 'produce' | 'view' | None for Lab Production Jobs."""
    if is_old_goat_role(role) or has_min_role(role, "Master"):
        return "manage"
    if has_min_role(role, "admin"):
        return "manage"
    if _user_has_capability(user_id, CAP_PRODUCTION_JOBS_MANAGE):
        return "manage"
    if _user_has_capability(user_id, CAP_PRODUCTION_JOBS_PRODUCE):
        return "produce"
    if _user_has_capability(user_id, CAP_PRODUCTION_JOBS_VIEW):
        return "view"
    return None


def can_access_production_jobs(role: str | None, user_id: int | None = None) -> bool:
    return production_jobs_access_level(role, user_id) is not None


def can_manage_production_jobs(role: str | None, user_id: int | None = None) -> bool:
    return production_jobs_access_level(role, user_id) == "manage"


def can_produce_on_production_jobs(role: str | None, user_id: int | None = None) -> bool:
    level = production_jobs_access_level(role, user_id)
    return level in ("manage", "produce")


def grant_user_capability(user_id: int, capability: str) -> None:
    from db import get_db

    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO user_capabilities (user_id, capability) VALUES (?, ?)",
        (int(user_id), str(capability)),
    )
    db.commit()


def production_jobs_required(*, min_level: str = "view"):
    """Decorator: require Production Jobs access. min_level: view | produce | manage."""

    order = {"view": 1, "produce": 2, "manage": 3}

    def decorator(f: F) -> F:
        @wraps(f)
        def decorated_function(*args, **kwargs):  # type: ignore[no-untyped-def]
            from flask import current_app

            if not bool(current_app.config.get("TVW_ENABLE_LAB_MODULES")):
                flash("Lab modules are disabled.", "warning")
                return redirect(url_for("tvw_community.community_feed"))
            level = production_jobs_access_level(session.get("role"), session.get("user_id"))
            if level is None or order.get(level, 0) < order.get(min_level, 1):
                flash("You do not have Production Jobs access.", "danger")
                return redirect(url_for("tvw_community.community_feed"))
            return f(*args, **kwargs)

        return decorated_function  # type: ignore[return-value]

    return decorator


def tvw_enforce_account_before_request(*, for_api: bool = False):
    """Block banned/suspended users on TVW surfaces. Clear session and redirect or JSON 403."""
    from models import users_repo

    uid = session.get("user_id")
    if not uid:
        return None
    # Keep session role aligned with DB so promotions/demotions apply on the next TVW request
    # without relying on re-login (signed cookie session cannot be server-invalidated).
    db_role = users_repo.fetch_user_role(int(uid))
    if db_role is None:
        session.clear()
        if for_api:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Account not found.",
                        "code": "unknown_user",
                    }
                ),
                403,
            )
        flash("Please sign in again.", "warning")
        return redirect(url_for("auth.login"))
    dr = str(db_role).strip()
    sr = str(session.get("role") or "").strip()
    if sr != dr:
        session["role"] = dr
    violation = users_repo.tvw_account_access_violation(int(uid))
    if not violation:
        return None
    session.clear()
    if for_api:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Account access restricted.",
                    "code": violation,
                }
            ),
            403,
        )
    if violation == "banned":
        flash("This account has been banned.", "danger")
    elif violation == "suspended":
        flash("This account is suspended.", "danger")
    else:
        flash("This account is not available.", "danger")
    return redirect(url_for("auth.login"))


def login_required(f: F) -> F:
    """Require an authenticated session."""

    @wraps(f)
    def decorated_function(*args, **kwargs):  # type: ignore[no-untyped-def]
        if "user_id" not in session:
            wants_json = (
                request.path.startswith("/api/")
                or (request.accept_mimetypes.best == "application/json")
                or (request.headers.get("X-Requested-With") == "XMLHttpRequest")
            )
            if wants_json:
                return jsonify({"status": "error", "message": "Not logged in."}), 401
            flash("Please log in to continue.", "warning")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated_function  # type: ignore[return-value]


def role_required(*roles: str):
    """Require a minimum role level.

    Usage: @role_required("Old Goat", "Master")
    """

    def decorator(f: F) -> F:
        @wraps(f)
        def decorated_function(*args, **kwargs):  # type: ignore[no-untyped-def]
            user_role = session.get("role", "Kid")
            if roles and not any(has_min_role(user_role, r) for r in roles):
                flash(
                    f"Unauthorized. {user_role} clearance is insufficient.",
                    "danger",
                )
                return redirect(url_for("tvw_community.community_home"))
            return f(*args, **kwargs)

        return decorated_function  # type: ignore[return-value]

    return decorator

