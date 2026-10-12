from flask import Blueprint, render_template, request, session, jsonify, redirect, url_for, flash
from functools import wraps
from db import get_db
from werkzeug.security import generate_password_hash

from utils.auth import login_required

users_bp = Blueprint('users', __name__)

# --- PERMISSION PROTECTIONS ---

def master_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Double check both session and DB for ultimate security
        if session.get('role') != 'Master':
            return jsonify({"status": "error", "message": "Master access required."}), 403
        return f(*args, **kwargs)
    return decorated_function

# --- ROUTES ---

@users_bp.route("/admin/users")
@login_required
@master_required
def manage_users():
    """Landing page for the Master to view and manage all personnel."""
    db = get_db()
    # Fetch all users including their status and last login
    users = db.execute("""
        SELECT id, username, role, status, last_login 
        FROM users 
        ORDER BY role DESC, username ASC
    """).fetchall()
    return render_template("admin_users.html", users=users)

@users_bp.route("/admin/users/update", methods=["POST"])
@login_required
@master_required
def update_user_account():
    """Handles role changes, status toggles, and password resets."""
    db = get_db()
    data = request.get_json()
    
    user_id = data.get('id')
    new_role = data.get('role')
    new_status = data.get('status')
    new_password = data.get('password')

    # SAFETY CHECK: Prevent the current Master from locking themselves out
    if int(user_id) == session.get('user_id'):
        if new_role != 'Master':
            return jsonify({"status": "error", "message": "You cannot demote yourself. Another Master must do this."}), 400
        if new_status == 'Disabled':
            return jsonify({"status": "error", "message": "You cannot disable your own account."}), 400

    try:
        # 1. Handle Password Reset if provided
        if new_password and len(new_password.strip()) > 0:
            hashed_pw = generate_password_hash(new_password)
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hashed_pw, user_id))
            
            # Log the password change
            db.execute("""
                INSERT INTO audit_logs (actor_id, action, target_id) 
                VALUES (?, ?, ?)
            """, (session['user_id'], "Password Reset", user_id))

        # 2. Update Role and Status
        db.execute("""
            UPDATE users 
            SET role = ?, status = ? 
            WHERE id = ?
        """, (new_role, new_status, user_id))

        # 3. Audit Log (Rule 7)
        db.execute("""
            INSERT INTO audit_logs (actor_id, action, target_id) 
            VALUES (?, ?, ?)
        """, (session['user_id'], f"Account Update: Role={new_role}, Status={new_status}", user_id))

        db.commit()
        return jsonify({"status": "success", "message": "User updated successfully."})

    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

@users_bp.route("/admin/users/create", methods=["POST"])
@login_required
@master_required
def create_user():
    """Allows Master to onboard new lab staff."""
    db = get_db()
    data = request.get_json()
    
    username = data.get('username')
    password = data.get('password')
    role = data.get('role', 'Standard')

    if not username or not password:
        return jsonify({"status": "error", "message": "Username and Password required."}), 400

    try:
        hashed_pw = generate_password_hash(password)
        db.execute("""
            INSERT INTO users (username, password_hash, role, status) 
            VALUES (?, ?, ?, 'Active')
        """, (username, hashed_pw, role))
        
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": "Username already exists."}), 400