from flask import Blueprint, render_template, request, jsonify, session
from utils.auth import login_required

from services.reference_service import search_flavor_reference
from services.inventory_service import quick_add_reference_to_inventory

main = Blueprint('main', __name__)

@main.route('/reference-library')
@login_required
def reference_library():
    return render_template('reference_library.html')

@main.route('/api/flavors/search')
@login_required
def api_search_flavors():
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])

    return jsonify(search_flavor_reference(q, limit=100))

@main.route('/api/inventory/quick-add', methods=['POST'])
@login_required
def quick_add_inventory():
    data = request.json
    ref_id = data.get('reference_id')
    payload, status = quick_add_reference_to_inventory(
        reference_id=ref_id,
        actor_role=session.get("role"),
    )
    return jsonify(payload), status