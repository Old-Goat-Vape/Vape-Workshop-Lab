from __future__ import annotations

from .calculator import MixInput, compute_mix_step_breakdown
from .guided_narrative import (
    STEP_ADD_INGREDIENT,
    STEP_CAUTION,
    STEP_COMPLETION,
    STEP_INSTRUCTION,
    STEP_RECOVERY,
    STEP_TARE,
    STEP_TRANSITION,
    build_guided_narrative_steps,
    infer_ingredient_role,
)


def _guided_pour_order_key(ing: dict) -> tuple:
    """Bench pour order: nicotine → flavours by role → PG → VG."""
    cat = str(ing.get("category") or "").lower()
    name = str(ing.get("name") or "")
    if cat == "nicotine":
        return (0, 0, name)
    if cat == "pg":
        return (3, 0, name)
    if cat == "vg":
        return (4, 0, name)

    role = infer_ingredient_role(
        ingredient_name=name,
        ingredient_percent=ing.get("pct"),
        ingredient_category=cat,
        flavor_profile=ing.get("flavor_profile"),
    )
    role_band = {
        "base_main_note": 1,
        "support": 2,
        "accent": 3,
        "micro_accent": 3,
        "additive": 3,
        "sweetener": 3.5,
        "coolant": 3.6,
    }.get(role, 2)
    return (1, role_band, name)


def build_guided_assistant_steps(
    request: MixInput,
    *,
    tone_profile: str = "tvw",
) -> list[dict]:
    base_steps = compute_mix_step_breakdown(request)
    ingredients: list[dict] = []
    for b in base_steps:
        ingredients.append(
            {
                "ingredient_id": b.get("ingredient_id"),
                "name": str(b.get("name") or b.get("title") or "Ingredient"),
                "brand": str(b.get("brand") or ""),
                "category": str(b.get("category") or "").lower(),
                "target_grams": float(b.get("grams") or 0.0),
                "ml": float(b.get("ml") or 0.0),
                "pct": b.get("percent"),
                "flavor_profile": b.get("flavor_profile"),
            }
        )
    ingredients.sort(key=_guided_pour_order_key)
    return build_guided_narrative_steps(
        tone_profile=tone_profile,
        ingredients=ingredients,
        session_seed=f"{float(request.target_ml):.4f}:{float(request.target_nic_mg_ml):.4f}",
    )
