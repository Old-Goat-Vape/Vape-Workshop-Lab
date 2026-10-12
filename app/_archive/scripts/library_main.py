from flask import Blueprint, render_template, request, session, jsonify, redirect, url_for
from functools import wraps
from db import get_db # Assuming you have a get_db helper

from utils.auth import login_required

library_bp = Blueprint('library', __name__)

# --- PERMISSION DECORATORS ---

def master_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'Master':
            return jsonify({"error": "Master access required"}), 403
        return f(*args, **kwargs)
    return decorated_function

# --- ROUTES ---

@library_bp.route("/library")
@login_required
def index():
    db = get_db()
    # Fetch all active items
    items = db.execute("SELECT * FROM library_items WHERE active = 1 ORDER BY category, name").fetchall()
    return render_template("library_main.html", items=items)

@library_bp.route("/library/adjust", methods=["POST"])
@login_required
def adjust_stock():
    db = get_db()
    data = request.get_json()
    
    # Update stock
    db.execute("""
        UPDATE library_items 
        SET current_stock = current_stock + ? 
        WHERE id = ?
    """, (data['amount'], data['item_id']))
    
    # Log the change
    db.execute("""
        INSERT INTO stock_logs (item_id, user_id, change_amount, reason)
        VALUES (?, ?, ?, ?)
    """, (data['item_id'], session['user_id'], data['amount'], "Manual Adjustment"))
    
    db.commit()
    return jsonify({"status": "success"})

@library_bp.route("/library/merge", methods=["POST"])
@login_required
@master_required # Only Master can see/trigger this
def merge_items():
    # Logic to combine two items (as discussed in Step 4)
    return jsonify({"status": "success"})