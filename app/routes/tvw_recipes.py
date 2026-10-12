from __future__ import annotations

import logging
import os
import re
import sqlite3
import uuid

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

from db import SQLITE_BUSY_USER_MESSAGE, is_sqlite_busy_error, sqlite_retry
from models import recipes_repo, users_repo
from services import guided_mix_session_service
from services import guided_mix_start_service
from services import recipe_library_discovery_service
from services import recipe_library_presentation
from services import stash_service
from services import what_can_i_make_service
from services import recipe_builder_service
from services.guided_mix_start_service import GuidedMixFormError
from services.recipe_builder_service import RecipeBuilderFormError
from utils.guided_mix_perf import (
    guided_mix_perf_span,
    log_guided_mix_stage,
    new_guided_mix_request_id,
)
from utils.auth import (
    can_access_tvw_admin,
    is_old_goat_role,
    login_required,
    tvw_enforce_account_before_request,
)

tvw_recipes_bp = Blueprint("tvw_recipes", __name__)


def _editor_flavour_seed(items: list) -> list:
    ids = [int(i.get("ingredient_id") or 0) for i in (items or [])]
    ids = [x for x in ids if x > 0]
    if not ids:
        return []
    return list(recipes_repo.fetch_flavour_reference_map(ids).values())


@tvw_recipes_bp.before_request
def _tvw_recipes_account_gate():
    return tvw_enforce_account_before_request(for_api=False)


def _render_recipe_builder(
    *,
    mode: str,
    recipe=None,
    active_version=None,
    items=None,
    tags=None,
    flavour_options=None,
    form_values=None,
    form_errors=None,
    flavour_rows=None,
):
    return render_template(
        "tvw_recipe_builder.html",
        mode=mode,
        recipe=recipe,
        active_version=active_version,
        items=items or [],
        tags=tags or [],
        flavour_options=flavour_options or [],
        form_values=form_values or {},
        form_errors=form_errors or {},
        flavour_rows=flavour_rows,
    )


@tvw_recipes_bp.route("/recipes/new")
@login_required
def tvw_recipe_new():
    return _render_recipe_builder(mode="create")


def _slugify(name: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    return text or "recipe"


def _next_version(last_version: str | None) -> str:
    if not last_version:
        return "1.0"
    try:
        return f"{(float(last_version) + 0.1):.1f}"
    except Exception:
        return "1.0"


@tvw_recipes_bp.route("/recipes", methods=["GET"])
@login_required
def tvw_recipe_library():
    search = (request.args.get("search") or "").strip()
    status = (request.args.get("status") or "").strip().lower()
    visibility = (request.args.get("visibility") or "").strip().lower()
    tag = (request.args.get("tag") or "").strip().lower()
    sort_by = (request.args.get("sort") or "updated").strip().lower()
    if sort_by not in {"updated", "newest", "name", "stash", "rating", "favorites"}:
        sort_by = "updated"
    sort_dir = (request.args.get("dir") or "desc").strip().lower()
    if sort_dir not in {"asc", "desc"}:
        sort_dir = "desc"
    batch_ml_raw = (request.args.get("batch_ml") or "").strip()
    target_batch_ml = None
    if batch_ml_raw:
        try:
            target_batch_ml = float(batch_ml_raw)
        except ValueError:
            target_batch_ml = None

    page_raw = (request.args.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() else 1
    page_size = 20
    uid = int(session.get("user_id")) if session.get("user_id") is not None else None
    role_s = str(session.get("role") or "")
    viewer_is_old_goat = is_old_goat_role(role_s)
    username_s = str(session.get("username") or "")
    favorites_only = (request.args.get("favorites") or "").strip() in ("1", "true", "yes")
    favorites_user_id = uid if favorites_only and uid is not None else None
    mine_only = (request.args.get("mine") or "").strip() in ("1", "true", "yes")
    owner_user_id = uid if mine_only and uid is not None else None

    stash_meta: dict | None = None
    if sort_by == "stash":
        recipes, total, stash_meta = recipe_library_discovery_service.fetch_tvw_library_stash_sorted_page(
            search=search,
            status=status,
            visibility=visibility,
            tag=tag,
            page=page,
            page_size=page_size,
            user_id=uid,
            viewer_is_old_goat=viewer_is_old_goat,
            role=role_s,
            username=username_s,
            target_batch_ml=target_batch_ml,
            favorites_user_id=favorites_user_id,
        )
        total_pages = recipe_library_discovery_service.stash_sorted_total_pages(
            ranked_pool_size=int((stash_meta or {}).get("stash_rank_pool") or 0),
            page_size=page_size,
        )
    else:
        recipes, total = recipes_repo.fetch_tvw_library(
            search=search,
            status=status,
            visibility=visibility,
            tag=tag,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_dir=sort_dir,
            viewer_user_id=uid,
            viewer_is_old_goat=viewer_is_old_goat,
            favorites_user_id=favorites_user_id,
            owner_user_id=owner_user_id,
        )
        recipe_library_discovery_service.enrich_recipes_with_stash_summary(
            recipes,
            user_id=uid,
            role=role_s,
            username=username_s,
            target_batch_ml=target_batch_ml,
        )
        total_pages = (total + page_size - 1) // page_size if total else 1

    recipe_library_presentation.enrich_recipes_for_library_cards(
        recipes,
        viewer_user_id=uid,
        viewer_username=username_s,
    )
    scope_counts = recipe_library_presentation.library_scope_counts(
        viewer_user_id=uid,
        viewer_is_old_goat=viewer_is_old_goat,
    )
    active_scope = recipe_library_presentation.detect_library_scope(
        favorites_only=favorites_only,
        mine_only=mine_only,
        visibility=visibility,
    )
    sort_label = recipe_library_presentation.sort_label(sort_by=sort_by, sort_dir=sort_dir)
    advanced_filters_active = bool(
        status or tag or (visibility and active_scope not in {"public", "private"}) or sort_by != "updated" or sort_dir != "desc"
    )

    return render_template(
        "tvw_recipe_library.html",
        recipes=recipes,
        total=total,
        page=page,
        total_pages=total_pages,
        filters={
            "search": search,
            "status": status,
            "visibility": visibility,
            "tag": tag,
            "sort": sort_by,
            "dir": sort_dir,
            "batch_ml": batch_ml_raw,
            "favorites": favorites_only,
            "mine": mine_only,
        },
        scope_counts=scope_counts,
        active_scope=active_scope,
        sort_label=sort_label,
        advanced_filters_active=advanced_filters_active,
        stash_meta=stash_meta,
        viewer_is_old_goat=viewer_is_old_goat,
        viewer_username=username_s,
    )


@tvw_recipes_bp.route("/what-can-i-make")
@login_required
def tvw_what_can_i_make():
    raw_batch = (request.args.get("batch_ml") or "").strip()
    target_batch_ml = None
    if raw_batch:
        try:
            target_batch_ml = float(raw_batch)
        except ValueError:
            target_batch_ml = None
    bundle = what_can_i_make_service.build_what_can_i_make_bundle(
        user_id=int(session.get("user_id")) if session.get("user_id") is not None else None,
        role=str(session.get("role") or ""),
        username=str(session.get("username") or ""),
        target_batch_ml=target_batch_ml,
    )
    return render_template("tvw_what_can_i_make.html", wcm=bundle)


@tvw_recipes_bp.route("/my-mixes")
@login_required
def tvw_my_mixes():
    uid = int(session.get("user_id"))
    filt = (request.args.get("filter") or "all").strip().lower()
    if filt not in ("all", "in_progress", "completed", "guided", "quick"):
        filt = "all"
    page_raw = (request.args.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() else 1
    page = max(1, page)
    page_size = 20
    rows, total = guided_mix_session_service.list_user_mix_sessions(
        user_id=uid,
        page=page,
        page_size=page_size,
        filter_mode=filt,
    )
    total_pages = (total + page_size - 1) // page_size if total else 1
    if page > total_pages and total > 0:
        return redirect(url_for("tvw_recipes.tvw_my_mixes", filter=filt, page=total_pages))
    return render_template(
        "tvw_my_mixes.html",
        sessions=rows,
        filter_active=filt,
        page=page,
        total=total,
        total_pages=total_pages,
    )


@tvw_recipes_bp.route("/guided-mix")
@login_required
def tvw_guided_mix_index():
    return redirect(url_for("tvw_recipes.tvw_my_mixes"))


@tvw_recipes_bp.route("/guided-mix/session/<int:session_id>")
@login_required
def tvw_guided_mix_session(session_id: int):
    req_id = session.pop("_guided_mix_perf_req", None) or new_guided_mix_request_id()
    from utils.guided_mix_perf import set_guided_mix_request_id

    set_guided_mix_request_id(str(req_id))
    with guided_mix_perf_span("route_guided_session_get", session_id=int(session_id), request=req_id) as span:
        uid = int(session.get("user_id"))
        with guided_mix_perf_span("session-page-bundle", session_id=int(session_id)):
            bundle = guided_mix_session_service.get_guided_session_bundle(session_id=session_id, user_id=uid)
        if not bundle:
            abort(404)
        with guided_mix_perf_span("session-page-recipe-gate", session_id=int(session_id)):
            recipe = recipes_repo.fetch_recipe(int(bundle["recipe_id"]))
            if not recipe or not recipes_repo.can_view_recipe_in_tvw_community(
                recipe,
                viewer_user_id=uid,
                viewer_is_old_goat=is_old_goat_role(session.get("role")),
            ):
                flash("This mix session is unavailable.", "warning")
                return redirect(url_for("tvw_recipes.tvw_my_mixes"))
        with guided_mix_perf_span("session-page-render", session_id=int(session_id)):
            html = render_template(
                "tvw_guided_mix_session.html",
                session_id=session_id,
                bundle=bundle,
                recipe=recipe,
            )
        span["html_bytes"] = len(html.encode("utf-8")) if isinstance(html, str) else 0
        log_guided_mix_stage(
            "session-page",
            session_id=int(session_id),
            html_bytes=span.get("html_bytes"),
            step_count=len(bundle.get("steps") or []),
            mix_summary=bool(bundle.get("mix_summary")),
        )
        return html


@tvw_recipes_bp.route("/recipes/<slug>/guided-mix/start", methods=["POST"])
@login_required
def tvw_guided_mix_start_post(slug: str):
    return _start_guided_mix_from_recipe(slug)


@tvw_recipes_bp.route("/recipes/<slug>/guided-mix", methods=["GET", "POST"])
@login_required
def tvw_guided_mix_start(slug: str):
    """Legacy URL — POST creates a session; GET redirects to recipe detail mixing panel."""
    if request.method == "POST":
        return _start_guided_mix_from_recipe(slug)
    recipe = recipes_repo.fetch_recipe_by_slug_or_id(slug)
    if not recipe:
        abort(404)
    uid = int(session.get("user_id")) if session.get("user_id") is not None else None
    if not recipes_repo.can_view_recipe_in_tvw_community(
        recipe,
        viewer_user_id=uid,
        viewer_is_old_goat=is_old_goat_role(session.get("role")),
    ):
        flash("This recipe is unavailable.", "warning")
        return redirect(url_for("tvw_recipes.tvw_recipe_library"))
    detail_slug = str(recipe.get("slug") or recipe["id"])
    qs = request.query_string.decode("utf-8")
    dest = url_for("tvw_recipes.tvw_recipe_detail", slug=detail_slug)
    if qs:
        dest = f"{dest}?{qs}"
    return redirect(f"{dest}#mix-card")


def _start_guided_mix_from_recipe(slug: str):
    req_id = new_guided_mix_request_id()
    with guided_mix_perf_span("route_guided_mix_start", slug=str(slug), request=req_id) as span:
        with guided_mix_perf_span("load-recipe"):
            recipe = recipes_repo.fetch_recipe_by_slug_or_id(slug)
        if not recipe:
            abort(404)
        uid = int(session.get("user_id")) if session.get("user_id") is not None else None
        if uid is None:
            abort(401)
        if not recipes_repo.can_view_recipe_in_tvw_community(
            recipe,
            viewer_user_id=uid,
            viewer_is_old_goat=is_old_goat_role(session.get("role")),
        ):
            flash("This recipe is unavailable.", "warning")
            return redirect(url_for("tvw_recipes.tvw_recipe_library"))
        try:
            with guided_mix_perf_span("validate-form"):
                targets = guided_mix_start_service.parse_guided_mix_form(request.form)
            with guided_mix_perf_span("create-session", recipe_id=int(recipe["id"])):
                sid, _rv, steps = guided_mix_session_service.create_guided_mix_session(
                    user_id=uid,
                    recipe_id=int(recipe["id"]),
                    **targets.as_create_kwargs(),
                )
            span["session_id"] = sid
            span["step_count"] = len(steps)
            log_guided_mix_stage(
                "start-route",
                session_id=sid,
                step_count=len(steps),
                recipe_id=int(recipe["id"]),
            )
            # Redirect only — do not build the bench bundle on POST.
            session["_guided_mix_perf_req"] = req_id
            return redirect(url_for("tvw_recipes.tvw_guided_mix_session", session_id=sid))
        except GuidedMixFormError as exc:
            return _render_recipe_detail(
                slug,
                mix_form=exc.values,
                mix_errors=exc.errors,
            )
        except ValueError as exc:
            preserved = guided_mix_start_service.mix_form_from_query_args(request.form) or {}
            return _render_recipe_detail(
                slug,
                mix_form=preserved,
                mix_errors={"mix_summary": str(exc)},
            )
        except sqlite3.OperationalError as exc:
            if is_sqlite_busy_error(exc):
                preserved = guided_mix_start_service.mix_form_from_query_args(request.form) or {}
                return _render_recipe_detail(
                    slug,
                    mix_form=preserved,
                    mix_errors={
                        "mix_summary": (
                            "The database was busy while starting your mix. "
                            "Please try Start guided mix again."
                        )
                    },
                )
            raise


def _render_recipe_detail(
    slug: str,
    *,
    mix_form: dict[str, str] | None = None,
    mix_errors: dict[str, str] | None = None,
):
    recipe = recipes_repo.fetch_recipe_by_slug_or_id(slug)
    if not recipe:
        abort(404)
    uid = int(session["user_id"]) if session.get("user_id") is not None else None
    if not recipes_repo.can_view_recipe_in_tvw_community(
        recipe,
        viewer_user_id=uid,
        viewer_is_old_goat=is_old_goat_role(session.get("role")),
    ):
        flash("This recipe is unavailable.", "warning")
        return redirect(url_for("tvw_recipes.tvw_recipe_library"))

    effective_mix_form = mix_form if mix_form is not None else guided_mix_start_service.mix_form_from_query_args(request.args)

    requested_version_raw = (request.args.get("version_id") or "").strip()
    if effective_mix_form and str(effective_mix_form.get("version_id") or "").strip().isdigit():
        requested_version_raw = str(effective_mix_form.get("version_id") or "").strip()
    requested_version = int(requested_version_raw) if requested_version_raw.isdigit() else None
    batch_ml_raw = (request.args.get("batch_ml") or "").strip()
    try:
        batch_ml_param = float(batch_ml_raw) if batch_ml_raw else None
    except ValueError:
        batch_ml_param = None

    versions = recipes_repo.fetch_recipe_versions(int(recipe["id"]))
    versions_by_id = {int(v["id"]): v for v in versions}
    published_version_id = int(recipe["current_version_id"]) if recipe.get("current_version_id") else None
    published_version = (
        versions_by_id.get(published_version_id) if published_version_id is not None else None
    )
    current_version = None
    if published_version_id is not None:
        current_version = versions_by_id.get(published_version_id)
    if requested_version is not None and requested_version in versions_by_id:
        current_version = versions_by_id[requested_version]
    if current_version is None and versions:
        current_version = versions[0]

    if current_version is None:
        abort(404)

    viewing_version_id = int(current_version["id"])
    viewing_is_published_current = (
        published_version_id is not None and viewing_version_id == published_version_id
    )

    ingredients = recipes_repo.fetch_version_ingredients_for_view(
        viewing_version_id,
        recipe_id=int(recipe["id"]),
        recipe_code=str(recipe.get("recipe_code") or ""),
    )
    total_flavour_pct = sum(float(i.get("amount_percent") or 0.0) for i in ingredients)

    role = str(session.get("role") or "")
    username = str(session.get("username") or "")
    stash_panel = recipe_library_discovery_service.build_recipe_version_stash_panel(
        version_id=viewing_version_id,
        user_id=uid,
        role=role,
        username=username,
        target_batch_ml=batch_ml_param,
    )
    collab = recipes_repo.fetch_recipe_collab_snapshot(int(recipe["id"]))

    user_id_session = session.get("user_id")
    can_edit = bool(
        user_id_session
        and (
            int(user_id_session) == int(recipe.get("created_by") or 0)
            or can_access_tvw_admin(role)
        )
    )
    is_owner = bool(uid is not None and int(recipe.get("created_by") or 0) == int(uid))
    can_cost_recipe = bool(
        current_app.config.get("TVW_ENABLE_LAB_MODULES")
        and str(session.get("username") or "").strip().lower() == "old goat"
    )
    cost_calculator_url = None
    if can_cost_recipe:
        cost_calculator_url = url_for(
            "recipes.cost_calculator",
            recipe_id=int(recipe["id"]),
            recipe_version_id=viewing_version_id,
        )
    moderation_snapshot = {
        "status": str(recipe.get("moderation_status") or "active"),
        "reason": str(recipe.get("moderation_reason") or ""),
        "hidden_from_public": bool(int(recipe.get("is_hidden") or 0) == 1),
        "inactive": bool(int(recipe.get("active") or 1) == 0),
    }

    recipe_engagement = recipes_repo.fetch_recipe_engagement_for_viewer(
        recipe_id=int(recipe["id"]), viewer_user_id=uid
    )
    mixer_notes_preview, mixer_notes_total = guided_mix_session_service.list_recipe_mixer_notes(
        recipe_id=int(recipe["id"]),
        limit=3,
    )
    tags = recipes_repo.fetch_recipe_tags(int(recipe["id"]))
    detail = recipe_library_presentation.build_recipe_detail_presentation(
        recipe=recipe,
        tags=tags,
        active_version=current_version,
        ingredients=ingredients,
        total_flavour_pct=round(total_flavour_pct, 2),
        stash_panel=stash_panel,
        mixer_notes_preview=mixer_notes_preview,
    )

    return render_template(
        "tvw_recipe_detail.html",
        recipe=recipe,
        versions=versions,
        published_version=published_version,
        published_version_id=published_version_id,
        active_version=current_version,
        viewing_is_published_current=viewing_is_published_current,
        ingredients=ingredients,
        tags=tags,
        detail=detail,
        total_flavour_pct=round(total_flavour_pct, 2),
        stash_panel=stash_panel,
        collab=collab,
        can_edit=can_edit,
        can_cost_recipe=can_cost_recipe,
        cost_calculator_url=cost_calculator_url,
        recipe_engagement=recipe_engagement,
        is_owner=is_owner,
        moderation_snapshot=moderation_snapshot,
        mixer_notes_preview=detail["mixer_notes_preview"],
        mixer_notes_total=mixer_notes_total,
        viewer_user_id=uid,
        mix_form=effective_mix_form or {},
        mix_errors=mix_errors or {},
        guided_mix_start_url=url_for("tvw_recipes.tvw_guided_mix_start_post", slug=(recipe.get("slug") or recipe["id"])),
    )


@tvw_recipes_bp.route("/recipes/<slug>")
@login_required
def tvw_recipe_detail(slug: str):
    return _render_recipe_detail(slug)


@tvw_recipes_bp.route("/recipes/<slug>/mix-logs")
@login_required
def tvw_recipe_mix_logs(slug: str):
    recipe = recipes_repo.fetch_recipe_by_slug_or_id(slug)
    if not recipe:
        abort(404)
    uid = int(session["user_id"]) if session.get("user_id") is not None else None
    if not recipes_repo.can_view_recipe_in_tvw_community(
        recipe,
        viewer_user_id=uid,
        viewer_is_old_goat=is_old_goat_role(session.get("role")),
    ):
        flash("This recipe is unavailable.", "warning")
        return redirect(url_for("tvw_recipes.tvw_recipe_library"))

    page_raw = (request.args.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() else 1
    page = max(1, page)
    page_size = 20
    entries, total = guided_mix_session_service.list_recipe_batch_history(
        recipe_id=int(recipe["id"]),
        page=page,
        page_size=page_size,
    )
    total_pages = (total + page_size - 1) // page_size if total else 1
    if page > total_pages and total > 0:
        return redirect(
            url_for(
                "tvw_recipes.tvw_recipe_mix_logs",
                slug=recipe.get("slug") or recipe["id"],
                page=total_pages,
            )
        )

    return render_template(
        "tvw_recipe_mix_logs.html",
        recipe=recipe,
        entries=entries,
        page=page,
        total=total,
        total_pages=total_pages,
        viewer_user_id=uid,
    )


@tvw_recipes_bp.route("/recipes/<slug>/edit")
@login_required
def tvw_recipe_edit(slug: str):
    recipe = recipes_repo.fetch_recipe_by_slug_or_id(slug)
    if not recipe:
        abort(404)
    if not recipes_repo.can_view_recipe_in_tvw_community(
        recipe,
        viewer_user_id=int(session.get("user_id") or 0) if session.get("user_id") else None,
        viewer_is_old_goat=is_old_goat_role(session.get("role")),
    ):
        abort(404)
    user_id = session.get("user_id")
    role = str(session.get("role") or "")
    can_edit = user_id and (
        int(user_id) == int(recipe.get("created_by") or 0) or can_access_tvw_admin(role)
    )
    if not can_edit:
        flash("You can only edit your own recipes.", "warning")
        return redirect(url_for("tvw_recipes.tvw_recipe_detail", slug=recipe.get("slug") or recipe["id"]))

    versions = recipes_repo.fetch_recipe_versions(int(recipe["id"]))
    active_version = None
    if recipe.get("current_version_id"):
        active_version = next((v for v in versions if int(v["id"]) == int(recipe["current_version_id"])), None)
    if active_version is None and versions:
        active_version = versions[0]
    items = recipes_repo.fetch_version_items_for_editor(int(active_version["id"])) if active_version else []
    flavour_options = _editor_flavour_seed(items)
    return _render_recipe_builder(
        mode="edit",
        recipe=recipe,
        active_version=active_version,
        items=items,
        tags=recipes_repo.fetch_recipe_tags(int(recipe["id"])),
        flavour_options=flavour_options,
    )


@tvw_recipes_bp.route("/recipes/save", methods=["POST"])
@login_required
def tvw_recipe_save():
    logger = logging.getLogger("tvw.recipe_save")
    req_id = uuid.uuid4().hex[:10]
    user_id = int(session.get("user_id") or 0)
    if users_repo.user_is_muted(user_id):
        flash("Your account is muted. You cannot create or edit recipes.", "warning")
        return redirect(request.referrer or url_for("tvw_recipes.tvw_recipe_library"))

    recipe_id_raw = (request.form.get("recipe_id") or "").strip()
    is_edit = recipe_id_raw.isdigit()
    existing = None
    if is_edit:
        existing = recipes_repo.fetch_recipe(int(recipe_id_raw))
        if not existing:
            abort(404)
        can_edit = int(existing.get("created_by") or 0) == user_id or can_access_tvw_admin(
            session.get("role")
        )
        if not recipes_repo.can_view_recipe_in_tvw_community(
            dict(existing),
            viewer_user_id=user_id,
            viewer_is_old_goat=is_old_goat_role(session.get("role")),
        ):
            flash("Recipe not found.", "warning")
            return redirect(url_for("tvw_recipes.tvw_recipe_library"))
        if not can_edit:
            flash("You can only edit your own recipes.", "warning")
            return redirect(
                url_for("tvw_recipes.tvw_recipe_detail", slug=existing.get("slug") or existing["id"])
            )

    try:
        payload = recipe_builder_service.parse_recipe_builder_form(request.form, is_edit=is_edit)
    except RecipeBuilderFormError as exc:
        recipe = existing
        active_version = None
        if recipe and recipe.get("current_version_id"):
            versions = recipes_repo.fetch_recipe_versions(int(recipe["id"]))
            active_version = next(
                (v for v in versions if int(v["id"]) == int(recipe["current_version_id"])),
                versions[0] if versions else None,
            )
        flavour_options = _editor_flavour_seed(
            [{"ingredient_id": r.get("ingredient_id")} for r in exc.rows]
        )
        return _render_recipe_builder(
            mode="edit" if is_edit else "create",
            recipe=recipe,
            active_version=active_version,
            tags=[t.strip() for t in (exc.values.get("tags") or "").split(",") if t.strip()],
            flavour_options=flavour_options,
            form_values=exc.values,
            form_errors=exc.errors,
            flavour_rows=exc.rows,
        )

    name = payload.name
    status = payload.status
    visibility = payload.visibility
    notes = payload.notes
    tags = payload.tags
    ratio = payload.target_ratio
    items = payload.items
    total_pct = payload.total_pct
    recipe_id = payload.recipe_id
    stage = "begin"

    def _persist_recipe_save() -> dict:
        """Atomic create/update: recipe + community fields + version + items + tags."""
        nonlocal stage
        saved_id = recipe_id
        with recipes_repo.transaction():
            if saved_id is None:
                stage = "insert-recipe"
                saved_id = recipes_repo.insert_recipe(
                    name=name,
                    recipe_code="",
                    status=status.upper(),
                    created_by=user_id,
                )
                slug = f"{_slugify(name)}-{saved_id}"
            else:
                stage = "update-recipe"
                recipes_repo.update_recipe(
                    recipe_id=saved_id,
                    name=name,
                    recipe_code=str(existing.get("recipe_code") or ""),
                    status=status.upper(),
                )
                slug = existing.get("slug") or f"{_slugify(name)}-{saved_id}"

            stage = "update-community-fields"
            recipes_repo.update_recipe_community_fields(
                recipe_id=saved_id,
                visibility=visibility,
                status=status.upper(),
                slug=slug,
                library_type="personal" if recipe_id is None else None,
            )
            stage = "insert-version"
            last_version = recipes_repo.fetch_last_version_number(saved_id)
            new_version = _next_version(last_version)
            version_id = recipes_repo.insert_recipe_version(
                recipe_id=saved_id,
                version_number=new_version,
                target_vg_pg_ratio=ratio,
                total_flavor_percent=round(total_pct, 2),
                notes=notes,
            )
            stage = "insert-items"
            recipes_repo.insert_recipe_items(version_id=version_id, items=items)
            stage = "update-current-version"
            recipes_repo.set_current_version(recipe_id=saved_id, version_id=version_id)
            stage = "save-tags"
            recipes_repo.replace_recipe_tags(recipe_id=saved_id, tags=tags)
            stage = "commit"
        return {
            "recipe_id": saved_id,
            "slug": slug,
            "version": new_version,
            "created": recipe_id is None,
        }

    def _builder_error_response(message: str):
        values = recipe_builder_service.form_values_from_request(request.form)
        rows = recipe_builder_service.parse_flavour_rows(request.form)
        flavour_options = _editor_flavour_seed(
            [{"ingredient_id": r.get("ingredient_id")} for r in rows]
        )
        return _render_recipe_builder(
            mode="edit" if is_edit else "create",
            recipe=existing,
            tags=[t.strip() for t in (values.get("tags") or "").split(",") if t.strip()],
            flavour_options=flavour_options,
            form_values=values,
            form_errors={"_summary": message, "mix_summary": message},
            flavour_rows=rows,
        )

    try:
        saved = sqlite_retry(_persist_recipe_save, attempts=15, base_delay_s=0.05)
        if recipe_id is None:
            flash("Recipe created successfully.", "success")
        else:
            flash(f"Saved as version {saved['version']}.", "success")
        return redirect(url_for("tvw_recipes.tvw_recipe_detail", slug=saved["slug"]))
    except sqlite3.OperationalError as exc:
        logger.exception(
            "[recipe-save] request=%s stage=%s pid=%s error=%s",
            req_id,
            stage,
            os.getpid(),
            exc,
        )
        if is_sqlite_busy_error(exc):
            return _builder_error_response(SQLITE_BUSY_USER_MESSAGE)
        return _builder_error_response(
            "Could not save recipe. Please try again. "
            f"(Reference {req_id})"
        )
    except Exception as exc:
        logger.exception(
            "[recipe-save] request=%s stage=%s error=%s",
            req_id,
            stage,
            exc,
        )
        return _builder_error_response(
            "Could not save recipe. Please try again. "
            f"(Reference {req_id})"
        )


@tvw_recipes_bp.route("/flavours")
@login_required
def tvw_flavours():
    search = (request.args.get("search") or "").strip()
    manufacturer = (request.args.get("manufacturer") or "").strip()
    sort_by = (request.args.get("sort_by") or "name").strip().lower()
    sort_dir = (request.args.get("sort_dir") or "asc").strip().lower()
    page_raw = (request.args.get("page") or "1").strip()
    page = int(page_raw) if page_raw.isdigit() else 1
    ps_raw = (request.args.get("page_size") or "25").strip()
    page_size = int(ps_raw) if ps_raw.isdigit() else 25
    page_size = max(10, min(100, page_size))
    flavours, total = recipes_repo.fetch_tvw_flavours(
        search=search,
        manufacturer=manufacturer,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        page_size=page_size,
    )
    manufacturers = recipes_repo.fetch_tvw_flavour_manufacturers(limit=1000)
    total_pages = (total + page_size - 1) // page_size if total else 1
    shown_count = len(flavours)
    row_start = (page - 1) * page_size + 1 if total and shown_count else 0
    row_end = (page - 1) * page_size + shown_count
    return render_template(
        "tvw_flavour_index.html",
        flavours=flavours,
        total=total,
        shown_count=shown_count,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        row_start=row_start,
        row_end=row_end,
        search=search,
        manufacturer=manufacturer,
        manufacturers=manufacturers,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


@tvw_recipes_bp.route("/flavours/<int:flavour_id>")
@login_required
def tvw_flavour_detail(flavour_id: int):
    canonical_id = recipes_repo.resolve_tvw_flavour_id(flavour_id)
    if canonical_id != flavour_id:
        return redirect(url_for("tvw_recipes.tvw_flavour_detail", flavour_id=canonical_id), code=302)
    flavour = recipes_repo.fetch_tvw_flavour(flavour_id)
    if not flavour:
        abort(404)
    recipes_repo.attach_tvw_flavour_usage_display(flavour)
    ref_ids = recipes_repo.tvw_flavour_ingredient_ref_ids(flavour)
    community_counts = recipes_repo.fetch_tvw_flavour_community_counts(flavour_id)
    recipes_total = recipes_repo.count_tvw_public_current_recipes_using_refs(ref_ids)
    rp_raw = (request.args.get("recipes_page") or "1").strip()
    recipes_page = int(rp_raw) if rp_raw.isdigit() else 1
    recipes_page = max(1, recipes_page)
    recipes_page_size = 15
    recipes_total_pages = (
        (recipes_total + recipes_page_size - 1) // recipes_page_size if recipes_total else 1
    )
    if recipes_total and recipes_page > recipes_total_pages:
        return redirect(
            url_for(
                "tvw_recipes.tvw_flavour_detail",
                flavour_id=flavour_id,
                recipes_page=recipes_total_pages,
            ),
            code=302,
        )
    recipes_offset = (recipes_page - 1) * recipes_page_size
    recipes_using = recipes_repo.fetch_tvw_public_current_recipes_using_refs(
        ref_ids, offset=recipes_offset, limit=recipes_page_size
    )
    uid = int(session.get("user_id")) if session.get("user_id") is not None else None
    notes = recipes_repo.fetch_tvw_flavour_notes(flavour_id, viewer_user_id=uid)
    reviews = recipes_repo.fetch_tvw_flavour_reviews(flavour_id, viewer_user_id=uid)
    stash_binding = stash_service.fetch_stash_binding_for_flavour(
        user_id=int(session.get("user_id")) if session.get("user_id") is not None else None,
        role=str(session.get("role") or ""),
        username=str(session.get("username") or ""),
        flavour_id=flavour_id,
    )
    return render_template(
        "tvw_flavour_detail.html",
        flavour=flavour,
        notes=notes,
        reviews=reviews,
        recipes_using=recipes_using,
        recipes_total=recipes_total,
        recipes_page=recipes_page,
        recipes_page_size=recipes_page_size,
        recipes_total_pages=recipes_total_pages,
        community_counts=community_counts,
        stash_binding=stash_binding,
    )


@tvw_recipes_bp.route("/stash")
@login_required
def tvw_stash():
    search = (request.args.get("search") or "").strip()
    sort_by = (request.args.get("sort_by") or "name").strip().lower()
    sort_dir = (request.args.get("sort_dir") or "asc").strip().lower()
    rows, stash_source, is_lab_linked = stash_service.build_stash_page_rows(
        user_id=int(session.get("user_id")) if session.get("user_id") is not None else None,
        role=str(session.get("role") or ""),
        username=str(session.get("username") or ""),
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    quick_add_options = stash_service.fetch_stash_quick_add_options(limit=1000)
    return render_template(
        "tvw_stash.html",
        stash_rows=rows,
        stash_source=stash_source,
        is_lab_linked=is_lab_linked,
        quick_add_options=quick_add_options,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )

