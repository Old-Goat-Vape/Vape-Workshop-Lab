from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from db import get_db
from utils.auth import login_required, role_required

from services.reference_service import search_flavor_reference
from services.inventory_service import quick_add_reference_to_inventory
from models import inventory_repo

api_lab_bp = Blueprint("api_lab", __name__)

@api_lab_bp.route("/reference-library")
@login_required
def reference_library():
    """REDIRECTS to the new universal library view."""
    return redirect(url_for('library.index', view='reference'))

@api_lab_bp.route("/api/flavors/library_dump")
@login_required
def library_dump():
    return jsonify(inventory_repo.select_reference_library_dump())

@api_lab_bp.route("/api/flavors/search")
@login_required
def search_flavors():
    query = request.args.get('q', '').strip()
    return jsonify(search_flavor_reference(query))

@api_lab_bp.route("/api/inventory/quick-add", methods=["POST"])
@login_required
def quick_add_tester():
    data = request.get_json()
    ref_id = data.get('reference_id')
    payload, status = quick_add_reference_to_inventory(
        reference_id=ref_id,
        actor_role=session.get("role"),
    )
    return jsonify(payload), status