"""
Commercial-style Pod/Dispo target fingerprints (bands 0..1 unless noted).

Used only by Pod/Dispo conversion — DTL/MTL unchanged.
Subtype profiles blend with GENERAL (65% general / 35% subtype) for target bands.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

# General “commercial disposable” feel: punchy, saturated, sweet backbone, controlled cooling.
DISPO_COMMERCIAL_GENERAL: Dict[str, Any] = {
    "impact_score": (0.46, 0.88),
    "saturation_score": (0.52, 0.92),
    "sweet_backbone_score": (0.35, 0.72),
    "body_density_score": (0.36, 0.74),
    "cooling_presentation_score": (0.05, 0.26),
    "topnote_punch_score": (0.50, 0.88),
    "harshness_risk_max": 0.55,
    "coil_risk_max": 0.60,
}

# Optional subtypes: overrides merged into GENERAL (weighted).
DISPO_SUBTYPE_PROFILES: Dict[str, Dict[str, Tuple[float, float] | float]] = {
    "candy_fruit": {
        "impact_score": (0.52, 0.92),
        "sweet_backbone_score": (0.45, 0.80),
        "topnote_punch_score": (0.58, 0.92),
        "cooling_presentation_score": (0.04, 0.22),
        "harshness_risk_max": 0.52,
    },
    "cool_fruit": {
        "cooling_presentation_score": (0.12, 0.32),
        "topnote_punch_score": (0.48, 0.85),
        "impact_score": (0.44, 0.86),
        "harshness_risk_max": 0.50,
    },
    "creamy_fruit": {
        "body_density_score": (0.42, 0.78),
        "sweet_backbone_score": (0.38, 0.70),
        "topnote_punch_score": (0.46, 0.82),
        "impact_score": (0.42, 0.82),
    },
    "beverage": {
        "body_density_score": (0.32, 0.68),
        "sweet_backbone_score": (0.40, 0.75),
        "topnote_punch_score": (0.48, 0.85),
        "impact_score": (0.45, 0.85),
    },
    "light_dessert": {
        "body_density_score": (0.40, 0.72),
        "sweet_backbone_score": (0.36, 0.68),
        "saturation_score": (0.46, 0.82),
        "impact_score": (0.40, 0.78),
        "harshness_risk_max": 0.54,
    },
    "tobacco": {
        "body_density_score": (0.44, 0.78),
        "sweet_backbone_score": (0.30, 0.62),
        "topnote_punch_score": (0.36, 0.72),
        "impact_score": (0.38, 0.74),
        "saturation_score": (0.46, 0.82),
        "cooling_presentation_score": (0.04, 0.22),
        "harshness_risk_max": 0.46,
        "coil_risk_max": 0.54,
    },
}

GENERAL_WEIGHT = 0.65
SUBTYPE_WEIGHT = 0.35


def _merge_band(
    lo_g: float, hi_g: float, lo_s: float | None, hi_s: float | None
) -> Tuple[float, float]:
    if lo_s is None or hi_s is None:
        return (lo_g, hi_g)
    lo = GENERAL_WEIGHT * lo_g + SUBTYPE_WEIGHT * lo_s
    hi = GENERAL_WEIGHT * hi_g + SUBTYPE_WEIGHT * hi_s
    lo, hi = min(lo, hi), max(lo, hi)
    if hi - lo < 0.02:
        hi = lo + 0.02
    return (lo, hi)


def _merge_ceiling(g: float, s: float | None) -> float:
    if s is None:
        return g
    return GENERAL_WEIGHT * g + SUBTYPE_WEIGHT * float(s)


def merge_disposubtype_into_targets(subtype_key: str | None) -> Dict[str, Any]:
    """Full commercial target dict for Pod conversion (bands + ceilings)."""
    out: Dict[str, Any] = dict(DISPO_COMMERCIAL_GENERAL)
    sub = DISPO_SUBTYPE_PROFILES.get(subtype_key or "", {})
    if not sub:
        return out

    for key, val in sub.items():
        if key in ("harshness_risk_max", "coil_risk_max"):
            out[key] = _merge_ceiling(float(out[key]), float(val) if val is not None else None)
            continue
        if isinstance(val, tuple) and len(val) == 2:
            g = out.get(key)
            if isinstance(g, tuple) and len(g) == 2:
                out[key] = _merge_band(float(g[0]), float(g[1]), float(val[0]), float(val[1]))
    return out

