"""
Target signal profiles for conversion preview.

Heuristic and explainable:
- DTL: no conversion.
- MTL: total flavor still scaled by ~0.72, then distribution moves toward MTL target bands.
- Pod/Dispo: total flavour is target-driven (see `total_flavor_*` on POD_DISPO) — increased
  saturation into the 8.5–14% band, not globally reduced. Distribution shifts toward Pod/Dispo
  signal targets with role-based scaling.
"""

from __future__ import annotations


TARGET_SIGNAL_PROFILES = {
    # DTL aims for a balanced, layered “as the recipe is” feel.
    "DTL": {
        "top_note_score": (0.40, 0.65),
        "body_score": (0.35, 0.65),
        "sweetness_score": (0.18, 0.45),
        "cooling_score": (0.00, 0.25),
        "saturation_score": (0.35, 0.70),
        "harshness_risk_max": 0.70,
        "coil_risk_max": 0.70,
        # Category emphasis weights used when adjusting allocation distribution.
        "emphasis": {"top": 1.0, "body": 1.0, "sweet": 1.0, "cooling": 0.6},
    },
    # MTL tightens focus and clarity; it should feel less “diffuse”.
    "MTL": {
        "top_note_score": (0.48, 0.72),
        "body_score": (0.32, 0.60),
        "sweetness_score": (0.20, 0.48),
        # Keep cooling under control; too much tends to become sharp.
        "cooling_score": (0.00, 0.20),
        "saturation_score": (0.45, 0.78),
        "harshness_risk_max": 0.62,
        "coil_risk_max": 0.70,
        "emphasis": {"top": 1.25, "body": 0.95, "sweet": 0.95, "cooling": 0.55},
    },
    # Pod/Dispo: higher flavour load + punch; total % is enforced separately in mixing_engine.
    "POD_DISPO": {
        # Hard floor and preferred band for *sum of flavour %* (not PG/VG).
        "total_flavor_min_pct": 8.0,
        "total_flavor_target_band": (8.5, 14.0),
        # When synthesizing a single aim inside the band (sparse recipes, etc.).
        "total_flavor_aim_pct": 10.5,
        "top_note_score": (0.55, 0.85),
        "body_score": (0.38, 0.70),
        "sweetness_score": (0.18, 0.50),
        # Cooling is selective; too much can raise harshness.
        "cooling_score": (0.00, 0.22),
        "saturation_score": (0.55, 0.90),
        "harshness_risk_max": 0.58,
        "coil_risk_max": 0.62,
        "emphasis": {"top": 1.35, "body": 1.10, "sweet": 1.05, "cooling": 0.60},
        # Role weights for *relative* moves after target total is set (main > support > accent).
        "role_scale_weights": {
            "main_note": 1.28,
            "body": 1.08,
            "sweetener": 1.02,
            "sweetener_support": 1.05,
            "cooling": 0.85,
            "accent": 0.52,
            "default": 0.92,
        },
    },
}

