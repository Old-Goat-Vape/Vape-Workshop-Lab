from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from db import SQLITE_BUSY_USER_MESSAGE, is_sqlite_busy_error, sqlite_retry
from models import recipes_repo
from utils.auth import ROLE_HIERARCHY


@dataclass(frozen=True)
class UserContext:
    user_id: Optional[int]
    username_lower: str
    role: str
    level: int
    is_old_goat: bool
    show_cost: bool


def user_context_from_session(session: Dict[str, Any]) -> UserContext:
    username = (session.get("username") or "").strip()
    username_lower = username.lower()
    role = session.get("role", "Kid")
    is_old_goat = username_lower == "old goat"
    # Old Goat is absolute top authority.
    level = ROLE_HIERARCHY.get("Old Goat", 8) if is_old_goat else ROLE_HIERARCHY.get(role, 0)
    return UserContext(
        user_id=session.get("user_id"),
        username_lower=username_lower,
        role=role,
        level=level,
        is_old_goat=is_old_goat,
        show_cost=is_old_goat,
    )


def _normalize_library_scope(
    *,
    raw: str,
    is_old_goat: bool,
) -> Tuple[str, str]:
    """Map query param to internal scope + canonical param for URLs/forms.

    Returns (internal_scope, library_type_query_value).
    Internal: all | mine | reference | shared
    Query value: all | personal | reference | shared (personal = mine)
    """
    v = (raw or "all").strip().lower()
    if v not in {"all", "personal", "reference", "shared"}:
        v = "all"

    if v == "personal":
        return "mine", "personal"

    if v == "reference":
        if is_old_goat:
            return "reference", "reference"
        return "all", "all"

    if v == "shared":
        if is_old_goat:
            return "shared", "shared"
        return "all", "all"

    return "all", "all"


def reference_recipe_restricted_for_user(
    *,
    recipe: Optional[Dict[str, Any]],
    is_old_goat: bool,
    viewer_user_id: Optional[int] = None,
) -> bool:
    """True if this recipe is reference-only for others (non–Old Goat cannot browse/mix it).

    Always allows the creator to open their own row even if mis-tagged as reference.
    """
    if recipe is None or is_old_goat:
        return False
    if (recipe.get("library_type") or "").strip().lower() != "reference":
        return False
    try:
        cid = int(recipe.get("created_by")) if recipe.get("created_by") is not None else None
    except (TypeError, ValueError):
        cid = None
    if viewer_user_id is not None and cid is not None and cid == int(viewer_user_id):
        return False
    return True


def library_view(
    *,
    session: Dict[str, Any],
    status_filter: str,
    search: str,
    library_type: str = "all",
    page: int = 1,
    page_size: int = 24,
) -> Dict[str, Any]:
    """Build the recipe library view model with visibility + enrichment (paged, efficient)."""
    user = user_context_from_session(session)

    include_dev_archived = user.is_old_goat
    include_advanced = user.is_old_goat or user.level >= 4

    # One-time automatic backfill for library_type; safe + idempotent.
    # Keep it lightweight: only runs when NULL rows exist.
    recipes_repo.backfill_recipe_library_types_if_needed(active_only=True)

    allow_nicotine = user.is_old_goat or user.level >= 3

    try:
        uid_int = int(session.get("user_id")) if session.get("user_id") is not None else None
    except (TypeError, ValueError):
        uid_int = None

    library_scope, library_type_selected = _normalize_library_scope(
        raw=library_type,
        is_old_goat=user.is_old_goat,
    )

    # Non–Old Goat: never browse reference recipes (query string cannot override).
    exclude_reference = not user.is_old_goat

    recipes, total_count = recipes_repo.fetch_recipes_library_page(
        include_dev_archived=include_dev_archived,
        include_advanced=include_advanced,
        status_filter=status_filter,
        search=search,
        library_scope=library_scope,
        exclude_reference=exclude_reference,
        current_user_id=uid_int,
        page=page,
        page_size=page_size,
        allow_nicotine=allow_nicotine,
        show_cost=user.show_cost,
    )

    # Post-processing kept intentionally small.
    enriched: List[Dict[str, Any]] = []
    for r in recipes:
        created_by = r.get("created_by")
        is_mine = uid_int is not None and created_by == uid_int
        lib_t = r.get("library_type") or "reference"
        is_ref = lib_t == "reference"
        enriched.append(
            {
                "id": r["id"],
                "name": r["name"],
                "recipe_code": r.get("recipe_code"),
                "status": r.get("status"),
                "image_path": r.get("image_path"),
                "has_nicotine": 1 if int(r.get("has_nicotine") or 0) == 1 else 0,
                "latest_version_number": r.get("latest_version_number") or "1.0",
                "latest_version_id": r.get("latest_version_id"),
                "cost_per_ml": r.get("cost_per_ml") if user.show_cost else None,
                "library_type": lib_t,
                "created_by": created_by,
                "is_mine": is_mine,
                "badge_shared": (not is_mine) and (not is_ref),
                "can_mix": (user.level >= 4) or (user.level >= 1 and r.get("status") == "Production"),
            }
        )

    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 24))
    total_pages = (total_count + page_size - 1) // page_size if total_count else 1

    return {
        "recipes": enriched,
        "show_cost": user.show_cost,
        "user_level": user.level,
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "library_type_selected": library_type_selected,
        "show_reference_library_filter": user.is_old_goat,
        "show_shared_library_filter": user.is_old_goat,
    }


def pricing_sheet_view(
    *,
    session: Dict[str, Any],
    status_filter: str = "",
    search: str = "",
) -> Tuple[Optional[Dict[str, Any]], Optional[str], str]:
    """Lab costing worksheet — concentrate cost/ml from catalog; overhead + margins in browser."""
    user = user_context_from_session(session)
    if not user.show_cost:
        return None, "Recipe costing is restricted to the Old Goat account.", "danger"

    try:
        uid_int = int(session.get("user_id")) if session.get("user_id") is not None else None
    except (TypeError, ValueError):
        uid_int = None

    recipes, total_count = recipes_repo.fetch_recipes_library_page(
        include_dev_archived=True,
        include_advanced=True,
        status_filter=(status_filter or "").strip(),
        search=(search or "").strip(),
        library_scope="all",
        exclude_reference=False,
        current_user_id=uid_int,
        page=1,
        page_size=500,
        allow_nicotine=True,
        show_cost=True,
    )

    rows: List[Dict[str, Any]] = []
    for r in recipes:
        rows.append(
            {
                "id": r["id"],
                "name": r["name"],
                "recipe_code": r.get("recipe_code") or "",
                "status": r.get("status") or "",
                "has_nicotine": bool(int(r.get("has_nicotine") or 0)),
                "cost_per_ml": float(r.get("cost_per_ml") or 0.0),
            }
        )

    return (
        {
            "recipes": rows,
            "total_count": total_count,
            "status_filter": (status_filter or "").strip(),
            "search": (search or "").strip(),
        },
        None,
        "",
    )


def cost_calculator_view(
    *,
    session: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[str], str]:
    """Focused single-recipe batch profit calculator — Old Goat only."""
    user = user_context_from_session(session)
    if not user.show_cost:
        return None, "Recipe costing is restricted to the Old Goat account.", "danger"

    try:
        uid_int = int(session.get("user_id")) if session.get("user_id") is not None else None
    except (TypeError, ValueError):
        uid_int = None

    from services import recipe_cost_calculator_service

    return (
        {
            "recipes": recipe_cost_calculator_service.recipe_picker_rows(session_user_id=uid_int),
        },
        None,
        "",
    )


def require_old_goat(user: UserContext) -> Tuple[bool, str]:
    if not user.is_old_goat:
        return False, "Only the Old Goat can create or edit formulas."
    return True, ""


def generate_recipe_code(name: str, provided_code: str) -> str:
    code = (provided_code or "").upper().strip()
    if not code or code == "AUTO":
        code = "".join([p[0] for p in name.split() if p]).upper()[:4]
    return code


def next_version_number(last_version: Optional[str]) -> str:
    if not last_version:
        return "1.0"
    return "{:.1f}".format(float(last_version) + 0.1)


def save_recipe_ajax(
    *,
    session: Dict[str, Any],
    payload: Dict[str, Any],
    image_path: Optional[str] = None,
) -> Tuple[Dict[str, Any], int]:
    user = user_context_from_session(session)
    ok, msg = require_old_goat(user)
    if not ok:
        return {"status": "error", "message": f"Unauthorized: {msg}"}, 403

    if not payload:
        return {"status": "error", "message": "No data"}, 400

    name = (payload.get("name") or "Untitled Recipe").strip()
    recipe_id = payload.get("recipe_id")
    new_status = payload.get("status", "Development")
    code = generate_recipe_code(name, payload.get("code", ""))

    ratio = payload.get("ratio", "70/30")
    total_pct = float(payload.get("total_pct", 0) or 0)

    # Normalize items to (ingredient_id, percent)
    items: List[Tuple[int, float]] = []
    for item in payload.get("ingredients", []):
        ing_id = item.get("ingredient_id") or item.get("id")
        if not ing_id:
            continue
        items.append((int(ing_id), float(item.get("percent", 0) or 0)))

    try:
        def _persist() -> Tuple[int, str]:
            with recipes_repo.transaction():
                if recipe_id:
                    recipe_id_int = int(recipe_id)
                    old_status = recipes_repo.fetch_recipe_status(recipe_id_int)
                    if old_status and old_status != new_status:
                        recipes_repo.insert_audit_log(
                            actor_id=int(user.user_id or 0),
                            action="RECIPE_STATE_CHANGE",
                            target_id=recipe_id_int,
                            details=f"{old_status} -> {new_status}",
                            timestamp=datetime.now(),
                        )
                    recipes_repo.update_recipe(
                        recipe_id=recipe_id_int,
                        name=name,
                        recipe_code=code,
                        status=new_status,
                    )
                else:
                    recipe_id_int = recipes_repo.insert_recipe(
                        name=name,
                        recipe_code=code,
                        status=new_status,
                        created_by=int(user.user_id or 0),
                    )

                if image_path is not None:
                    recipes_repo.update_recipe_image(recipe_id=recipe_id_int, image_path=image_path)

                last_v = recipes_repo.fetch_last_version_number(recipe_id_int)
                new_v = next_version_number(last_v)

                version_id = recipes_repo.insert_recipe_version(
                    recipe_id=recipe_id_int,
                    version_number=new_v,
                    target_vg_pg_ratio=ratio,
                    total_flavor_percent=total_pct,
                )

                recipes_repo.insert_recipe_items(version_id=version_id, items=items)
                recipes_repo.set_current_version(recipe_id=recipe_id_int, version_id=version_id)
                return recipe_id_int, new_v

        recipe_id_int, new_v = sqlite_retry(_persist, attempts=15, base_delay_s=0.05)
        return {"status": "success", "recipe_id": recipe_id_int, "version": new_v}, 200
    except sqlite3.OperationalError as exc:
        if is_sqlite_busy_error(exc):
            return {"status": "error", "message": SQLITE_BUSY_USER_MESSAGE}, 503
        return {"status": "error", "message": "Could not save recipe. Please try again."}, 500
    except Exception:
        return {"status": "error", "message": "Could not save recipe. Please try again."}, 500


def editor_existing_data(*, session: Dict[str, Any], recipe_id: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    user = user_context_from_session(session)
    ok, msg = require_old_goat(user)
    if not ok:
        return None, msg

    recipe = recipes_repo.fetch_recipe(recipe_id)
    version = recipes_repo.fetch_current_version(recipe_id) or recipes_repo.fetch_latest_version(recipe_id)
    if not recipe or not version:
        return None, "Record not found."

    items = recipes_repo.fetch_version_items_for_editor(int(version["id"]))
    existing_data = {
        "recipe_id": recipe["id"],
        "name": recipe["name"],
        "code": recipe.get("recipe_code"),
        "status": recipe.get("status"),
        "image_path": recipe.get("image_path"),
        "ratio": version.get("target_vg_pg_ratio"),
        "ingredients": items,
    }
    return existing_data, None


def can_view_recipe(*, user: UserContext, recipe_status: str) -> Tuple[bool, Optional[str]]:
    if user.is_old_goat:
        return True, None
    if recipe_status in ["Development", "Archived"]:
        return False, "This recipe is currently restricted."
    if recipe_status == "Advanced" and user.level < 4:
        return False, "Advanced recipes require Ram level or higher."
    return True, None


def recipe_view(*, session: Dict[str, Any], recipe_id: int) -> Tuple[Optional[Dict[str, Any]], Optional[str], str]:
    user = user_context_from_session(session)

    recipe = recipes_repo.fetch_recipe(recipe_id)
    version = recipes_repo.fetch_current_version(recipe_id) or recipes_repo.fetch_latest_version(recipe_id)
    if not recipe or not version:
        return None, "Recipe not found.", "danger"

    try:
        vid = int(user.user_id) if user.user_id is not None else None
    except (TypeError, ValueError):
        vid = None
    if reference_recipe_restricted_for_user(
        recipe=dict(recipe),
        is_old_goat=user.is_old_goat,
        viewer_user_id=vid,
    ):
        return None, "Reference formulas are restricted to the Old Goat account.", "warning"

    ok, message = can_view_recipe(user=user, recipe_status=str(recipe.get("status")))
    if not ok:
        return None, message or "Restricted.", "warning"

    ingredients = recipes_repo.fetch_version_ingredients_for_view(
        int(version["id"]),
        recipe_id=int(recipe["id"]),
        recipe_code=str(recipe.get("recipe_code") or ""),
    )
    has_nicotine = any(bool(ing.get("is_nicotine")) for ing in ingredients)

    if has_nicotine and user.level < 3 and not user.is_old_goat:
        return None, "Nicotine recipes are restricted.", "danger"

    return {
        "recipe": recipe,
        "version": version,
        "ingredients": ingredients,
        "has_nicotine": has_nicotine,
        "show_cost": user.show_cost,
        "user_level": user.level,
    }, None, "success"

