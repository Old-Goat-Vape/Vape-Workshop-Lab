from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash, generate_password_hash

# Centralized auth helpers
from utils.auth import login_required
from db import get_db
from models import users_repo

# Registering this with url_prefix='' in app.py makes these top-level routes
auth_bp = Blueprint("auth", __name__)

# --- ROUTES ---

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    # Prevent logged-in users from seeing the login screen again
    if "user_id" in session:
        return redirect(url_for("tvw_community.community_feed"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = users_repo.fetch_user_by_username(username)
        if user and check_password_hash(user["password_hash"], password):
            if user["status"] == "Disabled":
                flash("This account has been disabled by an administrator.", "danger")
                return redirect(url_for("auth.login"))
            if int(user.get("is_banned") or 0) or str(user.get("moderation_status") or "").strip().lower() == "banned":
                flash("This account has been banned.", "danger")
                return redirect(url_for("auth.login"))
            if int(user.get("is_suspended") or 0) or str(user.get("moderation_status") or "").strip().lower() == "suspended":
                flash("This account is suspended.", "danger")
                return redirect(url_for("auth.login"))

            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"] 

            users_repo.update_last_login(int(user["id"]))
            
            flash(f"Welcome back, {user['username']}. Role: {user['role']}", "success")
            return redirect(url_for("tvw_community.community_feed"))
        
        flash("Invalid credentials. Try again.", "danger")
    
    return render_template("login.html")


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