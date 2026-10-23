from __future__ import annotations

from core.calculator import (
    FlavorInput,
    MixInput,
    build_guided_assistant_steps,
    compute_mix_with_policy,
    compute_mix_step_breakdown_with_policy,
    format_carrier_display,
    resolve_carrier,
    tvw_mix_policy,
)
from models import recipes_repo

_TVW_POLICY = tvw_mix_policy()


def _raw_carrier_from_row(row) -> str:
    """Keep explicit PG/VG only; blank/unknown stays blank for assumed-default resolution."""
    if not isinstance(row, dict):
        return ""
    text = str(row.get("carrier") or row.get("base_carrier") or "").strip().upper()
    return text if text in {"PG", "VG"} else ""


def _flavor_from_row(row) -> FlavorInput:
    name = row.get("name") if isinstance(row, dict) else row["name"]
    percent = row.get("amount_percent") if isinstance(row, dict) else row["amount_percent"]
    density = row.get("density", 1.036) if isinstance(row, dict) else row["density"]
    brand = str(row.get("brand") or "") if isinstance(row, dict) else ""
    dens = float(density or 1.036)
    return FlavorInput(
        name=str(name),
        percent=float(percent or 0.0),
        density=dens,
        brand=brand,
        carrier=_raw_carrier_from_row(row),
    )


def _finished_composition_dict(result) -> dict:
    return {
        "final_pg_ml": float(result.final_pg_ml),
        "final_vg_ml": float(result.final_vg_ml),
        "final_pg_percent": float(result.final_pg_percent),
        "final_vg_percent": float(result.final_vg_percent),
        "target_pg_percent": float(result.target_pg_percent),
        "target_vg_percent": float(result.target_vg_percent),
        "flavor_pg_ml": float(result.flavor_pg_ml),
        "flavor_vg_ml": float(result.flavor_vg_ml),
        "nicotine_pg_ml": float(result.nicotine_pg_ml),
        "nicotine_vg_ml": float(result.nicotine_vg_ml),
        "standalone_pg_ml": float(result.pg_ml),
        "standalone_vg_ml": float(result.vg_ml),
        "standalone_pg_g": float(result.pg_g),
        "standalone_vg_g": float(result.vg_g),
        "total_flavor_percent": float(result.total_flavor_percent),
        "ratio_achievable": bool(result.ratio_achievable),
        "ratio_warning": str(result.ratio_warning or ""),
        "final_ratio_label": (
            f"{int(round(result.final_pg_percent))}/{int(round(result.final_vg_percent))}"
        ),
        "target_ratio_label": (
            f"{int(round(result.target_pg_percent))}/{int(round(result.target_vg_percent))}"
        ),
    }


def build_mix_input_from_recipe_version(
    *,
    recipe_id: int,
    version_id: int,
    target_ml: float,
    target_nic_mg_ml: float,
    nic_base_mg_ml: float,
    pg_ratio: float,
    vg_ratio: float,
    nicotine_carrier: str,
) -> MixInput:
    version_row = recipes_repo.fetch_version(version_id)
    if not version_row or int(version_row.get("recipe_id") or 0) != int(recipe_id):
        raise ValueError("Recipe/version mismatch.")

    formula_rows = recipes_repo.fetch_version_items_with_stock(version_id)
    if not formula_rows:
        raise ValueError("Recipe version has no ingredients.")

    flavors = [_flavor_from_row(row) for row in formula_rows]

    # Keep recipe_id arg for future policy checks and audit linking.
    _ = recipe_id
    return MixInput(
        target_ml=float(target_ml),
        target_nic_mg_ml=float(target_nic_mg_ml),
        nic_base_mg_ml=float(nic_base_mg_ml),
        pg_ratio=float(pg_ratio),
        vg_ratio=float(vg_ratio),
        nicotine_carrier=str(nicotine_carrier or "PG"),
        flavors=flavors,
    )


def planned_finished_composition_for_recipe_version(**kwargs) -> dict:
    mix_input = build_mix_input_from_recipe_version(**kwargs)
    result, _evaluation = compute_mix_with_policy(
        mix_input,
        policy=_TVW_POLICY,
        hard_fail=False,
    )
    return _finished_composition_dict(result)


def quick_mix_for_recipe_version(**kwargs) -> dict:
    mix_input = build_mix_input_from_recipe_version(**kwargs)
    result, evaluation = compute_mix_with_policy(
        mix_input,
        policy=_TVW_POLICY,
        hard_fail=False,
    )
    finished = _finished_composition_dict(result)
    warnings = list(evaluation.warnings)
    if result.ratio_warning:
        warnings.append(result.ratio_warning)
    return {
        "totals": {
            "target_ml": mix_input.target_ml,
            "target_nic_mg_ml": mix_input.target_nic_mg_ml,
            "total_flavor_percent": result.total_flavor_percent,
            "flavor_ml": result.flavor_ml,
            "nicotine_ml": result.nicotine_ml,
            # Standalone base pours (operator bottle additions).
            "pg_ml": result.pg_ml,
            "vg_ml": result.vg_ml,
            "standalone_pg_ml": result.pg_ml,
            "standalone_vg_ml": result.vg_ml,
            # Finished e-liquid carrier composition.
            "final_pg_ml": result.final_pg_ml,
            "final_vg_ml": result.final_vg_ml,
            "final_pg_percent": result.final_pg_percent,
            "final_vg_percent": result.final_vg_percent,
            "target_pg_percent": result.target_pg_percent,
            "target_vg_percent": result.target_vg_percent,
            "ratio_achievable": result.ratio_achievable,
            "final_ratio_label": finished["final_ratio_label"],
            "target_ratio_label": finished["target_ratio_label"],
        },
        "weights": {
            "flavor_g": result.flavor_g,
            "nicotine_g": result.nicotine_g,
            "pg_g": result.pg_g,
            "vg_g": result.vg_g,
            "standalone_pg_g": result.pg_g,
            "standalone_vg_g": result.vg_g,
        },
        "finished_composition": finished,
        "policy": {
            "name": _TVW_POLICY.name,
            "warnings": warnings,
            "hard_stops": list(evaluation.hard_stops),
        },
    }


def guided_mix_steps_for_recipe_version(**kwargs) -> list[dict]:
    mix_input = build_mix_input_from_recipe_version(**kwargs)
    _steps, _evaluation = compute_mix_step_breakdown_with_policy(
        mix_input,
        policy=_TVW_POLICY,
        hard_fail=False,
    )
    return build_guided_assistant_steps(mix_input, tone_profile="tvw")


def live_mix_for_builder(
    *,
    target_ml: float,
    target_nic_mg_ml: float,
    nic_base_mg_ml: float,
    pg_ratio: float,
    vg_ratio: float,
    nicotine_carrier: str,
    rows: list[dict],
) -> dict:
    """Live builder calculation using shared core calculator (no duplication)."""
    by_id = recipes_repo.fetch_flavour_reference_map(
        [int(r.get("ingredient_id") or 0) for r in rows if int(r.get("ingredient_id") or 0) > 0]
    )
    flavors: list[FlavorInput] = []
    line_items: list[dict] = []
    for r in rows:
        try:
            rid = int(r.get("ingredient_id") or 0)
            pct = float(r.get("percent") or 0.0)
        except (TypeError, ValueError):
            continue
        if rid <= 0 or pct <= 0:
            continue
        ref = by_id.get(rid)
        if not ref:
            continue
        dens = float(ref.get("density") or 1.036)
        raw_carrier = _raw_carrier_from_row(
            {"carrier": ref.get("carrier") or r.get("carrier") or ""}
        )
        f = FlavorInput(
            name=str(ref.get("name") or f"Flavor #{rid}"),
            percent=float(pct),
            density=dens,
            brand=str(ref.get("brand") or ""),
            carrier=raw_carrier,
        )
        flavors.append(f)
    mix_input = MixInput(
        target_ml=float(target_ml),
        target_nic_mg_ml=float(target_nic_mg_ml),
        nic_base_mg_ml=float(nic_base_mg_ml),
        pg_ratio=float(pg_ratio),
        vg_ratio=float(vg_ratio),
        nicotine_carrier=str(nicotine_carrier or "PG"),
        flavors=flavors,
    )
    result, evaluation = compute_mix_with_policy(
        mix_input,
        policy=_TVW_POLICY,
        hard_fail=False,
    )
    assumed_pg_count = 0
    for f in mix_input.flavors:
        ml_i = float(mix_input.target_ml) * (float(f.percent) / 100.0)
        carrier, carrier_source = resolve_carrier(f.carrier)
        if carrier_source == "assumed_default":
            assumed_pg_count += 1
        line_items.append(
            {
                "name": f.name,
                "brand": f.brand,
                "percent": round(float(f.percent), 4),
                "ml": round(float(ml_i), 4),
                "grams": round(float(ml_i * f.density), 4),
                "carrier": carrier,
                "carrier_source": carrier_source,
                "carrier_label": format_carrier_display(carrier=carrier, source=carrier_source),
            }
        )
    # Composition bar: flavour / nicotine / standalone bases (pour view).
    composition_ml = {
        "flavor": float(result.flavor_ml),
        "nicotine": float(result.nicotine_ml),
        "pg": float(result.pg_ml),
        "vg": float(result.vg_ml),
    }
    total_mix_ml = sum(composition_ml.values())
    if total_mix_ml <= 0:
        composition_pct = {"flavor": 0.0, "nicotine": 0.0, "pg": 0.0, "vg": 0.0}
    else:
        composition_pct = {
            k: round((v / total_mix_ml) * 100.0, 3) for k, v in composition_ml.items()
        }
    finished = _finished_composition_dict(result)
    warnings = list(evaluation.warnings)
    if result.ratio_warning:
        warnings.append(result.ratio_warning)
    return {
        "totals": {
            "target_ml": float(mix_input.target_ml),
            "target_nic_mg_ml": float(mix_input.target_nic_mg_ml),
            "total_flavor_percent": float(result.total_flavor_percent),
            "flavor_ml": float(result.flavor_ml),
            "nicotine_ml": float(result.nicotine_ml),
            "pg_ml": float(result.pg_ml),
            "vg_ml": float(result.vg_ml),
            "total_mix_ml": round(total_mix_ml, 4),
            "final_pg_percent": float(result.final_pg_percent),
            "final_vg_percent": float(result.final_vg_percent),
            "ratio_achievable": bool(result.ratio_achievable),
            "final_ratio_label": finished["final_ratio_label"],
            "target_ratio_label": finished["target_ratio_label"],
        },
        "weights": {
            "flavor_g": float(result.flavor_g),
            "nicotine_g": float(result.nicotine_g),
            "pg_g": float(result.pg_g),
            "vg_g": float(result.vg_g),
        },
        "line_items": line_items,
        "composition_ml": composition_ml,
        "composition_pct": composition_pct,
        "finished_composition": finished,
        "carrier_diagnostics": {
            "assumed_pg_flavour_count": assumed_pg_count,
            "note": (
                f"{assumed_pg_count} flavour(s) use PG (assumed) — carrier not set on the concentrate."
                if assumed_pg_count
                else None
            ),
        },
        "policy": {
            "name": _TVW_POLICY.name,
            "warnings": warnings,
            "hard_stops": list(evaluation.hard_stops),
        },
    }
