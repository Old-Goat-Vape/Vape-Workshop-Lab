from __future__ import annotations

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, session, url_for

from services import community_discovery_service, guided_mix_session_service
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


@tvw_community_bp.route("/profile/<username>")
def profile(username: str):
    return jsonify({"status": "success", "username": username})


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
