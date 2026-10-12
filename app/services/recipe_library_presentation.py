"""Presentation helpers for the TVW recipe library page (no query/permission changes)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from models import recipes_repo


def _parse_dt(raw: Any) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def human_recipe_date(raw: Any, *, prefix: str = "Updated") -> str | None:
    dt = _parse_dt(raw)
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    delta = now - dt
    days = max(0, delta.days)
    if days == 0:
        return f"{prefix} today"
    if days == 1:
        return f"{prefix} yesterday"
    if days < 7:
        return f"{prefix} {days} days ago"
    weeks = days // 7
    if weeks == 1:
        return f"{prefix} 1 week ago"
    if weeks < 5:
        return f"{prefix} {weeks} weeks ago"
    months = max(1, days // 30)
    if months == 1:
        return f"{prefix} 1 month ago"
    if months < 12:
        return f"{prefix} {months} months ago"
    years = max(1, days // 365)
    if years == 1:
        return f"{prefix} 1 year ago"
    return f"{prefix} {years} years ago"


def first_recipe_tag(tags_csv: str | None) -> str | None:
    tags = str(tags_csv or "").strip()
    if not tags:
        return None
    first = tags.split(",")[0].strip()
    return first or None


def library_scope_counts(
    *,
    viewer_user_id: int | None,
    viewer_is_old_goat: bool,
) -> dict[str, int]:
    def _count(**kwargs: Any) -> int:
        where, params = recipes_repo.build_tvw_library_where(
            viewer_user_id=viewer_user_id,
            viewer_is_old_goat=viewer_is_old_goat,
            **kwargs,
        )
        return recipes_repo.fetch_tvw_library_count(where=where, params=params)

    uid = int(viewer_user_id) if viewer_user_id is not None else None
    return {
        "total": _count(),
        "mine": _count(owner_user_id=uid) if uid is not None else 0,
        "public": _count(visibility="public"),
    }


def detect_library_scope(
    *,
    favorites_only: bool,
    mine_only: bool,
    visibility: str,
) -> str:
    if favorites_only:
        return "favorites"
    if mine_only:
        return "mine"
    vis = str(visibility or "").strip().lower()
    if vis == "public":
        return "public"
    if vis == "private":
        return "private"
    return "all"


def sort_label(*, sort_by: str, sort_dir: str) -> str:
    sb = str(sort_by or "updated").lower()
    sd = str(sort_dir or "desc").lower()
    labels = {
        "updated": "Recently updated",
        "newest": "Newest posted",
        "rating": "Top rated",
        "favorites": "Most favorited",
        "name": "Name",
        "stash": "Stash match",
    }
    base = labels.get(sb, "Recently updated")
    if sb == "stash":
        return base
    if sb in {"updated", "newest", "rating", "favorites"}:
        return base if sd != "asc" else f"{base} (oldest first)"
    if sb == "name":
        return "Name A–Z" if sd != "desc" else "Name Z–A"
    return base


def enrich_recipes_for_library_cards(
    recipes: list[dict[str, Any]],
    *,
    viewer_user_id: int | None,
    viewer_username: str,
) -> None:
    viewer_name = str(viewer_username or "").strip().lower()
    for recipe in recipes:
        owner = str(recipe.get("owner_username") or "").strip()
        recipe["library_is_mine"] = bool(
            viewer_user_id is not None
            and owner
            and owner.lower() != "unknown"
            and owner.lower() == viewer_name
        )
        recipe["library_is_reference"] = str(recipe.get("status") or "").strip().lower() == "reference"
        recipe["library_category"] = first_recipe_tag(recipe.get("tags_csv"))
        recipe["library_updated_label"] = human_recipe_date(recipe.get("updated_at"), prefix="Updated")
        recipe["library_added_label"] = human_recipe_date(recipe.get("created_at"), prefix="Added")
        vis = str(recipe.get("visibility") or "private").strip().lower()
        if recipe["library_is_reference"]:
            recipe["library_visibility_pill"] = "Reference"
        elif vis == "public":
            recipe["library_visibility_pill"] = "Public"
        elif vis == "private":
            recipe["library_visibility_pill"] = "Private"
        elif vis == "unlisted":
            recipe["library_visibility_pill"] = "Unlisted"
        else:
            recipe["library_visibility_pill"] = vis.title() or "Private"
        owner_display = owner if owner and owner.lower() != "unknown" else None
        recipe["library_owner_label"] = owner_display


def parse_pg_vg_from_ratio(ratio_str: Any) -> tuple[int, int]:
    """Return (pg_percent, vg_percent) from stored VG/PG ratio like ``70/30``."""
    raw = str(ratio_str or "70/30").strip()
    parts = raw.replace(":", "/").split("/")
    if len(parts) != 2:
        return 30, 70
    try:
        vg = int(round(float(parts[0].strip())))
        pg = int(round(float(parts[1].strip())))
    except ValueError:
        return 30, 70
    return pg, vg


def parse_imported_source_from_notes(notes: Any) -> dict[str, str | None]:
    text = str(notes or "").strip()
    if "Imported VC DTL reference" not in text:
        return {"label": None, "url": None}
    url = None
    if "(" in text and ")" in text:
        url = text.rsplit("(", 1)[-1].rstrip(")").strip() or None
    return {"label": "VC DTL Reference", "url": url}


def _ingredient_stash_map(stash_panel: dict[str, Any]) -> dict[int, dict[str, Any]]:
    ss = stash_panel.get("stash_summary") or {}
    wcm = ss.get("wcm_qty") or {}
    lines = wcm.get("lines") or []
    out: dict[int, dict[str, Any]] = {}
    for ln in lines:
        rid = ln.get("ingredient_id")
        if rid is None:
            continue
        out[int(rid)] = ln
    return out


def _recipe_visibility_pill(recipe: dict[str, Any]) -> str:
    is_reference = str(recipe.get("status") or "").strip().lower() == "reference"
    vis = str(recipe.get("visibility") or "private").strip().lower()
    if is_reference:
        return "Reference"
    if vis == "public":
        return "Public"
    if vis == "private":
        return "Private"
    if vis == "unlisted":
        return "Unlisted"
    return vis.title() or "Private"


def _status_display_label(status: str | None) -> str:
    st = str(status or "WIP").strip().upper()
    labels = {
        "WIP": "Work in progress",
        "HELP": "Need help",
        "READY": "Ready",
        "REFERENCE": "Reference",
    }
    return labels.get(st, st.replace("_", " ").title())


def build_recipe_detail_presentation(
    *,
    recipe: dict[str, Any],
    tags: list[str],
    active_version: dict[str, Any],
    ingredients: list[dict[str, Any]],
    total_flavour_pct: float,
    stash_panel: dict[str, Any],
    mixer_notes_preview: list[dict[str, Any]],
) -> dict[str, Any]:
    owner = str(recipe.get("owner_username") or "").strip()
    owner_known = bool(owner and owner.lower() != "unknown")
    is_reference = str(recipe.get("status") or "").strip().lower() == "reference"
    recipe_code = str(recipe.get("recipe_code") or "").strip()
    version_notes = str(active_version.get("notes") or "")
    imported_source = parse_imported_source_from_notes(version_notes)
    is_imported = bool(
        imported_source.get("label")
        or (not owner_known and (is_reference or recipe_code.upper().startswith("VCREF-")))
    )

    pg_pct, vg_pct = parse_pg_vg_from_ratio(active_version.get("target_vg_pg_ratio"))
    updated_label = human_recipe_date(recipe.get("updated_at"), prefix="Updated")
    if not updated_label:
        updated_label = human_recipe_date(active_version.get("created_at"), prefix="Updated")

    notes_preview = version_notes.strip()
    creator_notes_body = ""
    creator_notes_is_source_only = False
    if notes_preview and not (is_imported and imported_source.get("label") and "Imported VC DTL reference" in notes_preview):
        creator_notes_body = notes_preview
    elif is_imported and imported_source.get("label"):
        creator_notes_is_source_only = True

    enriched_notes: list[dict[str, Any]] = []
    for note in mixer_notes_preview:
        row = dict(note)
        row["updated_label"] = human_recipe_date(note.get("updated_at"), prefix="Mixed")
        enriched_notes.append(row)

    return {
        "category": first_recipe_tag(", ".join(tags)) if tags else None,
        "owner_known": owner_known,
        "owner_username": owner if owner_known else None,
        "is_imported": is_imported,
        "imported_source_label": imported_source.get("label"),
        "imported_source_url": imported_source.get("url"),
        "visibility_pill": _recipe_visibility_pill(recipe),
        "is_reference": is_reference,
        "status_label": _status_display_label(recipe.get("status")),
        "status_code": str(recipe.get("status") or "WIP").strip().upper(),
        "updated_label": updated_label,
        "version_label": f"Version {active_version.get('version_number') or '1'}",
        "flavour_count": len(ingredients),
        "total_flavour_pct": total_flavour_pct,
        "facts_batch_ml": stash_panel.get("batch_ml") or 60,
        "facts_pg_pct": pg_pct,
        "facts_vg_pct": vg_pct,
        "facts_pg_vg_label": f"{pg_pct}/{vg_pct} PG/VG",
        "creator_notes_body": creator_notes_body,
        "creator_notes_is_source_only": creator_notes_is_source_only,
        "ingredient_stash": _ingredient_stash_map(stash_panel),
        "mixer_notes_preview": enriched_notes,
    }
