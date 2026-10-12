from flask import Blueprint, jsonify

api_lab_bp = Blueprint('api_lab', __name__)

@api_lab_bp.route('/api/status')
def status():
    return jsonify({"status": "Old Goat Lab API is live"})