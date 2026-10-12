from flask import Blueprint, request, jsonify, flash, redirect, url_for, session
from utils.auth import login_required

from services.inventory_service import (
    add_from_reference_to_inventory,
    low_stock_status,
)

from services.mixing_engine import finalize_inventory_deduction

inventory_bp = Blueprint('inventory', __name__)

@inventory_bp.route('/inventory/add-from-reference/<int:ref_id>')
@login_required
def add_tester(ref_id):
    """Adds a new physical bottle to the lab based on `flavor_reference`."""
    body, status = add_from_reference_to_inventory(reference_id=ref_id)
    flash(body.get("message", "Done."), "success" if status == 200 else "danger")
    return redirect(url_for("library.index"))

@inventory_bp.route('/inventory/deduct-batch', methods=['POST'])
@login_required
def deduct_batch():
    """Deduct poured grams from inventory and log the batch."""
    data = request.get_json() or {}
    body, status = finalize_inventory_deduction(payload=data, user_id=int(session.get("user_id") or 0))
    return jsonify(body), status

@inventory_bp.route('/inventory/status')
@login_required
def stock_status():
    """Returns a quick summary of low stock items from library_items."""
    return jsonify(low_stock_status(threshold_ml=5.0))