"""Dashboard community discovery — featured recipe + tabbed collection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from flask import url_for

from models import recipes_repo

DISCOVERY_TAB_SIZE = 3
DISCOVERY_FETCH_BUFFER = 6


def _parse_dt(raw: Any) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            chunk = text[:26] if "." in fmt else text[:19] if " " in fmt else text[:10]
            parsed = datetime.strptime(chunk, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _relative_phrase(dt: datetime | None, *, prefix: str) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = _now_utc() - dt
    days = delta.days
    if days < 0:
        return None
    if days == 0:
        return f"{prefix} today"
    if days == 1:
        return f"{prefix} yesterday"
    if days < 7:
        return f"{prefix} {days} days ago"
    if days < 14:
        return f"{prefix} this week"
    return None


def recipe_detail_slug(recipe: dict) -> str:
    return str(recipe.get("slug") or recipe.get("id") or "")


def recipe_can_guided_mix(recipe: dict) -> bool:
    return bool(recipe.get("current_version_id"))


def _first_tag(recipe: dict) -> str | None:
    tags = str(recipe.get("tags_csv") or "").strip()
    if not tags:
        return None
    first = tags.split(",")[0].strip()
    return first or None


def _has_real_rating(recipe: dict) -> bool:
    count = int(recipe.get("recipe_rating_count") or 0)
    avg = recipe.get("avg_recipe_rating")
    return count > 0 and avg is not None


def _rating_label(recipe: dict) -> str | None:
    if not _has_real_rating(recipe):
        return None
    count = int(recipe.get("recipe_rating_count") or 0)
    avg = float(recipe.get("avg_recipe_rating"))
    return f"Rated {avg:.1f}" if count > 0 else None


def _favorites_label(recipe: dict) -> str | None:
    count = int(recipe.get("recipe_favorite_count") or 0)
    if count <= 0:
        return None
    if count == 1:
        return "Saved once"
    return f"Saved {count} times"


def contextual_label(recipe: dict, *, tab_id: str) -> str | None:
    if tab_id == "updated":
        return _relative_phrase(_parse_dt(recipe.get("updated_at")), prefix="Updated")
    if tab_id == "rated":
        return _rating_label(recipe)
    if tab_id == "popular":
        return _favorites_label(recipe)
    return None


def _visibility_ok(recipe: dict, *, viewer_is_old_goat: bool) -> bool:
    if viewer_is_old_goat:
        return True
    return recipes_repo.recipe_dict_visible_in_public_lists(recipe)


def _take_unique(
    rows: list[dict],
    *,
    limit: int,
    seen: set[int],
    viewer_is_old_goat: bool,
    require_rating: bool = False,
    require_favorites: bool = False,
) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        rid = int(row["id"])
        if rid in seen:
            continue
        if not _visibility_ok(row, viewer_is_old_goat=viewer_is_old_goat):
            continue
        if require_rating and not _has_real_rating(row):
            continue
        if require_favorites and int(row.get("recipe_favorite_count") or 0) <= 0:
            continue
        seen.add(rid)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _library(
    *,
    sort_by: str,
    viewer_user_id: int | None,
    viewer_is_old_goat: bool,
    exclude_ids: list[int],
    min_rating_count: int = 0,
    min_favorite_count: int = 0,
) -> list[dict]:
    rows, _total = recipes_repo.fetch_tvw_library(
        page=1,
        page_size=DISCOVERY_FETCH_BUFFER,
        sort_by=sort_by,
        sort_dir="desc",
        viewer_user_id=viewer_user_id,
        viewer_is_old_goat=viewer_is_old_goat,
        exclude_recipe_ids=exclude_ids or None,
        public_discovery_only=True,
        min_rating_count=min_rating_count,
        min_favorite_count=min_favorite_count,
    )
    return rows


def _enrich_featured(recipe: dict) -> dict:
    out = dict(recipe)
    version_id = recipe.get("current_version_id")
    if version_id:
        out["version_notes"] = recipes_repo.fetch_recipe_version_notes(version_id=int(version_id))
    else:
        out["version_notes"] = ""
    out["primary_tag"] = _first_tag(recipe)
    out["detail_href"] = url_for(
        "tvw_recipes.tvw_recipe_detail", slug=recipe_detail_slug(recipe)
    )
    out["can_mix"] = recipe_can_guided_mix(recipe)
    if out["can_mix"]:
        out["mix_href"] = url_for(
            "tvw_recipes.tvw_guided_mix_start", slug=recipe_detail_slug(recipe)
        )
    else:
        out["mix_href"] = None
    out["show_rating_meta"] = _has_real_rating(recipe)
    return out


def _enrich_tab_recipe(recipe: dict, *, tab_id: str) -> dict:
    out = dict(recipe)
    out["context_label"] = contextual_label(recipe, tab_id=tab_id)
    out["primary_tag"] = _first_tag(recipe)
    out["detail_href"] = url_for(
        "tvw_recipes.tvw_recipe_detail", slug=recipe_detail_slug(recipe)
    )
    out["can_mix"] = recipe_can_guided_mix(recipe)
    out["mix_href"] = (
        url_for("tvw_recipes.tvw_guided_mix_start", slug=recipe_detail_slug(recipe))
        if out["can_mix"]
        else None
    )
    out["show_rating_meta"] = _has_real_rating(recipe) and tab_id != "rated"
    return out


def build_community_discovery_context(
    *,
    viewer_user_id: int | None,
    viewer_is_old_goat: bool,
) -> dict[str, Any]:
    """Build featured recipe + tabbed discovery lists with practical deduplication.

    Category variables (always assigned before tabs are built):
      created_rows  — newest by created_at (featured / recently added)
      rated_rows    — genuine user ratings only (min_rating_count=1)
      updated_rows  — recipe.updated_at order
      popular_rows  — save/favorite count >= 1
    """
    # --- 1. Newest eligible public recipe (Recently added feature) ---
    created_rows = _library(
        sort_by="created",
        viewer_user_id=viewer_user_id,
        viewer_is_old_goat=viewer_is_old_goat,
        exclude_ids=[],
    )
    featured_raw = None
    for row in created_rows:
        if row.get("created_at") and _visibility_ok(row, viewer_is_old_goat=viewer_is_old_goat):
            featured_raw = row
            break
    featured = _enrich_featured(featured_raw) if featured_raw else None
    featured_id = int(featured["id"]) if featured else None
    exclude: list[int] = [featured_id] if featured_id is not None else []
    seen: set[int] = set(exclude)

    # --- 2. Genuinely rated recipes (Top rated) ---
    rated_rows = _take_unique(
        _library(
            sort_by="rating",
            viewer_user_id=viewer_user_id,
            viewer_is_old_goat=viewer_is_old_goat,
            exclude_ids=exclude,
            min_rating_count=1,
        ),
        limit=DISCOVERY_TAB_SIZE,
        seen=seen,
        viewer_is_old_goat=viewer_is_old_goat,
        require_rating=True,
    )
    exclude = list(seen)

    # --- 3. Recently updated recipes ---
    updated_rows = _take_unique(
        _library(
            sort_by="updated",
            viewer_user_id=viewer_user_id,
            viewer_is_old_goat=viewer_is_old_goat,
            exclude_ids=exclude,
        ),
        limit=DISCOVERY_TAB_SIZE,
        seen=seen,
        viewer_is_old_goat=viewer_is_old_goat,
    )
    exclude = list(seen)

    # --- 4. Popular recipes (real save/favorite count) ---
    popular_rows = _take_unique(
        _library(
            sort_by="favorites",
            viewer_user_id=viewer_user_id,
            viewer_is_old_goat=viewer_is_old_goat,
            exclude_ids=exclude,
            min_favorite_count=1,
        ),
        limit=DISCOVERY_TAB_SIZE,
        seen=seen,
        viewer_is_old_goat=viewer_is_old_goat,
        require_favorites=True,
    )

    # All four category lists are defined above on every path before building tabs.
    tabs = [
        {
            "id": "updated",
            "label": "Recently updated",
            "recipes": [
                _enrich_tab_recipe(row, tab_id="updated") for row in updated_rows
            ],
            "see_all_href": url_for(
                "tvw_recipes.tvw_recipe_library", sort="updated", dir="desc"
            ),
            "see_all_text": "See all updated",
        },
        {
            "id": "rated",
            "label": "Top rated",
            "recipes": [
                _enrich_tab_recipe(row, tab_id="rated") for row in rated_rows
            ],
            "see_all_href": url_for(
                "tvw_recipes.tvw_recipe_library", sort="rating", dir="desc"
            ),
            "see_all_text": "See all top rated",
        },
        {
            "id": "popular",
            "label": "Popular",
            "recipes": [
                _enrich_tab_recipe(row, tab_id="popular") for row in popular_rows
            ],
            "see_all_href": url_for(
                "tvw_recipes.tvw_recipe_library", sort="favorites", dir="desc"
            ),
            "see_all_text": "See all popular",
        },
    ]

    has_discovery = bool(
        featured or any(tab["recipes"] for tab in tabs)
    )

    featured_badge = "Recently added"
    if featured:
        created = _parse_dt(featured.get("created_at"))
        if created:
            age = _now_utc() - created.astimezone(timezone.utc)
            if age < timedelta(days=7):
                featured_badge = "New recipe"

    return {
        "discovery_featured": featured,
        "discovery_featured_badge": featured_badge,
        "discovery_tabs": tabs,
        "has_discovery": has_discovery,
    }
