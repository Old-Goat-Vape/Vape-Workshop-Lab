from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from models import cost_catalogue_repo, recipes_repo
from services import catalogue_price_resolver


@dataclass(frozen=True)
class BatchCostInputs:
    batch_volume_ml: float
    bottle_size_ml: float
    selling_price_per_bottle: float
    base_cost_per_ml: float = 0.0
    bottle_cost: float = 5.0
    label_cost: float = 4.0
    packaging_cost: float = 0.0
    labour_per_bottle: float = 0.0
    waste_percent: float = 0.0
    use_canonical_base: bool = True
    vg_ratio: float = 70.0
    pg_ratio: float = 30.0


def ingredient_price_missing(price_per_ml: Any) -> bool:
    if price_per_ml is None:
        return True
    try:
        return float(price_per_ml) <= 0.0
    except (TypeError, ValueError):
        return True


def resolve_ingredients_for_costing(
    *,
    recipe_id: int,
    version_id: int,
    raw_ingredients: List[Dict[str, Any]],
    catalogue_rows: Optional[List[Dict[str, Any]]] = None,
    overrides: Optional[Dict[int, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Apply price resolution priority: manual price > catalogue override > auto-resolve > linked row."""
    catalogue = catalogue_rows if catalogue_rows is not None else cost_catalogue_repo.fetch_all_catalogue_for_costing()
    ov = overrides if overrides is not None else cost_catalogue_repo.fetch_cost_overrides_for_version(
        recipe_id=int(recipe_id), version_id=int(version_id)
    )

    resolved: List[Dict[str, Any]] = []
    for ing in raw_ingredients:
        ingredient_id = int(ing.get("ingredient_id") or 0)
        row = dict(ing)
        ovr = ov.get(ingredient_id) or {}
        manual_price = ovr.get("manual_price_per_ml")
        catalogue_override_id = ovr.get("catalogue_reference_id")

        resolution = catalogue_price_resolver.resolve_price_item(
            ing,
            catalogue,
            override_catalogue_id=int(catalogue_override_id) if catalogue_override_id else None,
        )

        price_source = "catalogue"
        effective_price: Optional[float] = resolution.get("price_per_ml")
        match_status = str(resolution.get("status") or "missing")
        manual_price_active = False

        if manual_price is not None and float(manual_price) > 0:
            effective_price = float(manual_price)
            price_source = "manual_price"
            match_status = "manual_price"
            manual_price_active = True
        elif catalogue_override_id and resolution.get("status") == "manual":
            price_source = "manual_catalogue"
            match_status = "manual"
        elif resolution.get("status") == "check_match" and effective_price is not None:
            price_source = "auto_low_confidence"

        missing = ingredient_price_missing(effective_price)
        row.update(
            {
                "price_per_ml": None if missing else float(effective_price),
                "missing_price": missing,
                "match_status": match_status,
                "match_confidence": int(resolution.get("confidence_score") or 0),
                "resolved_catalogue_id": resolution.get("catalogue_id"),
                "resolved_label": resolution.get("resolved_label") or "",
                "price_source": price_source,
                "manual_price_per_ml": float(manual_price) if manual_price_active else None,
                "manual_price_active": manual_price_active,
                "catalogue_candidates": resolution.get("candidates") or [],
                "is_nicotine": int(ing.get("is_nicotine") or 0),
                "canonical_variant": resolution.get("canonical_variant"),
                "price_source_detail": resolution.get("price_source_detail") or {},
            }
        )
        resolved.append(row)
    return resolved


def compute_concentrate_cost_per_ml(ingredients: List[Dict[str, Any]]) -> tuple[float, List[Dict[str, Any]]]:
    """Sum priced flavour lines; return (cost_per_ml, ingredient lines with flags)."""
    lines: List[Dict[str, Any]] = []
    total = 0.0
    for ing in ingredients:
        pct = float(ing.get("amount_percent") or ing.get("percent") or 0.0)
        price = ing.get("price_per_ml")
        missing = bool(ing.get("missing_price")) or ingredient_price_missing(price)
        unit = 0.0 if missing else float(price)
        line_cost_per_ml = unit * (pct / 100.0)
        if not missing:
            total += line_cost_per_ml
        line = {
            "ingredient_id": int(ing.get("ingredient_id") or ing.get("id") or 0),
            "name": str(ing.get("name") or ""),
            "brand": str(ing.get("brand") or ""),
            "amount_percent": pct,
            "price_per_ml": None if missing else float(price),
            "missing_price": missing,
            "cost_per_ml_contribution": round(line_cost_per_ml, 6),
            "match_status": ing.get("match_status"),
            "match_confidence": ing.get("match_confidence"),
            "resolved_catalogue_id": ing.get("resolved_catalogue_id"),
            "resolved_label": ing.get("resolved_label") or "",
            "price_source": ing.get("price_source") or "",
            "manual_price_per_ml": ing.get("manual_price_per_ml"),
            "manual_price_active": bool(ing.get("manual_price_active")),
            "catalogue_candidates": ing.get("catalogue_candidates") or [],
        }
        lines.append(line)
    return round(total, 6), lines


def calculate_batch_cost(
    *,
    ingredients: List[Dict[str, Any]],
    inputs: BatchCostInputs,
) -> Dict[str, Any]:
    """Pure batch costing from recipe ingredient rows + user assumptions."""
    batch_ml = max(0.0, float(inputs.batch_volume_ml or 0.0))
    bottle_ml = max(0.0, float(inputs.bottle_size_ml or 0.0))
    waste_pct = max(0.0, float(inputs.waste_percent or 0.0))
    effective_ml = batch_ml * (1.0 + waste_pct / 100.0) if batch_ml > 0 else 0.0

    concentrate_per_ml, ingredient_lines = compute_concentrate_cost_per_ml(ingredients)
    missing_price_count = sum(1 for x in ingredient_lines if x["missing_price"])
    low_confidence_count = sum(1 for x in ingredient_lines if x.get("match_status") == "check_match")
    pricing_incomplete = missing_price_count > 0

    # Attach volume and line cost for the current batch (rally planning).
    for line in ingredient_lines:
        pct = float(line.get("amount_percent") or 0.0)
        required_ml = round(effective_ml * (pct / 100.0), 4) if effective_ml > 0 else 0.0
        price = line.get("price_per_ml")
        missing = bool(line.get("missing_price"))
        line_cost = 0.0 if missing or price is None else round(float(price) * required_ml, 4)
        line["required_ml"] = required_ml
        line["line_cost"] = line_cost
        line["price_freshness"] = line.get("price_freshness") or ""
        line["price_source_label"] = line.get("resolved_label") or line.get("price_source") or ""


    total_flavour_cost = concentrate_per_ml * effective_ml

    flavour_pct_total = sum(float(x.get("amount_percent") or 0.0) for x in ingredient_lines)
    nic_pct_total = sum(
        float(x.get("amount_percent") or 0.0)
        for x in ingredients
        if int(x.get("is_nicotine") or 0) == 1
    )
    nic_ids = {int(i.get("ingredient_id") or 0) for i in ingredients if int(i.get("is_nicotine") or 0) == 1}
    total_nicotine_cost = sum(
        float(x.get("line_cost") or 0.0) for x in ingredient_lines if int(x.get("ingredient_id") or 0) in nic_ids
    )

    warnings: List[str] = []
    canonical_base = None
    total_pg_cost = 0.0
    total_vg_cost = 0.0

    if bool(getattr(inputs, "use_canonical_base", True)):
        try:
            from services.blck_canonical_costing_service import compute_base_liquid_breakdown

            canonical_base = compute_base_liquid_breakdown(
                effective_batch_ml=effective_ml,
                flavour_percent_total=flavour_pct_total,
                nicotine_percent=nic_pct_total,
                vg_ratio=float(getattr(inputs, "vg_ratio", 70.0) or 70.0),
                pg_ratio=float(getattr(inputs, "pg_ratio", 30.0) or 30.0),
            )
            total_base_cost = float(canonical_base.get("total_base_cost") or 0.0)
            total_pg_cost = float(canonical_base.get("total_pg_cost") or 0.0)
            total_vg_cost = float(canonical_base.get("total_vg_cost") or 0.0)
            for w in canonical_base.get("warnings") or []:
                if w not in warnings:
                    warnings.append(w)
        except Exception as exc:
            warnings.append(f"Canonical BLCK base pricing unavailable: {exc}")
            total_base_cost = max(0.0, float(inputs.base_cost_per_ml or 0.0)) * effective_ml
    else:
        total_base_cost = max(0.0, float(inputs.base_cost_per_ml or 0.0)) * effective_ml

    full_bottles = int(batch_ml // bottle_ml) if bottle_ml > 0 else 0
    leftover_ml = round(batch_ml - (full_bottles * bottle_ml), 4) if bottle_ml > 0 else batch_ml

    per_bottle = full_bottles
    bottle_unit = max(0.0, float(inputs.bottle_cost or 0.0))
    label_unit = max(0.0, float(inputs.label_cost or 0.0))
    packaging_unit = max(0.0, float(inputs.packaging_cost or 0.0))
    labour_unit = max(0.0, float(inputs.labour_per_bottle or 0.0))

    total_bottle_cost = bottle_unit * per_bottle
    total_label_cost = label_unit * per_bottle
    total_packaging_cost = packaging_unit * per_bottle
    total_labour_cost = labour_unit * per_bottle

    total_batch_cost = (
        total_flavour_cost
        + total_base_cost
        + total_bottle_cost
        + total_label_cost
        + total_packaging_cost
        + total_labour_cost
    )

    cost_per_ml = (total_batch_cost / batch_ml) if batch_ml > 0 else 0.0
    cost_per_bottle = (total_batch_cost / full_bottles) if full_bottles > 0 else 0.0
    sell_price = max(0.0, float(inputs.selling_price_per_bottle or 0.0))
    total_sales_value = sell_price * full_bottles
    gross_profit = total_sales_value - total_batch_cost
    profit_per_bottle = (gross_profit / full_bottles) if full_bottles > 0 else 0.0
    margin_percent = (gross_profit / total_sales_value * 100.0) if total_sales_value > 0 else 0.0
    markup_percent = (gross_profit / total_batch_cost * 100.0) if total_batch_cost > 0 else 0.0

    if pricing_incomplete:
        warnings.append(
            f"Estimated/incomplete cost — {missing_price_count} ingredient(s) have no price "
            "(missing prices are not counted as R0.00)."
        )
    if low_confidence_count:
        warnings.append(
            f"{low_confidence_count} ingredient(s) need catalogue match review (check match)."
        )
    if batch_ml <= 0:
        warnings.append("Batch volume must be greater than zero.")
    if bottle_ml <= 0:
        warnings.append("Bottle size must be greater than zero.")
    if full_bottles == 0 and batch_ml > 0:
        warnings.append("Batch is smaller than one full bottle — per-bottle profit uses zero bottles.")

    return {
        "inputs": {
            "batch_volume_ml": batch_ml,
            "bottle_size_ml": bottle_ml,
            "selling_price_per_bottle": sell_price,
            "base_cost_per_ml": float(
                (canonical_base or {}).get("blended_base_cost_per_ml")
                if canonical_base
                else (inputs.base_cost_per_ml or 0.0)
            ),
            "use_canonical_base": bool(getattr(inputs, "use_canonical_base", True)),
            "vg_ratio": float(getattr(inputs, "vg_ratio", 70.0) or 70.0),
            "pg_ratio": float(getattr(inputs, "pg_ratio", 30.0) or 30.0),
            "bottle_cost": bottle_unit,
            "label_cost": label_unit,
            "packaging_cost": packaging_unit,
            "labour_per_bottle": labour_unit,
            "waste_percent": waste_pct,
            "effective_batch_ml": round(effective_ml, 4),
        },
        "recipe": {
            "concentrate_cost_per_ml": concentrate_per_ml,
            "pricing_incomplete": pricing_incomplete,
            "pricing_complete": not pricing_incomplete,
            "missing_price_count": missing_price_count,
            "cost_status_label": (
                "Estimated/incomplete cost"
                if pricing_incomplete
                else "Complete cost"
            ),
            "ingredients": ingredient_lines,
        },
        "canonical_base": canonical_base,
        "outputs": {
            "full_bottles": full_bottles,
            "leftover_ml": leftover_ml,
            "total_flavour_cost": round(total_flavour_cost, 4),
            "total_base_cost": round(total_base_cost, 4),
            "total_nicotine_cost": round(total_nicotine_cost, 4),
            "total_pg_cost": round(total_pg_cost, 4),
            "total_vg_cost": round(total_vg_cost, 4),
            "total_bottle_cost": round(total_bottle_cost, 4),
            "total_label_cost": round(total_label_cost, 4),
            "total_packaging_cost": round(total_packaging_cost, 4),
            "total_labour_cost": round(total_labour_cost, 4),
            "total_batch_cost": round(total_batch_cost, 4),
            "cost_per_ml": round(cost_per_ml, 6),
            "cost_per_bottle": round(cost_per_bottle, 4),
            "selling_price_per_bottle": sell_price,
            "total_sales_value": round(total_sales_value, 4),
            "gross_profit": round(gross_profit, 4),
            "profit_per_bottle": round(profit_per_bottle, 4),
            "margin_percent": round(margin_percent, 2),
            "markup_percent": round(markup_percent, 2),
            "rally_quantities": {
                "1": round(cost_per_bottle * 1, 4) if full_bottles > 0 else None,
                "5": round(cost_per_bottle * 5, 4) if full_bottles > 0 else None,
                "10": round(cost_per_bottle * 10, 4) if full_bottles > 0 else None,
            },
        },
        "warnings": warnings,
    }


def build_batch_cost_for_recipe(
    *,
    recipe_id: int,
    recipe_version_id: int | None = None,
    inputs: BatchCostInputs,
) -> Dict[str, Any]:
    version_id = recipes_repo.resolve_recipe_version_id_for_mix(
        recipe_id=int(recipe_id),
        requested_version_id=int(recipe_version_id) if recipe_version_id else None,
    )
    recipe = recipes_repo.fetch_recipe(int(recipe_id))
    version = recipes_repo.fetch_version(version_id)
    raw_ingredients = recipes_repo.fetch_version_items_with_pricing(version_id)
    catalogue = cost_catalogue_repo.fetch_all_catalogue_for_costing()
    overrides = cost_catalogue_repo.fetch_cost_overrides_for_version(recipe_id=int(recipe_id), version_id=version_id)
    ingredients = resolve_ingredients_for_costing(
        recipe_id=int(recipe_id),
        version_id=version_id,
        raw_ingredients=raw_ingredients,
        catalogue_rows=catalogue,
        overrides=overrides,
    )
    result = calculate_batch_cost(ingredients=ingredients, inputs=inputs)
    result["recipe_meta"] = {
        "recipe_id": int(recipe_id),
        "recipe_name": (recipe or {}).get("name") or "",
        "recipe_code": (recipe or {}).get("recipe_code") or "",
        "version_id": version_id,
        "version_number": (version or {}).get("version_number") or "",
    }
    return result


def recipe_picker_rows(
    *,
    session_user_id: int | None,
    search: str = "",
    page_size: int = 2000,
) -> List[Dict[str, Any]]:
    """Workshop-canonical recipe list for Lab costing (Old Goat / show_cost callers).

    Reads the shared ``recipes`` / ``recipe_versions`` tables — no Lab copy.
    Includes public, private, reference, and Workshop-created recipes.
    """
    recipes, _total = recipes_repo.fetch_recipes_library_page(
        include_dev_archived=True,
        include_advanced=True,
        status_filter="",
        search=(search or "").strip(),
        library_scope="all",
        exclude_reference=False,
        current_user_id=session_user_id,
        page=1,
        page_size=max(100, int(page_size or 2000)),
        allow_nicotine=True,
        show_cost=True,
    )
    rows: List[Dict[str, Any]] = []
    for r in recipes:
        rid = int(r["id"])
        current = recipes_repo.fetch_current_version(rid)
        latest_vid = r.get("latest_version_id")
        versions = recipes_repo.fetch_recipe_versions(rid)
        visibility = str(r.get("visibility") or "private").lower()
        library_type = str(r.get("library_type") or "").lower()
        status = str(r.get("status") or "")
        owner = str(r.get("owner_username") or r.get("created_by_username") or "")
        tags = recipes_repo.fetch_recipe_tags(rid)
        badges: List[str] = []
        if visibility == "public":
            badges.append("Public")
        elif visibility == "private":
            badges.append("Private")
        elif visibility == "unlisted":
            badges.append("Unlisted")
        if library_type == "reference" or status.upper() == "REFERENCE":
            badges.append("Reference")
        else:
            badges.append("Workshop-created")
        rows.append(
            {
                "id": rid,
                "name": r.get("name") or "",
                "recipe_code": r.get("recipe_code") or "",
                "status": status,
                "visibility": visibility,
                "library_type": library_type,
                "owner_username": owner,
                "tags": tags,
                "badges": badges,
                "current_version_id": int(current["id"]) if current else None,
                "latest_version_id": int(latest_vid) if latest_vid else None,
                "versions": [
                    {"id": int(v["id"]), "version_number": v.get("version_number") or ""}
                    for v in versions
                ],
            }
        )
    return rows
