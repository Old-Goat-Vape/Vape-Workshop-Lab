"""
Family-aware conversion targets (structural layer for future allocator).

Defines editable emphasis and band targets per (target_mode, recipe_family).
MTL and POD_DISPO are supported; DTL has no family-specific conversion row here.

Values are heuristic defaults — tune without changing engine wiring.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

# Keys must match detect_recipe_family() in services/mixing_engine.py
RECIPE_FAMILY_KEYS = (
    "fruit_candy",
    "dessert_bakery",
    "beverage",
    "tobacco",
    "cooling_focused",
    "mixed",
)

# Type aliases for readability in this module:
# - total_uplift_band: (min_rel, max_rel) expected relative uplift vs source DTL total (next phase).
# - saturation_target: (lo, hi) target band for saturation_score (0..1) when aligning signals.
# - *_emphasis: relative weight vs baseline 1.0 for allocation / emphasis in a future allocator.
# - harshness_ceiling / coil_risk_ceiling: max acceptable risk scores (0..1).

FamilyProfile = Dict[str, Any]


def _p(
    *,
    display_name: str,
    total_uplift_band: Tuple[float, float],
    saturation_target: Tuple[float, float],
    sweetness_emphasis: float,
    body_emphasis: float,
    brightness_emphasis: float,
    cooling_emphasis: float,
    harshness_ceiling: float,
    coil_risk_ceiling: float,
    topnote_emphasis: float,
) -> FamilyProfile:
    return {
        "display_name": display_name,
        "total_uplift_band": total_uplift_band,
        "saturation_target": saturation_target,
        "sweetness_emphasis": sweetness_emphasis,
        "body_emphasis": body_emphasis,
        "brightness_emphasis": brightness_emphasis,
        "cooling_emphasis": cooling_emphasis,
        "harshness_ceiling": harshness_ceiling,
        "coil_risk_ceiling": coil_risk_ceiling,
        "topnote_emphasis": topnote_emphasis,
    }


# --- MTL: small uplift vs DTL total; clarity and coil-friendly bias ---
FAMILY_TARGETS_MTL: Dict[str, FamilyProfile] = {
    "fruit_candy": _p(
        display_name="MTL · Fruit / Candy",
        total_uplift_band=(0.015, 0.085),
        saturation_target=(0.50, 0.82),
        sweetness_emphasis=1.05,
        body_emphasis=0.95,
        brightness_emphasis=1.12,
        cooling_emphasis=0.92,
        harshness_ceiling=0.58,
        coil_risk_ceiling=0.68,
        topnote_emphasis=1.22,
    ),
    "dessert_bakery": _p(
        display_name="MTL · Dessert / Bakery",
        total_uplift_band=(0.012, 0.072),
        saturation_target=(0.46, 0.78),
        sweetness_emphasis=1.12,
        body_emphasis=1.18,
        brightness_emphasis=0.88,
        cooling_emphasis=0.85,
        harshness_ceiling=0.60,
        coil_risk_ceiling=0.70,
        topnote_emphasis=0.92,
    ),
    "beverage": _p(
        display_name="MTL · Beverage",
        total_uplift_band=(0.014, 0.078),
        saturation_target=(0.48, 0.80),
        sweetness_emphasis=1.02,
        body_emphasis=0.88,
        brightness_emphasis=1.18,
        cooling_emphasis=0.90,
        harshness_ceiling=0.58,
        coil_risk_ceiling=0.68,
        topnote_emphasis=1.15,
    ),
    "tobacco": _p(
        display_name="MTL · Tobacco",
        total_uplift_band=(0.010, 0.065),
        saturation_target=(0.44, 0.74),
        sweetness_emphasis=0.95,
        body_emphasis=1.15,
        brightness_emphasis=0.72,
        cooling_emphasis=0.78,
        harshness_ceiling=0.52,
        coil_risk_ceiling=0.62,
        topnote_emphasis=0.85,
    ),
    "cooling_focused": _p(
        display_name="MTL · Cooling-forward",
        total_uplift_band=(0.012, 0.070),
        saturation_target=(0.46, 0.76),
        sweetness_emphasis=0.98,
        body_emphasis=0.92,
        brightness_emphasis=1.02,
        cooling_emphasis=1.05,
        harshness_ceiling=0.54,
        coil_risk_ceiling=0.64,
        topnote_emphasis=1.05,
    ),
    "mixed": _p(
        display_name="MTL · Mixed / balanced",
        total_uplift_band=(0.012, 0.075),
        saturation_target=(0.46, 0.78),
        sweetness_emphasis=1.0,
        body_emphasis=1.0,
        brightness_emphasis=1.0,
        cooling_emphasis=0.95,
        harshness_ceiling=0.60,
        coil_risk_ceiling=0.68,
        topnote_emphasis=1.0,
    ),
}

# --- Pod/Dispo: higher uplift band; saturation-first; family tilts emphasis ---
FAMILY_TARGETS_POD_DISPO: Dict[str, FamilyProfile] = {
    "fruit_candy": _p(
        display_name="Pod/Dispo · Fruit / Candy",
        total_uplift_band=(0.045, 0.180),
        saturation_target=(0.58, 0.92),
        sweetness_emphasis=1.08,
        body_emphasis=1.02,
        brightness_emphasis=1.15,
        cooling_emphasis=0.95,
        harshness_ceiling=0.54,
        coil_risk_ceiling=0.58,
        topnote_emphasis=1.28,
    ),
    "dessert_bakery": _p(
        display_name="Pod/Dispo · Dessert / Bakery",
        total_uplift_band=(0.038, 0.150),
        saturation_target=(0.52, 0.88),
        sweetness_emphasis=1.18,
        body_emphasis=1.22,
        brightness_emphasis=0.85,
        cooling_emphasis=0.88,
        harshness_ceiling=0.56,
        coil_risk_ceiling=0.60,
        topnote_emphasis=0.95,
    ),
    "beverage": _p(
        display_name="Pod/Dispo · Beverage",
        total_uplift_band=(0.040, 0.165),
        saturation_target=(0.54, 0.90),
        sweetness_emphasis=1.05,
        body_emphasis=0.95,
        brightness_emphasis=1.20,
        cooling_emphasis=0.92,
        harshness_ceiling=0.54,
        coil_risk_ceiling=0.58,
        topnote_emphasis=1.18,
    ),
    "tobacco": _p(
        display_name="Pod/Dispo · Tobacco",
        total_uplift_band=(0.032, 0.125),
        saturation_target=(0.48, 0.82),
        sweetness_emphasis=0.98,
        body_emphasis=1.20,
        brightness_emphasis=0.70,
        cooling_emphasis=0.82,
        harshness_ceiling=0.48,
        coil_risk_ceiling=0.55,
        topnote_emphasis=0.88,
    ),
    "cooling_focused": _p(
        display_name="Pod/Dispo · Cooling-forward",
        total_uplift_band=(0.038, 0.145),
        saturation_target=(0.52, 0.86),
        sweetness_emphasis=1.02,
        body_emphasis=1.00,
        brightness_emphasis=1.00,
        cooling_emphasis=1.08,
        harshness_ceiling=0.50,
        coil_risk_ceiling=0.56,
        topnote_emphasis=1.08,
    ),
    "mixed": _p(
        display_name="Pod/Dispo · Mixed / balanced",
        total_uplift_band=(0.038, 0.155),
        saturation_target=(0.54, 0.90),
        sweetness_emphasis=1.05,
        body_emphasis=1.08,
        brightness_emphasis=1.00,
        cooling_emphasis=0.92,
        harshness_ceiling=0.55,
        coil_risk_ceiling=0.58,
        topnote_emphasis=1.10,
    ),
}

MODE_FAMILY_TABLE: Dict[str, Dict[str, FamilyProfile]] = {
    "MTL": FAMILY_TARGETS_MTL,
    "POD_DISPO": FAMILY_TARGETS_POD_DISPO,
}


def get_family_target_profile(mode_key: str, dominant_family: str) -> FamilyProfile:
    """
    Return the family-aware target profile for this mode + dominant family.
    Unknown families fall back to "mixed".
    DTL: informational only — returns a neutral profile (allocator does not use it yet).
    """
    raw = str(mode_key or "").strip().upper()
    if raw == "DTL":
        fam = str(dominant_family or "mixed").strip().lower()
        if fam not in RECIPE_FAMILY_KEYS:
            fam = "mixed"
        base = dict(FAMILY_TARGETS_MTL.get(fam) or FAMILY_TARGETS_MTL["mixed"])
        base["display_name"] = f"DTL (info only) · {base['display_name'].split('·')[-1].strip()}"
        return base

    mk = "POD_DISPO" if raw in {"POD_DISPO", "POD", "DISPO", "POD/DISPO"} else raw
    if mk not in MODE_FAMILY_TABLE:
        mk = "MTL"
    fam = str(dominant_family or "mixed").strip().lower()
    if fam not in RECIPE_FAMILY_KEYS:
        fam = "mixed"
    table = MODE_FAMILY_TABLE[mk]
    return dict(table.get(fam) or table["mixed"])


def get_family_target_profile_key(mode_key: str, dominant_family: str) -> str:
    raw = str(mode_key or "").strip().upper()
    if raw == "DTL":
        fam = str(dominant_family or "mixed").strip().lower()
        if fam not in RECIPE_FAMILY_KEYS:
            fam = "mixed"
        return f"DTL|info|{fam}"

    mk = "POD_DISPO" if raw in {"POD_DISPO", "POD", "DISPO", "POD/DISPO"} else raw
    if mk not in MODE_FAMILY_TABLE:
        mk = "MTL"
    fam = str(dominant_family or "mixed").strip().lower()
    if fam not in RECIPE_FAMILY_KEYS:
        fam = "mixed"
    return f"{mk}|{fam}"


def preview_label_for_target_mode(target_mode: str) -> str:
    """When target is DTL, family rows are informational only (no MTL/Pod family profile applied)."""
    tm = str(target_mode or "").strip().upper()
    if tm in {"DTL"}:
        return "DTL — family profile not applied (no conversion)"
    return ""

