from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app
from werkzeug.security import check_password_hash, generate_password_hash

from utils.auth import login_required
from db import get_db
from models import users_repo

# Registering this with url_prefix='' in app.py makes these top-level routes
auth_bp = Blueprint("auth", __name__)

# Neutral recovery copy — never confirm whether a username exists.
_FORGOT_PASSWORD_NEUTRAL = (
    "If an account exists for that username, an administrator must reset the password. "
    "Contact Old Goat or a lab admin to request a reset."
)


def _establish_session(user: dict, *, remember: bool) -> None:
    session.clear()
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]
    session["pwd_sig"] = users_repo.password_signature(user.get("password_hash"))
    session.permanent = bool(remember)


def _account_blocked_message(user: dict) -> str | None:
    if user.get("status") == "Disabled":
        return "This account has been disabled by an administrator."
    if int(user.get("is_banned") or 0) or str(user.get("moderation_status") or "").strip().lower() == "banned":
        return "This account has been banned."
    if int(user.get("is_suspended") or 0) or str(user.get("moderation_status") or "").strip().lower() == "suspended":
        return "This account is suspended."
    return None


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("tvw_community.community_feed"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        remember = str(request.form.get("remember_me") or "").strip().lower() in {
            "1",
            "on",
            "true",
            "yes",
        }

        user = users_repo.fetch_user_by_username(username)
        if user and check_password_hash(user["password_hash"], password):
            blocked = _account_blocked_message(user)
            if blocked:
                flash(blocked, "danger")
                return redirect(url_for("auth.login"))

            _establish_session(user, remember=remember)
            users_repo.update_last_login(int(user["id"]))

            if int(user.get("must_change_password") or 0):
                flash("Please set a new password before continuing.", "warning")
                return redirect(url_for("auth.change_password"))

            flash(f"Welcome back, {user['username']}. Role: {user['role']}", "success")
            return redirect(url_for("tvw_community.community_feed"))

        flash("Invalid credentials. Try again.", "danger")

    return render_template("login.html")


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Admin-assisted recovery — no email reset infrastructure in this app."""
    if "user_id" in session:
        return redirect(url_for("tvw_community.community_feed"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        # Always show the same message (no account enumeration).
        # Optionally log that a reset was requested when the user exists.
        if username:
            user = users_repo.fetch_user_by_username(username)
            if user:
                try:
                    users_repo.insert_audit_log(
                        actor_id=int(user["id"]),
                        action="password_reset_requested",
                        target_id=int(user["id"]),
                        details="User requested password help from login (admin-assisted).",
                        timestamp=datetime.utcnow().isoformat(timespec="seconds"),
                    )
                    get_db().commit()
                except Exception:
                    get_db().rollback()
        flash(_FORGOT_PASSWORD_NEUTRAL, "info")
        return redirect(url_for("auth.forgot_password"))

    return render_template("forgot_password.html")


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    uid = int(session.get("user_id") or 0)
    user = users_repo.fetch_user_by_id(uid)
    if not user:
        session.clear()
        return redirect(url_for("auth.login"))

    forced = int(user.get("must_change_password") or 0) == 1

    if request.method == "POST":
        current = request.form.get("current_password") or ""
        new_pw = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        errors: list[str] = []
        if not check_password_hash(user["password_hash"], current):
            errors.append(
                "Temporary password is incorrect."
                if forced
                else "Current password is incorrect."
            )
        if len(new_pw) < 6:
            errors.append("New password must be at least 6 characters.")
        if new_pw != confirm:
            errors.append("New passwords do not match.")
        if not errors and check_password_hash(user["password_hash"], new_pw):
            errors.append("Choose a different password than the current one.")

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template("change_password.html", forced=forced)

        hashed = generate_password_hash(new_pw)
        users_repo.update_user_password_hash(
            user_id=uid,
            password_hash=hashed,
            must_change_password=False,
        )
        users_repo.clear_must_change_password(uid)
        get_db().commit()
        # Refresh session signature so old cookies stop working elsewhere.
        session["pwd_sig"] = users_repo.password_signature(hashed)
        flash("Password updated.", "success")
        return redirect(url_for("tvw_community.community_feed"))

    return render_template("change_password.html", forced=forced)


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    """Public self-registration for community users."""
    if "user_id" in session:
        return redirect(url_for("tvw_community.community_feed"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""

        errors: list[str] = []
        if len(username) < 2:
            errors.append("Username must be at least 2 characters.")
        if len(username) > 80:
            errors.append("Username is too long.")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != confirm:
            errors.append("Passwords do not match.")
        if users_repo.fetch_user_by_username(username):
            errors.append("That username is already registered.")

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template("signup.html")

        try:
            hashed = generate_password_hash(password)
            users_repo.insert_user(username=username, password_hash=hashed, role="member")
            get_db().commit()
            flash("Account created. Sign in with your new credentials.", "success")
            return redirect(url_for("auth.login"))
        except Exception:
            get_db().rollback()
            flash("Could not create account. Try a different username.", "danger")

    return render_template("signup.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("Signed out successfully.", "info")
    return redirect(url_for("auth.login"))
