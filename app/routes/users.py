from flask import Blueprint, render_template, request, session, jsonify, redirect, url_for, flash
from functools import wraps
from werkzeug.security import generate_password_hash
from datetime import datetime

from utils.auth import ROLE_HIERARCHY, login_required
from models import users_repo

users_bp = Blueprint('users', __name__)

def foreman_required(f):
    """Minimum rank to manage personnel is Foreman (5)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_role = session.get('role', 'Kid')
        user_name = session.get('username', '').lower()
        
        # Absolute Authority Override
        if user_name == 'old goat':
            return f(*args, **kwargs)
            
        if ROLE_HIERARCHY.get(user_role, 0) < 5:
            flash("Clearance level too low for personnel files.", "danger")
            return redirect(url_for('library.index'))
        return f(*args, **kwargs)
    return decorated_function

# --- ROUTES ---

@users_bp.route("/personnel")
@login_required
@foreman_required
def manage_users():
    users = []
    for u in users_repo.list_users_with_mix_stats():
        if u['username'].lower() == 'old goat':
            u['role'] = 'Master'  
        
        # PROMOTION LOGIC (Brief Item 4)
        u['suggested_promotion'] = False
        if u['role'] == 'Kid' and u['total_mixes'] >= 20: u['suggested_promotion'] = True
        if u['role'] == 'Hand' and u['total_mixes'] >= 50: u['suggested_promotion'] = True
        
        users.append(u)
    
    return render_template("personnel.html", 
                           users=users, 
                           roles=ROLE_HIERARCHY.keys(),
                           hierarchy=ROLE_HIERARCHY,
                           current_user_role=session.get('role'))

@users_bp.route("/personnel/update", methods=["POST"])
@login_required
@foreman_required
def update_user_account():
    data = request.get_json()
    
    user_id = data.get('id')
    new_role = data.get('role')
    new_status = data.get('status')
    new_password = data.get('password')

    admin_role = session.get('role')
    admin_name = session.get('username', '').lower()
    admin_lvl = ROLE_HIERARCHY.get("Old Goat", 8) if admin_name == 'old goat' else ROLE_HIERARCHY.get(admin_role, 0)

    target_user = users_repo.fetch_user_by_id(int(user_id))
    if not target_user:
        return jsonify({"status": "error", "message": "User not found."}), 404

    target_name = target_user['username'].lower()
    target_lvl = ROLE_HIERARCHY.get("Old Goat", 8) if target_name == 'old goat' else ROLE_HIERARCHY.get(target_user['role'], 0)

    # --- SAFETY CHECKS (Brief Item 2) ---
    
    if target_name == 'old goat':
        return jsonify({"status": "error", "message": "The Old Goat is immutable."}), 403

    if int(user_id) == session.get('user_id'):
        if new_role and new_role != admin_role:
            return jsonify({"status": "error", "message": "You cannot change your own rank."}), 400
        if new_status == 'Disabled':
            return jsonify({"status": "error", "message": "You cannot disable your own account."}), 400

    # Hierarchy Protection (Brief Item 2)
    if admin_name != 'old goat' and admin_role != 'Master':
        if target_lvl >= admin_lvl:
            return jsonify({"status": "error", "message": "You cannot edit a peer or superior."}), 403
        if new_role and ROLE_HIERARCHY.get(new_role, 0) >= admin_lvl:
            return jsonify({"status": "error", "message": "You cannot promote someone to your rank or higher."}), 403

    try:
        # 1. Handle Password Reset
        if new_password and len(new_password.strip()) > 0:
            hashed_pw = generate_password_hash(new_password)
            users_repo.update_user_password_hash(user_id=int(user_id), password_hash=hashed_pw)
            users_repo.insert_audit_log(
                actor_id=int(session["user_id"]),
                action="Password Reset",
                target_id=int(user_id),
                details=f"By {admin_name}",
                timestamp=datetime.now(),
            )

        # 2. Update Role and Status
        users_repo.update_user_role_status(user_id=int(user_id), role=new_role, status=new_status)

        # 3. Audit Log (Brief Item 10)
        users_repo.insert_audit_log(
            actor_id=int(session["user_id"]),
            action="Account Update",
            target_id=int(user_id),
            details=f"Role -> {new_role}, Status -> {new_status}",
            timestamp=datetime.now(),
        )
        # Commit once after all changes
        from db import get_db
        get_db().commit()
        return jsonify({"status": "success", "message": "User updated successfully."})

    except Exception as e:
        from db import get_db
        get_db().rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

@users_bp.route("/personnel/create", methods=["POST"])
@login_required
@foreman_required
def create_user():
    data = request.get_json()
    
    username = data.get('username')
    password = data.get('password')
    role = data.get('role', 'Kid')

    admin_role = session.get('role')
    admin_name = session.get('username', '').lower()
    admin_lvl = ROLE_HIERARCHY.get("Old Goat", 8) if admin_name == 'old goat' else ROLE_HIERARCHY.get(admin_role, 0)
    
    new_role_lvl = ROLE_HIERARCHY.get(role, 0)

    if admin_name != 'old goat' and admin_role != 'Master':
        if new_role_lvl >= admin_lvl:
            return jsonify({"status": "error", "message": "Unauthorized rank assignment."}), 403

    if not username or not password:
        return jsonify({"status": "error", "message": "Username and Password required."}), 400

    try:
        hashed_pw = generate_password_hash(password)
        users_repo.insert_user(username=username, password_hash=hashed_pw, role=role)
        from db import get_db
        get_db().commit()
        return jsonify({"status": "success", "message": f"User {username} onboarded."})
    except Exception as e:
        from db import get_db
        get_db().rollback()
        return jsonify({"status": "error", "message": "Username already exists."}), 400