"""Recipe builder form parsing and validation for create/edit."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from models import recipes_repo
from services import tvw_mix_service, recipe_story_service


class RecipeBuilderFormError(ValueError):
    """Invalid recipe builder form with field-level errors and preserved input."""

    def __init__(self, errors: dict[str, str], *, values: dict[str, Any], rows: list[dict[str, Any]]) -> None:
        self.errors = errors
        self.values = values
        self.rows = rows
        super().__init__(errors.get("_summary") or "Validation failed")


@dataclass
class RecipeBuilderPayload:
    name: str
    status: str
    visibility: str
    notes: str
    tags: list[str]
    target_ratio: str
    items: list[tuple[int, float]]
    total_pct: float
    recipe_id: int | None = None
    calc: dict[str, Any] = field(default_factory=dict)
    story: dict[str, Any] = field(default_factory=dict)


_ALLOWED_VISIBILITY = {"private", "public", "unlisted"}
_ALLOWED_STATUS = {"wip", "help", "ready"}


def _parse_float_field(raw: Any, default: float) -> float:
    """Parse float; empty → default. Numeric 0 is kept (not treated as missing)."""
    text = "" if raw is None else str(raw).strip()
    if text == "":
        return float(default)
    return float(text)


def _clamp_pct(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def normalize_complementary_pg_vg(pg: float, vg: float) -> tuple[float, float]:
    """Ensure PG + VG = 100 with values in [0, 100]. Prefer PG as anchor when inconsistent."""
    pg_c = _clamp_pct(pg)
    vg_c = _clamp_pct(vg)
    total = pg_c + vg_c
    if abs(total - 100.0) <= 0.01:
        # Snap tiny drift so VG complements PG exactly.
        return round(pg_c, 4), round(100.0 - pg_c, 4)
    if total <= 0:
        return 30.0, 70.0
    # Normalize proportionally, then snap VG to complement.
    pg_n = round((pg_c / total) * 100.0, 4)
    return pg_n, round(100.0 - pg_n, 4)


def _parse_ratio(raw: str) -> tuple[float, float] | None:
    text = str(raw or "").strip()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)$", text)
    if not m:
        return None
    vg = float(m.group(1))
    pg = float(m.group(2))
    if vg < 0 or pg < 0:
        return None
    return vg, pg


def parse_flavour_rows(form) -> list[dict[str, Any]]:
    ingredient_ids = form.getlist("ingredient_id[]")
    percents = form.getlist("ingredient_percent[]")
    names = form.getlist("ingredient_name[]")
    brands = form.getlist("ingredient_brand[]")
    rows: list[dict[str, Any]] = []
    max_len = max(len(ingredient_ids), len(percents), len(names), len(brands), 0)
    for idx in range(max_len):
        rows.append(
            {
                "ingredient_id": (ingredient_ids[idx] if idx < len(ingredient_ids) else "").strip(),
                "percent": (percents[idx] if idx < len(percents) else "").strip(),
                "name": (names[idx] if idx < len(names) else "").strip(),
                "brand": (brands[idx] if idx < len(brands) else "").strip(),
            }
        )
    if not rows:
        rows.append({"ingredient_id": "", "percent": "", "name": "", "brand": ""})
    return rows


def form_values_from_request(form) -> dict[str, Any]:
    story, _ = recipe_story_service.parse(form)
    return {
        **story,
        "name": (form.get("name") or "").strip(),
        "visibility": (form.get("visibility") or "private").strip().lower(),
        "status": (form.get("status") or "wip").strip().lower(),
        "tags": (form.get("tags") or "").strip(),
        "notes": (form.get("notes") or "").strip(),
        "target_ratio": (form.get("target_ratio") or "70/30").strip(),
        "recipe_id": (form.get("recipe_id") or "").strip(),
        "calc_target_ml": (form.get("calc_target_ml") if form.get("calc_target_ml") is not None else "60"),
        "calc_target_nic": (form.get("calc_target_nic") if form.get("calc_target_nic") is not None else "3"),
        "calc_pg_ratio": (form.get("calc_pg_ratio") if form.get("calc_pg_ratio") is not None else "30"),
        "calc_vg_ratio": (form.get("calc_vg_ratio") if form.get("calc_vg_ratio") is not None else "70"),
        "calc_nic_base": (form.get("calc_nic_base") if form.get("calc_nic_base") is not None else "100"),
        "calc_nicotine_carrier": (form.get("calc_nicotine_carrier") or "PG").strip(),
    }


def form_values_from_version(active_version: dict[str, Any] | None, *, recipe: dict[str, Any] | None = None) -> dict[str, Any]:
    """Populate builder fields from a saved version (edit mode)."""
    _ = recipe
    ver = active_version or {}
    ratio = str(ver.get("target_vg_pg_ratio") or "70/30")
    parsed = _parse_ratio(ratio)
    if parsed:
        # stored as VG/PG
        pg, vg = normalize_complementary_pg_vg(parsed[1], parsed[0])
    else:
        pg, vg = 30.0, 70.0
    batch = ver.get("default_batch_ml")
    nic = ver.get("default_nic_mg_ml")
    nic_base = ver.get("default_nic_base_mg_ml")
    carrier = str(ver.get("default_nicotine_carrier") or "PG").strip().upper() or "PG"
    return {
        "name": "",
        "visibility": "private",
        "status": "wip",
        "tags": "",
        "notes": str(ver.get("notes") or ""),
        "target_ratio": f"{vg:g}/{pg:g}",
        "recipe_id": "",
        "calc_target_ml": str(int(batch) if batch is not None and float(batch) == int(float(batch)) else (batch if batch is not None else 60)),
        "calc_target_nic": str(nic if nic is not None else 3),
        "calc_pg_ratio": str(int(pg) if float(pg) == int(pg) else pg),
        "calc_vg_ratio": str(int(vg) if float(vg) == int(vg) else vg),
        "calc_nic_base": str(int(nic_base) if nic_base is not None and float(nic_base) == int(float(nic_base)) else (nic_base if nic_base is not None else 100)),
        "calc_nicotine_carrier": carrier if carrier in {"PG", "VG"} else "PG",
    }


def parse_recipe_builder_form(form, *, is_edit: bool = False) -> RecipeBuilderPayload:
    values = form_values_from_request(form)
    # Keep raw strings for redisplay; coerce empties to defaults without wiping 0.
    values["calc_target_ml"] = str(values.get("calc_target_ml") if str(values.get("calc_target_ml") or "").strip() != "" else "60").strip()
    values["calc_target_nic"] = str(values.get("calc_target_nic") if str(values.get("calc_target_nic") or "").strip() != "" else "3").strip()
    values["calc_pg_ratio"] = str(values.get("calc_pg_ratio") if str(values.get("calc_pg_ratio") or "").strip() != "" else "30").strip()
    values["calc_vg_ratio"] = str(values.get("calc_vg_ratio") if str(values.get("calc_vg_ratio") or "").strip() != "" else "70").strip()
    values["calc_nic_base"] = str(values.get("calc_nic_base") if str(values.get("calc_nic_base") or "").strip() != "" else "100").strip()

    rows = parse_flavour_rows(form)
    story, errors = recipe_story_service.parse(form)

    name = values["name"]
    if not name:
        errors["name"] = "Recipe name is required."

    visibility = values["visibility"]
    if visibility not in _ALLOWED_VISIBILITY:
        errors["visibility"] = "Choose Private or Public."

    status = values["status"]
    if status not in _ALLOWED_STATUS:
        errors["status"] = "Choose a valid recipe status."
    if status == "help":
        visibility = values["visibility"] = "public"

    try:
        pg_ratio = _parse_float_field(values["calc_pg_ratio"], 30.0)
        vg_ratio = _parse_float_field(values["calc_vg_ratio"], 70.0)
    except (TypeError, ValueError):
        pg_ratio = 30.0
        vg_ratio = 70.0
        errors["calc_ratio"] = "PG and VG must be numbers between 0 and 100."

    pg_ratio, vg_ratio = normalize_complementary_pg_vg(pg_ratio, vg_ratio)
    values["calc_pg_ratio"] = str(pg_ratio)
    values["calc_vg_ratio"] = str(vg_ratio)
    # Canonical stored ratio is VG/PG (legacy format).
    ratio = f"{vg_ratio:g}/{pg_ratio:g}"
    values["target_ratio"] = ratio

    if pg_ratio < 0 or vg_ratio < 0 or pg_ratio > 100 or vg_ratio > 100:
        errors["calc_ratio"] = "PG and VG must be between 0 and 100."
    if abs((pg_ratio + vg_ratio) - 100.0) > 0.01:
        errors["calc_ratio"] = "PG and VG must add up to 100%."

    items: list[tuple[int, float]] = []
    total_pct = 0.0
    row_errors: list[str] = []
    seen_ids: set[int] = set()
    for idx, row in enumerate(rows):
        ing_raw = str(row.get("ingredient_id") or "").strip()
        pct_raw = str(row.get("percent") or "").strip()
        if not ing_raw and not pct_raw:
            continue
        if not ing_raw:
            row_errors.append(f"Row {idx + 1}: select a flavour.")
            continue
        if not pct_raw:
            row_errors.append(f"Row {idx + 1}: enter a percentage.")
            continue
        try:
            ing_id = int(ing_raw)
            pct = float(pct_raw)
        except (TypeError, ValueError):
            row_errors.append(f"Row {idx + 1}: invalid flavour or percentage.")
            continue
        if pct <= 0:
            row_errors.append(f"Row {idx + 1}: percentage must be greater than zero.")
            continue
        if ing_id in seen_ids:
            row_errors.append(f"Row {idx + 1}: duplicate flavour.")
            continue
        seen_ids.add(ing_id)
        items.append((ing_id, pct))
        total_pct += pct

    if not items:
        errors["flavours"] = "Add at least one flavour with a percentage."

    if row_errors:
        errors["flavours"] = errors.get("flavours") or row_errors[0]
        errors["_flavour_rows"] = "; ".join(row_errors[:3])

    ref_map = recipes_repo.fetch_flavour_reference_map([i for i, _ in items], recipe_eligible_only=True)
    for ing_id, _pct in items:
        if ing_id not in ref_map:
            errors["flavours"] = f"Unknown flavour (id {ing_id}) or product unavailable as a mixing ingredient. Search and select from the library."
            break

    try:
        target_ml = _parse_float_field(values["calc_target_ml"], 60.0)
        target_nic = _parse_float_field(values["calc_target_nic"], 3.0)
        nic_base = _parse_float_field(values["calc_nic_base"], 100.0)
    except (TypeError, ValueError):
        target_ml, target_nic, nic_base = 60.0, 3.0, 100.0
        errors["calc"] = "Batch size and nicotine values must be numbers."

    if target_ml <= 0:
        errors["calc"] = "Batch size must be greater than zero."
    if target_nic < 0:
        errors["calc"] = "Target nicotine cannot be negative."

    calc = {
        "target_ml": float(target_ml),
        "target_nic_mg_ml": float(target_nic),
        "nic_base_mg_ml": float(nic_base),
        "pg_ratio": float(pg_ratio),
        "vg_ratio": float(vg_ratio),
        "nicotine_carrier": values["calc_nicotine_carrier"] or "PG",
    }

    if items and not errors.get("flavours") and not errors.get("calc_ratio") and not errors.get("calc"):
        try:
            mix_rows = [{"ingredient_id": i, "percent": p} for i, p in items]
            result = tvw_mix_service.live_mix_for_builder(rows=mix_rows, **calc)
            hard_stops = list((result.get("policy") or {}).get("hard_stops") or [])
            if hard_stops:
                errors["_summary"] = hard_stops[0]
                errors["calc"] = hard_stops[0]
        except Exception as exc:
            errors["calc"] = str(exc)

    recipe_id = None
    rid_raw = values.get("recipe_id") or ""
    if str(rid_raw).isdigit():
        recipe_id = int(rid_raw)

    if errors:
        if "_summary" not in errors:
            errors["_summary"] = next(iter(errors.values()))
        raise RecipeBuilderFormError(errors, values=values, rows=rows)

    return RecipeBuilderPayload(
        name=name,
        status=status,
        visibility=visibility,
        notes=values["notes"],
        tags=[t.strip() for t in values["tags"].split(",") if t.strip()] if values["tags"] else [],
        target_ratio=ratio,
        items=items,
        total_pct=total_pct,
        recipe_id=recipe_id if is_edit or recipe_id else None,
        calc=calc,
        story=story,
    )
