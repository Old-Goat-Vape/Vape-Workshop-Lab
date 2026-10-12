from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
import json
import uuid
from datetime import datetime
from functools import wraps
from werkzeug.security import check_password_hash

from utils.auth import ROLE_HIERARCHY, login_required
from db import get_db

from services.recipe_service import reference_recipe_restricted_for_user, user_context_from_session
from services import guided_mix_session_service

from services.mixing_engine import (
    validate_safety,
    init_batch_for_recipe,
    reconcile_batch_plan,
    finalize_batch_transaction,
)

from models import recipes_repo
from models import mix_logs_repo
from models import users_repo


mix_bp = Blueprint("mix", __name__)


def _parse_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def rank_required(min_rank_value):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_role = session.get("role", "Kid")
            if ROLE_HIERARCHY.get(user_role, 0) < min_rank_value:
                return jsonify({"status": "error", "message": "Insufficient rank."}), 403
            return f(*args, **kwargs)

        return decorated_function

    return decorator


@mix_bp.route("/mix/safety-check", methods=["POST"])
@login_required
def safety_check():
    data = request.get_json(silent=True) or {}

    ok, message = validate_safety(data)
    if not ok:
        return jsonify({"status": "error", "message": message}), 400

    session["safety_confirmed_at"] = datetime.now().isoformat()
    session["safety_data"] = data
    return jsonify({"status": "success"})


@mix_bp.route("/mix/init", methods=["POST"])
@login_required
def init_batch():
    if "safety_confirmed_at" not in session:
        flash("Safety protocols must be confirmed first.", "danger")
        return redirect(url_for("recipes.library"))

    recipe_id = (request.form.get("recipe_id") or "").strip()
    version_id = (request.form.get("version_id") or "").strip()
    vessel_size = _parse_float(request.form.get("vessel_size", 0), 0.0)
    style = request.form.get("style", "MTL")
    target_mix_mode = request.form.get("target_mix_mode", "DTL")
    mix_mode = request.form.get("mix_mode", "longfill")
    nic_target_mg_per_ml = _parse_float(request.form.get("nic_target_mg_per_ml"), 0.0)
    nic_base_strength_mg_per_ml = _parse_float(request.form.get("nic_base_strength_mg_per_ml"), 100.0)
    nic_carrier = request.form.get("nic_carrier", "PG")
    user_role = session.get("role", "Kid")
    guided_session_id_raw = (request.form.get("guided_session_id") or "").strip()

    if not recipe_id.isdigit():
        flash("Missing recipe id for mix start.", "danger")
        return redirect(url_for("recipes.library"))

    rp = recipes_repo.fetch_recipe(int(recipe_id))
    if rp is None:
        flash("Recipe not found.", "danger")
        return redirect(url_for("recipes.library"))
    uctx = user_context_from_session(dict(session))
    try:
        vid = int(session.get("user_id")) if session.get("user_id") is not None else None
    except (TypeError, ValueError):
        vid = None
    if reference_recipe_restricted_for_user(
        recipe=dict(rp), is_old_goat=uctx.is_old_goat, viewer_user_id=vid
    ):
        flash("Reference formulas are restricted to the Old Goat account.", "danger")
        return redirect(url_for("recipes.library"))

    # Legacy/templates might not send version_id; resolve latest on server.
    if not version_id.isdigit():
        latest = recipes_repo.fetch_latest_version(int(recipe_id))
        if not latest:
            flash("Recipe has no versions available to mix.", "danger")
            return redirect(url_for("recipes.library"))
        version_id = str(latest["id"])

    og_override = (request.form.get("og_stock_override") or "").strip() == "1"
    if og_override and not uctx.is_old_goat:
        og_override = False

    ok, result = init_batch_for_recipe(
        recipe_id=int(recipe_id),
        version_id=int(version_id),
        vessel_size=vessel_size,
        style=style,
        mix_mode=mix_mode,
        target_mix_mode=target_mix_mode,
        nic_target_mg_per_ml=nic_target_mg_per_ml,
        nic_base_strength_mg_per_ml=nic_base_strength_mg_per_ml,
        nic_carrier=nic_carrier,
        user_role=user_role,
        role_hierarchy=ROLE_HIERARCHY,
        stock_override_requested=og_override,
        is_old_goat=uctx.is_old_goat,
    )

    if not ok:
        flash(result.get("message", "Unable to initialise batch."), "danger")
        return redirect(url_for("recipes.library"))

    batch = result["batch"]
    if guided_session_id_raw.isdigit():
        batch["guided_session_id"] = int(guided_session_id_raw)
    has_nicotine = result["has_nicotine"]
    recipe_name = result["recipe_name"]

    session["mix_token"] = str(uuid.uuid4())
    batch["safety_confirmed"] = session.get("safety_data")

    return render_template(
        "mix_longfill.html",
        recipe_name=recipe_name,
        batch_number=batch["batch_number"],
        has_nicotine=has_nicotine,
        vessel_size=batch.get("vessel_size"),
        style=batch.get("style"),
        mix_mode=batch.get("mix_mode"),
        num_units=batch.get("num_units"),
        total_volume=batch.get("total_volume"),
        retail_bottle_ml=batch.get("retail_bottle_ml"),
        fill_per_bottle_ml=batch.get("fill_per_bottle_ml"),
        user_role=user_role,
        stock_override_active=bool(batch.get("stock_override_used")),
        # Master-only fine-tuning (Old Goat is NOT allowed to edit percentages).
        can_master_tune=ROLE_HIERARCHY.get(user_role, 0) >= ROLE_HIERARCHY.get("Master", 7),
        mix_batch=batch,
    )


@mix_bp.route("/mix/tune-formula", methods=["POST"])
@login_required
def tune_formula():
    data = request.get_json(silent=True) or {}
    user_role = session.get("role", "Kid")
    uctx = user_context_from_session(dict(session))
    # Master-only fine-tuning (Old Goat is NOT allowed to edit percentages).
    can_tune = ROLE_HIERARCHY.get(user_role, 0) >= ROLE_HIERARCHY.get("Master", 7)
    if not can_tune:
        return jsonify({"status": "error", "message": "Preview only — fine-tuning is restricted to Master users."}), 403
    try:
        recipe_id = int(data.get("recipe_id") or 0)
        version_id = int(data.get("version_id") or 0)
        vessel_size = _parse_float(data.get("vessel_size"), 0.0)
        style = str(data.get("style") or "MTL")
        mix_mode = str(data.get("mix_mode") or "longfill")
        target_mix_mode = str(data.get("target_mix_mode") or "DTL")
        nic_target_mg_per_ml = _parse_float(data.get("nic_target_mg_per_ml"), 0.0)
        nic_base_strength_mg_per_ml = _parse_float(data.get("nic_base_strength_mg_per_ml"), 100.0)
        nic_carrier = str(data.get("nic_carrier") or "PG")
        tuned_formula = data.get("tuned_formula") or {}
        if not isinstance(tuned_formula, dict):
            tuned_formula = {}
    except Exception:
        return jsonify({"status": "error", "message": "Invalid tuning payload."}), 400

    rp = recipes_repo.fetch_recipe(recipe_id)
    try:
        vid = int(session.get("user_id")) if session.get("user_id") is not None else None
    except (TypeError, ValueError):
        vid = None
    if rp and reference_recipe_restricted_for_user(
        recipe=dict(rp), is_old_goat=uctx.is_old_goat, viewer_user_id=vid
    ):
        return jsonify({"status": "error", "message": "Reference formulas are restricted to the Old Goat account."}), 403

    og_tune_override = bool(data.get("og_stock_override"))
    if og_tune_override and not uctx.is_old_goat:
        og_tune_override = False

    ok, result = init_batch_for_recipe(
        recipe_id=recipe_id,
        version_id=version_id,
        vessel_size=vessel_size,
        style=style,
        mix_mode=mix_mode,
        target_mix_mode=target_mix_mode,
        nic_target_mg_per_ml=nic_target_mg_per_ml,
        nic_base_strength_mg_per_ml=nic_base_strength_mg_per_ml,
        nic_carrier=nic_carrier,
        tuned_formula=tuned_formula,
        allow_formula_tuning=True,
        user_role=user_role,
        role_hierarchy=ROLE_HIERARCHY,
        stock_override_requested=og_tune_override,
        is_old_goat=uctx.is_old_goat,
    )
    if not ok:
        return jsonify({"status": "error", "message": result.get("message", "Unable to apply tuning.")}), 400
    batch = result["batch"]
    return jsonify({"status": "success", "batch": batch, "conversion_preview": batch.get("conversion_preview", {})}), 200


@mix_bp.route("/mix/<int:recipe_id>", methods=["GET"])
@login_required
def mix_legacy_redirect(recipe_id: int):
    return redirect(url_for("recipes.view_recipe", id=recipe_id))


@mix_bp.route("/mix/reconcile", methods=["POST"])
@login_required
def reconcile():
    data = request.json
    vessel_size = _parse_float(data.get("vessel_size"), 0.0)
    prev_poured = data.get("previously_poured", [])
    num_units = int(data.get("unit_count"))
    version_id = int(data.get("version_id"))
    style = data.get("style")
    mix_mode = data.get("mix_mode", "longfill")
    nic = data.get("nicotine") or {}
    formula_rows = data.get("formula_rows", [])
    allow_continue_with_drift = bool(data.get("allow_continue_with_drift"))
    no_bigger_container = bool(data.get("no_bigger_container"))
    guided_session_id = int(data.get("guided_session_id") or 0)
    ingredient_context = data.get("ingredient_context") if isinstance(data.get("ingredient_context"), dict) else {}

    result = reconcile_batch_plan(
        vessel_size=vessel_size,
        previously_poured=prev_poured,
        unit_count=num_units,
        version_id=version_id,
        style=style,
        mix_mode=mix_mode,
        nic_target_mg_per_ml=_parse_float(nic.get("target_mg_per_ml"), 0.0),
        nic_base_strength_mg_per_ml=_parse_float(nic.get("base_strength_mg_per_ml"), 100.0),
        nic_carrier=str(nic.get("carrier") or "PG"),
        formula_rows=formula_rows if isinstance(formula_rows, list) else None,
        allow_continue_with_drift=allow_continue_with_drift,
        no_bigger_container=no_bigger_container,
    )
    status = str(result.get("status") or "").lower()
    if guided_session_id > 0 and session.get("user_id"):
        if status == "policy_blocked":
            guided_mix_session_service.insert_dynamic_guided_event_step(
                session_id=guided_session_id,
                user_id=int(session.get("user_id")),
                event_type="policy_block",
                message=str(result.get("message") or "Reconcile blocked by policy."),
                detail="Resolve the block before continuing guided flow.",
                severity="danger",
                tone_profile="lab" if "lab" in str((result.get("policy") or {}).get("name") or "").lower() else "tvw",
                context=ingredient_context,
            )
        elif status == "capacity_exceeded":
            req_exact = float(result.get("required_volume_ml") or 0.0)
            req_hint = float(result.get("required_vessel") or 0.0)
            guided_mix_session_service.insert_dynamic_guided_event_step(
                session_id=guided_session_id,
                user_id=int(session.get("user_id")),
                event_type="reconcile_required",
                message=(
                    f"Capacity exceeded. Minimum required volume: {req_exact:.2f} ml"
                    + (f" (suggested size ≥ {req_hint:.0f} ml)." if req_hint > 0 else ".")
                ),
                detail="Enter your actual larger container size in ml, then reconcile.",
                severity="warning",
                tone_profile="tvw",
                context=ingredient_context,
            )
        elif status == "continue_with_drift":
            guided_mix_session_service.insert_dynamic_guided_event_step(
                session_id=guided_session_id,
                user_id=int(session.get("user_id")),
                event_type="reconcile_required",
                message="No larger vessel available. Continuing with drift-adjusted targets.",
                detail="PG/balance was recalculated around the overpour. Flavor outcome has changed.",
                severity="warning",
                tone_profile="tvw",
                context=ingredient_context,
            )
        elif status == "success":
            guided_mix_session_service.insert_dynamic_guided_event_step(
                session_id=guided_session_id,
                user_id=int(session.get("user_id")),
                event_type="reconcile_required",
                message="Reconcile completed. Confirm adjusted targets and continue.",
                detail="Proceed to the next guided ingredient using recalculated targets.",
                severity="info",
                tone_profile="tvw",
                context=ingredient_context,
            )
    if status == "policy_blocked":
        return jsonify(result), 409
    if status == "scrap_required":
        return jsonify(result), 409
    if status in {"error", "capacity_exceeded"}:
        return jsonify(result), 400
    if guided_session_id > 0 and session.get("user_id") and status in {"success", "continue_with_drift"}:
        guided_mix_session_service.update_guided_session_vessel_ml(
            session_id=guided_session_id,
            user_id=int(session["user_id"]),
            vessel_ml=float(vessel_size),
        )
    return jsonify(result), 200


@mix_bp.route("/mix/reconcile/scrap-authorize", methods=["POST"])
@login_required
def reconcile_scrap_authorize():
    data = request.get_json(silent=True) or {}
    authorizer_username = str(data.get("authorizer_username") or "").strip()
    authorizer_password = str(data.get("authorizer_password") or "")
    payload = data.get("batch_payload") if isinstance(data.get("batch_payload"), dict) else {}
    if not authorizer_username or not authorizer_password:
        return jsonify({"status": "error", "message": "Authorizer credentials are required."}), 400
    user = users_repo.fetch_user_by_username(authorizer_username)
    if not user or not check_password_hash(str(user.get("password_hash") or ""), authorizer_password):
        return jsonify({"status": "error", "message": "Invalid authorizer credentials."}), 401
    role = str(user.get("role") or "")
    # Manager alias maps to Foreman in this role model.
    if ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY.get("Foreman", 5):
        return jsonify({"status": "error", "message": "Authorizer role is insufficient."}), 403
    if not payload:
        return jsonify({"status": "error", "message": "Missing batch payload for scrap finalization."}), 400
    payload["scrapped"] = True
    payload["scrap_reason"] = str(data.get("scrap_reason") or "overpour_no_bigger_container")
    payload["supervisor_id"] = int(user.get("id"))
    user_role = session.get("role", "Kid")
    uctx = user_context_from_session(dict(session))
    response_body, status_code = finalize_batch_transaction(
        payload=payload,
        user_role=user_role,
        role_hierarchy=ROLE_HIERARCHY,
        user_id=session["user_id"],
        mix_token=session.get("mix_token"),
        safety_data=session.get("safety_data"),
        user_is_old_goat=uctx.is_old_goat,
    )
    db = get_db()
    users_repo.insert_audit_log(
        actor_id=int(user.get("id")),
        action="mix_scrap_authorized",
        target_id=int(response_body.get("batch_id") or 0),
        details=json.dumps(
            {
                "authorized_for_user_id": int(session.get("user_id") or 0),
                "authorized_for_username": str(session.get("username") or ""),
                "scrap_reason": payload.get("scrap_reason"),
                "status_code": status_code,
            }
        ),
        timestamp=datetime.now().isoformat(),
    )
    db.commit()
    return jsonify({**response_body, "scrapped": True, "authorized_by": authorizer_username}), status_code


@mix_bp.route("/mix/finalize", methods=["POST"])
@login_required
@rank_required(2)
def finalize_batch():
    data = request.json

    user_role = session.get("role", "Kid")
    uctx = user_context_from_session(dict(session))
    response_body, status_code = finalize_batch_transaction(
        payload=data,
        user_role=user_role,
        role_hierarchy=ROLE_HIERARCHY,
        user_id=session["user_id"],
        mix_token=session.get("mix_token"),
        safety_data=session.get("safety_data"),
        user_is_old_goat=uctx.is_old_goat,
    )

    if response_body.get("status") == "success":
        session.pop("mix_token", None)
        session.pop("safety_confirmed_at", None)

    return jsonify(response_body), status_code


@mix_bp.route("/mix/history")
@login_required
def history():
    batches = mix_logs_repo.list_mix_history()
    return render_template("mix_history.html", batches=batches)

