from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Tuple

from flask import Blueprint, Response, current_app, jsonify, request, session

from core.calculator import tvw_mix_policy
from db import get_db, is_sqlite_busy_error, SQLITE_BUSY_BENCH_MESSAGE, SQLITE_BUSY_USER_MESSAGE
from models import recipes_repo, tvw_reports_repo, users_repo
from services import guided_mix_session_service
from services import holdings_bridge_service
from services import stash_service
from services import tvw_mix_service
from utils.auth import is_old_goat_role, login_required, tvw_enforce_account_before_request
from utils.guided_mix_perf import guided_mix_perf_span, json_byte_size, log_guided_step_complete_summary

tvw_api_bp = Blueprint("tvw_api", __name__, url_prefix="/api")


@tvw_api_bp.before_request
def _tvw_api_account_gate():
    return tvw_enforce_account_before_request(for_api=True)


@tvw_api_bp.errorhandler(sqlite3.OperationalError)
def _tvw_api_sqlite_locked(exc: sqlite3.OperationalError):
    if not is_sqlite_busy_error(exc):
        current_app.logger.exception("TVW API sqlite error: %s", exc)
        return jsonify({"status": "error", "message": "Database error.", "code": "database_error"}), 500
    path = str(request.path or "")
    if "/mix/guided/session/" in path and "/complete" in path:
        message = SQLITE_BUSY_BENCH_MESSAGE
    elif "/recipes/save" in path:
        message = SQLITE_BUSY_USER_MESSAGE
    else:
        message = "Database is busy. Try again in a moment."
    current_app.logger.warning("TVW API sqlite busy after retries: %s path=%s", exc, path)
    return (
        jsonify(
            {
                "status": "error",
                "message": message,
                "code": "database_locked",
            }
        ),
        503,
    )


def _current_user_id() -> int | None:
    try:
        return int(session.get("user_id")) if session.get("user_id") is not None else None
    except (TypeError, ValueError):
        return None


def _now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _muted_ugc_block() -> Tuple[Response, int] | None:
    uid = _current_user_id()
    if uid is not None and users_repo.user_is_muted(int(uid)):
        return (
            jsonify({"status": "error", "message": "Account is muted.", "code": "muted"}),
            403,
        )
    return None


def _gate_recipe_response(recipe_id: int) -> Tuple[Response, int] | None:
    r = recipes_repo.fetch_recipe(recipe_id)
    if not r:
        return jsonify({"status": "error", "message": "Recipe not found."}), 404
    if not recipes_repo.can_view_recipe_in_tvw_community(
        dict(r),
        viewer_user_id=_current_user_id(),
        viewer_is_old_goat=is_old_goat_role(session.get("role")),
    ):
        return jsonify({"status": "error", "message": "Recipe not found."}), 404
    return None


def _resolve_version_id(*, recipe_id: int, requested_version_id: int | None) -> int:
    return recipes_repo.resolve_recipe_version_id_for_mix(
        recipe_id=recipe_id, requested_version_id=requested_version_id
    )


@tvw_api_bp.route("/community/feed", methods=["GET"])
@login_required
def community_feed():
    page_raw = (request.args.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() else 1
    recipes, total = recipes_repo.fetch_tvw_library(
        search=(request.args.get("search") or "").strip(),
        status=(request.args.get("status") or "").strip().lower(),
        visibility=(request.args.get("visibility") or "").strip().lower(),
        tag=(request.args.get("tag") or "").strip().lower(),
        page=page,
        page_size=12,
        viewer_user_id=_current_user_id(),
        viewer_is_old_goat=is_old_goat_role(session.get("role")),
    )
    return jsonify(
        {
            "status": "success",
            "product": "The Vape Workshop Calculator",
            "community_feed": recipes,
            "total": total,
            "page": page,
        }
    ), 200


@tvw_api_bp.route("/reports", methods=["POST"])
@login_required
def submit_content_report():
    """Logged-in users flag recipes, notes, reviews, or recipe feedback for moderation."""
    uid = _current_user_id()
    if uid is None:
        return jsonify({"status": "error", "message": "Not logged in."}), 401
    data = request.get_json(silent=True) or {}
    target_type = str(data.get("target_type") or "").strip().lower()
    reason_raw = str(data.get("reason") or "").strip().lower().replace("-", "_")
    alias = {
        "wrongcategory": "wrong_category",
        "nota_real_flavour": "not_flavour",
        "not_a_realflavour": "not_flavour",
    }
    reason = alias.get(reason_raw.replace(" ", "_"), reason_raw)
    detail = str(data.get("detail") or "").strip() or None
    try:
        tid = int(data.get("target_id"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "target_id is required."}), 400
    try:
        rid = tvw_reports_repo.insert_content_report(
            reporter_user_id=int(uid),
            target_type=target_type,
            target_id=tid,
            reason=reason,
            detail=detail,
            viewer_is_old_goat=is_old_goat_role(session.get("role")),
        )
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    return jsonify({"status": "success", "report_id": rid}), 201


@tvw_api_bp.route("/recipes/<int:recipe_id>/status", methods=["POST"])
@login_required
def set_recipe_status(recipe_id: int):
    denied = _muted_ugc_block()
    if denied:
        return denied
    denied = _gate_recipe_response(recipe_id)
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    new_status = str(data.get("status") or "").strip().lower()
    allowed = {"wip", "help", "ready"}
    if new_status not in allowed:
        return jsonify({"status": "error", "message": "Invalid status."}), 400
    db = get_db()
    version_id = int(data.get("recipe_version_id") or 0) or None
    row = db.execute("SELECT status FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not row:
        return jsonify({"status": "error", "message": "Recipe not found."}), 404
    previous = str(row["status"] or "")
    db.execute("UPDATE recipes SET status = ? WHERE id = ?", (new_status.upper(), recipe_id))
    db.execute(
        """
        INSERT INTO recipe_status_updates (recipe_id, recipe_version_id, from_status, to_status, changed_by, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            recipe_id,
            version_id,
            previous,
            new_status.upper(),
            _current_user_id(),
            str(data.get("reason") or ""),
            _now_utc_iso(),
        ),
    )
    db.commit()
    return jsonify({"status": "success"}), 200


@tvw_api_bp.route("/recipes/<int:recipe_id>/status", methods=["GET"])
@login_required
def list_recipe_status_updates(recipe_id: int):
    denied = _gate_recipe_response(recipe_id)
    if denied:
        return denied
    db = get_db()
    version_filter_raw = request.args.get("recipe_version_id", "").strip()
    version_id = int(version_filter_raw) if version_filter_raw.isdigit() else None
    where = "WHERE recipe_id = ?"
    params: list = [recipe_id]
    if version_id is not None:
        where += " AND recipe_version_id = ?"
        params.append(version_id)
    rows = db.execute(
        f"""
        SELECT id, recipe_id, recipe_version_id, from_status, to_status, changed_by, reason, created_at
        FROM recipe_status_updates
        {where}
        ORDER BY id DESC
        """,
        tuple(params),
    ).fetchall()
    return jsonify({"status": "success", "items": [dict(r) for r in rows]}), 200


@tvw_api_bp.route("/recipes/<int:recipe_id>/feedback", methods=["POST"])
@login_required
def add_recipe_feedback(recipe_id: int):
    denied = _muted_ugc_block()
    if denied:
        return denied
    denied = _gate_recipe_response(recipe_id)
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    db = get_db()
    version_id = int(data.get("recipe_version_id") or 0) or None
    main = str(data.get("body") or "").strip()
    extras: list[str] = []
    dev = str(data.get("device") or "").strip()
    issue = str(data.get("issue_type") or "").strip()
    sug = str(data.get("suggested_changes") or "").strip()
    if dev:
        extras.append(f"Device: {dev}")
    if issue:
        extras.append(f"Issue type: {issue}")
    if sug:
        extras.append(f"Suggested changes: {sug}")
    if extras:
        combined = "\n".join(extras)
        body_out = f"{combined}\n\n{main}" if main else combined
    else:
        body_out = main
    cur = db.execute(
        """
        INSERT INTO recipe_feedback (recipe_id, recipe_version_id, author_user_id, feedback_type, body, score_overall, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            recipe_id,
            version_id,
            _current_user_id(),
            str(data.get("feedback_type") or "structured"),
            body_out,
            float(data.get("score_overall") or 0.0),
            _now_utc_iso(),
        ),
    )
    feedback_id = int(cur.lastrowid)
    metrics_in = dict(data.get("metrics") or {})
    sd_raw = data.get("steep_days")
    if sd_raw is not None and str(sd_raw).strip() != "":
        try:
            metrics_in["steep_days"] = float(sd_raw)
        except (TypeError, ValueError):
            pass
    for metric_key, metric_value in metrics_in.items():
        try:
            mv = float(metric_value)
        except (TypeError, ValueError):
            continue
        db.execute(
            "INSERT INTO recipe_feedback_metrics (feedback_id, metric_key, metric_value) VALUES (?, ?, ?)",
            (feedback_id, str(metric_key), mv),
        )
    db.commit()
    return jsonify({"status": "success", "feedback_id": feedback_id}), 201


@tvw_api_bp.route("/recipes/<int:recipe_id>/feedback", methods=["GET"])
@login_required
def list_recipe_feedback(recipe_id: int):
    denied = _gate_recipe_response(recipe_id)
    if denied:
        return denied
    db = get_db()
    version_filter_raw = request.args.get("recipe_version_id", "").strip()
    version_id = int(version_filter_raw) if version_filter_raw.isdigit() else None
    uid = _current_user_id()
    uvis = recipes_repo.tvw_community_content_visible_sql("rf")
    where_parts = ["rf.recipe_id = ?"]
    params: list = [recipe_id]
    if uid is not None:
        where_parts.append(f"({uvis} OR rf.author_user_id = ?)")
        params.append(int(uid))
    else:
        where_parts.append(uvis)
    if version_id is not None:
        where_parts.append("rf.recipe_version_id = ?")
        params.append(version_id)
    wsql = " AND ".join(where_parts)
    rows = db.execute(
        f"""
        SELECT
            rf.id,
            rf.recipe_id,
            rf.recipe_version_id,
            rf.author_user_id,
            rf.feedback_type,
            rf.body,
            rf.score_overall,
            COALESCE(rf.is_hidden, 0) AS is_hidden,
            COALESCE(rf.moderation_status, 'active') AS moderation_status,
            COALESCE(rf.moderation_reason, '') AS moderation_reason,
            rf.created_at,
            COALESCE(u.username, 'unknown') AS author_username
        FROM recipe_feedback rf
        LEFT JOIN users u ON u.id = rf.author_user_id
        WHERE {wsql}
        ORDER BY rf.id DESC
        """,
        tuple(params),
    ).fetchall()
    row_dicts = [dict(r) for r in rows]
    fids = [int(r["id"]) for r in row_dicts]
    metrics_by_fb: dict[int, dict[str, float]] = {}
    if fids:
        ph = ",".join("?" * len(fids))
        mrows = db.execute(
            f"""
            SELECT feedback_id, metric_key, metric_value
            FROM recipe_feedback_metrics
            WHERE feedback_id IN ({ph})
            ORDER BY id ASC
            """,
            fids,
        ).fetchall()
        for m in mrows:
            fid = int(m["feedback_id"])
            metrics_by_fb.setdefault(fid, {})[str(m["metric_key"])] = float(m["metric_value"] or 0.0)
    items = []
    for payload in row_dicts:
        payload["metrics"] = metrics_by_fb.get(int(payload["id"]), {})
        mine = uid is not None and int(payload.get("author_user_id") or 0) == int(uid)
        payload["owner_moderation_visible"] = bool(mine)
        payload["public_visible"] = bool(
            int(payload.get("is_hidden") or 0) == 0
            and str(payload.get("moderation_status") or "active").strip().lower() == "active"
        )
        items.append(payload)
    return jsonify({"status": "success", "items": items}), 200


@tvw_api_bp.route("/recipes/<int:recipe_id>/engagement", methods=["GET"])
@login_required
def get_recipe_engagement(recipe_id: int):
    denied = _gate_recipe_response(recipe_id)
    if denied:
        return denied
    snap = recipes_repo.fetch_recipe_engagement_for_viewer(
        recipe_id=recipe_id, viewer_user_id=_current_user_id()
    )
    return jsonify({"status": "success", **snap}), 200


@tvw_api_bp.route("/recipes/<int:recipe_id>/favorite", methods=["POST"])
@login_required
def set_recipe_favorite_api(recipe_id: int):
    denied = _muted_ugc_block()
    if denied:
        return denied
    denied = _gate_recipe_response(recipe_id)
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    favorited = bool(data.get("favorited", True))
    uid = _current_user_id()
    if uid is None:
        return jsonify({"status": "error", "message": "Not logged in."}), 401
    recipes_repo.set_recipe_favorite(user_id=int(uid), recipe_id=recipe_id, favorited=favorited)
    snap = recipes_repo.fetch_recipe_engagement_for_viewer(recipe_id=recipe_id, viewer_user_id=int(uid))
    return jsonify({"status": "success", **snap}), 200


@tvw_api_bp.route("/recipes/<int:recipe_id>/rating", methods=["POST"])
@login_required
def set_recipe_star_rating_api(recipe_id: int):
    denied = _muted_ugc_block()
    if denied:
        return denied
    denied = _gate_recipe_response(recipe_id)
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    uid = _current_user_id()
    if uid is None:
        return jsonify({"status": "error", "message": "Not logged in."}), 401
    try:
        rating = int(data.get("rating"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "rating must be an integer 1–5."}), 400
    if rating < 1 or rating > 5:
        return jsonify({"status": "error", "message": "rating must be between 1 and 5."}), 400
    recipes_repo.upsert_recipe_star_rating(user_id=int(uid), recipe_id=recipe_id, rating=rating)
    snap = recipes_repo.fetch_recipe_engagement_for_viewer(recipe_id=recipe_id, viewer_user_id=int(uid))
    return jsonify({"status": "success", **snap}), 200


@tvw_api_bp.route("/flavours/<int:flavour_id>/notes", methods=["POST"])
@login_required
def add_flavour_note(flavour_id: int):
    denied = _muted_ugc_block()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    db = get_db()
    cur = db.execute(
        "INSERT INTO flavour_notes (flavour_id, author_user_id, note, created_at) VALUES (?, ?, ?, ?)",
        (flavour_id, _current_user_id(), str(data.get("note") or ""), _now_utc_iso()),
    )
    db.commit()
    return jsonify({"status": "success", "note_id": int(cur.lastrowid)}), 201


@tvw_api_bp.route("/flavours/<int:flavour_id>/notes", methods=["GET"])
@login_required
def list_flavour_notes(flavour_id: int):
    db = get_db()
    uid = _current_user_id()
    uvis = recipes_repo.tvw_community_content_visible_sql("fn")
    if uid is not None:
        wsql = f"fn.flavour_id = ? AND ({uvis} OR fn.author_user_id = ?)"
        args = (flavour_id, int(uid))
    else:
        wsql = f"fn.flavour_id = ? AND {uvis}"
        args = (flavour_id,)
    rows = db.execute(
        f"""
        SELECT
            fn.id,
            fn.flavour_id,
            fn.author_user_id,
            fn.note,
            COALESCE(fn.is_hidden, 0) AS is_hidden,
            COALESCE(fn.moderation_status, 'active') AS moderation_status,
            COALESCE(fn.moderation_reason, '') AS moderation_reason,
            fn.created_at,
            COALESCE(u.username, 'unknown') AS author_username
        FROM flavour_notes fn
        LEFT JOIN users u ON u.id = fn.author_user_id
        WHERE {wsql}
        ORDER BY fn.id DESC
        """,
        args,
    ).fetchall()
    items: list[dict] = []
    for r in rows:
        item = dict(r)
        mine = uid is not None and int(item.get("author_user_id") or 0) == int(uid)
        item["owner_moderation_visible"] = bool(mine)
        item["public_visible"] = bool(
            int(item.get("is_hidden") or 0) == 0
            and str(item.get("moderation_status") or "active").strip().lower() == "active"
        )
        items.append(item)
    return jsonify({"status": "success", "items": items}), 200


@tvw_api_bp.route("/flavours/<int:flavour_id>/reviews", methods=["POST"])
@login_required
def add_flavour_review(flavour_id: int):
    denied = _muted_ugc_block()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO flavour_reviews (flavour_id, author_user_id, rating, review, steep_days, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            flavour_id,
            _current_user_id(),
            float(data.get("rating") or 0.0),
            str(data.get("review") or ""),
            int(data.get("steep_days") or 0),
            _now_utc_iso(),
        ),
    )
    db.commit()
    return jsonify({"status": "success", "review_id": int(cur.lastrowid)}), 201


@tvw_api_bp.route("/flavours/<int:flavour_id>/reviews", methods=["GET"])
@login_required
def list_flavour_reviews(flavour_id: int):
    db = get_db()
    uid = _current_user_id()
    uvis = recipes_repo.tvw_community_content_visible_sql("fr")
    if uid is not None:
        wsql = f"fr.flavour_id = ? AND ({uvis} OR fr.author_user_id = ?)"
        args = (flavour_id, int(uid))
    else:
        wsql = f"fr.flavour_id = ? AND {uvis}"
        args = (flavour_id,)
    rows = db.execute(
        f"""
        SELECT
            fr.id,
            fr.flavour_id,
            fr.author_user_id,
            fr.rating,
            fr.review,
            fr.steep_days,
            COALESCE(fr.is_hidden, 0) AS is_hidden,
            COALESCE(fr.moderation_status, 'active') AS moderation_status,
            COALESCE(fr.moderation_reason, '') AS moderation_reason,
            fr.created_at,
            COALESCE(u.username, 'unknown') AS author_username
        FROM flavour_reviews fr
        LEFT JOIN users u ON u.id = fr.author_user_id
        WHERE {wsql}
        ORDER BY fr.id DESC
        """,
        args,
    ).fetchall()
    items: list[dict] = []
    for r in rows:
        item = dict(r)
        mine = uid is not None and int(item.get("author_user_id") or 0) == int(uid)
        item["owner_moderation_visible"] = bool(mine)
        item["public_visible"] = bool(
            int(item.get("is_hidden") or 0) == 0
            and str(item.get("moderation_status") or "active").strip().lower() == "active"
        )
        items.append(item)
    return jsonify({"status": "success", "items": items}), 200


@tvw_api_bp.route("/stash", methods=["POST"])
@login_required
def add_stash_item():
    denied = _muted_ugc_block()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    user_id = _current_user_id()
    if holdings_bridge_service.is_old_goat_user(
        user_id=user_id,
        role=session.get("role"),
        username=session.get("username"),
    ):
        try:
            ref_raw = data.get("reference_id")
            if ref_raw is not None and str(ref_raw).strip():
                result = holdings_bridge_service.upsert_old_goat_shared_holding_by_reference_id(
                    reference_id=int(ref_raw),
                    quantity_ml=float(data.get("quantity_ml") or 0.0),
                )
            else:
                result = holdings_bridge_service.upsert_old_goat_shared_holding(
                    flavour_id=int(data.get("flavour_id") or 0),
                    quantity_ml=float(data.get("quantity_ml") or 0.0),
                )
            return jsonify({"status": "success", "source": "lab_shared_holdings", **result}), 201
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

    fid = int(data.get("flavour_id") or 0)
    if not fid:
        return jsonify({"status": "error", "message": "flavour_id is required."}), 400
    sid = stash_service.upsert_user_stash_item(
        user_id=int(user_id or 0),
        flavour_id=fid,
        quantity_ml=float(data.get("quantity_ml") or 0.0),
        is_favorite=bool(data.get("is_favorite")),
        last_used_at=_now_utc_iso(),
    )
    return jsonify({"status": "success", "stash_item_id": int(sid)}), 201


@tvw_api_bp.route("/stash", methods=["GET"])
@login_required
def list_stash_items():
    user_id = _current_user_id()
    if holdings_bridge_service.is_old_goat_user(
        user_id=user_id,
        role=session.get("role"),
        username=session.get("username"),
    ):
        items = holdings_bridge_service.list_old_goat_shared_holdings()
        return jsonify({"status": "success", "source": "lab_shared_holdings", "items": items}), 200

    rows = stash_service.fetch_member_stash_aggregated(user_id=int(user_id or 0))
    out = []
    for r in rows:
        d = dict(r)
        d["user_id"] = int(user_id or 0)
        d["id"] = int(d.pop("stash_item_id"))
        d["canonical_name"] = d.get("canonical_name")
        d["brand"] = d.get("brand")
        out.append(d)
    return jsonify({"status": "success", "source": "user_stash_items", "items": out}), 200


@tvw_api_bp.route("/stash", methods=["PATCH"])
@login_required
def patch_stash_item():
    denied = _muted_ugc_block()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    user_id = _current_user_id()
    if holdings_bridge_service.is_old_goat_user(
        user_id=user_id,
        role=session.get("role"),
        username=session.get("username"),
    ):
        try:
            ref_raw = data.get("reference_id")
            qty = data.get("quantity_ml")
            if qty is None:
                return jsonify({"status": "error", "message": "quantity_ml is required."}), 400
            if ref_raw is not None and str(ref_raw).strip():
                result = holdings_bridge_service.upsert_old_goat_shared_holding_by_reference_id(
                    reference_id=int(ref_raw),
                    quantity_ml=float(qty),
                )
            else:
                result = holdings_bridge_service.upsert_old_goat_shared_holding(
                    flavour_id=int(data.get("flavour_id") or 0),
                    quantity_ml=float(qty),
                )
            return jsonify({"status": "success", "source": "lab_shared_holdings", **result}), 200
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

    fid = int(data.get("flavour_id") or 0)
    if not fid:
        return jsonify({"status": "error", "message": "flavour_id is required."}), 400
    db = get_db()
    row = db.execute(
        "SELECT id, quantity_ml, is_favorite FROM user_stash_items WHERE user_id = ? AND flavour_id = ? ORDER BY id ASC LIMIT 1",
        (int(user_id or 0), fid),
    ).fetchone()
    qty = float(row["quantity_ml"] or 0.0) if row else 0.0
    if "quantity_ml" in data and data.get("quantity_ml") is not None:
        qty = float(data.get("quantity_ml") or 0.0)
    fav_i = int(row["is_favorite"] or 0) if row else 0
    if "is_favorite" in data:
        fav_i = 1 if bool(data.get("is_favorite")) else 0
    sid = stash_service.upsert_user_stash_item(
        user_id=int(user_id or 0),
        flavour_id=fid,
        quantity_ml=qty,
        is_favorite=bool(fav_i),
        last_used_at=_now_utc_iso(),
    )
    return jsonify({"status": "success", "stash_item_id": int(sid)}), 200


@tvw_api_bp.route("/stash/reference/<int:reference_id>", methods=["DELETE"])
@login_required
def delete_stash_by_reference(reference_id: int):
    denied = _muted_ugc_block()
    if denied:
        return denied
    user_id = _current_user_id()
    if not holdings_bridge_service.is_old_goat_user(
        user_id=user_id,
        role=session.get("role"),
        username=session.get("username"),
    ):
        return jsonify({"status": "error", "message": "Not available for this account."}), 403
    try:
        holdings_bridge_service.remove_old_goat_shared_holding_by_reference_id(reference_id=int(reference_id))
        return jsonify({"status": "success", "source": "lab_shared_holdings"}), 200
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@tvw_api_bp.route("/stash/<int:flavour_id>", methods=["DELETE"])
@login_required
def delete_stash_item(flavour_id: int):
    denied = _muted_ugc_block()
    if denied:
        return denied
    user_id = _current_user_id()
    if holdings_bridge_service.is_old_goat_user(
        user_id=user_id,
        role=session.get("role"),
        username=session.get("username"),
    ):
        try:
            holdings_bridge_service.remove_old_goat_shared_holding(flavour_id=int(flavour_id))
            return jsonify({"status": "success", "source": "lab_shared_holdings"}), 200
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

    n = stash_service.delete_all_user_stash_for_flavour(user_id=int(user_id or 0), flavour_id=int(flavour_id))
    if not n:
        return jsonify({"status": "error", "message": "Stash item not found."}), 404
    return jsonify({"status": "success"}), 200


@tvw_api_bp.route("/mix/quick", methods=["POST"])
@login_required
def quick_mix():
    data = request.get_json(silent=True) or {}
    uid = _current_user_id()
    try:
        recipe_id = int(data.get("recipe_id") or 0)
        denied = _gate_recipe_response(recipe_id)
        if denied:
            return denied
        resolved_version_id = _resolve_version_id(
            recipe_id=recipe_id,
            requested_version_id=int(data.get("version_id") or 0) or None,
        )
        target_ml = float(data.get("target_ml") or 30.0)
        target_nic_mg_ml = float(data.get("target_nic_mg_ml") or 0.0)
        nic_base_mg_ml = float(data.get("nic_base_mg_ml") or 100.0)
        pg_ratio = float(data.get("pg_ratio") or 30.0)
        vg_ratio = float(data.get("vg_ratio") or 70.0)
        nicotine_carrier = str(data.get("nicotine_carrier") or "PG")
        payload = tvw_mix_service.quick_mix_for_recipe_version(
            recipe_id=recipe_id,
            version_id=resolved_version_id,
            target_ml=target_ml,
            target_nic_mg_ml=target_nic_mg_ml,
            nic_base_mg_ml=nic_base_mg_ml,
            pg_ratio=pg_ratio,
            vg_ratio=vg_ratio,
            nicotine_carrier=nicotine_carrier,
        )
        if uid:
            guided_mix_session_service.record_quick_mix_session(
                user_id=int(uid),
                recipe_version_id=int(resolved_version_id),
                target_ml=target_ml,
                target_nic_mg_ml=target_nic_mg_ml,
                nic_base_mg_ml=nic_base_mg_ml,
                pg_ratio=pg_ratio,
                vg_ratio=vg_ratio,
                nicotine_carrier=nicotine_carrier,
            )
        return jsonify({"status": "success", "resolved_version_id": resolved_version_id, "result": payload}), 200
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@tvw_api_bp.route("/mix/guided/session", methods=["POST"])
@login_required
def guided_mix_session():
    data = request.get_json(silent=True) or {}
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"status": "error", "message": "Not logged in."}), 401
    try:
        recipe_id = int(data.get("recipe_id") or 0)
        denied = _gate_recipe_response(recipe_id)
        if denied:
            return denied
        session_id, resolved_version_id, steps = guided_mix_session_service.create_guided_mix_session(
            user_id=user_id,
            recipe_id=recipe_id,
            requested_version_id=int(data.get("version_id") or 0) or None,
            target_ml=float(data.get("target_ml") or 30.0),
            target_nic_mg_ml=float(data.get("target_nic_mg_ml") or 0.0),
            nic_base_mg_ml=float(data.get("nic_base_mg_ml") or 100.0),
            pg_ratio=float(data.get("pg_ratio") or 30.0),
            vg_ratio=float(data.get("vg_ratio") or 70.0),
            nicotine_carrier=str(data.get("nicotine_carrier") or "PG"),
            nicotine_strictness_mode=str(data.get("nicotine_strictness_mode") or data.get("strictness_mode") or "personal"),
        )
        return jsonify(
            {
                "status": "success",
                "mix_session_id": session_id,
                "resolved_version_id": resolved_version_id,
                "steps": steps,
            }
        ), 201
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@tvw_api_bp.route("/mix/guided/sessions", methods=["GET"])
@login_required
def list_guided_mix_sessions():
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"status": "error", "message": "Not logged in."}), 401
    items = guided_mix_session_service.list_user_guided_sessions(user_id=user_id)
    if (request.args.get("in_progress") or "").strip() == "1":
        items = [i for i in items if i.get("in_progress")]
    return jsonify({"status": "success", "items": items}), 200


@tvw_api_bp.route("/mix/history", methods=["GET"])
@login_required
def list_mix_history():
    """Personal quick + guided rows (newest first; not a public feed)."""
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"status": "error", "message": "Not logged in."}), 401
    try:
        lim = int((request.args.get("limit") or "100").strip())
    except ValueError:
        lim = 100
    lim = max(1, min(200, lim))
    filt = (request.args.get("filter") or "all").strip().lower()
    items, total = guided_mix_session_service.list_user_mix_sessions(
        user_id=int(user_id),
        page=1,
        page_size=lim,
        filter_mode=filt,
    )
    return jsonify({"status": "success", "items": items, "total": total}), 200


@tvw_api_bp.route("/recipes/flavour-search", methods=["GET"])
@login_required
def recipe_builder_flavour_search():
    query = (request.args.get("q") or "").strip()
    try:
        limit = int(request.args.get("limit") or 100)
    except (TypeError, ValueError):
        limit = 100
    items = recipes_repo.search_flavour_reference_for_builder(query=query, limit=limit)
    return jsonify({"status": "success", "query": query, "items": items}), 200


@tvw_api_bp.route("/recipes/live-calc", methods=["POST"])
@login_required
def recipe_builder_live_calc():
    """Live builder calc endpoint backed by shared core calculator."""
    data = request.get_json(silent=True) or {}
    rows = data.get("rows") or []
    if not isinstance(rows, list):
        rows = []
    try:
        payload = tvw_mix_service.live_mix_for_builder(
            target_ml=float(data.get("target_ml") or 60.0),
            target_nic_mg_ml=float(data.get("target_nic_mg_ml") or 3.0),
            nic_base_mg_ml=float(data.get("nic_base_mg_ml") or 100.0),
            pg_ratio=float(data.get("pg_ratio") or 30.0),
            vg_ratio=float(data.get("vg_ratio") or 70.0),
            nicotine_carrier=str(data.get("nicotine_carrier") or "PG"),
            rows=rows,
        )
        return jsonify({"status": "success", "result": payload}), 200
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@tvw_api_bp.route("/mix/guided/session/<int:session_id>", methods=["GET"])
@login_required
def get_guided_mix_session(session_id: int):
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"status": "error", "message": "Not logged in."}), 401
    bundle = guided_mix_session_service.get_guided_session_bundle(session_id=session_id, user_id=user_id)
    if not bundle:
        return jsonify({"status": "error", "message": "Mix session not found."}), 404
    denied = _gate_recipe_response(int(bundle["recipe_id"]))
    if denied:
        return denied
    return jsonify({"status": "success", "session": bundle}), 200


@tvw_api_bp.route("/mix/guided/session/<int:session_id>/notes", methods=["POST"])
@login_required
def save_guided_mix_session_notes(session_id: int):
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"status": "error", "message": "Not logged in."}), 401
    data = request.get_json(silent=True) or {}
    outcome = guided_mix_session_service.save_guided_session_user_notes(
        session_id=int(session_id),
        user_id=int(user_id),
        user_notes=str(data.get("user_notes") or ""),
    )
    if not outcome.get("ok"):
        return jsonify({"status": "error", "message": outcome.get("message") or "Could not save notes."}), 409
    bundle = guided_mix_session_service.get_guided_session_bundle(session_id=session_id, user_id=user_id)
    if bundle:
        denied = _gate_recipe_response(int(bundle["recipe_id"]))
        if denied:
            return denied
    return jsonify(
        {
            "status": "success",
            "message": "Notes saved.",
            "user_notes": outcome.get("user_notes") or "",
            "user_notes_updated_at": outcome.get("user_notes_updated_at"),
            "session": bundle,
        }
    ), 200


@tvw_api_bp.route("/recipes/<int:recipe_id>/mixer-notes", methods=["GET"])
@login_required
def get_recipe_mixer_notes(recipe_id: int):
    denied = _gate_recipe_response(recipe_id)
    if denied:
        return denied
    try:
        limit = int((request.args.get("limit") or "3").strip())
    except ValueError:
        limit = 3
    limit = max(1, min(20, limit))
    notes, total = guided_mix_session_service.list_recipe_mixer_notes(
        recipe_id=int(recipe_id),
        limit=limit,
    )
    return jsonify({"status": "success", "notes": notes, "total": total}), 200


@tvw_api_bp.route("/recipes/<int:recipe_id>/batch-history", methods=["GET"])
@login_required
def get_recipe_batch_history(recipe_id: int):
    denied = _gate_recipe_response(recipe_id)
    if denied:
        return denied
    try:
        page = int((request.args.get("page") or "1").strip())
    except ValueError:
        page = 1
    try:
        page_size = int((request.args.get("page_size") or "20").strip())
    except ValueError:
        page_size = 20
    entries, total = guided_mix_session_service.list_recipe_batch_history(
        recipe_id=int(recipe_id),
        page=page,
        page_size=page_size,
    )
    return jsonify({"status": "success", "items": entries, "total": total, "page": max(1, page)}), 200


@tvw_api_bp.route("/mix/guided/session/<int:session_id>/steps/<int:step_no>/complete", methods=["POST"])
@login_required
def complete_guided_mix_step(session_id: int, step_no: int):
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"status": "error", "message": "Not logged in."}), 401
    data = request.get_json(silent=True) or {}
    undo = bool(data.get("undo"))
    accept_under = bool(data.get("accept_under"))
    under_action = str(data.get("under_action") or "").strip().lower() or None
    nicotine_action = str(data.get("nicotine_action") or "").strip().lower() or None
    measured_grams = data.get("measured_grams")
    try:
        measured_grams_value = None if measured_grams is None or str(measured_grams).strip() == "" else float(measured_grams)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "measured_grams must be a number."}), 400

    outcome: dict
    perf: dict
    bundle = None
    with guided_mix_perf_span(
        "route_step_complete",
        session_id=int(session_id),
        step_no=int(step_no),
        undo=undo,
    ) as route_span:
        try:
            with guided_mix_perf_span("service_complete_step", session_id=int(session_id), step_no=int(step_no)):
                outcome = guided_mix_session_service.complete_guided_step_with_policy(
                    session_id=session_id,
                    user_id=user_id,
                    step_no=step_no,
                    undo=undo,
                    measured_grams=measured_grams_value,
                    policy=tvw_mix_policy(),
                    accept_under=accept_under,
                    under_action=under_action,
                    nicotine_action=nicotine_action,
                )
        except sqlite3.OperationalError as exc:
            if is_sqlite_busy_error(exc):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": SQLITE_BUSY_BENCH_MESSAGE,
                            "code": "database_locked",
                        }
                    ),
                    503,
                )
            raise
        perf = dict(outcome.pop("_perf", None) or {})
        with guided_mix_perf_span("service_build_bundle", session_id=int(session_id), step_no=int(step_no)):
            bundle = guided_mix_session_service.get_guided_session_bundle(
                session_id=session_id,
                user_id=user_id,
                for_client=True,
            )
        route_span["response_payload_bytes"] = json_byte_size(bundle)

    if bundle:
        denied = _gate_recipe_response(int(bundle["recipe_id"]))
        if denied:
            return denied
    log_guided_step_complete_summary(
        session_id=int(session_id),
        step_no=int(step_no),
        ok=bool(outcome.get("ok")),
        **perf,
    )
    if not outcome.get("ok"):
        message = outcome.get("message") or "Mix session not found."
        policy_payload = outcome.get("policy") or {}
        status_code = 409 if policy_payload.get("hard_stops") else 404
        return (
            jsonify(
                {
                    "status": "error",
                    "message": message,
                    "policy": policy_payload,
                    "session": bundle,
                    "inserted_step_nos": outcome.get("inserted_step_nos") or [],
                    "reading": outcome.get("reading") or {},
                    "awaiting_under_decision": bool(outcome.get("awaiting_under_decision")),
                    "under_pour_active": bool(outcome.get("under_pour_active")),
                    "awaiting_nicotine_recovery": bool(outcome.get("awaiting_nicotine_recovery")),
                    "nicotine_recovery_active": bool(outcome.get("nicotine_recovery_active")),
                    "awaiting_nicotine_over_decision": bool(outcome.get("awaiting_nicotine_over_decision")),
                    "awaiting_overpour_recovery": bool(outcome.get("awaiting_overpour_recovery")),
                }
            ),
            status_code,
        )
    return (
        jsonify(
            {
                "status": "success",
                "session": bundle,
                "policy": outcome.get("policy") or {},
                "inserted_step_nos": outcome.get("inserted_step_nos") or [],
                "reading": outcome.get("reading") or {},
                "awaiting_under_decision": bool(outcome.get("awaiting_under_decision")),
                "under_pour_active": bool(outcome.get("under_pour_active")),
                "awaiting_nicotine_recovery": bool(outcome.get("awaiting_nicotine_recovery")),
                "nicotine_recovery_active": bool(outcome.get("nicotine_recovery_active")),
                "awaiting_nicotine_over_decision": bool(outcome.get("awaiting_nicotine_over_decision")),
                "awaiting_overpour_recovery": bool(outcome.get("awaiting_overpour_recovery")),
            }
        ),
        200,
    )


@tvw_api_bp.route("/mix/guided/session/<int:session_id>/events", methods=["POST"])
@login_required
def insert_guided_mix_event(session_id: int):
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"status": "error", "message": "Not logged in."}), 401
    data = request.get_json(silent=True) or {}
    outcome = guided_mix_session_service.insert_dynamic_guided_event_step(
        session_id=int(session_id),
        user_id=int(user_id),
        event_type=str(data.get("event_type") or "policy_warning"),
        message=str(data.get("message") or "Guided event triggered."),
        detail=str(data.get("detail") or ""),
        severity=str(data.get("severity") or "warning"),
        tone_profile=str(data.get("tone_profile") or "tvw"),
        context=(data.get("context") if isinstance(data.get("context"), dict) else {}),
    )
    if not outcome.get("ok"):
        return jsonify({"status": "error", "message": outcome.get("message") or "Could not insert event step."}), 404
    bundle = guided_mix_session_service.get_guided_session_bundle(session_id=session_id, user_id=user_id)
    if bundle:
        denied = _gate_recipe_response(int(bundle["recipe_id"]))
        if denied:
            return denied
    return jsonify({"status": "success", **outcome, "session": bundle}), 200

