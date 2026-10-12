"""
Pod/Dispo recipe subtype profiles — tunable layer on top of family-aware targets.

Used only when target mode is POD_DISPO. Does not replace family detection;
it refines uplift, emphasis, commercial merge keys, and perceptual dominance.

Canonical keys (pod_subtype) match user-facing names:
  candy_fruit_dispo, cool_fruit_dispo, creamy_fruit_dispo, beverage_dispo,
  dessert_dispo, tobacco_pod, general_dispo
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# --- Canonical subtype → merge key for config/dispo_commercial_profiles.merge_disposubtype_into_targets
SUBTYPE_MERGE_KEY: Dict[str, str | None] = {
    "candy_fruit_dispo": "candy_fruit",
    "cool_fruit_dispo": "cool_fruit",
    "creamy_fruit_dispo": "creamy_fruit",
    "beverage_dispo": "beverage",
    "dessert_dispo": "light_dessert",
    "tobacco_pod": "tobacco",
    "general_dispo": None,
}

# Emphasis multipliers applied on top of get_family_target_profile() (Pod table).
# total_uplift_scale: multiply (lo, hi) of total_uplift_band.
# saturation_shift: add to (lo, hi) of saturation_target (clamped).
PodSubtypeProfile = Dict[str, Any]

POD_SUBTYPE_PROFILES: Dict[str, PodSubtypeProfile] = {
    "candy_fruit_dispo": {
        "display_name": "Candy fruit · Pod/Dispo",
        "description": "Strong top-note punch, high sweetness support, high saturation bias.",
        "emphasis": {
            "topnote_emphasis": 1.12,
            "sweetness_emphasis": 1.14,
            "brightness_emphasis": 1.10,
            "body_emphasis": 0.94,
            "cooling_emphasis": 0.94,
        },
        "total_uplift_scale": (1.04, 1.10),
        "saturation_shift": (0.02, 0.03),
        "harshness_ceiling_shift": 0.0,
        "coil_risk_ceiling_shift": 0.0,
        "perceptual": {
            "label_exp_scale": 1.14,
            "body_log_scale": 1.05,
            "hidden_cap_strength": 1.12,
            "min_label_share_base": 0.34,
            "min_label_share_imp_scale": 0.20,
            "high_out_scale": 1.12,
        },
        "fit_score_bonus": 6.0,
        "fit_note": "Candy-fruit dispo profile: punchy, sweet-forward conversion.",
    },
    "cool_fruit_dispo": {
        "display_name": "Cool fruit · Pod/Dispo",
        "description": "Strong top punch with controlled cooling allowance and tighter harshness bias.",
        "emphasis": {
            "topnote_emphasis": 1.10,
            "sweetness_emphasis": 1.04,
            "brightness_emphasis": 1.06,
            "body_emphasis": 0.96,
            "cooling_emphasis": 1.12,
        },
        "total_uplift_scale": (1.02, 1.08),
        "saturation_shift": (0.01, 0.02),
        "harshness_ceiling_shift": -0.03,
        "coil_risk_ceiling_shift": -0.02,
        "perceptual": {
            "label_exp_scale": 1.10,
            "body_log_scale": 1.04,
            "hidden_cap_strength": 1.08,
            "min_label_share_base": 0.32,
            "min_label_share_imp_scale": 0.18,
            "high_out_scale": 1.08,
        },
        "fit_score_bonus": 4.0,
        "fit_note": "Cool-fruit dispo profile: WS/menthol-aware, still label-forward.",
    },
    "creamy_fruit_dispo": {
        "display_name": "Creamy fruit · Pod/Dispo",
        "description": "Label note preserved with more room for body/cream support; moderate top forcing.",
        "emphasis": {
            "topnote_emphasis": 1.02,
            "sweetness_emphasis": 1.08,
            "brightness_emphasis": 0.94,
            "body_emphasis": 1.14,
            "cooling_emphasis": 0.96,
        },
        "total_uplift_scale": (1.03, 1.09),
        "saturation_shift": (0.01, 0.02),
        "harshness_ceiling_shift": 0.0,
        "coil_risk_ceiling_shift": 0.0,
        "perceptual": {
            "label_exp_scale": 1.04,
            "body_log_scale": 0.92,
            "hidden_cap_strength": 0.88,
            "min_label_share_base": 0.26,
            "min_label_share_imp_scale": 0.14,
            "high_out_scale": 0.96,
        },
        "fit_score_bonus": 3.0,
        "fit_note": "Creamy-fruit dispo profile: balance label with cream/body bridge.",
    },
    "beverage_dispo": {
        "display_name": "Beverage · Pod/Dispo",
        "description": "Brightness and clean top clarity; moderate body.",
        "emphasis": {
            "topnote_emphasis": 1.10,
            "sweetness_emphasis": 1.05,
            "brightness_emphasis": 1.16,
            "body_emphasis": 0.92,
            "cooling_emphasis": 0.98,
        },
        "total_uplift_scale": (1.03, 1.09),
        "saturation_shift": (0.01, 0.025),
        "harshness_ceiling_shift": -0.02,
        "coil_risk_ceiling_shift": 0.0,
        "perceptual": {
            "label_exp_scale": 1.08,
            "body_log_scale": 1.06,
            "hidden_cap_strength": 1.06,
            "min_label_share_base": 0.30,
            "min_label_share_imp_scale": 0.17,
            "high_out_scale": 1.10,
        },
        "fit_score_bonus": 5.0,
        "fit_note": "Beverage dispo profile: clarity-first, bright top structure.",
    },
    "dessert_dispo": {
        "display_name": "Dessert · Pod/Dispo",
        "description": "More body density and sweet backbone; less aggressive top forcing.",
        "emphasis": {
            "topnote_emphasis": 0.92,
            "sweetness_emphasis": 1.12,
            "brightness_emphasis": 0.82,
            "body_emphasis": 1.18,
            "cooling_emphasis": 0.90,
        },
        "total_uplift_scale": (1.02, 1.08),
        "saturation_shift": (0.0, 0.02),
        "harshness_ceiling_shift": 0.02,
        "coil_risk_ceiling_shift": 0.02,
        "perceptual": {
            "label_exp_scale": 0.88,
            "body_log_scale": 0.98,
            "hidden_cap_strength": 0.82,
            "min_label_share_base": 0.22,
            "min_label_share_imp_scale": 0.12,
            "high_out_scale": 0.88,
        },
        "fit_score_bonus": 2.0,
        "fit_note": "Dessert dispo profile: density and sweetness over sharp top chase.",
    },
    "tobacco_pod": {
        "display_name": "Tobacco · Pod",
        "description": "Strong body, low brightness push, strict harshness control; restrained label forcing.",
        "emphasis": {
            "topnote_emphasis": 0.82,
            "sweetness_emphasis": 0.96,
            "brightness_emphasis": 0.72,
            "body_emphasis": 1.22,
            "cooling_emphasis": 0.88,
        },
        "total_uplift_scale": (0.96, 1.02),
        "saturation_shift": (-0.02, 0.0),
        "harshness_ceiling_shift": -0.04,
        "coil_risk_ceiling_shift": -0.03,
        "perceptual": {
            "label_exp_scale": 0.72,
            "body_log_scale": 1.08,
            "hidden_cap_strength": 0.78,
            "min_label_share_base": 0.18,
            "min_label_share_imp_scale": 0.10,
            "high_out_scale": 0.72,
        },
        "fit_score_bonus": -6.0,
        "fit_note": "Tobacco pod profile: conservative boosts; preserve accents.",
    },
    "general_dispo": {
        "display_name": "General · Pod/Dispo",
        "description": "Balanced commercial fallback when subtype is unclear.",
        "emphasis": {
            "topnote_emphasis": 1.0,
            "sweetness_emphasis": 1.0,
            "brightness_emphasis": 1.0,
            "body_emphasis": 1.0,
            "cooling_emphasis": 1.0,
        },
        "total_uplift_scale": (1.0, 1.0),
        "saturation_shift": (0.0, 0.0),
        "harshness_ceiling_shift": 0.0,
        "coil_risk_ceiling_shift": 0.0,
        "perceptual": {
            "label_exp_scale": 1.0,
            "body_log_scale": 1.0,
            "hidden_cap_strength": 1.0,
            "min_label_share_base": 0.28,
            "min_label_share_imp_scale": 0.16,
            "high_out_scale": 1.0,
        },
        "fit_score_bonus": 0.0,
        "fit_note": "General dispo profile: balanced subtype fallback.",
    },
}


def _clamp_band(lo: float, hi: float) -> Tuple[float, float]:
    lo, hi = float(lo), float(hi)
    if hi - lo < 0.02:
        hi = lo + 0.02
    return lo, hi


def apply_pod_subtype_to_family_profile(
    base: Dict[str, Any],
    pod_subtype: str,
) -> Dict[str, Any]:
    """Return a new family profile dict with subtype emphasis / bands applied."""
    out = dict(base)
    prof = POD_SUBTYPE_PROFILES.get(pod_subtype) or POD_SUBTYPE_PROFILES["general_dispo"]
    em = prof.get("emphasis") or {}
    for k in ("topnote_emphasis", "sweetness_emphasis", "body_emphasis", "brightness_emphasis", "cooling_emphasis"):
        if k in em:
            out[k] = float(out.get(k) or 1.0) * float(em[k])

    tub = out.get("total_uplift_band")
    if isinstance(tub, tuple) and len(tub) == 2:
        sl, sh = prof.get("total_uplift_scale") or (1.0, 1.0)
        out["total_uplift_band"] = _clamp_band(float(tub[0]) * float(sl), float(tub[1]) * float(sh))

    sat = out.get("saturation_target")
    if isinstance(sat, tuple) and len(sat) == 2:
        dlo, dhi = prof.get("saturation_shift") or (0.0, 0.0)
        out["saturation_target"] = _clamp_band(
            max(0.0, min(0.98, float(sat[0]) + float(dlo))),
            max(0.02, min(1.0, float(sat[1]) + float(dhi))),
        )

    out["harshness_ceiling"] = max(
        0.25,
        min(0.85, float(out.get("harshness_ceiling") or 0.55) + float(prof.get("harshness_ceiling_shift") or 0.0)),
    )
    out["coil_risk_ceiling"] = max(
        0.25,
        min(0.85, float(out.get("coil_risk_ceiling") or 0.58) + float(prof.get("coil_risk_ceiling_shift") or 0.0)),
    )

    out["pod_subtype_display_name"] = prof.get("display_name")
    out["pod_subtype_key"] = pod_subtype
    return out


def get_pod_perceptual_config(pod_subtype: str) -> Dict[str, float]:
    prof = POD_SUBTYPE_PROFILES.get(pod_subtype) or POD_SUBTYPE_PROFILES["general_dispo"]
    perc = prof.get("perceptual") or {}
    base = POD_SUBTYPE_PROFILES["general_dispo"].get("perceptual") or {}
    out = dict(base)
    out.update({k: float(v) for k, v in perc.items()})
    return out


def get_subtype_fit_bonus_and_note(pod_subtype: str) -> Tuple[float, str]:
    prof = POD_SUBTYPE_PROFILES.get(pod_subtype) or POD_SUBTYPE_PROFILES["general_dispo"]
    return float(prof.get("fit_score_bonus") or 0.0), str(prof.get("fit_note") or "")


def _rw(role: str) -> float:
    r = str(role or "").strip().lower()
    if r == "main_note":
        return 1.0
    if r == "accent":
        return 0.32
    if r == "body":
        return 0.82
    if r in {"sweetener", "sweetener_support"}:
        return 0.68
    if r == "cooling":
        return 0.90
    return 0.52


def detect_pod_subtype(
    *,
    fam_info: Dict[str, Any],
    rows: List[Dict[str, Any]],
    items_by_id: Dict[int, Dict[str, Any]],
    dispo_fp: Dict[str, Any] | None,
    original_total_pct: float,
) -> Dict[str, Any]:
    """
    Weighted-by-% subtype detection. Returns:
      pod_subtype, pod_subtype_confidence (0..1), pod_subtype_reasoning (bullets),
      merge_key (for commercial target merge), display_name
    """
    total = max(1e-9, float(original_total_pct))
    dom = str(fam_info.get("dominant_family") or "mixed").strip().lower()
    sec = fam_info.get("secondary_family")

    m_tobacco = m_fruit = m_candy = m_cream = m_cool = m_bev = m_dessert = 0.0
    for r in rows:
        rid = int(r["ingredient_id"])
        pct = float(r.get("original_pct") or r.get("amount_percent") or 0.0)
        if pct <= 1e-12:
            continue
        meta = items_by_id.get(rid) or {}
        role = str(meta.get("resolved_role") or "").strip().lower()
        prof = str(meta.get("resolved_profile") or "").strip().lower()
        w = _rw(role) * pct

        if prof == "tobacco" or "tobacco" in prof.split():
            m_tobacco += w
        if prof in {"fruit", "tropical", "fruit_candy"}:
            m_fruit += w
        if prof in {"candy", "fruit_candy"}:
            m_candy += w
        if prof in {"cream", "custard"}:
            m_cream += w
        if role == "cooling" or prof == "cooling":
            m_cool += w
        if prof == "beverage":
            m_bev += w
        if prof in {"bakery", "dessert", "dessert_bakery"}:
            m_dessert += w

    tw = sum(
        [
            m_tobacco,
            m_fruit,
            m_candy,
            m_cream,
            m_cool,
            m_bev,
            m_dessert,
        ]
    ) or 1e-9

    def n(x: float) -> float:
        return max(0.0, float(x)) / tw

    cool_share = m_cool / total
    cool_score = float(dispo_fp.get("cooling_presentation_score") or 0.0) if dispo_fp else 0.0
    fruit_any = m_fruit + m_candy > 1e-6

    scores: Dict[str, float] = {
        "tobacco_pod": 2.2 * n(m_tobacco) + (1.5 if dom == "tobacco" else 0.0) + 0.4 * (1.0 if sec == "tobacco" else 0.0),
        "candy_fruit_dispo": 1.8 * n(m_candy) * n(m_fruit) * 2.5 + (0.8 if dom == "fruit_candy" else 0.0),
        "cool_fruit_dispo": (1.6 if (cool_share >= 0.07 or cool_score > 0.15) and fruit_any else 0.0)
        * (n(m_fruit) + n(m_candy) + 0.3)
        + (0.6 if dom == "cooling_focused" else 0.0),
        "creamy_fruit_dispo": 1.9 * n(m_cream) * (n(m_fruit) + n(m_candy) + 0.15) + (0.5 if dom == "fruit_candy" else 0.0),
        "beverage_dispo": 2.0 * n(m_bev) + (1.0 if dom == "beverage" else 0.0),
        "dessert_dispo": 1.7 * n(m_dessert) + (1.1 if dom == "dessert_bakery" else 0.0) + 0.3 * n(m_cream),
        "general_dispo": 0.22,
    }

    # Disambiguation: cool fruit vs candy fruit when both score
    if scores["cool_fruit_dispo"] > 0 and scores["candy_fruit_dispo"] > scores["cool_fruit_dispo"]:
        if cool_share < 0.055 and cool_score < 0.13:
            scores["cool_fruit_dispo"] *= 0.45

    if scores["creamy_fruit_dispo"] > scores["candy_fruit_dispo"] and n(m_cream) > 0.12:
        scores["candy_fruit_dispo"] *= 0.55

    best_key = max(scores, key=lambda k: scores[k])
    best = scores[best_key]
    sorted_vals = sorted(scores.values(), reverse=True)
    second = sorted_vals[1] if len(sorted_vals) > 1 else 0.0
    margin = (best - second) / max(best, 0.08)
    confidence = max(0.38, min(0.94, 0.42 + 0.48 * _clamp01(margin)))

    if best < 0.42 and best_key != "general_dispo":
        best_key = "general_dispo"
        confidence = max(0.38, confidence * 0.88)

    reasoning: List[str] = [
        f"Weighted masses (role-weighted): tobacco {m_tobacco/total*100:.1f}%, "
        f"fruit/candy {((m_fruit+m_candy)/total*100):.1f}%, cream {m_cream/total*100:.1f}%, "
        f"cooling {m_cool/total*100:.1f}%, beverage {m_bev/total*100:.1f}%, dessert {m_dessert/total*100:.1f}%."
    ]
    reasoning.append(f"Dominant family: {dom}" + (f" (secondary: {sec})" if sec else "") + ".")
    reasoning.append(f"Cooling share {cool_share*100:.1f}% · cooling presentation score {cool_score:.2f}.")
    reasoning.append(f"Selected subtype '{best_key}' (score {best:.2f} vs runner-up {second:.2f}).")

    prof_meta = POD_SUBTYPE_PROFILES.get(best_key, POD_SUBTYPE_PROFILES["general_dispo"])
    return {
        "pod_subtype": best_key,
        "pod_subtype_confidence": round(confidence, 4),
        "pod_subtype_reasoning": reasoning,
        "pod_subtype_merge_key": SUBTYPE_MERGE_KEY.get(best_key),
        "pod_subtype_display_name": prof_meta.get("display_name"),
        "pod_subtype_description": prof_meta.get("description"),
    }


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))
