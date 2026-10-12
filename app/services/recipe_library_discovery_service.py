"""Recipe library discovery: stash summaries and optional stash-first sort (community-safe, no Lab UI)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from models import recipes_repo
from services.what_can_i_make_service import (
    DEFAULT_TARGET_BATCH_ML,
    MAX_TARGET_BATCH_ML,
    MIN_TARGET_BATCH_ML,
    fetch_stash_reference_qty_ml,
    fetch_version_ingredients_map,
    map_reference_ids_to_flavour_pages,
    summarize_recipe_stash_fit,
)

# Max rows loaded for stash-priority sort (recent-first pool, then re-ordered in memory).
STASH_RANK_SCAN_CAP = 1200


def _sort_ts(rec: dict[str, Any]) -> float:
    u = rec.get("updated_at")
    if not u:
        return 0.0
    if isinstance(u, datetime):
        return float(u.timestamp())
    try:
        return float(datetime.fromisoformat(str(u).replace("Z", "")).timestamp())
    except Exception:
        return 0.0


def enrich_recipes_with_stash_summary(
    recipes: list[dict[str, Any]],
    *,
    user_id: int | None,
    role: str | None,
    username: str | None,
    target_batch_ml: float | None = None,
) -> None:
    """Attach `stash_summary` + `library_batch_ml` in place (batched ingredient + stash queries)."""
    if not recipes:
        return
    raw = float(target_batch_ml) if target_batch_ml is not None else DEFAULT_TARGET_BATCH_ML
    target_ml = max(MIN_TARGET_BATCH_ML, min(MAX_TARGET_BATCH_ML, raw))

    stash_qty, _src = fetch_stash_reference_qty_ml(
        user_id=user_id, role=role, username=username
    )
    vids = [int(r["current_version_id"]) for r in recipes if r.get("current_version_id")]
    ving = fetch_version_ingredients_map(vids)
    all_refs: set[int] = set()
    for vid in vids:
        for row in ving.get(int(vid), []):
            all_refs.add(int(row["ingredient_id"]))
    ref_map = map_reference_ids_to_flavour_pages(all_refs)

    for r in recipes:
        cv = r.get("current_version_id")
        ing = ving.get(int(cv), []) if cv else []
        r["library_batch_ml"] = target_ml
        r["stash_summary"] = summarize_recipe_stash_fit(
            ingredients=ing,
            stash_qty=stash_qty,
            target_batch_ml=target_ml,
            ref_to_flavour=ref_map,
        )


def build_recipe_version_stash_panel(
    *,
    version_id: int,
    user_id: int | None,
    role: str | None,
    username: str | None,
    target_batch_ml: float | None = None,
) -> dict[str, Any]:
    """Single-version stash hint for recipe detail (same rules as WCM / library discovery)."""
    raw = float(target_batch_ml) if target_batch_ml is not None else DEFAULT_TARGET_BATCH_ML
    target_ml = max(MIN_TARGET_BATCH_ML, min(MAX_TARGET_BATCH_ML, raw))
    stash_qty, stash_source = fetch_stash_reference_qty_ml(
        user_id=user_id, role=role, username=username
    )
    vid = int(version_id)
    ving = fetch_version_ingredients_map([vid])
    ing = ving.get(vid, [])
    refs = {int(r["ingredient_id"]) for r in ing}
    ref_map = map_reference_ids_to_flavour_pages(refs)
    summary = summarize_recipe_stash_fit(
        ingredients=ing,
        stash_qty=stash_qty,
        target_batch_ml=target_ml,
        ref_to_flavour=ref_map,
    )
    return {
        "stash_summary": summary,
        "batch_ml": target_ml,
        "stash_source": stash_source,
        "ref_to_flavour": ref_map,
    }


def _sort_by_stash_rank(recipes: list[dict[str, Any]]) -> None:
    recipes.sort(
        key=lambda r: (
            int((r.get("stash_summary") or {}).get("sort_rank") or 99),
            -_sort_ts(r),
            -int(r.get("id") or 0),
        )
    )


def fetch_tvw_library_stash_sorted_page(
    *,
    search: str = "",
    status: str = "",
    visibility: str = "",
    tag: str = "",
    page: int = 1,
    page_size: int = 20,
    user_id: int | None,
    viewer_is_old_goat: bool,
    role: str | None,
    username: str | None,
    target_batch_ml: float | None = None,
    favorites_user_id: int | None = None,
) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    """Stash-priority sort over the most recent `STASH_RANK_SCAN_CAP` matches (V1-safe)."""
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 20))
    where, params = recipes_repo.build_tvw_library_where(
        search=search,
        status=status,
        visibility=visibility,
        tag=tag,
        favorites_user_id=favorites_user_id,
        viewer_user_id=user_id,
        viewer_is_old_goat=viewer_is_old_goat,
    )
    total_matches = recipes_repo.fetch_tvw_library_count(where=where, params=params)
    pool_n = min(STASH_RANK_SCAN_CAP, total_matches) if total_matches else 0
    if pool_n <= 0:
        return [], total_matches, {
            "stash_rank_pool": 0,
            "stash_rank_cap": STASH_RANK_SCAN_CAP,
            "total_match_count": total_matches,
        }

    candidates = recipes_repo.fetch_tvw_library_rows_for_stash_ranking(
        where=where,
        params=params,
        limit=pool_n,
        viewer_user_id=user_id,
    )
    enrich_recipes_with_stash_summary(
        candidates,
        user_id=user_id,
        role=role,
        username=username,
        target_batch_ml=target_batch_ml,
    )
    _sort_by_stash_rank(candidates)

    offset = (page - 1) * page_size
    page_rows = candidates[offset : offset + page_size]
    meta = {
        "stash_rank_pool": len(candidates),
        "stash_rank_cap": STASH_RANK_SCAN_CAP,
        "total_match_count": total_matches,
    }
    return page_rows, total_matches, meta


def stash_sorted_total_pages(*, ranked_pool_size: int, page_size: int) -> int:
    if ranked_pool_size <= 0:
        return 1
    return (ranked_pool_size + page_size - 1) // page_size
