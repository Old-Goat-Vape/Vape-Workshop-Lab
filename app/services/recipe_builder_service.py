"""Recipe builder form parsing and validation for create/edit."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from models import recipes_repo
from services import tvw_mix_service


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


_ALLOWED_VISIBILITY = {"private", "public", "unlisted"}
_ALLOWED_STATUS = {"wip", "help", "ready"}


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
    return {
        "name": (form.get("name") or "").strip(),
        "visibility": (form.get("visibility") or "private").strip().lower(),
        "status": (form.get("status") or "wip").strip().lower(),
        "tags": (form.get("tags") or "").strip(),
        "notes": (form.get("notes") or "").strip(),
        "target_ratio": (form.get("target_ratio") or "70/30").strip(),
        "recipe_id": (form.get("recipe_id") or "").strip(),
        "calc_target_ml": (form.get("calc_target_ml") or "60").strip(),
        "calc_target_nic": (form.get("calc_target_nic") or "3").strip(),
        "calc_pg_ratio": (form.get("calc_pg_ratio") or "30").strip(),
        "calc_vg_ratio": (form.get("calc_vg_ratio") or "70").strip(),
        "calc_nic_base": (form.get("calc_nic_base") or "100").strip(),
        "calc_nicotine_carrier": (form.get("calc_nicotine_carrier") or "PG").strip(),
    }


def parse_recipe_builder_form(form, *, is_edit: bool = False) -> RecipeBuilderPayload:
    values = form_values_from_request(form)
    rows = parse_flavour_rows(form)
    errors: dict[str, str] = {}

    name = values["name"]
    if not name:
        errors["name"] = "Recipe name is required."

    visibility = values["visibility"]
    if visibility not in _ALLOWED_VISIBILITY:
        errors["visibility"] = "Choose Private or Public."

    status = values["status"]
    if status not in _ALLOWED_STATUS:
        errors["status"] = "Choose a valid recipe status."

    ratio = values["target_ratio"]
    parsed_ratio = _parse_ratio(ratio)
    if not parsed_ratio:
        errors["target_ratio"] = "Enter a valid PG/VG ratio such as 70/30 (VG/PG)."

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

    ref_map = recipes_repo.fetch_flavour_reference_map([i for i, _ in items])
    for ing_id, _pct in items:
        if ing_id not in ref_map:
            errors["flavours"] = f"Unknown flavour (id {ing_id}). Search and select from the library."
            break

    try:
        pg_ratio = float(values["calc_pg_ratio"])
        vg_ratio = float(values["calc_vg_ratio"])
    except (TypeError, ValueError):
        pg_ratio = 30.0
        vg_ratio = 70.0

    if abs((pg_ratio + vg_ratio) - 100.0) > 0.01:
        errors["calc_ratio"] = "PG and VG must add up to 100%."

    calc = {
        "target_ml": float(values["calc_target_ml"] or 60),
        "target_nic_mg_ml": float(values["calc_target_nic"] or 3),
        "nic_base_mg_ml": float(values["calc_nic_base"] or 100),
        "pg_ratio": pg_ratio,
        "vg_ratio": vg_ratio,
        "nicotine_carrier": values["calc_nicotine_carrier"] or "PG",
    }

    if items and not errors.get("flavours") and not errors.get("calc_ratio"):
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
    )
