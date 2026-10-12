from flask import Blueprint, render_template, request, session, jsonify, redirect, url_for, flash

from utils.auth import login_required
from services.inventory_service import (
    effective_user_level,
    rank_required,
    build_library_index_view,
    reclassify_all_references,
    adjust_stock_with_reason,
    manual_add_stock,
    quick_add_stock,
    get_edit_item_view,
    update_item_from_form,
    merge_library_items,
    archive_library_item,
    get_flavor_detail_view,
)

library_bp = Blueprint('library', __name__)

# --- ROUTES ---

@library_bp.route("/library")
@login_required
def index():
    view_type = request.args.get('view', 'inventory') 
    current_cat = request.args.get('cat', 'Flavor')
    search_query = request.args.get('search', '').strip()
    page = max(1, int(request.args.get("page", 1) or 1))
    page_size = max(10, min(200, int(request.args.get("page_size", 50) or 50)))

    user_name = session.get("username", "")
    user_level = effective_user_level(username=user_name, role=session.get("role"))
    is_old_goat = (user_name or "").lower() == "old goat"

    if current_cat == 'Nicotine' and user_level < 3:
        flash("Unauthorized category access.", "warning")
        return redirect(url_for('library.index', cat='Flavor'))

    ctx = build_library_index_view(
        view_type=view_type,
        current_cat=current_cat,
        search_query=search_query,
        user_level=user_level,
        is_old_goat=is_old_goat,
        page=page,
        page_size=page_size,
    )
    return render_template("library_universal.html", **ctx)


@library_bp.route("/library/flavor/<int:reference_id>/detail")
@login_required
def flavor_detail(reference_id: int):
    view_type = (request.args.get("view") or "reference").strip().lower()
    body, status = get_flavor_detail_view(reference_id=reference_id, view_type=view_type)
    return jsonify(body), status

@library_bp.route("/library/reclassify-all", methods=["POST"])
@login_required
@rank_required(6) # Foreman or higher
def reclassify_all():
    body, status = reclassify_all_references(actor_id=session.get("user_id"))
    return jsonify(body), status

@library_bp.route("/library/adjust-stock", methods=["POST"])
@login_required
@rank_required(3) 
def adjust_stock():
    item_id = request.form.get("item_id")
    qty_change = float(request.form.get("qty", 0))
    reason = request.form.get("reason", "").strip()

    body, status = adjust_stock_with_reason(
        actor_id=session.get("user_id"),
        actor_username=session.get("username"),
        actor_role=session.get("role"),
        item_id=int(item_id),
        qty_change=qty_change,
        reason=reason,
    )
    return jsonify(body), status

@library_bp.route("/library/manual-add", methods=["POST"])
@login_required
@rank_required(6) 
def manual_add():
    brand = request.form.get("brand", "Unknown").strip()
    name = request.form.get("name", "").strip()
    amount = float(request.form.get("amount", 0))
    unit = request.form.get("unit", "ml")

    body, status = manual_add_stock(
        actor_id=session.get("user_id"),
        brand=brand,
        name=name,
        amount=amount,
        unit=unit,
    )
    if status == 200:
        flash(f"Successfully added {brand} {name}.", "success")
        return redirect(url_for("library.index", cat=body.get("category")))
    flash(body.get("message", "Error"), "danger")
    return redirect(url_for("library.index"))

@library_bp.route("/library/quick-add/<int:ref_id>", methods=["POST"])
@login_required
@rank_required(3) 
def quick_add(ref_id):
    user_size = request.form.get("size")
    body, status = quick_add_stock(
        actor_id=session.get("user_id"),
        ref_id=ref_id,
        size=user_size,
    )
    return jsonify(body), status

@library_bp.route("/library/edit/<int:item_id>", methods=["GET", "POST"])
@login_required
@rank_required(5) 
def edit_item(item_id):
    user_name = session.get('username', '').lower()

    if request.method == "POST":
        body, status = update_item_from_form(
            actor_id=session.get("user_id"),
            actor_username=session.get("username"),
            item_id=item_id,
            name=request.form.get("name"),
            brand=request.form.get("brand"),
            category=request.form.get("category"),
            stock=float(request.form.get("current_stock", 0)),
            unit=request.form.get("unit"),
            cost_per_g=request.form.get("cost_per_g"),
        )
        if status == 200:
            flash("Saved successfully.", "success")
            return redirect(url_for("library.index", cat=body.get("category")))
        flash(body.get("message", "Error"), "danger")

    item = get_edit_item_view(item_id=item_id)
    return render_template("library_edit.html", item=item, is_old_goat=(user_name == 'old goat'))

@library_bp.route("/library/merge", methods=["POST"])
@login_required
@rank_required(7) 
def merge_items():
    source_id = request.form.get("source_id") 
    target_id = request.form.get("target_id") 

    body, status = merge_library_items(
        actor_id=session.get("user_id"),
        source_id=int(source_id),
        target_id=int(target_id),
    )
    return jsonify(body), status

@library_bp.route("/library/delete/<int:item_id>", methods=["POST"])
@login_required
@rank_required(7) 
def delete_item(item_id):
    body, status = archive_library_item(actor_id=session.get("user_id"), item_id=item_id)
    if status == 200:
        return redirect(url_for("library.index"))
    flash(body.get("message", "Error"), "danger")
    return redirect(url_for("library.index"))