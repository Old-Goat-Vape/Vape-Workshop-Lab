from __future__ import annotations

import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from db import SQLITE_BUSY_USER_MESSAGE, is_sqlite_busy_error, sqlite_retry
from models import tvw_admin_repo, tvw_reports_repo
from utils.auth import (
    can_access_tvw_admin,
    can_perform_destructive_tvw_admin_actions,
    login_required,
    tvw_enforce_account_before_request,
)

tvw_admin_bp = Blueprint("tvw_admin", __name__, url_prefix="/admin")


@tvw_admin_bp.before_request
def _tvw_admin_account_gate():
    return tvw_enforce_account_before_request(for_api=False)


def _require_tvw_admin():
    role = session.get("role")
    if not can_access_tvw_admin(role):
        abort(403)
    return role


@tvw_admin_bp.route("")
@login_required
def dashboard():
    _require_tvw_admin()
    counts = tvw_admin_repo.fetch_admin_dashboard_counts()
    recent = tvw_admin_repo.list_admin_audit_logs(limit=20)
    return render_template("tvw_admin_dashboard.html", counts=counts, recent_logs=recent)


@tvw_admin_bp.route("/flavours")
@login_required
def flavour_moderation():
    _require_tvw_admin()
    search = (request.args.get("search") or "").strip()
    manufacturer = (request.args.get("manufacturer") or "").strip()
    status = (request.args.get("status") or "").strip().lower()
    page_raw = (request.args.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() else 1
    rows, total = tvw_admin_repo.list_flavour_moderation_rows(
        search=search,
        manufacturer=manufacturer,
        status=status,
        page=page,
        page_size=50,
    )
    manufacturers = tvw_admin_repo.list_flavour_manufacturers()
    total_pages = (total + 49) // 50 if total else 1
    return render_template(
        "tvw_admin_flavours.html",
        rows=rows,
        total=total,
        page=page,
        total_pages=total_pages,
        search=search,
        manufacturer=manufacturer,
        status=status,
        manufacturers=manufacturers,
        can_destructive=can_perform_destructive_tvw_admin_actions(session.get("role")),
    )


@tvw_admin_bp.route("/flavours/<int:flavour_id>/update", methods=["POST"])
@login_required
def update_flavour(flavour_id: int):
    role = _require_tvw_admin()
    action = (request.form.get("action") or "").strip()
    reason = (request.form.get("reason") or "").strip()
    canonical_name = (request.form.get("canonical_name") or "").strip() or None
    brand = (request.form.get("brand") or "").strip() or None
    category = (request.form.get("category") or "").strip() or None

    kwargs = {
        "flavour_id": flavour_id,
        "actor_user_id": int(session.get("user_id") or 0),
        "actor_role": str(role or ""),
        "moderation_reason": reason,
    }
    if action == "hide":
        kwargs.update({"is_hidden": 1, "moderation_status": "hidden"})
    elif action == "unhide":
        kwargs.update({"is_hidden": 0, "moderation_status": "active"})
    elif action == "mark_invalid":
        kwargs.update({"is_invalid": 1, "moderation_status": "invalid"})
    elif action == "mark_valid":
        kwargs.update({"is_invalid": 0, "moderation_status": "active"})
    elif action == "edit":
        kwargs.update({"canonical_name": canonical_name, "brand": brand, "category": category})
    else:
        flash("Unknown moderation action.", "warning")
        return redirect(url_for("tvw_admin.flavour_moderation"))

    tvw_admin_repo.update_flavour_moderation(**kwargs)
    flash("Flavour moderation action saved.", "success")
    return redirect(url_for("tvw_admin.flavour_moderation"))


@tvw_admin_bp.route("/flavours/cleanup")
@login_required
def flavour_identity_cleanup():
    _require_tvw_admin()
    search = (request.args.get("search") or "").strip()
    manufacturer = (request.args.get("manufacturer") or "").strip()
    view = (request.args.get("view") or "").strip().lower()
    page_raw = (request.args.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() else 1
    rows, total = tvw_admin_repo.list_flavour_identity_cleanup_rows(
        search=search,
        manufacturer=manufacturer,
        view=view,
        page=page,
        page_size=50,
    )
    manufacturers = tvw_admin_repo.list_flavour_manufacturers()
    total_pages = (total + 49) // 50 if total else 1
    return render_template(
        "tvw_admin_flavours_cleanup.html",
        rows=rows,
        total=total,
        page=page,
        total_pages=total_pages,
        search=search,
        manufacturer=manufacturer,
        view=view,
        manufacturers=manufacturers,
        can_merge=can_perform_destructive_tvw_admin_actions(session.get("role")),
    )


@tvw_admin_bp.route("/flavours/merge/pick")
@login_required
def flavour_merge_pick():
    _require_tvw_admin()
    src_raw = (request.args.get("source") or "").strip()
    tgt_raw = (request.args.get("target") or "").strip()
    if src_raw.isdigit() and tgt_raw.isdigit():
        return redirect(
            url_for(
                "tvw_admin.flavour_merge_compare",
                source_id=int(src_raw),
                target_id=int(tgt_raw),
            )
        )
    return render_template(
        "tvw_admin_flavour_merge_pick.html",
        can_merge=can_perform_destructive_tvw_admin_actions(session.get("role")),
    )


@tvw_admin_bp.route("/flavours/merge/<int:source_id>/<int:target_id>", methods=["GET", "POST"])
@login_required
def flavour_merge_compare(source_id: int, target_id: int):
    _require_tvw_admin()
    role = session.get("role")
    can_merge = can_perform_destructive_tvw_admin_actions(role)
    if request.method == "POST":
        if not can_merge:
            flash("Only Master or Old Goat may run flavour merges.", "danger")
            return redirect(
                url_for("tvw_admin.flavour_merge_compare", source_id=source_id, target_id=target_id)
            )
        action = (request.form.get("action") or "").strip().lower()
        reason = (request.form.get("reason") or "").strip()
        try:
            if action == "execute_merge":
                tvw_admin_repo.execute_flavour_merge_into(
                    source_flavour_id=source_id,
                    target_flavour_id=target_id,
                    actor_user_id=int(session.get("user_id") or 0),
                    actor_role=str(role or ""),
                    reason=reason,
                )
                flash("Full flavour merge completed. Source row kept as merged alias (not deleted).", "success")
            elif action == "merge_pointer_only":
                tvw_admin_repo.set_flavour_merge_pointer_only(
                    source_flavour_id=source_id,
                    target_flavour_id=target_id,
                    actor_user_id=int(session.get("user_id") or 0),
                    actor_role=str(role or ""),
                    reason=reason,
                )
                flash("Pointer-only merge recorded (notes/reviews moved; no recipe/stash on source).", "success")
            else:
                flash("Unknown merge action.", "warning")
                return redirect(
                    url_for("tvw_admin.flavour_merge_compare", source_id=source_id, target_id=target_id)
                )
        except ValueError as exc:
            flash(str(exc), "warning")
            return redirect(
                url_for("tvw_admin.flavour_merge_compare", source_id=source_id, target_id=target_id)
            )
        return redirect(url_for("tvw_admin.flavour_identity_cleanup"))

    src = tvw_admin_repo.fetch_flavour_identity_bundle(source_id)
    tgt = tvw_admin_repo.fetch_flavour_identity_bundle(target_id)
    if not src or not tgt:
        abort(404)
    return render_template(
        "tvw_admin_flavour_merge.html",
        source=src,
        target=tgt,
        can_merge=can_merge,
    )


@tvw_admin_bp.route("/recipes")
@login_required
def recipe_moderation():
    _require_tvw_admin()
    search = (request.args.get("search") or "").strip()
    visibility = (request.args.get("visibility") or "").strip().lower()
    status = (request.args.get("status") or "").strip().lower()
    moderation_status = (request.args.get("moderation_status") or "").strip().lower()
    hidden_filter = (request.args.get("hidden") or "").strip().lower()
    page_raw = (request.args.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() else 1
    rows, total = tvw_admin_repo.list_recipe_moderation_rows(
        search=search,
        visibility=visibility,
        status=status,
        moderation_status=moderation_status,
        hidden_filter=hidden_filter,
        page=page,
        page_size=30,
    )
    total_pages = (total + 29) // 30 if total else 1
    return render_template(
        "tvw_admin_recipes.html",
        rows=rows,
        total=total,
        page=page,
        total_pages=total_pages,
        search=search,
        visibility=visibility,
        status=status,
        moderation_status=moderation_status,
        hidden_filter=hidden_filter,
    )


@tvw_admin_bp.route("/recipes/<int:recipe_id>")
@login_required
def recipe_moderation_detail(recipe_id: int):
    _require_tvw_admin()
    bundle = tvw_admin_repo.fetch_admin_recipe_bundle(recipe_id)
    if not bundle:
        abort(404)
    return render_template("tvw_admin_recipe_detail.html", recipe=bundle)


@tvw_admin_bp.route("/recipes/<int:recipe_id>/update", methods=["POST"])
@login_required
def update_recipe_moderation(recipe_id: int):
    role = _require_tvw_admin()
    action = (request.form.get("action") or "").strip().lower()
    reason = (request.form.get("reason") or "").strip()
    mod_status = (request.form.get("moderation_status") or "").strip() or None
    try:
        def _persist_moderation() -> None:
            tvw_admin_repo.update_recipe_moderation(
                recipe_id=recipe_id,
                actor_user_id=int(session.get("user_id") or 0),
                actor_role=str(role or ""),
                action=action,
                reason=reason,
                moderation_status=mod_status if action == "set_moderation" else None,
            )

        sqlite_retry(_persist_moderation, attempts=15, base_delay_s=0.05)
        if action == "hide":
            flash("Recipe hidden from public views.", "success")
        elif action == "unhide":
            flash("Recipe unhidden.", "success")
        elif action == "archive":
            flash("Recipe archived (inactive).", "success")
        elif action == "restore":
            flash("Recipe restored to active.", "success")
        elif action == "set_moderation":
            flash(f"Recipe moderation status set to {str(mod_status or '').upper()}.", "success")
        else:
            flash("Recipe moderation saved.", "success")
    except ValueError as exc:
        flash(str(exc), "warning")
    except sqlite3.OperationalError as exc:
        if is_sqlite_busy_error(exc):
            flash(SQLITE_BUSY_USER_MESSAGE, "danger")
        else:
            flash("Could not update recipe moderation. Please try again.", "danger")
    return redirect(url_for("tvw_admin.recipe_moderation_detail", recipe_id=recipe_id))


@tvw_admin_bp.route("/community")
@login_required
def community_moderation():
    _require_tvw_admin()
    content_type = (request.args.get("type") or "feedback").strip().lower()
    search = (request.args.get("search") or "").strip()
    moderation_status = (request.args.get("moderation_status") or "").strip().lower()
    page_raw = (request.args.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() else 1
    rows, total = tvw_admin_repo.list_community_content_queue(
        content_type=content_type,
        search=search,
        moderation_status=moderation_status,
        page=page,
        page_size=35,
    )
    total_pages = (total + 34) // 35 if total else 1
    return render_template(
        "tvw_admin_community.html",
        rows=rows,
        total=total,
        page=page,
        total_pages=total_pages,
        content_type=content_type,
        search=search,
        moderation_status=moderation_status,
    )


@tvw_admin_bp.route("/community/<kind>/<int:content_id>/update", methods=["POST"])
@login_required
def update_community_content(kind: str, content_id: int):
    role = _require_tvw_admin()
    action = (request.form.get("action") or "").strip().lower()
    reason = (request.form.get("reason") or "").strip()
    try:
        tvw_admin_repo.update_community_content_moderation(
            kind=kind,
            content_id=content_id,
            actor_user_id=int(session.get("user_id") or 0),
            actor_role=str(role or ""),
            action=action,
            reason=reason,
        )
        message_map = {
            "hide": "Content hidden from public view.",
            "unhide": "Content unhidden.",
            "mark_spam": "Content marked as spam.",
            "mark_review": "Content queued for review.",
            "restore_active": "Content restored to active.",
        }
        flash(message_map.get(action, "Community content updated."), "success")
    except ValueError as exc:
        flash(str(exc), "warning")
    try:
        rp = int(request.form.get("return_page") or 1)
    except (TypeError, ValueError):
        rp = 1
    return redirect(
        url_for(
            "tvw_admin.community_moderation",
            type=request.form.get("return_type") or "feedback",
            search=request.form.get("return_search") or "",
            moderation_status=request.form.get("return_mod_status") or "",
            page=rp,
        )
    )


@tvw_admin_bp.route("/audit")
@login_required
def audit_log():
    _require_tvw_admin()
    logs = tvw_admin_repo.list_admin_audit_logs(limit=200)
    return render_template("tvw_admin_audit.html", logs=logs)


@tvw_admin_bp.route("/reports")
@login_required
def content_reports_queue():
    _require_tvw_admin()
    target_type = (request.args.get("target_type") or "").strip().lower()
    status = (request.args.get("status") or "").strip().lower()
    search = (request.args.get("search") or "").strip()
    page_raw = (request.args.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() else 1
    rows, total = tvw_reports_repo.list_content_reports(
        target_type=target_type,
        status=status,
        search=search,
        page=page,
        page_size=35,
    )
    total_pages = (total + 34) // 35 if total else 1
    return render_template(
        "tvw_admin_reports.html",
        rows=rows,
        total=total,
        page=page,
        total_pages=total_pages,
        target_type=target_type,
        status=status,
        search=search,
    )


@tvw_admin_bp.route("/reports/<int:report_id>/update", methods=["POST"])
@login_required
def update_content_report(report_id: int):
    role = _require_tvw_admin()
    action = (request.form.get("action") or "").strip().lower()
    note = (request.form.get("note") or "").strip()
    try:
        tvw_reports_repo.update_report_moderation(
            report_id=report_id,
            action=action,
            actor_user_id=int(session.get("user_id") or 0),
            actor_role=str(role or ""),
            note=note,
        )
        msg = {
            "mark_reviewed": "Report marked reviewed.",
            "dismiss": "Report dismissed.",
            "escalate": "Report escalated.",
            "reopen": "Report reopened.",
        }.get(action, "Report updated.")
        flash(msg, "success")
    except ValueError as exc:
        flash(str(exc), "warning")
    try:
        rp = int(request.form.get("return_page") or 1)
    except (TypeError, ValueError):
        rp = 1
    return redirect(
        url_for(
            "tvw_admin.content_reports_queue",
            target_type=request.form.get("return_target_type") or "",
            status=request.form.get("return_status") or "",
            search=request.form.get("return_search") or "",
            page=rp,
        )
    )


@tvw_admin_bp.route("/users")
@login_required
def user_moderation():
    _require_tvw_admin()
    search = (request.args.get("search") or "").strip()
    role_filter = (request.args.get("role") or "").strip().lower()
    moderation_status = (request.args.get("moderation_status") or "").strip().lower()
    suspended = (request.args.get("suspended") or "").strip().lower()
    banned = (request.args.get("banned") or "").strip().lower()
    muted = (request.args.get("muted") or "").strip().lower()
    page_raw = (request.args.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() else 1
    rows, total = tvw_admin_repo.list_user_moderation_rows(
        search=search,
        role_filter=role_filter,
        moderation_status=moderation_status,
        suspended=suspended,
        banned=banned,
        muted=muted,
        page=page,
        page_size=35,
    )
    total_pages = (total + 34) // 35 if total else 1
    role_options = tvw_admin_repo.list_distinct_user_roles()
    return render_template(
        "tvw_admin_users.html",
        rows=rows,
        total=total,
        page=page,
        total_pages=total_pages,
        search=search,
        role_filter=role_filter,
        moderation_status=moderation_status,
        suspended=suspended,
        banned=banned,
        muted=muted,
        role_options=role_options,
    )


@tvw_admin_bp.route("/users/<int:user_id>")
@login_required
def user_moderation_detail(user_id: int):
    role = _require_tvw_admin()
    bundle = tvw_admin_repo.fetch_admin_user_bundle(user_id)
    if not bundle:
        abort(404)
    assignable_roles = tvw_admin_repo.list_assignable_roles_for_actor(role)
    return render_template(
        "tvw_admin_user_detail.html",
        u=bundle,
        assignable_roles=assignable_roles,
        actor_role=role,
    )


@tvw_admin_bp.route("/users/<int:user_id>/role", methods=["POST"])
@login_required
def update_user_role(user_id: int):
    role = _require_tvw_admin()
    new_role = (request.form.get("new_role") or "").strip()
    reason = (request.form.get("reason") or "").strip()
    try:
        tvw_admin_repo.update_user_role_with_audit(
            target_user_id=user_id,
            new_role=new_role,
            actor_user_id=int(session.get("user_id") or 0),
            actor_role=str(role or ""),
            reason=reason,
        )
        flash("User role updated.", "success")
    except ValueError as exc:
        flash(str(exc), "warning")
    return redirect(url_for("tvw_admin.user_moderation_detail", user_id=user_id))


@tvw_admin_bp.route("/users/<int:user_id>/update", methods=["POST"])
@login_required
def update_user_moderation_action(user_id: int):
    role = _require_tvw_admin()
    action = (request.form.get("action") or "").strip().lower()
    reason = (request.form.get("reason") or "").strip()
    mod_status = (request.form.get("moderation_status") or "").strip() or None
    mod_note_kw: str | None = None
    if action == "set_moderation_note":
        raw = request.form.get("moderation_note")
        mod_note_kw = "" if raw is None else str(raw)
    try:
        tvw_admin_repo.update_user_moderation(
            target_user_id=user_id,
            actor_user_id=int(session.get("user_id") or 0),
            actor_role=str(role or ""),
            action=action,
            reason=reason,
            moderation_status=mod_status if action == "set_moderation_status" else None,
            moderation_note=mod_note_kw,
        )
        flash("User moderation saved.", "success")
    except ValueError as exc:
        flash(str(exc), "warning")
    return redirect(url_for("tvw_admin.user_moderation_detail", user_id=user_id))

