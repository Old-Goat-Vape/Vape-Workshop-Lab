from __future__ import annotations

import secrets

from flask import abort, request, Blueprint, current_app, flash, jsonify, redirect, render_template, session, url_for

from services import community_discovery_service, guided_mix_session_service, member_notification_service, recognition_service
from db import get_db, sqlite_retry
from models import recipes_repo
from utils.auth import (
    can_access_lab_bridge,
    is_old_goat_role,
    login_required,
    tvw_enforce_account_before_request,
)

tvw_community_bp = Blueprint("tvw_community", __name__)


@tvw_community_bp.before_request
def _tvw_community_account_gate():
    return tvw_enforce_account_before_request(for_api=False)


@tvw_community_bp.route("/")
def community_home():
    if session.get("user_id"):
        return redirect(url_for("tvw_community.community_feed"))
    return redirect(url_for("auth.login"))


@tvw_community_bp.route("/community")
@login_required
def community_feed():
    viewer = int(session["user_id"]) if session.get("user_id") is not None else None
    viewer_is_old_goat = is_old_goat_role(session.get("role"))
    discovery = community_discovery_service.build_community_discovery_context(
        viewer_user_id=viewer,
        viewer_is_old_goat=viewer_is_old_goat,
    )
    active_guided_mix = None
    if viewer is not None:
        for session_row in guided_mix_session_service.list_user_guided_sessions(
            user_id=viewer, limit=10
        ):
            if session_row.get("in_progress"):
                active_guided_mix = session_row
                break
    return render_template(
        "tvw_community_home.html",
        active_guided_mix=active_guided_mix,
        viewer_is_old_goat=viewer_is_old_goat,
        **discovery,
    )


@tvw_community_bp.route("/notifications", methods=["GET", "POST"])
@login_required
def notifications():
    user_id = int(session["user_id"])
    if request.method == "POST":
        token = request.form.get("notification_token", "").encode()
        expected = session.get("notification_token", "").encode()
        if not expected or not secrets.compare_digest(token, expected):
            abort(403)
        action = request.form.get("action")
        if action == "read_all":
            sqlite_retry(lambda: member_notification_service.mark_all_read(user_id))
            return redirect(url_for(".notifications"), code=303)
        if action != "open":
            abort(400)
        item = sqlite_retry(lambda: member_notification_service.open_notification(
            user_id, request.form.get("kind", ""), request.form.get("event_key", ""),
        ))
        if not item:
            flash("This notification is no longer available.", "info")
            return redirect(url_for(".notifications"), code=303)
        if item["kind"] == "recipe_shared":
            target = url_for("tvw_recipes.tvw_recipe_detail", slug=item["target_id"])
        else:
            target = url_for("tvw_recipes.tvw_batch_steeping", session_id=item["target_id"])
        return redirect(target, code=303)

    if "notification_token" not in session:
        session["notification_token"] = secrets.token_urlsafe(32)
    page = max(1, request.args.get("page", 1, type=int))
    if page > 1_000_000:
        abort(404)
    page_size = 25
    items = member_notification_service.list_notifications(user_id, page=page, page_size=page_size)
    if not items and page > 1:
        return redirect(url_for(".notifications"))
    return render_template(
        "tvw_notifications.html", items=items[:page_size], page=page,
        has_next=len(items) > page_size, notification_token=session["notification_token"],
    ), 200, {"Cache-Control": "private, no-store"}


@tvw_community_bp.route("/my-mixer-stats")
@login_required
def mixer_stats():
    activity = recipes_repo.fetch_mixer_activity_counts(int(session["user_id"]))
    completed_mixes = activity["quick_mixes"] + activity["guided_mixes"]
    earned_badges, next_milestone = recognition_service.mixer_progress(activity)
    return render_template(
        "tvw_mixer_stats.html",
        activity=activity,
        completed_mixes=completed_mixes,
        earned_badges=earned_badges,
        next_milestone=next_milestone,
    ), 200, {"Cache-Control": "private, no-store"}


@tvw_community_bp.route("/profile/<username>")
@login_required
def profile(username: str):
    user = get_db().execute("SELECT id, username FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
    if not user:
        abort(404)
    page = request.args.get("page", 1, type=int) or 1
    page = max(1, page)
    recipes, total = recipes_repo.fetch_tvw_library(
        owner_user_id=int(user["id"]), viewer_user_id=int(session["user_id"]),
        page=page, page_size=20,
    )
    recognition_service.annotate_recipes(recipes, include_mixers=False)
    return render_template("tvw_profile.html", profile_user=dict(user), recipes=recipes,
                           profile_badge=recognition_service.mixer_badge(int(user["id"])),
                           total=total, page=page, has_next=page * 20 < total)


@tvw_community_bp.route("/bridge/lab")
@login_required
def bridge_to_lab():
    if not bool(current_app.config.get("TVW_ENABLE_LAB_MODULES")):
        flash("Lab bridge is currently disabled.", "warning")
        return redirect(url_for("tvw_community.community_feed"))
    if not can_access_lab_bridge(session.get("role")):
        flash("Lab access is restricted to admin-level users.", "warning")
        return redirect(url_for("tvw_community.community_feed"))
    return redirect(url_for("dashboard_index"))


@tvw_community_bp.route('/first-mix')
@login_required
def first_mix():
    from services.what_can_i_make_service import fetch_version_ingredients_map, map_reference_ids_to_flavour_pages
    candidates = get_db().execute(f"""SELECT r.id,r.name,r.slug,r.current_version_id
        FROM recipes r JOIN recipe_items ri ON ri.version_id=r.current_version_id
        WHERE {recipes_repo.tvw_recipe_public_list_predicate_sql('r')}
          AND LOWER(r.status)='ready' AND COALESCE(r.slug,'')!=''
        GROUP BY r.id HAVING COUNT(*) BETWEEN 2 AND 4
          AND MIN(ri.amount_percent)>0 AND SUM(ri.amount_percent)<=30
        ORDER BY COUNT(*),r.id LIMIT 50""").fetchall()
    beginner_recipe = None
    ingredient_map = fetch_version_ingredients_map([row['current_version_id'] for row in candidates])
    links = map_reference_ids_to_flavour_pages({i['ingredient_id'] for items in ingredient_map.values() for i in items})
    for row in candidates:
        items = ingredient_map.get(row['current_version_id'], [])
        if items and all(links.get(i['ingredient_id']) for i in items):
            beginner_recipe = dict(row)
            break
    return render_template('tvw_first_mix.html', beginner_recipe=beginner_recipe)


@tvw_community_bp.route('/recipe-guide')
@login_required
def recipe_guide():
    from services.stash_recipe_guide_service import build_context
    context = build_context(user_id=int(session['user_id']), role=session.get('role'),
                            username=session.get('username'), main_id=request.args.get('main', type=int))
    return render_template('tvw_recipe_guide.html', **context), 200, {'Cache-Control': 'private, no-store'}
