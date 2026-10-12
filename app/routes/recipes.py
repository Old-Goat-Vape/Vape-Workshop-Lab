from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, current_app
import json
import os
import uuid
from werkzeug.utils import secure_filename

from utils.auth import login_required
from services import recipe_service
from models import recipes_repo

recipes_bp = Blueprint("recipes", __name__)

# ==========================================
# 0. THE LIBRARY VIEW (Visibility Matrix - Brief Item 2)
# ==========================================

@recipes_bp.route("/recipes")
@login_required
def library():
    status_filter = request.args.get('status', '')
    search = request.args.get('search', '')
    library_type = request.args.get('library_type', 'all')
    page_raw = request.args.get('page', '1')
    page = int(page_raw) if str(page_raw).strip().isdigit() else 1
    ctx = recipe_service.library_view(
        session=dict(session),
        status_filter=status_filter,
        search=search,
        library_type=library_type,
        page=page,
        page_size=24,
    )
    return render_template("recipe_library.html", **ctx)


@recipes_bp.route("/recipes/pricing")
@login_required
def pricing_sheet():
    # Empty default so Workshop WIP/READY recipes are not hidden behind Lab "Production".
    status_filter = request.args.get("status", "")
    search = request.args.get("search", "")
    ctx, err_msg, category = recipe_service.pricing_sheet_view(
        session=dict(session),
        status_filter=status_filter,
        search=search,
    )
    if err_msg:
        flash(err_msg, category)
        return redirect(url_for("recipes.library"))
    return render_template("recipe_pricing_sheet.html", **(ctx or {}))


@recipes_bp.route("/recipes/cost-calculator")
@login_required
def cost_calculator():
    ctx, err_msg, category = recipe_service.cost_calculator_view(session=dict(session))
    if err_msg:
        flash(err_msg, category)
        return redirect(url_for("recipes.library"))
    # Stable paths (avoid template url_for BuildError if dev server has stale route map).
    api_urls = {
        "calculate": "/recipes/cost-calculator/calculate",
        "catalogue_search": "/recipes/cost-calculator/catalogue-search",
        "save_catalogue_override": "/recipes/cost-calculator/override",
        "save_price_override": "/recipes/cost-calculator/price-override",
    }
    return render_template("recipe_cost_calculator.html", api_urls=api_urls, **(ctx or {}))


@recipes_bp.route("/recipes/cost-calculator/calculate")
@login_required
def cost_calculator_calculate():
    user = recipe_service.user_context_from_session(dict(session))
    if not user.show_cost:
        return jsonify({"status": "error", "message": "Recipe costing is restricted to the Old Goat account."}), 403

    from services.recipe_cost_calculator_service import BatchCostInputs, build_batch_cost_for_recipe

    try:
        recipe_id = int(request.args.get("recipe_id") or 0)
    except (TypeError, ValueError):
        recipe_id = 0
    if recipe_id <= 0:
        return jsonify({"status": "error", "message": "recipe_id is required."}), 400

    version_raw = (request.args.get("recipe_version_id") or "").strip()
    recipe_version_id = int(version_raw) if version_raw.isdigit() else None

    def _f(name: str, default: float = 0.0) -> float:
        try:
            return float(request.args.get(name, default))
        except (TypeError, ValueError):
            return default

    inputs = BatchCostInputs(
        batch_volume_ml=_f("batch_volume_ml", 500.0),
        bottle_size_ml=_f("bottle_size_ml", 30.0),
        selling_price_per_bottle=_f("selling_price_per_bottle", 0.0),
        base_cost_per_ml=_f("base_cost_per_ml", 0.0),
        bottle_cost=_f("bottle_cost", 5.0),
        label_cost=_f("label_cost", 4.0),
        packaging_cost=_f("packaging_cost", 0.0),
        labour_per_bottle=_f("labour_per_bottle", 0.0),
        waste_percent=_f("waste_percent", 0.0),
        use_canonical_base=(request.args.get("use_canonical_base", "1") or "1").strip() not in {"0", "false", "False"},
        vg_ratio=_f("vg_ratio", 70.0),
        pg_ratio=_f("pg_ratio", 30.0),
    )

    try:
        result = build_batch_cost_for_recipe(
            recipe_id=recipe_id,
            recipe_version_id=recipe_version_id,
            inputs=inputs,
        )
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    return jsonify({"status": "success", "result": result}), 200


def _require_old_goat_costing():
    user = recipe_service.user_context_from_session(dict(session))
    if not user.show_cost:
        return None, (jsonify({"status": "error", "message": "Recipe costing is restricted to the Old Goat account."}), 403)
    return user, None


@recipes_bp.route("/recipes/cost-calculator/catalogue-search")
@login_required
def cost_calculator_catalogue_search():
    _, err = _require_old_goat_costing()
    if err:
        return err
    from models import cost_catalogue_repo

    q = (request.args.get("q") or "").strip()
    try:
        limit = int(request.args.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    items = cost_catalogue_repo.search_catalogue_for_costing(query=q, limit=limit)
    return jsonify(
        {
            "status": "success",
            "items": [
                {
                    "id": int(r["id"]),
                    "brand": r.get("brand") or "",
                    "name": r.get("name") or "",
                    "price_per_ml": float(r.get("price_per_ml") or 0),
                    "label": f"{r.get('brand', '')} / {r.get('name', '')}".strip(" /"),
                }
                for r in items
            ],
        }
    ), 200


@recipes_bp.route("/recipes/cost-calculator/override", methods=["POST"])
@login_required
def cost_calculator_save_catalogue_override():
    _, err = _require_old_goat_costing()
    if err:
        return err
    from models import cost_catalogue_repo

    data = request.get_json(silent=True) or {}
    try:
        recipe_id = int(data.get("recipe_id") or 0)
        version_id = int(data.get("version_id") or 0)
        ingredient_id = int(data.get("ingredient_id") or 0)
        catalogue_id = int(data.get("catalogue_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid override payload."}), 400
    if recipe_id <= 0 or version_id <= 0 or ingredient_id <= 0 or catalogue_id <= 0:
        return jsonify({"status": "error", "message": "recipe_id, version_id, ingredient_id, catalogue_id required."}), 400
    if not cost_catalogue_repo.fetch_catalogue_row(catalogue_id):
        return jsonify({"status": "error", "message": "Catalogue item not found."}), 404
    cost_catalogue_repo.upsert_catalogue_override(
        recipe_id=recipe_id,
        version_id=version_id,
        ingredient_id=ingredient_id,
        catalogue_reference_id=catalogue_id,
    )
    return jsonify({"status": "success"}), 200


@recipes_bp.route("/recipes/cost-calculator/price-override", methods=["POST"])
@login_required
def cost_calculator_save_price_override():
    _, err = _require_old_goat_costing()
    if err:
        return err
    from models import cost_catalogue_repo

    data = request.get_json(silent=True) or {}
    try:
        recipe_id = int(data.get("recipe_id") or 0)
        version_id = int(data.get("version_id") or 0)
        ingredient_id = int(data.get("ingredient_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid payload."}), 400
    if recipe_id <= 0 or version_id <= 0 or ingredient_id <= 0:
        return jsonify({"status": "error", "message": "recipe_id, version_id, ingredient_id required."}), 400

    clear = bool(data.get("clear"))
    if clear:
        cost_catalogue_repo.clear_manual_price_override(
            recipe_id=recipe_id, version_id=version_id, ingredient_id=ingredient_id
        )
        return jsonify({"status": "success", "cleared": True}), 200

    try:
        manual_price = float(data.get("manual_price_per_ml"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "manual_price_per_ml required."}), 400
    if manual_price <= 0:
        return jsonify({"status": "error", "message": "Price must be greater than zero."}), 400

    cost_catalogue_repo.upsert_manual_price_override(
        recipe_id=recipe_id,
        version_id=version_id,
        ingredient_id=ingredient_id,
        manual_price_per_ml=manual_price,
    )
    return jsonify({"status": "success"}), 200

# ==========================================
# 1. CREATION & SAVE LOGIC (Authority: Old Goat Only - Brief Item 9)
# ==========================================

@recipes_bp.route("/recipes/create")
@login_required
def create_recipe_legacy():
    # Backwards-compatible: redirect old create URL to the new one.
    return redirect(url_for("recipes.create_recipe"))


@recipes_bp.route("/recipes/new")
@login_required
def create_recipe():
    user = recipe_service.user_context_from_session(dict(session))
    ok, msg = recipe_service.require_old_goat(user)
    if not ok:
        flash("Only the Old Goat can create new brand assets.", "danger")
        return redirect(url_for("recipes.library"))

    return render_template(
        "recipe_creator_pro.html",
        existing_data=json.dumps(None),
        is_edit=False,
    )

@recipes_bp.route("/recipes/save_ajax", methods=["POST"])
@login_required
def save_recipe_ajax():
    # Supports both JSON (legacy) and multipart/form-data (for image upload).
    payload = {}
    image_path = None
    old_image_rel = None

    if (request.content_type or "").startswith("multipart/form-data"):
        raw = (request.form.get("payload") or "").strip()
        if raw:
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {}
        else:
            payload = dict(request.form)

        old_image_rel = (request.form.get("existing_image_path") or "").strip() or None

        file = request.files.get("recipe_image")
        if file and file.filename:
            filename = secure_filename(file.filename)
            ext = os.path.splitext(filename)[1].lower().lstrip(".")
            allowed = {"jpg", "jpeg", "png", "webp"}
            if ext not in allowed:
                return jsonify({"status": "error", "message": "Invalid image type. Use jpg, png, or webp."}), 400

            upload_folder = current_app.config.get("RECIPE_UPLOAD_FOLDER")
            upload_subdir = current_app.config.get("RECIPE_UPLOAD_SUBDIR")
            if not upload_folder or not upload_subdir:
                return jsonify({"status": "error", "message": "Upload folder not configured."}), 500

            os.makedirs(upload_folder, exist_ok=True)
            unique = f"{uuid.uuid4().hex}.{ext}"
            abs_path = os.path.join(upload_folder, unique)
            file.save(abs_path)
            image_path = os.path.join(upload_subdir, unique).replace("\\", "/")

            # Optional cleanup: remove old file if it was in our recipe uploads folder.
            if old_image_rel and old_image_rel.startswith("uploads/recipes/"):
                try:
                    old_abs = os.path.join(current_app.root_path, "static", old_image_rel)
                    if os.path.exists(old_abs):
                        os.remove(old_abs)
                except Exception:
                    pass
    else:
        payload = request.get_json(silent=True) or {}

    body, status = recipe_service.save_recipe_ajax(
        session=dict(session),
        payload=payload or {},
        image_path=image_path,
    )
    return jsonify(body), status

# ==========================================
# 2. MANAGEMENT & VIEWS
# ==========================================

@recipes_bp.route("/recipes/edit/<int:recipe_id>")
@login_required
def edit_recipe_legacy(recipe_id):
    # Backwards-compatible: redirect old edit URL to the new one.
    return redirect(url_for("recipes.edit_recipe", recipe_id=recipe_id))


@recipes_bp.route("/recipes/<int:recipe_id>/edit")
@login_required
def edit_recipe(recipe_id):
    print("EDIT ROUTE HIT", recipe_id)
    existing_data, err = recipe_service.editor_existing_data(
        session=dict(session),
        recipe_id=recipe_id,
    )
    if err:
        if err == "Record not found.":
            flash(err, "danger")
        else:
            flash("Formula editing restricted to the Old Goat.", "danger")
        return redirect(url_for("recipes.library"))

    return render_template(
        "recipe_creator_pro.html",
        existing_data=json.dumps(existing_data),
        is_edit=True,
    )

@recipes_bp.route("/recipes/view/<int:id>")
@login_required
def view_recipe(id):
    ctx, err_msg, category = recipe_service.recipe_view(session=dict(session), recipe_id=id)
    if err_msg:
        flash(err_msg, category)
        return redirect(url_for("recipes.library"))

    return render_template("recipe_view.html", **(ctx or {}))