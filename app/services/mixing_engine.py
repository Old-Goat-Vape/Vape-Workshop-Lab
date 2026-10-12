import json
import math
import uuid
from datetime import datetime
from typing import Any, Dict, List, Tuple

from db import get_db
from models import inventory_repo, mix_logs_repo, recipes_repo, users_repo

from config.conversion_family_profiles import (
    RECIPE_FAMILY_KEYS,
    get_family_target_profile,
    get_family_target_profile_key,
    preview_label_for_target_mode,
)
from config.conversion_target_profiles import TARGET_SIGNAL_PROFILES
from config.dispo_commercial_profiles import merge_disposubtype_into_targets
from config.dispo_subtype_profiles import (
    apply_pod_subtype_to_family_profile,
    detect_pod_subtype,
    get_pod_perceptual_config,
    get_subtype_fit_bonus_and_note,
)
from core.calculator import build_guided_narrative_steps, build_tolerance_profile, evaluate_overpour, tvw_mix_policy


# ------------------------------------
# Domain configuration / constants
# ------------------------------------

ROLE_BATCH_LIMITS: Dict[str, int] = {
    "Kid": 100,
    "Hand": 250,
    "Mixer": 500,
    "Ram": 999_999,
    "Foreman": 999_999,
    "Old Goat": 999_999,
    "Master": 999_999,
}

STYLE_CONFIG: Dict[str, Dict[str, Any]] = {
    # Retail bottle sizing and ratios.
    # Long Fill uses a smaller "fill" volume per bottle (customer adds VG/nic later).
    # DTL longfill uses an "effective" volume for vessel planning (40ml) while only producing 30ml.
    "MTL": {
        "retail_bottle_ml": 30.0,
        "longfill_fill_ml": 15.0,
        "longfill_effective_ml": 15.0,
        "fullmix_bottle_ml": 30.0,
        "pg_ratio": 0.50,
        "vg_ratio": 0.50,
    },
    "DTL": {
        "retail_bottle_ml": 120.0,
        "longfill_fill_ml": 30.0,
        "longfill_effective_ml": 30.0,
        "fullmix_bottle_ml": 120.0,
        "pg_ratio": 0.30,
        "vg_ratio": 0.70,
    },
    "POD_DISPO": {
        "retail_bottle_ml": 30.0,
        "longfill_fill_ml": 15.0,
        "longfill_effective_ml": 15.0,
        "fullmix_bottle_ml": 30.0,
        "pg_ratio": 0.50,
        "vg_ratio": 0.50,
    },
}

MIX_MODE_LONGFILL = "longfill"
MIX_MODE_FULL = "fullmix"
FULL_MIX_MIN_LEVEL = 5  # Foreman / Old Goat / Master

TOTAL_WEIGHT_EPS_G = 0.01

REQUIRED_SAFETY_FLAGS = [
    "hands_washed",
    "gloves_worn",
    "equipment_clean",
    "bottles_clean",
    "ingredients_in_stock",
    "scale_tared",
    "vessel_selected",
]

# Density fallback used when deducting stock by grams -> ml
DEFAULT_DENSITY_G_PER_ML = 1.036
SOURCE_RECIPE_MODE_DEFAULT = "DTL"
TARGET_MODE_DTL = "DTL"
TARGET_MODE_MTL = "MTL"
TARGET_MODE_POD = "POD_DISPO"
# Master-only fine-tuning: only ROLE_HIERARCHY >= Master is allowed.
MASTER_MIN_LEVEL = 7


def _normalize_mix_mode(value: str | None) -> str:
    mode = str(value or MIX_MODE_LONGFILL).strip().lower()
    if mode in {MIX_MODE_LONGFILL, MIX_MODE_FULL}:
        return mode
    return MIX_MODE_LONGFILL


def _normalize_style(value: str | None) -> str:
    style = str(value or "MTL").strip().upper()
    if style in STYLE_CONFIG:
        return style
    return "MTL"


def _build_workflow_profile(*, style: str, mix_mode: str, vessel_size: float) -> dict[str, Any]:
    cfg = STYLE_CONFIG.get(style) or STYLE_CONFIG["MTL"]
    vessel_ml = max(0.0, float(vessel_size or 0.0))

    if mix_mode == MIX_MODE_LONGFILL:
        # MTL long fill: 15ml base for a 30ml retail bottle (customer tops up later).
        # DTL long fill: 30ml base for a 120ml retail bottle. With +10ml PG nic and +80ml VG top-up,
        # practical final ratio is ~33/67, which is intentional for this workflow.
        fill_per_unit_ml = float(cfg["longfill_fill_ml"])
        units = int(math.floor(vessel_ml / fill_per_unit_ml)) if fill_per_unit_ml > 0 else 0
        used_volume_ml = round(units * fill_per_unit_ml, 4)
        return {
            "style": style,
            "mix_mode": mix_mode,
            "is_longfill": True,
            "decanting_enabled": True,
            "headroom_applies": True,
            "include_vg_in_lab_mix": False,
            "retail_bottle_size_ml": float(cfg["retail_bottle_ml"]),
            "lab_fill_per_unit_ml": fill_per_unit_ml,
            "units_produced": units,
            "used_volume_ml": used_volume_ml,
            "headroom_ml": round(max(0.0, vessel_ml - used_volume_ml), 4),
            "total_mix_volume_ml": used_volume_ml,
        }

    return {
        "style": style,
        "mix_mode": mix_mode,
        "is_longfill": False,
        "decanting_enabled": False,
        "headroom_applies": False,
        "include_vg_in_lab_mix": True,
        "retail_bottle_size_ml": vessel_ml,
        "lab_fill_per_unit_ml": vessel_ml,
        "units_produced": 1,
        "used_volume_ml": vessel_ml,
        "headroom_ml": 0.0,
        "total_mix_volume_ml": vessel_ml,
    }


def _normalize_target_mix_mode(value: str | None) -> str:
    v = str(value or TARGET_MODE_DTL).strip().upper()
    if v in {"POD", "POD/DISPO", "POD_DISPO", "DISPO"}:
        return TARGET_MODE_POD
    if v == TARGET_MODE_MTL:
        return TARGET_MODE_MTL
    return TARGET_MODE_DTL


def _conversion_factor_for_target(target_mix_mode: str) -> float:
    """Uniform scale factor for MTL pre-baseline only. Pod/Dispo uses target totals, not this factor."""
    if target_mix_mode == TARGET_MODE_MTL:
        return 0.72
    if target_mix_mode == TARGET_MODE_POD:
        return 1.0
    return 1.0


def _pod_dispo_target_total_and_meta(original_total: float) -> tuple[float, dict[str, Any]]:
    """
    Pod/Dispo: drive total flavour % toward saturation (higher load), not DTL scaled down.

    - Hard minimum 8%; preferred band 8.5–14% from TARGET_SIGNAL_PROFILES.
    - Below minimum: scale up into the band.
    - Inside band: modest boost (especially when lower in the band).
    - Above band: cap at ceiling (extreme guardrail only).

    Example (fruit + cream ~9.65%): total moves toward ~10.8–11% with mains boosted more than accents.
    """
    prof = TARGET_SIGNAL_PROFILES["POD_DISPO"]
    tmin = float(prof["total_flavor_min_pct"])
    band_lo, band_hi = float(prof["total_flavor_target_band"][0]), float(prof["total_flavor_target_band"][1])
    aim = float(prof.get("total_flavor_aim_pct") or (band_lo + band_hi) / 2.0)
    ot = max(0.0, float(original_total))
    notes: list[str] = []

    if ot < 1e-9:
        target = aim
        notes.append(
            f"Pod/Dispo target saturation {target:.2f}% (band {band_lo}–{band_hi}%); recipe had 0% flavour total."
        )
    elif ot < tmin:
        target = min(band_hi, max(band_lo, ot * (band_lo / max(ot, 1e-9))))
        if target < tmin:
            target = tmin
        notes.append(
            f"Scaled total flavour from {ot:.2f}% up to {target:.2f}% (floor {tmin}%, target band {band_lo}–{band_hi}%)."
        )
    elif ot < band_lo:
        target = band_lo + 0.35 * (aim - band_lo)
        notes.append(
            f"Raised total from {ot:.2f}% to {target:.2f}% to enter Pod/Dispo band ({band_lo}–{band_hi}%)."
        )
    elif ot <= band_hi:
        span = max(1e-9, band_hi - band_lo)
        pos = (ot - band_lo) / span
        boost = 1.0 + (1.0 - pos) * 0.12 + 0.05
        target = min(band_hi, ot * boost)
        if target <= ot + 1e-6:
            target = min(band_hi, ot * 1.06)
        notes.append(
            f"Pod/Dispo saturation: {ot:.2f}% → {target:.2f}% (target band {band_lo}–{band_hi}%; increased load)."
        )
    else:
        target = band_hi
        notes.append(
            f"Total {ot:.2f}% capped to {band_hi}% for Pod/Dispo ceiling (guardrail); relative balance kept."
        )

    return round(target, 6), {
        "pod_target_saturation_pct": round(target, 4),
        "pod_target_band_pct": [band_lo, band_hi],
        "pod_minimum_total_flavor_pct": tmin,
        "pod_scaling_notes": notes,
    }


def _pod_role_weight(meta: dict[str, Any], prof: dict[str, Any]) -> float:
    rw = prof.get("role_scale_weights") or {}
    role = str(meta.get("resolved_role") or "").strip().lower()
    if role in rw:
        return float(rw[role])
    return float(rw.get("default", 0.92))


def _reason_for_adjustment(item: Dict[str, Any], target_mix_mode: str) -> str:
    cat = str(item.get("category") or "").lower()
    potency = str(item.get("resolved_potency") or "").lower()
    if target_mix_mode == TARGET_MODE_DTL:
        return "No conversion needed for DTL target."
    if "sweet" in cat:
        return "Reduced for cleaner coil behavior in lower-power devices."
    if "cool" in cat:
        return "Cooling reduced to avoid harshness in tighter airflow."
    if potency == "high":
        return "High-potency profile scaled down for target mode."
    if target_mix_mode == TARGET_MODE_POD:
        return "Pod/Dispo: moved toward higher saturation and target profile (not a global % cut)."
    return "Scaled by mode profile to keep balance in target style."


def _pod_suitability_score(value: str | None) -> float:
    v = str(value or "").strip().lower()
    if v in {"excellent", "top", "ideal"}:
        return 1.0
    if v in {"good", "very good"}:
        return 0.8
    if v in {"fair", "ok", "average"}:
        return 0.6
    if v in {"poor", "weak"}:
        return 0.4
    return 0.5


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _role_is_top(meta: dict[str, Any]) -> bool:
    role = str(meta.get("resolved_role") or "").strip().lower()
    profile = str(meta.get("resolved_profile") or "").strip().lower()
    return role in {"main_note", "accent"} or profile in {"fruit", "tropical", "candy", "beverage", "fruit_candy"}


def _role_is_body(meta: dict[str, Any]) -> bool:
    role = str(meta.get("resolved_role") or "").strip().lower()
    profile = str(meta.get("resolved_profile") or "").strip().lower()
    return role in {"body"} or profile in {"cream", "bakery", "dessert", "dessert_bakery", "custard"}


def _role_is_sweet(meta: dict[str, Any]) -> bool:
    role = str(meta.get("resolved_role") or "").strip().lower()
    profile = str(meta.get("resolved_profile") or "").strip().lower()
    return role in {"sweetener_support", "sweetener"} or profile in {"sweetener"}


def _role_is_cooling(meta: dict[str, Any]) -> bool:
    role = str(meta.get("resolved_role") or "").strip().lower()
    profile = str(meta.get("resolved_profile") or "").strip().lower()
    return role == "cooling" or profile == "cooling"


def _flavor_text_blob(meta: dict[str, Any]) -> str:
    return f"{meta.get('brand') or ''} {meta.get('name') or ''}".lower()


# Perceptual dominance (Pod/Dispo): name + role/profile heuristics — not a rewrite of allocation,
# only multipliers and post-pass caps/redistribution on top of existing budget weights.
_LABEL_DRIVER_KEYWORDS: tuple[str, ...] = (
    "strawberry",
    "strawb",
    "blue raz",
    "blue razz",
    "blue raspberry",
    "blueberry",
    "raspberry",
    "cherry",
    "grape",
    "cola",
    "lemon",
    "lime",
    "orange",
    "mango",
    "peach",
    "apple",
    "pineapple",
    "passion",
    "kiwi",
    "gummy",
    "candy",
    "watermelon",
    "dragonfruit",
    "lychee",
    "pomegranate",
    "fruit mix",
    "slush",
    "soda",
    "energy",
)
_HIDDEN_BODY_KEYWORDS: tuple[str, ...] = (
    "cactus",
    "honeydew",
    "cantaloupe",
    "melon",
    "cream",
    "custard",
    "vanilla bean",
    "whipped",
    "milk",
    "cheesecake",
    "biscuit",
    "cookie",
    "dough",
    "butter",
    "meringue",
    "mousse",
    "pudding",
)


def _label_driver_strength(meta: dict[str, Any]) -> float:
    """
    0..1 — how strongly this line reads as the recipe's 'label' (what you tell customers it is).
    """
    blob = _flavor_text_blob(meta)
    if not blob.strip():
        return 0.0
    hits = 0.0
    for kw in _LABEL_DRIVER_KEYWORDS:
        if kw in blob:
            hits += 1.0
    # "melon" alone is ambiguous; generic melon tends to be filler — exclude watermelon path above
    if "melon" in blob and "watermelon" not in blob and "honeydew" not in blob and "cantaloupe" not in blob:
        hits = max(0.0, hits - 0.5)

    score = min(1.0, 0.14 + 0.22 * hits)
    role = str(meta.get("resolved_role") or "").strip().lower()
    profile = str(meta.get("resolved_profile") or "").strip().lower()
    if role == "main_note" and profile in {"fruit", "tropical", "candy", "beverage", "fruit_candy"}:
        score = max(score, 0.48)
    if role == "main_note" and profile == "beverage":
        score = max(score, 0.55)
    if profile in {"fruit_candy", "candy"} and role in {"main_note", "accent"}:
        score = max(score, 0.42)
    return _clamp01(score)


def _hidden_body_strength(meta: dict[str, Any]) -> float:
    """
    0..1 — 'stealth' body/filler that can read louder than the label on high-output coils if over-boosted.
    """
    blob = _flavor_text_blob(meta)
    score = 0.0
    if "watermelon" in blob:
        return 0.12
    for kw in _HIDDEN_BODY_KEYWORDS:
        if kw == "melon" and "watermelon" in blob:
            continue
        if kw in blob:
            score = max(score, 0.72 if kw in {"cactus", "honeydew", "cantaloupe"} else 0.58)
    if "melon" in blob and "watermelon" not in blob:
        score = max(score, 0.68)

    role = str(meta.get("resolved_role") or "").strip().lower()
    profile = str(meta.get("resolved_profile") or "").strip().lower()
    if role == "body" or profile in {"cream", "custard", "bakery", "dessert", "dessert_bakery"}:
        score = max(score, 0.45)
    return _clamp01(score)


def _pod_perceptual_apply_to_weight(
    w_alloc: float,
    meta: dict[str, Any],
    tw: float,
    bw: float,
    *,
    impact_need_up: float,
    perceptual_cfg: dict[str, float] | None = None,
) -> tuple[float, list[str]]:
    """
    Pod/Dispo only: non-linear emphasis — top/label curves up (exp), body curves down (log),
    hidden-body lines capped in the weight domain before budget shares.
    """
    tags: list[str] = []
    cfg = perceptual_cfg or {}
    les = float(cfg.get("label_exp_scale", 1.0))
    bls = float(cfg.get("body_log_scale", 1.0))
    hcs = float(cfg.get("hidden_cap_strength", 1.0))
    hos = float(cfg.get("high_out_scale", 1.0))
    ld = _label_driver_strength(meta)
    hb = _hidden_body_strength(meta)
    is_top = _role_is_top(meta)
    is_body = _role_is_body(meta)
    imp = _clamp01(float(impact_need_up))

    if is_top:
        if ld >= 0.12:
            ex = math.exp(0.95 * les * ld * (0.55 + 0.45 * tw)) - 1.0
            mult = 1.0 + min(0.95, 0.42 * ex)
            w_alloc *= mult
            tags.append("perceptual label-driver boost (exp)")
        else:
            ex2 = math.exp(0.38 * les * tw) - 1.0
            w_alloc *= 1.0 + min(0.24, 0.19 * ex2)
            tags.append("perceptual top-note curve (exp)")

    if is_body:
        sub = math.log1p(2.4 * max(0.08, bw)) / math.log1p(2.4)
        base_bm = 0.52 + 0.48 * sub
        # bls>1 deepens sub-linear dampening; bls<1 eases it toward neutral (1.0).
        body_mult = max(0.45, 1.0 - (1.0 - base_bm) * min(1.2, bls))
        w_alloc *= body_mult
        tags.append("perceptual body dampening (log)")

    if is_body and hb >= 0.45:
        w_alloc *= max(0.30, 1.0 - 0.58 * hb * hcs)
        tags.append("hidden-body weight cap")

    if imp > 0.16 and is_top and ld >= 0.22:
        w_alloc *= 1.0 + hos * (0.11 * imp + 0.07 * imp * imp)
        tags.append("high-output top impact emphasis")

    return max(w_alloc, 1e-12), tags


def _renormalize_suggested_to_target(
    suggested_pct_by_rid: dict[int, float],
    present_ids: set[int],
    target_total: float,
) -> None:
    s = sum(max(0.0, float(suggested_pct_by_rid.get(rid, 0.0))) for rid in present_ids)
    if s <= 1e-12:
        return
    k = float(target_total) / s
    for rid in present_ids:
        suggested_pct_by_rid[rid] = max(0.0, float(suggested_pct_by_rid.get(rid, 0.0)) * k)


def _pod_perceptual_post_allocation(
    *,
    suggested_pct_by_rid: dict[int, float],
    original_pct_by_rid: dict[int, float],
    present_ids: set[int],
    items_by_id: dict[int, dict[str, Any]],
    extra_flavor_budget: float,
    impact_need_up: float,
    baseline_sum: float,
    perceptual_cfg: dict[str, float] | None = None,
) -> list[str]:
    """
    After proportional budget split: cap stealth body lines, push freed mass to label drivers,
    enforce a minimum share of extra budget on label-driving top notes for high-output dispo targets.
    """
    notes: list[str] = []
    cfg = perceptual_cfg or {}
    extra = max(0.0, float(extra_flavor_budget))
    if extra <= 1e-9:
        return notes
    target = float(baseline_sum)
    imp = _clamp01(float(impact_need_up))
    hcap = float(cfg.get("hidden_cap_strength", 1.0))

    freed = 0.0
    for rid in present_ids:
        meta = items_by_id.get(rid) or {}
        hb = _hidden_body_strength(meta)
        if hb < 0.38:
            continue
        orig = float(original_pct_by_rid.get(rid, 0.0))
        if orig <= 1e-9:
            continue
        cur = float(suggested_pct_by_rid.get(rid, 0.0))
        delta = cur - orig
        if delta <= 1e-9:
            continue
        max_rel = (0.11 + 0.07 * (1.0 - hb)) / max(0.72, min(1.45, hcap))
        cap_inc = orig * max_rel + 0.012
        if delta > cap_inc + 1e-9:
            freed += delta - cap_inc
            suggested_pct_by_rid[rid] = orig + cap_inc

    if freed > 1e-6:
        notes.append(f"Perceptual: capped hidden/body fillers (+{freed:.3f}% freed for label notes).")

    if freed > 1e-6:
        wsum = 0.0
        w_by: dict[int, float] = {}
        for rid in present_ids:
            meta = items_by_id.get(rid) or {}
            ld = _label_driver_strength(meta)
            if not _role_is_top(meta) or ld < 0.1:
                continue
            tw, _, _, _ = _alloc_category_weights(meta)
            w = max(1e-9, (ld * ld) * (0.38 + tw))
            w_by[rid] = w
            wsum += w
        if wsum > 1e-9:
            for rid, w in w_by.items():
                suggested_pct_by_rid[rid] = float(suggested_pct_by_rid.get(rid, 0.0)) + freed * (w / wsum)

    min_share = float(cfg.get("min_label_share_base", 0.28)) + float(cfg.get("min_label_share_imp_scale", 0.16)) * imp
    label_recv: dict[int, float] = {}
    for rid in present_ids:
        meta = items_by_id.get(rid) or {}
        ld = _label_driver_strength(meta)
        if _role_is_top(meta) and ld >= 0.16:
            label_recv[rid] = max(1e-9, ld * ld)

    if not label_recv:
        for rid in present_ids:
            meta = items_by_id.get(rid) or {}
            if str(meta.get("resolved_role") or "").strip().lower() != "main_note":
                continue
            tw, _, _, _ = _alloc_category_weights(meta)
            ld = _label_driver_strength(meta)
            label_recv[rid] = max(1e-9, (0.22 + ld) * (0.35 + tw))
    if not label_recv:
        for rid in present_ids:
            meta = items_by_id.get(rid) or {}
            if not _role_is_top(meta):
                continue
            tw, _, _, _ = _alloc_category_weights(meta)
            label_recv[rid] = max(1e-9, 0.28 + 0.5 * tw)

    label_delta = 0.0
    for rid in label_recv:
        orig = float(original_pct_by_rid.get(rid, 0.0))
        label_delta += max(0.0, float(suggested_pct_by_rid.get(rid, 0.0)) - orig)

    need = min_share * extra - label_delta
    if need <= 1e-4:
        _renormalize_suggested_to_target(suggested_pct_by_rid, present_ids, target)
        return notes

    donors: list[tuple[int, float, float]] = []
    for rid in present_ids:
        meta = items_by_id.get(rid) or {}
        hb = _hidden_body_strength(meta)
        orig = float(original_pct_by_rid.get(rid, 0.0))
        cur = float(suggested_pct_by_rid.get(rid, 0.0))
        d = cur - orig
        if d <= 1e-6:
            continue
        pri = hb * 1.85 + (0.35 if _role_is_body(meta) else 0.0)
        donors.append((rid, pri, d))
    donors.sort(key=lambda x: -x[1])

    taken = 0.0
    still = need
    for rid, _pri, d in donors:
        if still <= 1e-9:
            break
        orig = float(original_pct_by_rid.get(rid, 0.0))
        cur = float(suggested_pct_by_rid.get(rid, 0.0))
        room = max(0.0, cur - orig * 1.001)
        t = min(room, d, still)
        if t <= 1e-9:
            continue
        suggested_pct_by_rid[rid] = cur - t
        taken += t
        still -= t

    rs = sum(label_recv.values())
    if taken > 1e-6 and rs > 1e-9:
        for rid, w in label_recv.items():
            suggested_pct_by_rid[rid] = float(suggested_pct_by_rid.get(rid, 0.0)) + taken * (w / rs)
        notes.append(
            f"Perceptual: enforced min top-label impact (~{min_share * 100:.0f}% of extra budget); "
            f"shifted ~{taken:.3f}% toward label drivers."
        )

    _renormalize_suggested_to_target(suggested_pct_by_rid, present_ids, target)
    return notes


def _bias_norm(bias_value: float, max_abs: float = 3.0) -> float:
    # Bias maps are typically small positive integers. Normalize into [0,1].
    return _clamp01(float(bias_value) / float(max_abs))


def _role_weight_for_family_detection(role: str) -> float:
    """Down-weight accents so tiny accents do not dominate family detection."""
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


def _primary_family_for_ingredient(meta: dict[str, Any]) -> str:
    """
    Map one ingredient to a single primary recipe family (structural label).
    Order: tobacco / beverage / dessert / fruit-candy / cooling / mixed.
    """
    prof = str(meta.get("resolved_profile") or "").strip().lower()
    role = str(meta.get("resolved_role") or "").strip().lower()

    if prof == "tobacco" or "tobacco" in prof.split():
        return "tobacco"
    if prof == "beverage":
        return "beverage"
    if prof in {"cream", "bakery", "dessert", "dessert_bakery", "custard"}:
        return "dessert_bakery"
    if prof in {"fruit", "tropical", "candy", "fruit_candy"}:
        return "fruit_candy"
    if role == "cooling" or prof == "cooling":
        return "cooling_focused"
    if role == "main_note":
        if prof in {"cream", "bakery", "dessert", "dessert_bakery", "custard"}:
            return "dessert_bakery"
        if prof in {"fruit", "tropical", "candy", "fruit_candy", "beverage"}:
            return "fruit_candy"
        if prof == "tobacco" or "tobacco" in prof.split():
            return "tobacco"
        return "mixed"
    return "mixed"


def detect_recipe_family(
    *,
    rows: list[dict[str, Any]],
    items_by_id: dict[int, dict[str, Any]],
) -> Dict[str, Any]:
    """
    Weighted-by-% recipe family detection (accent mass down-weighted).

    Returns:
      dominant_family, secondary_family | None, family_confidence (0..1),
      family_mass_by_family, family_reasoning (short bullet strings).
    """
    keys: tuple[str, ...] = RECIPE_FAMILY_KEYS
    bucket: dict[str, float] = {k: 0.0 for k in keys}
    total_pct = 0.0

    for r in rows:
        rid = int(r["ingredient_id"])
        pct = float(r.get("original_pct") or r.get("amount_percent") or 0.0)
        if pct <= 1e-12:
            continue
        meta = items_by_id.get(rid) or {}
        rw = _role_weight_for_family_detection(str(meta.get("resolved_role") or ""))
        wmass = pct * rw
        fam = _primary_family_for_ingredient(meta)
        if fam not in bucket:
            fam = "mixed"
        bucket[fam] += wmass
        total_pct += pct

    tot_w = sum(bucket.values())
    if tot_w <= 1e-12:
        return {
            "dominant_family": "mixed",
            "secondary_family": None,
            "family_confidence": 0.35,
            "family_mass_by_family": {k: 0.0 for k in keys},
            "family_reasoning": ["No flavour percentage mass; defaulted to mixed."],
        }

    shares = {k: bucket[k] / tot_w for k in keys}
    sorted_f = sorted(shares.keys(), key=lambda k: shares[k], reverse=True)
    dom = sorted_f[0]
    sec: str | None = None
    if len(sorted_f) > 1 and shares[sorted_f[1]] >= 0.15 and sorted_f[1] != dom:
        sec = sorted_f[1]

    margin = shares[dom] - (shares[sorted_f[1]] if len(sorted_f) > 1 else 0.0)
    confidence = _clamp01(0.28 + 0.95 * shares[dom] + 0.35 * margin)
    if sec is not None and margin < 0.12:
        confidence *= 0.88

    reasoning: list[str] = [
        f"Dominant {dom} with {shares[dom]*100:.1f}% of role-weighted mass (recipe total flavour {total_pct:.2f}%)."
    ]
    if sec is not None:
        reasoning.append(f"Secondary {sec} at {shares[sec]*100:.1f}% of weighted mass.")
    top3 = sorted_f[:3]
    reasoning.append(
        "Mass split: "
        + ", ".join(f"{k} {shares[k]*100:.0f}%" for k in top3 if shares[k] >= 0.05)
        + "."
    )

    return {
        "dominant_family": dom,
        "secondary_family": sec,
        "family_confidence": round(confidence, 4),
        "family_mass_by_family": {k: round(shares[k], 4) for k in keys},
        "family_reasoning": reasoning,
    }


def compute_recipe_signal_profile(
    *,
    rows: list[dict[str, Any]],
    items_by_id: dict[int, dict[str, Any]],
    analytics_by_ref: dict[int, dict[str, Any]],
    present_ids: set[int],
) -> Dict[str, Any]:
    """
    Compute explainable, weighted recipe-level signal scores (0..1-ish).

    rows should include per-flavor `ingredient_id` and either `original_pct` or `amount_percent`.
    """
    total_pct = 0.0
    top_w = 0.0
    body_w = 0.0
    sweet_w = 0.0
    cooling_w = 0.0

    brightness_w = 0.0
    harshness_w = 0.0
    coil_risk_w = 0.0

    needs_review_count = 0
    analytics_coverage_hits = 0

    # Pairing coherence: count how many flavors have at least one “top pairing partner” also present.
    pair_hits = 0
    pair_den = 0

    pct_by_rid: dict[int, float] = {}

    for r in rows:
        rid = int(r["ingredient_id"])
        pct = float(r.get("amount_percent") or r.get("original_pct") or 0.0)
        pct_by_rid[rid] = pct
        total_pct += pct
        meta = items_by_id.get(rid) or {}

        if int(meta.get("needs_review") or 0) == 1:
            needs_review_count += 1

        if rid in analytics_by_ref:
            analytics_coverage_hits += 1

        role = str(meta.get("resolved_role") or "").strip().lower()
        profile = str(meta.get("resolved_profile") or "").strip().lower()

        # Category weights should be conditional (not “always add base weight”),
        # otherwise top/body/sweet signals never meaningfully change.
        top_weight = 0.0
        if role in {"main_note", "accent"}:
            top_weight = 1.0 if role == "main_note" else 0.65
        elif profile in {"fruit", "tropical", "candy", "beverage", "fruit_candy"}:
            top_weight = 0.8

        body_weight = 0.0
        if role == "body":
            body_weight = 1.0
        elif profile in {"cream", "bakery", "dessert", "dessert_bakery", "custard"}:
            body_weight = 0.85

        sweet_weight = 0.0
        if role in {"sweetener_support", "sweetener"}:
            sweet_weight = 1.0 if role == "sweetener" else 0.75
        elif profile == "sweetener":
            sweet_weight = 0.9

        cooling_weight = 0.0
        if role == "cooling" or profile == "cooling":
            cooling_weight = 1.0

        top_w += pct * top_weight
        body_w += pct * body_weight
        sweet_w += pct * sweet_weight
        cooling_w += pct * cooling_weight

        brightness_bias = float(meta.get("brightness_bias") or 0.0)
        harshness_bias = float(meta.get("harshness_bias") or 0.0)
        coil_risk = float(meta.get("coil_risk") or 0.0)

        brightness_w += pct * _bias_norm(brightness_bias, 3.0)
        # Harshness risk rises with harshness bias and cooling presence.
        harshness_w += pct * (_bias_norm(harshness_bias, 3.0) + 0.40 * cooling_weight)
        coil_risk_w += pct * _bias_norm(coil_risk, 3.0)

        # Pairing coherence: use analytics' top_pairings_json.
        analytics = analytics_by_ref.get(rid) or {}
        top_pairings = _safe_json_list(analytics.get("top_pairings_json"))
        if top_pairings:
            pair_den += 1
            if any(int(p.get("partner_id") or 0) in present_ids for p in top_pairings):
                pair_hits += 1

    denom = max(1e-9, total_pct)
    analytics_coverage = analytics_coverage_hits / max(1, len(present_ids))
    needs_review_fraction = needs_review_count / max(1, len(present_ids))

    # Convert weighted “category grams” into score ratios.
    top_note_score = _clamp01(top_w / denom)
    body_score = _clamp01(body_w / denom)
    sweetness_score = _clamp01(sweet_w / denom)
    cooling_score = _clamp01(cooling_w / denom)

    brightness_score = _clamp01(brightness_w / denom)
    harshness_risk_score = _clamp01(harshness_w / denom)
    coil_risk_score = _clamp01(coil_risk_w / denom)

    pairing_coherence_score = _clamp01(pair_hits / max(1, pair_den))

    # Saturation score: total flavor percent vs a rough “typical max”.
    saturation_score = _clamp01(total_pct / 18.0)

    # Candidate “fit” scores are explainable compositions, not black-box.
    pod_suit = 0.0
    mtl_focus = 0.0
    for rid in present_ids:
        meta = items_by_id.get(rid) or {}
        pct = pct_by_rid.get(rid, 0.0)
        pod_suit += pct * _pod_suitability_score(meta.get("resolved_pod_suitability"))

        # MTL focus: top+body roles + low harshness.
        role_strength = 0.0
        if _role_is_top(meta):
            role_strength += 1.0
        if _role_is_body(meta):
            role_strength += 0.6
        mtl_focus += pct * (role_strength * (1.0 - _bias_norm(float(meta.get("harshness_bias") or 0.0), 3.0)))

    pod_suit = pod_suit / denom
    mtl_focus = mtl_focus / denom

    dispo_suitability_score = _clamp01(pod_suit * (1.0 - 0.35 * harshness_risk_score - 0.25 * coil_risk_score))
    mtl_suitability_score = _clamp01(mtl_focus * (1.0 - 0.35 * harshness_risk_score - 0.20 * coil_risk_score))

    identity_fragility_score = _clamp01(
        0.45 * needs_review_fraction + 0.25 * (1.0 - analytics_coverage) + 0.30 * (1.0 - pairing_coherence_score)
    )

    return {
        "total_pct": total_pct,
        "top_note_score": top_note_score,
        "body_score": body_score,
        "sweetness_score": sweetness_score,
        "cooling_score": cooling_score,
        "brightness_score": brightness_score,
        "harshness_risk_score": harshness_risk_score,
        "coil_risk_score": coil_risk_score,
        "pairing_coherence_score": pairing_coherence_score,
        "saturation_score": saturation_score,
        "dispo_suitability_score": dispo_suitability_score,
        "mtl_suitability_score": mtl_suitability_score,
        "identity_fragility_score": identity_fragility_score,
        "analytics_coverage": analytics_coverage,
        "needs_review_fraction": needs_review_fraction,
    }


def _potency_factor_for_dispo(meta: dict[str, Any]) -> float:
    p = str(meta.get("resolved_potency") or "").strip().lower()
    if p == "high":
        return 1.0
    if p == "medium":
        return 0.78
    if p == "low":
        return 0.55
    return 0.68


def compute_dispo_recipe_fingerprint(
    *,
    rows: list[dict[str, Any]],
    items_by_id: dict[int, dict[str, Any]],
    analytics_by_ref: dict[int, dict[str, Any]],
    present_ids: set[int],
) -> dict[str, Any]:
    """
    Pod/Dispo-oriented fingerprint: extends compute_recipe_signal_profile with composite scores
    for commercial disposable-style behavior (all primary scores in ~0..1).
    """
    base = compute_recipe_signal_profile(
        rows=rows, items_by_id=items_by_id, analytics_by_ref=analytics_by_ref, present_ids=present_ids
    )
    total = max(1e-9, float(base["total_pct"]))
    main_mass = 0.0
    sweet_mass = 0.0
    body_mass = 0.0
    cool_mass = 0.0
    potency_weighted_main = 0.0

    for r in rows:
        rid = int(r["ingredient_id"])
        pct = float(r.get("amount_percent") or r.get("original_pct") or 0.0)
        meta = items_by_id.get(rid) or {}
        role = str(meta.get("resolved_role") or "").strip().lower()
        if role == "main_note":
            main_mass += pct
            potency_weighted_main += pct * _potency_factor_for_dispo(meta)
        if _role_is_sweet(meta):
            sweet_mass += pct
        if _role_is_body(meta):
            body_mass += pct
        if _role_is_cooling(meta):
            cool_mass += pct

    main_ratio = main_mass / total
    potency_adj = (potency_weighted_main / main_mass) if main_mass > 1e-9 else 0.68

    impact_score = _clamp01(
        0.30 * float(base["top_note_score"])
        + 0.20 * float(base["brightness_score"])
        + 0.28 * main_ratio * (0.5 + 0.5 * potency_adj)
        + 0.22 * float(base["saturation_score"])
    )
    sweet_backbone_score = _clamp01(
        0.70 * float(base["sweetness_score"]) + 0.30 * _clamp01((sweet_mass / total) * 1.35)
    )
    body_density_score = _clamp01(
        0.85 * float(base["body_score"]) + 0.15 * _clamp01((body_mass / total) * 1.15)
    )
    topnote_punch_score = _clamp01(
        0.50 * float(base["top_note_score"]) + 0.50 * float(base["brightness_score"])
    )
    cooling_presentation_score = float(base["cooling_score"])

    dispo_internal = _clamp01(
        0.22 * impact_score
        + 0.14 * float(base["saturation_score"])
        + 0.12 * sweet_backbone_score
        + 0.12 * body_density_score
        + 0.10 * topnote_punch_score
        + 0.12 * float(base["pairing_coherence_score"])
        + 0.10 * float(base["dispo_suitability_score"])
        - 0.18 * float(base["harshness_risk_score"])
        - 0.10 * float(base["coil_risk_score"])
        - 0.08 * float(base["identity_fragility_score"])
    )

    out: dict[str, Any] = {k: base[k] for k in base}
    out["impact_score"] = round(impact_score, 4)
    out["sweet_backbone_score"] = round(sweet_backbone_score, 4)
    out["body_density_score"] = round(body_density_score, 4)
    out["topnote_punch_score"] = round(topnote_punch_score, 4)
    out["cooling_presentation_score"] = round(cooling_presentation_score, 4)
    out["dispo_fit_score"] = round(dispo_internal, 4)
    out["main_note_pct_of_total"] = round(main_ratio * 100.0, 2)
    out["sweetener_pct_of_total"] = round((sweet_mass / total) * 100.0, 2)
    out["body_pct_of_total"] = round((body_mass / total) * 100.0, 2)
    out["cooling_pct_of_total"] = round((cool_mass / total) * 100.0, 2)
    out["score_rationale"] = {
        "impact_score": "top-note + brightness + main-note load (potency-weighted) + saturation",
        "sweet_backbone_score": "weighted sweetness signal + sweetener mass share",
        "body_density_score": "body signal + body-profile mass share",
        "topnote_punch_score": "blend of top-note presence and brightness",
        "cooling_presentation_score": "cooling mass-weighted presence (same basis as cooling_score)",
        "dispo_fit_score": "balanced composite minus harshness/coil/fragility penalties",
    }
    return out


def classify_dispo_recipe_subtype(
    fp: dict[str, Any],
    rows: list[dict[str, Any]],
    items_by_id: dict[int, dict[str, Any]],
) -> tuple[str, str]:
    """Light-weight recipe shape → commercial subtype key for target blending."""
    total = max(1e-9, float(fp.get("total_pct") or 0.0))
    cool_pct = 0.0
    candy_any = False
    fruit_any = False
    cream_any = False
    bev_any = False
    dessert_any = False

    for r in rows:
        rid = int(r["ingredient_id"])
        pct = float(r.get("amount_percent") or r.get("original_pct") or 0.0)
        meta = items_by_id.get(rid) or {}
        prof = str(meta.get("resolved_profile") or "").strip().lower()
        role = str(meta.get("resolved_role") or "").strip().lower()
        if _role_is_cooling(meta):
            cool_pct += pct
        if prof in {"candy", "fruit_candy"}:
            candy_any = True
        if prof in {"fruit", "tropical", "fruit_candy"}:
            fruit_any = True
        if prof in {"cream", "custard"}:
            cream_any = True
        if prof == "beverage":
            bev_any = True
        if prof in {"bakery", "dessert", "dessert_bakery"}:
            dessert_any = True

    cool_share = cool_pct / total
    cool_score = float(fp.get("cooling_presentation_score") or 0.0)

    if (cool_share >= 0.085 or cool_score > 0.16) and (fruit_any or candy_any):
        return ("cool_fruit", "Cool fruit dispo")
    if cream_any and (fruit_any or candy_any):
        return ("creamy_fruit", "Creamy fruit dispo")
    if candy_any and fruit_any:
        return ("candy_fruit", "Candy fruit dispo")
    if bev_any:
        return ("beverage", "Beverage dispo")
    if dessert_any and total < 11.5:
        return ("light_dessert", "Light dessert dispo")
    return ("general", "General commercial dispo")


def classify_dispo_fit_rating_and_advisories(
    fp: dict[str, Any],
    dispo_targets: dict[str, Any],
) -> tuple[str, float, list[str], list[str], list[str], list[str]]:
    """
    Returns: rating_key, adjusted_score, weaknesses, strengths, suggestions, advisories.
    rating_key: strong_dispo_candidate | workable_with_tuning | weak_dispo_candidate
    """
    weaknesses: list[str] = []
    strengths: list[str] = []
    suggestions: list[str] = []
    advisories: list[str] = []

    def _below(cur: float, band: tuple[float, float], margin: float = 0.02) -> bool:
        return float(cur) < float(band[0]) - margin

    def _above(cur: float, band: tuple[float, float], margin: float = 0.03) -> bool:
        return float(cur) > float(band[1]) + margin

    imp = float(fp["impact_score"])
    sat = float(fp["saturation_score"])
    sb = float(fp["sweet_backbone_score"])
    bd = float(fp["body_density_score"])
    cp = float(fp["cooling_presentation_score"])
    punch = float(fp["topnote_punch_score"])
    ib = dispo_targets["impact_score"]
    sband = dispo_targets["saturation_score"]
    sbb = dispo_targets["sweet_backbone_score"]
    bdb = dispo_targets["body_density_score"]
    cband = dispo_targets["cooling_presentation_score"]
    pband = dispo_targets["topnote_punch_score"]

    if _below(imp, ib, 0.04) or _below(punch, pband, 0.05):
        weaknesses.append("Low impact / top-note punch — may feel too soft for disposable-style delivery.")
    if _below(sat, sband):
        weaknesses.append("Saturation is low relative to commercial dispo norms.")
    if _below(sb, sbb):
        weaknesses.append("Sweet backbone is thin — palate may feel incomplete on a compact coil.")
    if _below(bd, bdb):
        weaknesses.append("Body density is low — vapour may feel hollow or thin.")
    if float(fp.get("harshness_risk_score") or 0) > float(dispo_targets["harshness_risk_max"]) - 0.04:
        weaknesses.append("Harshness risk is elevated — aggressive boosting could get scratchy.")
    if float(fp.get("pairing_coherence_score") or 0) < 0.28:
        weaknesses.append("Pairing coherence is weak — layered saturation may not lock together as in typical dispos.")
    if float(fp.get("identity_fragility_score") or 0) > 0.55:
        weaknesses.append("Recipe metadata/analytics are fragile — one-click conversion is less reliable.")

    if not _below(imp, ib, 0) and not _above(imp, ib):
        strengths.append("Impact sits in a commercial dispo-friendly band.")
    if not _below(punch, pband, 0) and not _above(punch, pband):
        strengths.append("Top-note punch aligns with typical disposable presentation.")
    if not _below(bd, bdb, 0):
        strengths.append("Body density supports a fuller dispo-style exhale.")
    if not _below(sb, sbb, 0):
        strengths.append("Sweet backbone is present enough for typical disposable palates.")
    if float(fp.get("pairing_coherence_score") or 0) >= 0.45:
        strengths.append("Strong pairing support — good candidate for coherent compact-device saturation.")
    if not _below(sat, sband, 0):
        strengths.append("Flavor load aligns with dispo saturation expectations.")

    if _above(cp, cband, 0.0):
        advisories.append("Cooling is already near the upper end for this commercial profile — avoid stacking more WS/menthol in manual tweaks.")
    if _above(imp, ib):
        advisories.append("Impact runs hot versus this profile — conversion will stay conservative on mains.")

    base_fit = float(fp.get("dispo_fit_score") or 0.0)
    weakness_penalty = min(0.28, 0.065 * len(weaknesses))
    adjusted = max(0.0, min(1.0, base_fit - weakness_penalty))

    if adjusted >= 0.62 and len(weaknesses) <= 1:
        rating = "strong_dispo_candidate"
    elif adjusted >= 0.45:
        rating = "workable_with_tuning"
    else:
        rating = "weak_dispo_candidate"

    if rating == "weak_dispo_candidate":
        suggestions.append(
            "Recipe is a weak candidate for one-click Pod conversion — consider manual structure (main note / body / sweetener) before relying on the preview."
        )
        suggestions.append(
            "If this style matters for your line, save a dedicated Pod variant after tuning rather than re-converting the DTL original repeatedly."
        )
    elif rating == "workable_with_tuning":
        suggestions.append(
            "Workable dispo candidate — review impact, body, and sweet backbone after conversion; small manual nudges may complete the commercial feel."
        )
    else:
        suggestions.append("Strong dispo candidate — conversion should track commercial behavior with lighter touch-ups.")

    if _below(sb, sbb, 0.01):
        suggestions.append(
            "If the mix still feels hollow after conversion, add a small amount of sweetener support or a body bridge flavor rather than only boosting mains."
        )

    return rating, adjusted, weaknesses, strengths, suggestions, advisories


def _band_need_score(current: float, band: tuple[float, float]) -> tuple[float, float]:
    """Return (need_up, need_down) as normalized magnitudes."""
    lo, hi = float(band[0]), float(band[1])
    eps = 1e-6
    if current < lo:
        return ((lo - current) / max(lo, eps), 0.0)
    if current > hi:
        return (0.0, (current - hi) / max(hi, eps))
    return (0.0, 0.0)


def _fit_label_from_score(score: float) -> str:
    s = float(score)
    if s >= 0.75:
        return "Strong"
    if s >= 0.55:
        return "Workable"
    return "Weak"


def _compute_identity_shift_score(
    *,
    baseline_pct_by_rid: dict[int, float],
    suggested_pct_by_rid: dict[int, float],
    items_by_id: dict[int, dict[str, Any]],
    analytics_by_ref: dict[int, dict[str, Any]],
    present_ids: set[int],
) -> float:
    """Drift from the “uniform scaling baseline” (so identity drift isn't inflated by total intensity)."""
    base_sum = sum(max(0.0, v) for v in baseline_pct_by_rid.values())
    if base_sum <= 1e-9:
        return 0.0

    drift_sum = sum(abs(float(suggested_pct_by_rid.get(rid, 0.0)) - float(baseline_pct_by_rid.get(rid, 0.0))) for rid in present_ids) / base_sum

    primary_ids: list[int] = []
    for rid in present_ids:
        meta = items_by_id.get(rid) or {}
        role = str(meta.get("resolved_role") or "").strip().lower()
        if role in {"main_note"}:
            primary_ids.append(rid)
    if not primary_ids:
        primary_ids = sorted(list(present_ids), key=lambda r: baseline_pct_by_rid.get(r, 0.0), reverse=True)[: min(3, max(1, len(present_ids)))]

    primary_shift = 0
    for rid in primary_ids:
        b = float(baseline_pct_by_rid.get(rid) or 0.0)
        s = float(suggested_pct_by_rid.get(rid) or 0.0)
        if b > 1e-9 and abs(s - b) / b > 0.25:
            primary_shift += 1
    primary_shift_fraction = primary_shift / max(1, len(primary_ids))

    # Pairing disruption: if one partner is boosted while the other is strongly reduced.
    pair_candidates: set[tuple[int, int]] = set()
    for rid in present_ids:
        analytics = analytics_by_ref.get(rid) or {}
        top_pairings = _safe_json_list(analytics.get("top_pairings_json"))
        for p in top_pairings:
            partner = int(p.get("partner_id") or 0)
            if partner in present_ids and partner != rid:
                a, b = sorted((rid, partner))
                pair_candidates.add((a, b))

    disrupt = 0
    total_pairs = len(pair_candidates) or 1
    for a, b in pair_candidates:
        da = float(suggested_pct_by_rid.get(a, 0.0) - baseline_pct_by_rid.get(a, 0.0))
        db = float(suggested_pct_by_rid.get(b, 0.0) - baseline_pct_by_rid.get(b, 0.0))
        ba = float(baseline_pct_by_rid.get(a, 0.0))
        bb = float(baseline_pct_by_rid.get(b, 0.0))
        if (ba > 1e-9 and da > 0.20 * ba and bb > 1e-9 and db < -0.20 * bb) or (
            bb > 1e-9 and db > 0.20 * bb and ba > 1e-9 and da < -0.20 * ba
        ):
            disrupt += 1

    pair_disrupt_fraction = disrupt / total_pairs

    shift_score = 0.55 * drift_sum + 0.25 * primary_shift_fraction + 0.20 * pair_disrupt_fraction
    return _clamp01(shift_score)


def _recipe_high_potency_mass_fraction(
    *,
    original_pct_by_rid: dict[int, float],
    items_by_id: dict[int, dict[str, Any]],
    present_ids: set[int],
) -> float:
    tot = 0.0
    hi = 0.0
    for rid in present_ids:
        pct = float(original_pct_by_rid.get(rid) or 0.0)
        if pct <= 1e-12:
            continue
        tot += pct
        p = str((items_by_id.get(rid) or {}).get("resolved_potency") or "").strip().lower()
        if p == "high":
            hi += pct
    return hi / max(1e-9, tot)


def _compute_adaptive_target_total_flavor(
    *,
    mode_key: str,
    original_total: float,
    family_profile: dict[str, Any],
    signal_profile: dict[str, Any],
    original_pct_by_rid: dict[int, float],
    items_by_id: dict[int, dict[str, Any]],
    present_ids: set[int],
) -> tuple[float, list[str]]:
    """
    target_total = original_total * (1 + relative_uplift), with uplift interpolated inside
    family total_uplift_band using saturation vs saturation_target and potency dampening.
    Pod/Dispo: also respect POD_DISPO band guardrails and never go below original total.
    """
    notes: list[str] = []
    ot = max(0.0, float(original_total))
    if ot < 1e-12:
        return 0.0, ["Original flavour total is 0%; target remains 0."]

    band = family_profile.get("total_uplift_band") or (0.015, 0.085)
    lo_u, hi_u = float(band[0]), float(band[1])
    sat_band = family_profile.get("saturation_target") or (0.45, 0.85)
    sat_lo, sat_hi = float(sat_band[0]), float(sat_band[1])
    cur_sat = float(signal_profile.get("saturation_score") or 0.5)

    span = max(1e-6, sat_hi - sat_lo)
    sat_pos = _clamp01((cur_sat - sat_lo) / span)
    # Low saturation score → push toward higher uplift within band.
    u_t = hi_u - (hi_u - lo_u) * sat_pos * 0.82

    pot_p = _recipe_high_potency_mass_fraction(
        original_pct_by_rid=original_pct_by_rid, items_by_id=items_by_id, present_ids=present_ids
    )
    u_t *= 1.0 - 0.24 * pot_p
    u_t = max(0.0, u_t)

    target = ot * (1.0 + u_t)
    notes.append(
        f"Adaptive relative uplift {u_t * 100:.2f}% (saturation signal {cur_sat:.3f} vs target band "
        f"{sat_lo:.2f}-{sat_hi:.2f}; high-potency mass fraction {pot_p:.2f})."
    )

    if mode_key == "POD_DISPO":
        prof = TARGET_SIGNAL_PROFILES["POD_DISPO"]
        band_lo, band_hi = float(prof["total_flavor_target_band"][0]), float(prof["total_flavor_target_band"][1])
        tmin = float(prof["total_flavor_min_pct"])
        target = max(target, ot)
        if ot < tmin:
            target = max(target, min(band_hi, tmin + 0.35 * (band_lo - tmin)))
        target = min(target, band_hi)
        notes.append(f"Pod/Dispo guardrails: band {band_lo:.1f}-{band_hi:.1f}%, min {tmin:.1f}%; target {target:.2f}%.")
    else:
        # MTL: keep uplift modest (hard cap vs source)
        target = max(ot, min(target, ot * 1.115))
        notes.append(f"MTL soft cap: target ≤ {1.115 * ot:.2f}% (115.5% of source).")

    return round(target, 6), notes


def _alloc_category_weights(meta: dict[str, Any]) -> tuple[float, float, float, float]:
    role = str(meta.get("resolved_role") or "").strip().lower()
    profile = str(meta.get("resolved_profile") or "").strip().lower()
    top_w = 0.0
    if role == "main_note":
        top_w = 1.0
    elif role == "accent":
        top_w = 0.65
    elif profile in {"fruit", "tropical", "candy", "beverage", "fruit_candy"}:
        top_w = 0.8
    body_w = 0.0
    if role == "body":
        body_w = 1.0
    elif profile in {"cream", "bakery", "dessert", "dessert_bakery", "custard"}:
        body_w = 0.85
    sweet_w = 0.0
    if role == "sweetener":
        sweet_w = 1.0
    elif role == "sweetener_support":
        sweet_w = 0.75
    elif profile == "sweetener":
        sweet_w = 0.9
    cool_w = 0.0
    if role == "cooling" or profile == "cooling":
        cool_w = 1.0
    return top_w, body_w, sweet_w, cool_w


def _potency_alloc_factor(potency: str) -> float:
    p = str(potency or "").strip().lower()
    if p == "high":
        return 0.28
    if p == "medium":
        return 0.68
    if p == "low":
        return 0.96
    return 0.62


def _base_budget_weight(
    *,
    meta: dict[str, Any],
    family_profile: dict[str, Any],
) -> float:
    """Family-tilted base weight for allocating extra flavour budget (not normalized)."""
    role = str(meta.get("resolved_role") or "").strip().lower()
    profile = str(meta.get("resolved_profile") or "").strip().lower()
    te = float(family_profile.get("topnote_emphasis") or 1.0)
    be = float(family_profile.get("body_emphasis") or 1.0)
    se = float(family_profile.get("sweetness_emphasis") or 1.0)
    br = float(family_profile.get("brightness_emphasis") or 1.0)
    ce = float(family_profile.get("cooling_emphasis") or 1.0)

    tw, bw, sw, cw = _alloc_category_weights(meta)

    if role == "main_note":
        w = (0.55 + 0.45 * te) * (0.62 + 0.38 * br) * max(0.15, tw)
    elif role == "accent":
        w = 0.06 * (0.75 + 0.25 * te)
    elif role == "body" or bw > 0.5:
        w = (0.58 + 0.42 * be) * max(0.2, bw + 0.15)
    elif role in {"sweetener", "sweetener_support"} or profile == "sweetener":
        w = (0.48 + 0.52 * se) * max(0.2, sw + 0.1)
    elif role == "cooling" or profile == "cooling":
        w = (0.38 + 0.62 * ce) * max(0.15, cw + 0.12)
    else:
        w = 0.32 * (0.8 + 0.2 * br)

    return max(w, 1e-9)


def _finalize_budget_allocation(
    *,
    suggested_pct_by_rid: dict[int, float],
    target_total: float,
    present_ids: set[int],
    items_by_id: dict[int, dict[str, Any]],
    analytics_by_ref: dict[int, dict[str, Any]],
    is_pod: bool,
) -> None:
    """Apply analytics max guardrails, then rescale so sum equals target_total (in-place)."""
    max_factor_pod = 1.55
    max_factor_mtl = 1.10

    def _clamp_max(rid: int, v: float) -> float:
        an = analytics_by_ref.get(rid) or {}
        mx = an.get("max_pct")
        if mx is None:
            return v
        cap = float(mx) * (max_factor_pod if is_pod else max_factor_mtl)
        return min(v, cap)

    for rid in list(present_ids):
        suggested_pct_by_rid[rid] = _clamp_max(rid, float(suggested_pct_by_rid.get(rid) or 0.0))

    s = sum(max(0.0, suggested_pct_by_rid.get(rid, 0.0)) for rid in present_ids)
    if s <= 1e-12:
        return
    k = float(target_total) / s
    for rid in present_ids:
        suggested_pct_by_rid[rid] = max(0.0, float(suggested_pct_by_rid.get(rid, 0.0)) * k)

    # Second pass: if any line still exceeds extreme cap after rescale, trim and redistribute once.
    overflow = 0.0
    for rid in present_ids:
        an = analytics_by_ref.get(rid) or {}
        mx = an.get("max_pct")
        if mx is None:
            continue
        cap = float(mx) * (max_factor_pod if is_pod else max_factor_mtl)
        val = float(suggested_pct_by_rid.get(rid, 0.0))
        if val > cap + 1e-9:
            overflow += val - cap
            suggested_pct_by_rid[rid] = cap

    if overflow > 1e-6:
        mains = [
            rid
            for rid in present_ids
            if str((items_by_id.get(rid) or {}).get("resolved_role") or "").strip().lower() == "main_note"
        ]
        if not mains:
            mains = sorted(present_ids, key=lambda r: float(suggested_pct_by_rid.get(r, 0.0)), reverse=True)[:3]
        add_each = overflow / max(1, len(mains))
        for rid in mains:
            suggested_pct_by_rid[rid] = float(suggested_pct_by_rid.get(rid, 0.0)) + add_each

    s2 = sum(max(0.0, suggested_pct_by_rid.get(rid, 0.0)) for rid in present_ids)
    if s2 > 1e-12 and abs(s2 - target_total) > 1e-4:
        k2 = float(target_total) / s2
        for rid in present_ids:
            suggested_pct_by_rid[rid] = max(0.0, float(suggested_pct_by_rid.get(rid, 0.0)) * k2)


def _accent_preservation_trim(
    *,
    suggested_pct_by_rid: dict[int, float],
    original_pct_by_rid: dict[int, float],
    present_ids: set[int],
    items_by_id: dict[int, dict[str, Any]],
    target_total: float,
) -> None:
    """Cap accent line growth; redistribute freed budget to mains (in-place)."""
    freed = 0.0
    for rid in present_ids:
        meta = items_by_id.get(rid) or {}
        if str(meta.get("resolved_role") or "").strip().lower() != "accent":
            continue
        orig = float(original_pct_by_rid.get(rid, 0.0))
        sug = float(suggested_pct_by_rid.get(rid, 0.0))
        cap = orig * 1.08 + 0.04
        if sug > cap + 1e-9:
            freed += sug - cap
            suggested_pct_by_rid[rid] = cap
    if freed <= 1e-9:
        return
    mains = [
        rid
        for rid in present_ids
        if str((items_by_id.get(rid) or {}).get("resolved_role") or "").strip().lower() == "main_note"
    ]
    if not mains:
        return
    mass = sum(float(suggested_pct_by_rid.get(rid, 0.0)) for rid in mains)
    if mass <= 1e-9:
        return
    for rid in mains:
        suggested_pct_by_rid[rid] = float(suggested_pct_by_rid.get(rid, 0.0)) + freed * (
            float(suggested_pct_by_rid.get(rid, 0.0)) / mass
        )
    s = sum(float(suggested_pct_by_rid.get(rid, 0.0)) for rid in present_ids)
    if s > 1e-12 and abs(s - target_total) > 1e-4:
        k = target_total / s
        for rid in present_ids:
            suggested_pct_by_rid[rid] = max(0.0, float(suggested_pct_by_rid.get(rid, 0.0)) * k)


def _compute_recipe_diagnostics(
    *,
    mode_key: str,
    current_profile: dict[str, Any],
    target_signal: dict[str, Any],
    family_profile: dict[str, Any],
    dispo_fp: dict[str, Any] | None,
    original_pct_by_rid: dict[int, float],
    present_ids: set[int],
    items_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Structured pre-allocation weakness flags (vs target bands / family ceilings)."""

    def _below_band(score: float, band: tuple[float, float], margin: float = 0.03) -> bool:
        return float(score) < float(band[0]) - margin

    def _above_band(score: float, band: tuple[float, float], margin: float = 0.02) -> bool:
        return float(score) > float(band[1]) + margin

    body = float(current_profile.get("body_score") or 0)
    sweet = float(current_profile.get("sweetness_score") or 0)
    top = float(current_profile.get("top_note_score") or 0)
    sat = float(current_profile.get("saturation_score") or 0)
    bright = float(current_profile.get("brightness_score") or 0)
    harsh = float(current_profile.get("harshness_risk_score") or 0)
    coil = float(current_profile.get("coil_risk_score") or 0)

    tb = target_signal.get("body_score") or (0.35, 0.65)
    ts = target_signal.get("sweetness_score") or (0.18, 0.45)
    tt = target_signal.get("top_note_score") or (0.4, 0.65)
    t_sat = target_signal.get("saturation_score") or (0.35, 0.7)
    h_max = float(family_profile.get("harshness_ceiling") or target_signal.get("harshness_risk_max") or 0.65)
    c_max = float(family_profile.get("coil_risk_ceiling") or target_signal.get("coil_risk_max") or 0.7)

    sat_tgt = family_profile.get("saturation_target")
    if isinstance(sat_tgt, tuple) and len(sat_tgt) == 2:
        low_sat = _below_band(sat, sat_tgt, 0.04)
    else:
        low_sat = _below_band(sat, t_sat, 0.04)

    low_body = _below_band(body, tb, 0.03)
    low_sweet = _below_band(sweet, ts, 0.03)
    low_top = _below_band(top, tt, 0.03) or _below_band(bright, (0.25, 0.75), 0.05)
    if mode_key == "POD_DISPO" and dispo_fp is not None:
        if float(dispo_fp.get("topnote_punch_score") or 0) < float(dispo_fp.get("impact_score") or 0) * 0.75:
            low_top = low_top or float(dispo_fp.get("topnote_punch_score") or 0) < 0.38
        low_sweet = low_sweet or float(dispo_fp.get("sweet_backbone_score") or 0) < 0.32
        low_body = low_body or float(dispo_fp.get("body_density_score") or 0) < 0.30

    high_harsh = harsh > h_max - 0.04
    high_coil = coil > c_max - 0.04

    ot = sum(max(0.0, original_pct_by_rid.get(r, 0.0)) for r in present_ids)
    overloaded = ot > 15.5 or sum(1 for r in present_ids if original_pct_by_rid.get(r, 0) > 5.5) >= 2

    main_m = 0.0
    acc_m = 0.0
    for rid in present_ids:
        pct = float(original_pct_by_rid.get(rid, 0.0))
        role = str((items_by_id.get(rid) or {}).get("resolved_role") or "").strip().lower()
        if role == "main_note":
            main_m += pct
        elif role == "accent":
            acc_m += pct
    imbalanced_clusters = ot > 1e-9 and acc_m > main_m * 0.55 and acc_m > 1.2

    return {
        "low_body": bool(low_body),
        "low_sweet_backbone": bool(low_sweet),
        "low_topnote_punch": bool(low_top),
        "low_saturation": bool(low_sat),
        "high_harshness_risk": bool(high_harsh),
        "high_coil_risk": bool(high_coil),
        "overloaded": bool(overloaded),
        "imbalanced_clusters": bool(imbalanced_clusters),
    }


def _compute_conversion_fit_rating(
    *,
    mode_key: str,
    dominant_family: str,
    family_confidence: float,
    diagnostics: dict[str, Any],
    current_profile: dict[str, Any],
    dispo_fp: dict[str, Any] | None,
    pod_subtype: str | None = None,
    pod_subtype_confidence: float | None = None,
) -> dict[str, Any]:
    """0–100 score, label strong|workable|weak, human reasons."""
    reasons: list[str] = []
    score = 58.0

    sat = float(current_profile.get("saturation_score") or 0)
    body = float(current_profile.get("body_score") or 0)
    pair_c = float(current_profile.get("pairing_coherence_score") or 0)
    harsh = float(current_profile.get("harshness_risk_score") or 0)

    score += min(14.0, sat * 16.0)
    score += min(10.0, body * 10.0)
    score += min(8.0, pair_c * 10.0)
    score += min(6.0, float(family_confidence or 0.5) * 8.0)
    score -= min(18.0, harsh * 22.0)

    dom = str(dominant_family or "mixed").lower()
    if mode_key == "POD_DISPO":
        if dom in {"fruit_candy", "beverage"}:
            score += 12.0
            reasons.append("Family profile is typically well-suited to Pod/Dispo saturation.")
        elif dom in {"dessert_bakery"}:
            score += 4.0
            reasons.append("Dessert/bakery recipes are workable on Pod with body-forward allocation.")
        elif dom == "tobacco":
            score -= 18.0
            reasons.append("Tobacco-forward recipes are often weak Pod/Dispo candidates (harshness/body tradeoffs).")
        elif dom == "cooling_focused":
            score -= 4.0
            reasons.append("Cooling-forward mixes need careful Pod handling to avoid harshness stacking.")
        if dispo_fp is not None:
            score += min(10.0, float(dispo_fp.get("dispo_fit_score") or 0) * 11.0)
        if pod_subtype:
            bonus, note = get_subtype_fit_bonus_and_note(pod_subtype)
            score += bonus * (0.55 + 0.45 * float(pod_subtype_confidence or 0.65))
            if note:
                reasons.append(f"Subtype ({pod_subtype}): {note}")
            _align = {
                "candy_fruit_dispo": {"fruit_candy"},
                "cool_fruit_dispo": {"fruit_candy", "cooling_focused"},
                "creamy_fruit_dispo": {"fruit_candy"},
                "beverage_dispo": {"beverage"},
                "dessert_dispo": {"dessert_bakery"},
                "tobacco_pod": {"tobacco"},
                "general_dispo": set(),
            }
            if dom in _align.get(pod_subtype, set()):
                score += 5.5 * float(pod_subtype_confidence or 0.65)
                reasons.append(f"Detected family '{dom}' aligns with Pod subtype '{pod_subtype}'.")
    else:
        score += 3.0
        reasons.append("MTL conversion: clarity and coil-friendly bias.")

    dcount = sum(1 for _k, v in diagnostics.items() if v)
    for k, v in diagnostics.items():
        if not v:
            continue
        if k == "low_body":
            score -= 5.0
        elif k == "low_sweet_backbone":
            score -= 4.0
        elif k == "low_topnote_punch":
            score -= 5.0
        elif k == "low_saturation":
            score -= 5.0
        elif k == "high_harshness_risk":
            score -= 8.0
        elif k == "high_coil_risk":
            score -= 5.0
        elif k == "overloaded":
            score -= 6.0
        elif k == "imbalanced_clusters":
            score -= 5.0

    if dcount >= 4:
        reasons.append("Several structural weaknesses detected — expect more conservative fit.")

    score = max(0.0, min(100.0, score))
    if score >= 72.0:
        label = "strong"
    elif score >= 46.0:
        label = "workable"
    else:
        label = "weak"

    if not reasons:
        reasons.append("Standard fit heuristics applied from current recipe signals vs mode targets.")

    return {"score": round(score, 2), "label": label, "reasons": reasons[:8]}


def _restraint_factor_and_notes(
    *,
    diagnostics: dict[str, Any],
    fit_rating: dict[str, Any],
    current_profile: dict[str, Any],
    target_signal: dict[str, Any],
) -> tuple[float, bool, list[str]]:
    """Scale down extra budget when recipe is already close to targets and fit is high."""
    notes: list[str] = []
    closeness = 0.0
    n_b = 0
    for key in ("top_note_score", "body_score", "sweetness_score", "saturation_score"):
        band = target_signal.get(key)
        if not isinstance(band, tuple) or len(band) != 2:
            continue
        lo, hi = float(band[0]), float(band[1])
        cur = float(current_profile.get(key) or 0.0)
        mid = (lo + hi) / 2.0
        span = max(1e-6, hi - lo)
        d = 1.0 - min(1.0, abs(cur - mid) / span)
        closeness += d
        n_b += 1
    closeness = closeness / max(1, n_b)

    issues = sum(1 for k, v in diagnostics.items() if v)
    fit_s = float(fit_rating.get("score") or 50.0)

    factor = 0.72 + 0.28 * closeness
    if fit_s >= 74.0:
        factor *= 0.90
    if issues <= 1:
        factor *= 0.92
    if issues >= 5:
        factor = min(1.0, factor * 1.05)
    factor = max(0.68, min(1.0, factor))

    applied = factor < 0.97
    if applied:
        notes.append("Recipe already close to target profile — adjustments kept conservative.")
    return factor, applied, notes


def _cluster_type_for_members(
    members: list[int], items_by_id: dict[int, dict[str, Any]]
) -> str:
    tags: set[str] = set()
    for rid in members:
        prof = str((items_by_id.get(rid) or {}).get("resolved_profile") or "").strip().lower()
        if prof in {"fruit", "tropical", "fruit_candy", "candy"}:
            tags.add("fruit")
        if prof in {"cream", "custard", "bakery", "dessert", "dessert_bakery"}:
            tags.add("cream")
        if prof == "beverage":
            tags.add("beverage")
        if prof == "tobacco" or "tobacco" in prof.split():
            tags.add("tobacco")
    if "fruit" in tags and "cream" in tags:
        return "fruit+cream"
    if "fruit" in tags:
        return "fruit"
    if "cream" in tags:
        return "creamy"
    if "beverage" in tags:
        return "beverage"
    if "tobacco" in tags:
        return "tobacco"
    return "mixed"


def _detect_dominant_clusters(
    *,
    present_ids: set[int],
    items_by_id: dict[int, dict[str, Any]],
    original_pct_by_rid: dict[int, float],
    analytics_by_ref: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Disjoint clusters from pairing links + simple profile complementarity (O(n^2) for small n)."""
    notes: list[str] = []
    ids = sorted(present_ids)
    parent: dict[int, int] = {i: i for i in ids}

    def _find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[rb] = ra

    for rid in ids:
        an = analytics_by_ref.get(rid) or {}
        for p in _safe_json_list(an.get("top_pairings_json")):
            pid = int(p.get("partner_id") or 0)
            if pid in present_ids and pid != rid:
                _union(rid, pid)

    prof_of = {i: str((items_by_id.get(i) or {}).get("resolved_profile") or "").strip().lower() for i in ids}

    def _class(p: str) -> str:
        if p in {"fruit", "tropical", "fruit_candy", "candy"}:
            return "f"
        if p in {"cream", "custard", "bakery", "dessert", "dessert_bakery"}:
            return "c"
        return "x"

    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            if _find(a) == _find(b):
                continue
            ca, cb = _class(prof_of[a]), _class(prof_of[b])
            if (ca == "f" and cb == "c") or (ca == "c" and cb == "f"):
                _union(a, b)

    groups: dict[int, list[int]] = {}
    for i in ids:
        r = _find(i)
        groups.setdefault(r, []).append(i)

    ot = sum(float(original_pct_by_rid.get(i, 0.0)) for i in ids)
    out: list[dict[str, Any]] = []
    for _root, members in groups.items():
        if len(members) < 2:
            continue
        w = sum(float(original_pct_by_rid.get(m, 0.0)) for m in members)
        if ot > 1e-9 and w / ot < 0.10:
            continue
        ctype = _cluster_type_for_members(members, items_by_id)
        out.append(
            {
                "type": ctype,
                "members": [int(m) for m in members],
                "weight_pct": round(w, 4),
            }
        )

    out.sort(key=lambda c: float(c.get("weight_pct") or 0.0), reverse=True)
    if out:
        notes.append(f"Detected {len(out)} flavour cluster(s) for balance preservation.")
    return out, notes


def _preserve_cluster_ratios(
    *,
    suggested_pct_by_rid: dict[int, float],
    original_pct_by_rid: dict[int, float],
    clusters: list[dict[str, Any]],
    original_total: float,
) -> list[str]:
    """Restore within-cluster proportions to match original recipe (same cluster % sum as before call)."""
    notes: list[str] = []
    ot = max(1e-9, float(original_total))
    for cl in clusters:
        members = [int(m) for m in cl.get("members") or []]
        if len(members) < 2:
            continue
        w = float(cl.get("weight_pct") or 0.0)
        if w / ot < 0.11:
            continue
        osum = sum(float(original_pct_by_rid.get(m, 0.0)) for m in members)
        if osum < 1e-9:
            continue
        csum = sum(float(suggested_pct_by_rid.get(m, 0.0)) for m in members)
        for m in members:
            suggested_pct_by_rid[m] = csum * float(original_pct_by_rid.get(m, 0.0)) / osum
        notes.append(
            f"Cluster '{cl.get('type', '?')}': preserved internal balance ({len(members)} flavours)."
        )
    return notes


def _strip_internal_family_keys(fp: dict[str, Any]) -> dict[str, Any]:
    out = dict(fp)
    for k in ("pod_subtype_display_name", "pod_subtype_key"):
        out.pop(k, None)
    return out


def _target_driven_convert_formula(
    *,
    mode: str,
    baseline_rows: list[dict[str, Any]],
    baseline_sum: float,
    original_rows: list[dict[str, Any]],
    items_by_id: dict[int, dict[str, Any]],
    analytics_by_ref: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Budget-based conversion (primary path for MTL / Pod-Dispo):
    - adaptive target_total from family profile + saturation + potency
    - extra_flavor_budget distributed by eligibility weights (not row multipliers)
    - analytics max guardrails only; identity preservation vs original DTL formulation
    """
    mode_key = "POD_DISPO" if mode == TARGET_MODE_POD else ("MTL" if mode == TARGET_MODE_MTL else "DTL")
    target = TARGET_SIGNAL_PROFILES.get(mode_key) or TARGET_SIGNAL_PROFILES["DTL"]
    original_total_pct = sum(float(r.get("original_pct") or 0.0) for r in original_rows)
    original_pct_by_rid: dict[int, float] = {int(r["ingredient_id"]): float(r.get("original_pct") or 0.0) for r in original_rows}

    present_ids: set[int] = {int(r["ingredient_id"]) for r in baseline_rows}
    analytics_coverage = sum(1 for rid in present_ids if rid in analytics_by_ref) / max(1, len(present_ids))

    # Identity reference = original (DTL) formulation, not a pre-scaled baseline.
    baseline_pct_by_rid: dict[int, float] = dict(original_pct_by_rid)

    recipe_dist_rows: list[dict[str, Any]] = [
        {"ingredient_id": int(r["ingredient_id"]), "amount_percent": float(original_pct_by_rid.get(int(r["ingredient_id"]), 0.0))}
        for r in baseline_rows
    ]

    current_profile = compute_recipe_signal_profile(
        rows=recipe_dist_rows,
        items_by_id=items_by_id,
        analytics_by_ref=analytics_by_ref,
        present_ids=present_ids,
    )

    fam_info = detect_recipe_family(rows=original_rows, items_by_id=items_by_id)
    family_profile = get_family_target_profile(mode, str(fam_info.get("dominant_family") or "mixed"))

    dispo_fp_before: dict[str, Any] | None = None
    dispo_targets_merged: dict[str, Any] | None = None
    dispo_subtype_key = "general"
    dispo_subtype_label = "General commercial dispo"
    pod_subtype_canonical = ""
    pod_subtype_confidence = 0.5
    pod_subtype_reasoning: list[str] = []
    pod_subtype_description = ""
    pod_subtype_merge_key: str | None = None
    perceptual_cfg_pod: dict[str, float] = {}
    dispo_fit_rating_key = ""
    dispo_fit_numeric = 0.0
    dispo_key_weaknesses: list[str] = []
    dispo_key_strengths: list[str] = []
    dispo_suggestions: list[str] = []
    dispo_advisories: list[str] = []

    if mode_key == "POD_DISPO":
        dispo_fp_before = compute_dispo_recipe_fingerprint(
            rows=recipe_dist_rows,
            items_by_id=items_by_id,
            analytics_by_ref=analytics_by_ref,
            present_ids=present_ids,
        )
        pod_subtype_info = detect_pod_subtype(
            fam_info=fam_info,
            rows=original_rows,
            items_by_id=items_by_id,
            dispo_fp=dispo_fp_before,
            original_total_pct=original_total_pct,
        )
        pod_subtype_canonical = str(pod_subtype_info.get("pod_subtype") or "general_dispo")
        pod_subtype_confidence = float(pod_subtype_info.get("pod_subtype_confidence") or 0.5)
        pod_subtype_reasoning = list(pod_subtype_info.get("pod_subtype_reasoning") or [])
        pod_subtype_description = str(pod_subtype_info.get("pod_subtype_description") or "")
        pod_subtype_merge_key = pod_subtype_info.get("pod_subtype_merge_key")
        dispo_subtype_label = str(pod_subtype_info.get("pod_subtype_display_name") or dispo_subtype_label)
        dispo_subtype_key = str(pod_subtype_merge_key or "general")
        family_profile = apply_pod_subtype_to_family_profile(family_profile, pod_subtype_canonical)
        perceptual_cfg_pod = get_pod_perceptual_config(pod_subtype_canonical)

        dispo_targets_merged = merge_disposubtype_into_targets(pod_subtype_merge_key)
        (
            dispo_fit_rating_key,
            dispo_fit_numeric,
            dispo_key_weaknesses,
            dispo_key_strengths,
            dispo_suggestions,
            dispo_advisories,
        ) = classify_dispo_fit_rating_and_advisories(dispo_fp_before, dispo_targets_merged)
        harsh_cap = float(dispo_targets_merged["harshness_risk_max"])
        coil_cap = float(dispo_targets_merged["coil_risk_max"])
    else:
        harsh_cap = float(target["harshness_risk_max"])
        coil_cap = float(target["coil_risk_max"])

    diagnostics = _compute_recipe_diagnostics(
        mode_key=mode_key,
        current_profile=current_profile,
        target_signal=target,
        family_profile=family_profile,
        dispo_fp=dispo_fp_before,
        original_pct_by_rid=original_pct_by_rid,
        present_ids=present_ids,
        items_by_id=items_by_id,
    )

    fit_rating = _compute_conversion_fit_rating(
        mode_key=mode_key,
        dominant_family=str(fam_info.get("dominant_family") or "mixed"),
        family_confidence=float(fam_info.get("family_confidence") or 0.5),
        diagnostics=diagnostics,
        current_profile=current_profile,
        dispo_fp=dispo_fp_before,
        pod_subtype=pod_subtype_canonical if mode_key == "POD_DISPO" and pod_subtype_canonical else None,
        pod_subtype_confidence=pod_subtype_confidence if mode_key == "POD_DISPO" else None,
    )

    adaptive_target_pre_restraint, adaptive_uplift_notes = _compute_adaptive_target_total_flavor(
        mode_key=mode_key,
        original_total=original_total_pct,
        family_profile=family_profile,
        signal_profile=current_profile,
        original_pct_by_rid=original_pct_by_rid,
        items_by_id=items_by_id,
        present_ids=present_ids,
    )
    extra_budget_pre = round(float(adaptive_target_pre_restraint) - original_total_pct, 6)
    restraint_factor, restraint_applied, restraint_notes = _restraint_factor_and_notes(
        diagnostics=diagnostics,
        fit_rating=fit_rating,
        current_profile=current_profile,
        target_signal=target,
    )
    extra_flavor_budget = round(extra_budget_pre * restraint_factor, 6)
    target_total = round(original_total_pct + extra_flavor_budget, 6)
    baseline_sum = float(target_total)

    if restraint_applied:
        adaptive_uplift_notes = list(adaptive_uplift_notes) + list(restraint_notes)
        if abs(restraint_factor - 1.0) > 1e-6:
            adaptive_uplift_notes.append(
                f"Restraint: extra flavour budget scaled ×{restraint_factor:.2f} "
                f"(adaptive raw budget {extra_budget_pre:+.2f}% → {extra_flavor_budget:+.2f}%)."
            )

    dominant_clusters, cluster_detection_notes = _detect_dominant_clusters(
        present_ids=present_ids,
        items_by_id=items_by_id,
        original_pct_by_rid=original_pct_by_rid,
        analytics_by_ref=analytics_by_ref,
    )
    if cluster_detection_notes:
        adaptive_uplift_notes.extend(cluster_detection_notes)

    weakness_summary: list[str] = []
    if diagnostics.get("low_body"):
        weakness_summary.append("Body signal is below the target band — the vape may feel hollow or thin.")
    if diagnostics.get("low_sweet_backbone"):
        weakness_summary.append("Sweet backbone is below target — candy/dispo presence may feel weak.")
    if diagnostics.get("low_topnote_punch"):
        weakness_summary.append("Top-note / brightness punch is below target — impact may feel muted.")
    if diagnostics.get("low_saturation"):
        weakness_summary.append("Saturation signal is low — overall flavour density may feel understated.")
    if diagnostics.get("high_harshness_risk"):
        weakness_summary.append("Harshness risk is elevated — bright/top boosts are limited by guardrails.")
    if diagnostics.get("high_coil_risk"):
        weakness_summary.append("Coil-risk signals are elevated — avoid pushing volatile accents.")
    if diagnostics.get("overloaded"):
        weakness_summary.append("Recipe structure looks overloaded (many high-% lines) — conversion will stay cautious.")
    if diagnostics.get("imbalanced_clusters"):
        weakness_summary.append("Accent mass dominates main notes — internal balance may be fragile.")

    adjustment_strategy_notes: list[str] = []
    if fit_rating.get("label") == "weak":
        adjustment_strategy_notes.append("Engine fit is weak for this mode — keep changes conservative and plan manual finishing.")
    elif fit_rating.get("label") == "strong":
        adjustment_strategy_notes.append("Engine fit is strong — standard budget allocation should track commercial intent well.")
    else:
        adjustment_strategy_notes.append("Engine fit is workable — allocation follows family tilt with guardrails.")
    if diagnostics.get("high_harshness_risk"):
        adjustment_strategy_notes.append("Harshness-aware: accent/top boosts are damped where harshness_bias is high.")
    if diagnostics.get("low_body") and mode_key == "POD_DISPO":
        adjustment_strategy_notes.append("Body-forward weighting enabled where roles/profiles allow (Pod/Dispo fullness).")
    if mode_key == "POD_DISPO" and pod_subtype_canonical:
        adjustment_strategy_notes.append(
            f"Pod subtype '{pod_subtype_canonical}' (confidence {pod_subtype_confidence:.0%}) tunes uplift emphasis, commercial merge targets, and perceptual dominance."
        )
        if pod_subtype_description:
            adjustment_strategy_notes.append(str(pod_subtype_description)[:240])

    cluster_preservation_notes: list[str] = []

    harsh_excess = max(0.0, float(current_profile["harshness_risk_score"]) - harsh_cap) / max(1e-6, harsh_cap)
    coil_excess = max(0.0, float(current_profile["coil_risk_score"]) - coil_cap) / max(1e-6, coil_cap)

    fit_score = current_profile.get("dispo_suitability_score") if mode_key == "POD_DISPO" else current_profile.get("mtl_suitability_score")
    if mode_key == "POD_DISPO" and dispo_fit_rating_key:
        fit_label = {"strong_dispo_candidate": "Strong", "workable_with_tuning": "Workable", "weak_dispo_candidate": "Weak"}.get(
            dispo_fit_rating_key, _fit_label_from_score(float(fit_score or 0.0))
        )
    else:
        fit_label = _fit_label_from_score(float(fit_score or 0.0))

    # Compute needed category moves.
    top_need_up, top_need_down = _band_need_score(float(current_profile["top_note_score"]), target["top_note_score"])
    body_need_up, body_need_down = _band_need_score(float(current_profile["body_score"]), target["body_score"])
    sweet_need_up, sweet_need_down = _band_need_score(float(current_profile["sweetness_score"]), target["sweetness_score"])
    cooling_need_up, cooling_need_down = _band_need_score(float(current_profile["cooling_score"]), target["cooling_score"])

    impact_need_up = impact_need_down = 0.0
    punch_need_up = punch_need_down = 0.0
    sweet_b_need_up = sweet_b_need_down = 0.0
    body_d_need_up = body_d_need_down = 0.0
    cool_pres_need_up = cool_pres_need_down = 0.0
    sat_need_up = sat_need_down = 0.0
    if mode_key == "POD_DISPO" and dispo_fp_before is not None and dispo_targets_merged is not None:
        impact_need_up, impact_need_down = _band_need_score(float(dispo_fp_before["impact_score"]), dispo_targets_merged["impact_score"])
        punch_need_up, punch_need_down = _band_need_score(
            float(dispo_fp_before["topnote_punch_score"]), dispo_targets_merged["topnote_punch_score"]
        )
        sweet_b_need_up, sweet_b_need_down = _band_need_score(
            float(dispo_fp_before["sweet_backbone_score"]), dispo_targets_merged["sweet_backbone_score"]
        )
        body_d_need_up, body_d_need_down = _band_need_score(
            float(dispo_fp_before["body_density_score"]), dispo_targets_merged["body_density_score"]
        )
        cool_pres_need_up, cool_pres_need_down = _band_need_score(
            float(dispo_fp_before["cooling_presentation_score"]), dispo_targets_merged["cooling_presentation_score"]
        )
        sat_need_up, sat_need_down = _band_need_score(float(dispo_fp_before["saturation_score"]), dispo_targets_merged["saturation_score"])

    emphasis = target.get("emphasis") or {"top": 1.0, "body": 1.0, "sweet": 1.0, "cooling": 1.0}
    top_em = float(emphasis.get("top") or 1.0)
    body_em = float(emphasis.get("body") or 1.0)
    sweet_em = float(emphasis.get("sweet") or 1.0)
    cool_em = float(emphasis.get("cooling") or 1.0)

    # Pairing support: for each rid, how supported it is by its known partners being present.
    pair_support: dict[int, bool] = {}
    for rid in present_ids:
        analytics = analytics_by_ref.get(rid) or {}
        top_pairings = _safe_json_list(analytics.get("top_pairings_json"))
        pair_support[rid] = any(int(p.get("partner_id") or 0) in present_ids for p in top_pairings)

    suggested_pct_by_rid: dict[int, float] = {}
    per_flavor_reasons: dict[int, str] = {}
    per_flavor_conf_note: dict[int, str] = {}
    is_pod = mode_key == "POD_DISPO"
    perceptual_dominance_notes: list[str] = []
    perceptual_tags_by_rid: dict[int, list[str]] = {}

    if extra_flavor_budget <= 1e-9:
        for rid in present_ids:
            suggested_pct_by_rid[rid] = float(original_pct_by_rid.get(rid, 0.0))
            per_flavor_reasons[rid] = "No extra flavour budget; preserving original DTL formulation."
            per_flavor_conf_note[rid] = "Analytics: guardrails only (not avg/min-driven)"
    else:
        weights: dict[int, float] = {}
        for rid in present_ids:
            meta = items_by_id.get(rid) or {}
            orig = float(original_pct_by_rid.get(rid, 0.0))
            tw, bw, sw, cw = _alloc_category_weights(meta)
            base_w = _base_budget_weight(meta=meta, family_profile=family_profile)
            sig_b = 1.0
            sig_b += 0.15 * (
                top_need_up * top_em * tw
                + body_need_up * body_em * bw
                + sweet_need_up * sweet_em * sw
                + cooling_need_up * cool_em * cw
            )
            sig_b -= 0.10 * (top_need_down * top_em * tw + cooling_need_down * cool_em * cw)
            if is_pod and dispo_targets_merged is not None:
                sig_b += 0.12 * (
                    impact_need_up * tw
                    + punch_need_up * tw
                    + body_d_need_up * bw
                    + sweet_b_need_up * sw * 0.75
                )
                if harsh_excess > 0.14:
                    sig_b *= max(0.72, 1.0 - 0.18 * min(1.0, harsh_excess))
            sig_b = max(0.4, sig_b)

            pw = _potency_alloc_factor(str(meta.get("resolved_potency") or ""))
            an = analytics_by_ref.get(rid) or {}
            head = 1.0
            if an.get("max_pct") is not None and orig >= float(an["max_pct"]) * 0.91:
                head = 0.11
            elif an.get("max_pct") is not None and orig >= float(an["max_pct"]) * 0.82:
                head = 0.50
            hb = float(meta.get("harshness_bias") or 0)
            harsh_f = 0.45 if hb >= 2.5 and tw > 0.5 else (0.72 if hb >= 1.9 else 1.0)
            ps = _pod_suitability_score(meta.get("resolved_pod_suitability"))
            pair_m = 1.085 if pair_support.get(rid) else 1.0
            nr = 0.78 if int(meta.get("needs_review") or 0) == 1 else 1.0
            w_alloc = base_w * sig_b * pw * head * harsh_f * ps * pair_m * nr
            if is_pod:
                w_alloc *= _pod_role_weight(meta, target)
            if is_pod and harsh_excess > 0.11 and cw > 0.45:
                w_alloc *= 0.80
            p_tags: list[str] = []
            if is_pod:
                w_alloc, p_tags = _pod_perceptual_apply_to_weight(
                    w_alloc,
                    meta,
                    tw,
                    bw,
                    impact_need_up=float(impact_need_up),
                    perceptual_cfg=perceptual_cfg_pod,
                )
                perceptual_tags_by_rid[rid] = p_tags
            weights[rid] = max(w_alloc, 1e-12)

        wsum = sum(weights.values())
        for rid in present_ids:
            orig = float(original_pct_by_rid.get(rid, 0.0))
            share = (weights[rid] / wsum) if wsum > 1e-12 else 0.0
            delta = extra_flavor_budget * share
            suggested_pct_by_rid[rid] = max(0.0, orig + delta)
            rs: list[str] = [
                f"extra budget Δ{delta:+.3f}% (share {share*100:.1f}% of budget)",
                "family-tilted eligibility weighting (not row multiplier)",
            ]
            if pair_support.get(rid):
                rs.append("pairing support")
            pt = perceptual_tags_by_rid.get(rid) or []
            if pt:
                rs.append("perceptual dominance: " + "; ".join(pt[:4]))
            per_flavor_reasons[rid] = "; ".join(rs)
            per_flavor_conf_note[rid] = "Analytics: max guardrails only (not avg/min-driven)"

        if is_pod:
            perceptual_dominance_notes.append(
                "Pod/Dispo perceptual layer: label-driving lines use an exponential boost curve; "
                "body lines use a sub-linear (log) curve; hidden fillers can be capped after the initial split."
            )
            n_tagged = sum(1 for r in present_ids if perceptual_tags_by_rid.get(r))
            if n_tagged:
                perceptual_dominance_notes.append(f"Perceptual cues applied on {n_tagged} flavour line(s) (see row reasons).")
            perceptual_dominance_notes.extend(
                _pod_perceptual_post_allocation(
                    suggested_pct_by_rid=suggested_pct_by_rid,
                    original_pct_by_rid=original_pct_by_rid,
                    present_ids=present_ids,
                    items_by_id=items_by_id,
                    extra_flavor_budget=float(extra_flavor_budget),
                    impact_need_up=float(impact_need_up),
                    baseline_sum=float(baseline_sum),
                    perceptual_cfg=perceptual_cfg_pod,
                )
            )

        _accent_preservation_trim(
            suggested_pct_by_rid=suggested_pct_by_rid,
            original_pct_by_rid=original_pct_by_rid,
            present_ids=present_ids,
            items_by_id=items_by_id,
            target_total=float(baseline_sum),
        )

        _finalize_budget_allocation(
            suggested_pct_by_rid=suggested_pct_by_rid,
            target_total=float(baseline_sum),
            present_ids=present_ids,
            items_by_id=items_by_id,
            analytics_by_ref=analytics_by_ref,
            is_pod=is_pod,
        )

        cluster_preservation_notes = _preserve_cluster_ratios(
            suggested_pct_by_rid=suggested_pct_by_rid,
            original_pct_by_rid=original_pct_by_rid,
            clusters=dominant_clusters,
            original_total=original_total_pct,
        )

        s_after_cluster = sum(float(suggested_pct_by_rid.get(rid, 0.0)) for rid in present_ids)
        if s_after_cluster > 1e-9 and abs(s_after_cluster - float(baseline_sum)) > 1e-4:
            kc = float(baseline_sum) / s_after_cluster
            for rid in present_ids:
                suggested_pct_by_rid[rid] = max(0.0, float(suggested_pct_by_rid.get(rid, 0.0)) * kc)

    # Identity preservation safeguard: dampen distribution drift beyond baseline.
    identity_shift_score = _compute_identity_shift_score(
        baseline_pct_by_rid=baseline_pct_by_rid,
        suggested_pct_by_rid=suggested_pct_by_rid,
        items_by_id=items_by_id,
        analytics_by_ref=analytics_by_ref,
        present_ids=present_ids,
    )
    id_threshold = 0.52 if mode_key == "POD_DISPO" else 0.40
    if identity_shift_score > id_threshold:
        # Soft dampening: keep it explainable and stable (Pod/Dispo allows a bit more structural lift).
        damp = max(0.45, 1.0 - (identity_shift_score - id_threshold) / 0.60)
        for rid in list(suggested_pct_by_rid.keys()):
            b = float(baseline_pct_by_rid.get(rid) or 0.0)
            s = float(suggested_pct_by_rid[rid])
            suggested_pct_by_rid[rid] = b + (s - b) * damp
        # Re-normalize after damping.
        suggested_sum2 = sum(suggested_pct_by_rid.values())
        if suggested_sum2 > 1e-9:
            norm2 = baseline_sum / suggested_sum2
            for rid in list(suggested_pct_by_rid.keys()):
                suggested_pct_by_rid[rid] = float(suggested_pct_by_rid[rid]) * norm2

    # Compute after-profile for strengths/warnings.
    suggested_dist_rows = [{"ingredient_id": rid, "amount_percent": suggested_pct_by_rid.get(rid, 0.0)} for rid in present_ids]
    after_profile = compute_recipe_signal_profile(
        rows=suggested_dist_rows,
        items_by_id=items_by_id,
        analytics_by_ref=analytics_by_ref,
        present_ids=present_ids,
    )

    dispo_fp_after: dict[str, Any] | None = None
    dispo_change_summary: list[str] = []
    if mode_key == "POD_DISPO":
        dispo_fp_after = compute_dispo_recipe_fingerprint(
            rows=suggested_dist_rows,
            items_by_id=items_by_id,
            analytics_by_ref=analytics_by_ref,
            present_ids=present_ids,
        )
        if dispo_fp_before:
            for key in (
                "impact_score",
                "saturation_score",
                "sweet_backbone_score",
                "body_density_score",
                "topnote_punch_score",
                "cooling_presentation_score",
            ):
                b = float(dispo_fp_before.get(key) or 0)
                a = float(dispo_fp_after.get(key) or 0)
                if abs(a - b) >= 0.02:
                    dispo_change_summary.append(f"{key}: {b:.2f} → {a:.2f}")

    harsh_warn_max = float(target["harshness_risk_max"])
    coil_warn_max = float(target["coil_risk_max"])
    cooling_warn_band = target["cooling_score"]
    if mode_key == "POD_DISPO" and dispo_targets_merged is not None:
        harsh_warn_max = float(dispo_targets_merged["harshness_risk_max"])
        coil_warn_max = float(dispo_targets_merged["coil_risk_max"])
        cooling_warn_band = dispo_targets_merged["cooling_presentation_score"]

    # Strengths: what moved in the right direction.
    strengths: list[str] = []
    strengths.append(
        f"Adaptive total: {original_total_pct:.2f}% → {target_total:.2f}% "
        f"(extra flavour budget {extra_flavor_budget:+.2f}%; family-aware allocation)."
    )
    if abs(float(adaptive_target_pre_restraint) - float(target_total)) > 1e-4:
        strengths.append(
            f"Pre-restraint adaptive target was {float(adaptive_target_pre_restraint):.2f}% "
            f"(restraint → {target_total:.2f}%)."
        )
    strengths.append(
        f"Conversion fit (engine): {fit_rating.get('label')} — score {fit_rating.get('score')}/100."
    )
    for _r in fit_rating.get("reasons") or []:
        if _r and _r not in strengths:
            strengths.append(_r)
    for _n in adaptive_uplift_notes:
        strengths.append(_n)
    if mode_key == "POD_DISPO" and dispo_fit_rating_key:
        strengths.append(
            f"Dispo fit: {dispo_fit_rating_key.replace('_', ' ')} "
            f"(score {dispo_fit_numeric:.2f}; {dispo_subtype_label})."
        )
        for s in dispo_key_strengths:
            if s and s not in strengths:
                strengths.append(s)
    else:
        strengths.append(f"{mode_key} fit rating: {fit_label}")
    top_delta = float(after_profile["top_note_score"]) - float(current_profile["top_note_score"])
    body_delta = float(after_profile["body_score"]) - float(current_profile["body_score"])
    sweet_delta = float(after_profile["sweetness_score"]) - float(current_profile["sweetness_score"])
    cooling_delta = float(after_profile["cooling_score"]) - float(current_profile["cooling_score"])

    if mode_key == "MTL":
        if top_delta > 0.05:
            strengths.append("MTL focus: tighter top-note clarity")
        if body_delta > 0.03:
            strengths.append("MTL smoothness: maintained enough body to avoid thinness")
        if cooling_delta < -0.02:
            strengths.append("MTL sharpness control: reduced cooling intensity")
    elif mode_key == "POD_DISPO":
        band = target.get("total_flavor_target_band") or (8.5, 14.0)
        strengths.append(
            f"Pod/Dispo total flavour baseline {baseline_sum:.2f}% (target band {band[0]}–{band[1]}%; saturation-first, not DTL-downscaled)."
        )
        if baseline_sum > original_total_pct + 0.04:
            strengths.append(
                f"Higher flavour load vs source recipe: {original_total_pct:.2f}% → {baseline_sum:.2f}% (Pod/Dispo saturation)."
            )
        if top_delta > 0.06:
            strengths.append("Pod/Dispo impact: stronger immediate top-note presence")
        if body_delta > 0.03:
            strengths.append("Pod/Dispo saturation: strengthened body support for fullness")
        if sweet_delta > 0.04 and float(after_profile["sweetness_score"]) <= float(target["sweetness_score"][1]) + 1e-9:
            strengths.append("Pod/Dispo palate: boosted sweetness support without runaway dominance")
        if cooling_delta > 0.03:
            strengths.append("Pod/Dispo freshness: selective cooling lift where appropriate")
        if pod_subtype_canonical:
            strengths.append(
                f"Pod subtype '{pod_subtype_canonical}' — {dispo_subtype_label} (confidence {pod_subtype_confidence:.0%})."
            )
            for rsn in pod_subtype_reasoning[:2]:
                if rsn and rsn not in strengths:
                    strengths.append(rsn)
        for _pn in perceptual_dominance_notes:
            if _pn and _pn not in strengths:
                strengths.append(_pn)

    pod_subtype_warnings: list[str] = []
    if mode_key == "POD_DISPO" and pod_subtype_canonical:
        if pod_subtype_canonical == "tobacco_pod" and diagnostics.get("high_harshness_risk"):
            pod_subtype_warnings.append(
                "Tobacco pod profile: harshness is elevated — conversion keeps bright/top surges conservative."
            )
        if pod_subtype_canonical == "cool_fruit_dispo" and float(current_profile.get("cooling_score") or 0) > 0.42:
            pod_subtype_warnings.append(
                "Cool-fruit subtype: cooling is already prominent — avoid stacking WS/menthol in manual tweaks."
            )
        if pod_subtype_canonical == "dessert_dispo" and diagnostics.get("low_body"):
            pod_subtype_warnings.append("Dessert subtype expects body density — body signal is still below target.")
        if pod_subtype_canonical == "beverage_dispo" and diagnostics.get("low_topnote_punch"):
            pod_subtype_warnings.append("Beverage subtype: top-note / clarity still below target after this pass.")
        if pod_subtype_canonical == "creamy_fruit_dispo" and diagnostics.get("imbalanced_clusters"):
            pod_subtype_warnings.append("Creamy-fruit subtype: cluster balance was fragile — review cream vs fruit balance.")
        if pod_subtype_canonical == "candy_fruit_dispo" and diagnostics.get("low_sweet_backbone"):
            pod_subtype_warnings.append("Candy-fruit subtype: sweet backbone still thin — consider sweetener support.")

    # Warnings (non-blocking).
    warnings: list[str] = []
    if fit_label == "Weak":
        warnings.append(f"{mode_key}: weak candidate fit; expect more conservative/less dramatic conversion.")
    if float(after_profile["harshness_risk_score"]) > harsh_warn_max + 1e-9:
        warnings.append(f"{mode_key}: harshness risk remains high after conversion; consider cautious tuning.")
    if float(after_profile["coil_risk_score"]) > coil_warn_max + 1e-9:
        warnings.append(f"{mode_key}: coil-risk signals are elevated; proceed carefully.")
    if float(after_profile["cooling_score"]) > float(cooling_warn_band[1]) + 1e-9:
        warnings.append(f"{mode_key}: cooling is near/above a typical band; may feel sharp on Pod/Dispo.")
    if float(after_profile["sweetness_score"]) > float(target["sweetness_score"][1]) + 1e-9:
        warnings.append(f"{mode_key}: sweetness is trending high; avoid turning the mix into a sugar-dominant profile.")
    if float(identity_shift_score) > 0.45:
        warnings.append("Conversion may drift from the original recipe identity (identity shift guardrail applied).")
    if analytics_coverage < 0.45:
        warnings.append("Analytics coverage is limited for this recipe; changes are more heuristic than data-driven.")
    if float(after_profile["pairing_coherence_score"]) < 0.25:
        warnings.append("Pairing coherence is low for this set; expect weaker synergy reliability.")

    if diagnostics.get("low_body"):
        warnings.append("Low body — may feel hollow on compact coils unless supported by body-forward lines.")
    if diagnostics.get("low_sweet_backbone"):
        warnings.append("Sweet backbone is insufficient vs target — consider sweetener support after tasting.")
    if diagnostics.get("low_topnote_punch"):
        warnings.append("Top-note punch is low vs target — recipe may feel muted after conversion.")
    if diagnostics.get("high_harshness_risk"):
        warnings.append("High harshness risk — top-note boost limited by harshness guardrails.")
    if diagnostics.get("high_coil_risk"):
        warnings.append("High coil-risk profile — volatile accent boosts are limited.")
    if mode_key == "POD_DISPO" and str(fit_rating.get("label") or "") == "weak":
        warnings.append("Recipe may feel too weak for Pod/Dispo — treat preview as advisory.")
    if restraint_applied:
        warnings.append("Recipe already near optimal — minimal changes applied (restraint scaling).")
    if cluster_preservation_notes:
        warnings.append("Cluster imbalance detected and corrected — dominant clusters scaled together.")

    for _psw in pod_subtype_warnings:
        if _psw and _psw not in warnings:
            warnings.append(_psw)

    if mode_key == "POD_DISPO" and dispo_fp_before is not None and dispo_targets_merged is not None:
        cband = dispo_targets_merged.get("cooling_presentation_score")
        if isinstance(cband, tuple) and len(cband) == 2:
            if float(dispo_fp_before.get("cooling_presentation_score") or 0) >= float(cband[1]) - 0.03:
                warnings.append("Cooling already high — further increase avoided where possible.")

    if mode_key == "POD_DISPO":
        warnings.extend([a for a in dispo_advisories if a not in warnings])
        if dispo_fp_after is not None and dispo_targets_merged is not None:
            dm = dispo_targets_merged
            if float(dispo_fp_after["impact_score"]) < float(dm["impact_score"][0]) - 0.03:
                warnings.append(
                    "Pod/Dispo: recipe may still feel too soft for disposable-style delivery after this pass."
                )
            if float(dispo_fp_after["sweet_backbone_score"]) < float(dm["sweet_backbone_score"][0]) - 0.03:
                warnings.append("Pod/Dispo: sweet backbone remains weak — consider a small sweetener-support tweak.")
            if float(dispo_fp_after["body_density_score"]) < float(dm["body_density_score"][0]) - 0.03:
                warnings.append("Pod/Dispo: body density is low and may feel hollow on a compact coil.")
            if float(dispo_fp_after["harshness_risk_score"]) > float(dm["harshness_risk_max"]) - 0.02:
                warnings.append(
                    "Pod/Dispo: harshness risk is elevated after conversion; ease bright accents if it vapes sharp."
                )
            if float(dispo_fp_after["cooling_presentation_score"]) >= float(dm["cooling_presentation_score"][1]) - 0.02:
                warnings.append("Pod/Dispo: cooling is already near the upper limit for this commercial profile.")
        if dispo_fit_rating_key == "weak_dispo_candidate":
            warnings.append(
                "Pod/Dispo: weak dispo candidate for one-click conversion — treat preview as advisory and plan manual finishing."
            )

    # Confidence: higher is more target-aligned & better-supported by analytics.
    needs_review_fraction = float(current_profile.get("needs_review_fraction") or 0.0)
    pairing_coh = float(after_profile.get("pairing_coherence_score") or 0.0)
    fit = float(current_profile.get("dispo_suitability_score") if mode_key == "POD_DISPO" else current_profile.get("mtl_suitability_score") or 0.0)
    if mode_key == "POD_DISPO":
        fit = 0.52 * float(dispo_fit_numeric) + 0.48 * float(fit)
    confidence_score = (
        0.34 * analytics_coverage
        + 0.20 * pairing_coh
        + 0.30 * fit
        + 0.16 * (1.0 - needs_review_fraction)
        - 0.40 * float(identity_shift_score)
    )
    confidence_score = max(0.0, min(1.0, float(confidence_score)))
    if confidence_score >= 0.78:
        confidence_label = "High"
    elif confidence_score >= 0.52:
        confidence_label = "Medium"
    else:
        confidence_label = "Low"

    # Build final rows.
    suggested_rows: list[dict[str, Any]] = []
    for br in baseline_rows:
        rid = int(br["ingredient_id"])
        original_pct = float(original_pct_by_rid.get(rid) or 0.0)
        suggested_pct = float(suggested_pct_by_rid.get(rid, 0.0))
        # Keep UI-friendly precision.
        suggested_pct = round(suggested_pct, 4)
        suggested_rows.append(
            {
                **br,
                "suggested_pct": suggested_pct,
                "final_pct": suggested_pct,
                "change_pct": round(suggested_pct - original_pct, 4),
                "reason": per_flavor_reasons.get(rid, "adjusted to target profile"),
                "confidence_note": per_flavor_conf_note.get(rid),
            }
        )

    meta: dict[str, Any] = {
        "confidence_score": float(confidence_score),
        "confidence_label": confidence_label,
        "warnings": warnings,
        "strengths": strengths,
        "identity_shift_score": float(identity_shift_score),
        "fit_label": fit_label,
        "after_signals": after_profile,
        "before_signals": current_profile,
        "target_total_flavor_pct": float(target_total),
        "extra_flavor_budget": float(extra_flavor_budget),
        "adaptive_uplift_notes": list(adaptive_uplift_notes),
        "conversion_engine": "budget_allocation_v1",
        "recipe_family_detected": str(fam_info.get("dominant_family") or "mixed"),
        "family_target_profile_key": get_family_target_profile_key(mode, str(fam_info.get("dominant_family") or "mixed")),
        "diagnostics": dict(diagnostics),
        "fit_rating": dict(fit_rating),
        "dominant_clusters": [
            {
                "type": c.get("type"),
                "member_count": len(c.get("members") or []),
                "weight_pct": c.get("weight_pct"),
            }
            for c in (dominant_clusters or [])
        ],
        "dominant_clusters_detail": list(dominant_clusters),
        "cluster_preservation_notes": list(cluster_preservation_notes),
        "adjustment_strategy_notes": list(adjustment_strategy_notes),
        "restraint_applied": bool(restraint_applied),
        "restraint_factor": float(restraint_factor),
        "weakness_summary": list(weakness_summary),
        "adaptive_target_total_pre_restraint": float(adaptive_target_pre_restraint),
        "perceptual_dominance_notes": list(perceptual_dominance_notes),
        "pod_subtype": pod_subtype_canonical if mode_key == "POD_DISPO" else None,
        "pod_subtype_confidence": float(pod_subtype_confidence) if mode_key == "POD_DISPO" else None,
        "pod_subtype_reasoning": list(pod_subtype_reasoning) if mode_key == "POD_DISPO" else [],
        "pod_subtype_display_name": str(dispo_subtype_label) if mode_key == "POD_DISPO" else None,
        "pod_subtype_description": str(pod_subtype_description) if mode_key == "POD_DISPO" else None,
        "pod_subtype_merge_key": pod_subtype_merge_key,
        "pod_subtype_warnings": list(pod_subtype_warnings) if mode_key == "POD_DISPO" else [],
        "family_target_profile_effective": (
            _strip_internal_family_keys(dict(family_profile)) if mode_key == "POD_DISPO" else None
        ),
    }
    if mode_key == "POD_DISPO" and dispo_fp_before is not None:
        def _fp_for_json(d: dict[str, Any]) -> dict[str, Any]:
            skip = {"score_rationale"}
            return {k: v for k, v in d.items() if k not in skip}

        meta["dispo_fingerprint"] = _fp_for_json(dispo_fp_before)
        meta["dispo_fingerprint_after"] = _fp_for_json(dispo_fp_after) if dispo_fp_after else None
        meta["dispo_subtype_key"] = dispo_subtype_key
        meta["dispo_subtype_label"] = dispo_subtype_label
        meta["dispo_fit_rating"] = dispo_fit_rating_key
        meta["dispo_fit_score"] = float(dispo_fit_numeric)
        meta["dispo_key_weaknesses"] = list(dispo_key_weaknesses)
        meta["dispo_key_strengths"] = list(dispo_key_strengths)
        meta["dispo_suggestions"] = list(dispo_suggestions)
        meta["dispo_advisories"] = list(dispo_advisories)
        meta["dispo_change_summary"] = dispo_change_summary
        meta["dispo_commercial_targets"] = {k: v for k, v in (dispo_targets_merged or {}).items()}
        meta["dispo_why_changed"] = (
            "Extra flavour budget was allocated by family-tilted eligibility (role, potency, analytics headroom, pairing) "
            "toward commercial dispo targets — see per-flavor reasons and dispo_change_summary."
        )
    return suggested_rows, meta



def _analytics_lookup_for_refs(*, analytics_rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for r in analytics_rows:
        rid = int(r["reference_id"])
        d = dict(r)
        out[rid] = d
    return out


def _safe_json_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return [raw]
    try:
        parsed = json.loads(str(raw))
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
        return []
    except Exception:
        return []


def _load_flavor_recipe_analytics_bulk(*, db, reference_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not reference_ids:
        return {}
    unique_ids = sorted(set(int(x) for x in reference_ids if x))
    placeholders = ",".join(["?"] * len(unique_ids))
    rows = db.execute(
        f"""
        SELECT *
        FROM flavor_recipe_analytics
        WHERE reference_id IN ({placeholders})
        """,
        unique_ids,
    ).fetchall()
    return _analytics_lookup_for_refs(analytics_rows=[dict(r) for r in rows])


def _compute_recipe_structure_signals(*, formula_rows: list[dict[str, Any]], items_by_id: dict[int, dict[str, Any]]) -> dict[str, float]:
    # Lightweight “structure analysis” from stored classification roles + observed recipe %.
    sums: dict[str, float] = {
        "top_pct": 0.0,
        "body_pct": 0.0,
        "sweet_pct": 0.0,
        "cooling_pct": 0.0,
        "brightness_bias_weighted": 0.0,
        "harshness_risk_weighted": 0.0,
        "coil_risk_weighted": 0.0,
    }
    for fr in formula_rows:
        rid = int(fr["ingredient_id"])
        pct = float(fr.get("amount_percent") or fr.get("original_pct") or 0.0)
        meta = items_by_id.get(rid) or {}
        role = str(meta.get("resolved_role") or "").strip().lower()
        profile = str(meta.get("resolved_profile") or "").strip().lower()

        if role in {"main_note", "accent"} or profile in {"fruit", "candy", "beverage", "fruit_candy"}:
            sums["top_pct"] += pct
        if role == "body" or profile in {"cream", "dessert", "dessert_bakery", "bakery", "custard"}:
            sums["body_pct"] += pct
        if role in {"sweetener_support", "sweetener"} or profile in {"sweetener"}:
            sums["sweet_pct"] += pct
        if role == "cooling" or profile == "cooling":
            sums["cooling_pct"] += pct

        sums["brightness_bias_weighted"] += pct * (float(meta.get("brightness_bias") or 0) / 2.0)
        sums["harshness_risk_weighted"] += pct * (float(meta.get("harshness_bias") or 0) / 2.0)
        sums["coil_risk_weighted"] += pct * (float(meta.get("coil_risk") or 0) / 2.0)

    return sums


def _compute_conversion_confidence_label(*, analytics_coverage: float, needs_review_count: int, pairing_support_count: int, flavor_count: int) -> tuple[float, str]:
    if flavor_count <= 0:
        return 0.0, "Low"
    # Explainable, rule-based confidence: coverage dominates; review flags and pairing support reduce it.
    needs_factor = 1.0 - min(0.5, needs_review_count / max(1, flavor_count) * 0.5)
    pairing_factor = min(1.0, pairing_support_count / max(1, flavor_count))
    score = (0.65 * analytics_coverage) + (0.2 * needs_factor) + (0.15 * pairing_factor)
    score = max(0.0, min(1.0, score))
    if score >= 0.78:
        return score, "High"
    if score >= 0.52:
        return score, "Medium"
    return score, "Low"


def _compute_tuning_guardrails(
    *,
    final_rows: list[dict[str, Any]],
    items_by_id: dict[int, dict[str, Any]],
    analytics_by_ref: dict[int, dict[str, Any]],
    suggested_total_pct: float,
) -> list[str]:
    # Advisory only: surface “you may have gone too far”.
    guardrails: list[str] = []
    total_pct = sum(float(r.get("final_pct") or 0.0) for r in final_rows)
    if suggested_total_pct and (total_pct > suggested_total_pct * 1.35 or total_pct < suggested_total_pct * 0.7):
        guardrails.append("Warning: total flavor percentage moved outside a typical conversion band; double-check balance for the target device.")

    for r in final_rows:
        rid = int(r["ingredient_id"])
        final_pct = float(r.get("final_pct") or 0.0)
        meta = items_by_id.get(rid) or {}
        analytics = analytics_by_ref.get(rid) or {}
        max_pct = analytics.get("max_pct")
        potency = str(meta.get("resolved_potency") or "").strip().lower()
        if max_pct is not None and final_pct > float(max_pct or 0.0) * 1.35:
            guardrails.append(f"Warning: {meta.get('brand','')} {meta.get('name','')} is pushed above its typical usage range.")
        if potency == "high" and final_pct > 5.0 and meta.get("coil_risk"):
            guardrails.append("Warning: a high-potency concentrate is both elevated and flagged with coil risk; proceed carefully.")

    # Sweetener/cooling extremes are common Pod/Dispo failure modes.
    sweet_sum = 0.0
    cooling_sum = 0.0
    for r in final_rows:
        rid = int(r["ingredient_id"])
        pct = float(r.get("final_pct") or 0.0)
        role = str(items_by_id.get(rid, {}).get("resolved_role") or "").strip().lower()
        profile = str(items_by_id.get(rid, {}).get("resolved_profile") or "").strip().lower()
        if role in {"sweetener_support", "sweetener"} or profile == "sweetener":
            sweet_sum += pct
        if role == "cooling" or profile == "cooling":
            cooling_sum += pct
    if sweet_sum > 3.0:
        guardrails.append("Advisory: sweetener total is high; sweetness can dominate and reduce perceived clarity on Pod/Dispo.")
    if cooling_sum > 2.0:
        guardrails.append("Advisory: cooling total is high; it may become harsh or steal attention from top notes.")

    # De-dup and cap.
    dedup: list[str] = []
    seen: set[str] = set()
    for g in guardrails:
        if g in seen:
            continue
        seen.add(g)
        dedup.append(g)
    return dedup[:6]


def _smart_convert_formula(
    *,
    mode: str,
    original_rows: list[dict[str, Any]],
    items_by_id: dict[int, dict[str, Any]],
    analytics_by_ref: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # Return (suggested_rows, meta) where meta includes totals, strengths, warnings, confidence.
    if mode == TARGET_MODE_DTL:
        total = sum(float(r.get("original_pct") or 0.0) for r in original_rows)
        rows: list[dict[str, Any]] = []
        for r in original_rows:
            original_pct = float(r.get("original_pct") or 0.0)
            rows.append(
                {
                    **r,
                    "suggested_pct": round(original_pct, 4),
                    "final_pct": round(original_pct, 4),
                    "change_pct": 0.0,
                    "reason": "DTL target: conversion not applied (suggested equals original).",
                    "confidence_note": "original",
                }
            )
        meta = {
            "baseline_sum": total,
            "suggested_total_pct": total,
            "final_total_pct": total,
            "confidence_score": 0.98,
            "confidence_label": "High",
            "warnings": [],
            "strengths": ["DTL: preserving the recipe’s original formulation profile."],
        }
        return rows, meta

    if mode == TARGET_MODE_POD:
        orig_total = sum(float(r.get("original_pct") or 0.0) for r in original_rows)
        _, pod_preview = _pod_dispo_target_total_and_meta(orig_total)
        baseline_rows = [{**r, "baseline_pct": float(r.get("original_pct") or 0.0)} for r in original_rows]
        baseline_sum = orig_total
        suggested_rows, meta = _target_driven_convert_formula(
            mode=mode,
            baseline_rows=baseline_rows,
            baseline_sum=baseline_sum,
            original_rows=original_rows,
            items_by_id=items_by_id,
            analytics_by_ref=analytics_by_ref,
        )
        meta.update(pod_preview)
        meta["pod_target_saturation_pct"] = meta.get("target_total_flavor_pct") or meta.get("pod_target_saturation_pct")
        meta["original_total_flavor_pct"] = round(orig_total, 4)
        strength0 = meta.get("strengths") or []
        meta["strengths"] = list(pod_preview.get("pod_scaling_notes") or []) + list(strength0)
        return suggested_rows, meta

    baseline_rows_mtl = [{**r, "baseline_pct": float(r.get("original_pct") or 0.0)} for r in original_rows]
    baseline_sum_mtl = sum(float(r.get("original_pct") or 0.0) for r in original_rows)

    return _target_driven_convert_formula(
        mode=mode,
        baseline_rows=baseline_rows_mtl,
        baseline_sum=baseline_sum_mtl,
        original_rows=original_rows,
        items_by_id=items_by_id,
        analytics_by_ref=analytics_by_ref,
    )


# ------------------------------------
# Safety / pre-flight checks
# ------------------------------------

def validate_safety(data: Dict[str, Any] | None) -> Tuple[bool, str | None]:
    """Validate that all required safety flags are present and truthy."""
    if not isinstance(data, dict):
        return False, "Safety payload missing."

    missing = [k for k in REQUIRED_SAFETY_FLAGS if not data.get(k)]
    if missing:
        return False, f"Safety protocols not confirmed. Missing: {', '.join(missing)}"
    return True, None


# ------------------------------------
# Batch initialisation
# ------------------------------------

def init_batch_for_recipe(
    *,
    recipe_id: int,
    version_id: int,
    vessel_size: float,
    style: str,
    mix_mode: str,
    target_mix_mode: str = TARGET_MODE_DTL,
    nic_target_mg_per_ml: float = 0.0,
    nic_base_strength_mg_per_ml: float = 100.0,
    nic_carrier: str = "PG",
    tuned_formula: Dict[str, float] | None = None,
    allow_formula_tuning: bool = False,
    user_role: str,
    role_hierarchy: Dict[str, int],
    stock_override_requested: bool = False,
    is_old_goat: bool = False,
) -> Tuple[bool, Dict[str, Any]]:
    """Prepare the initial batch plan for a mixing session.

    Returns (success, payload) where:
      - on success: payload contains batch data and view metadata
      - on failure: payload contains a single 'message' string for the caller to display
    """
    # 1. Role-based batch size limit
    max_allowed = ROLE_BATCH_LIMITS.get(user_role, 100)
    if vessel_size > max_allowed:
        return False, {
            "message": f"Your rank ({user_role}) is restricted to {max_allowed}ml batches."
        }

    # 2. Load recipe and items
    recipe_name = recipes_repo.fetch_recipe_name(recipe_id)
    if recipe_name is None:
        return False, {"message": "Recipe not found."}

    items = recipes_repo.fetch_version_items_with_stock(version_id)

    if not items:
        return False, {"message": "Recipe has no ingredients configured."}

    has_nicotine = any(bool(item.get("is_nicotine")) for item in items)
    user_lvl = role_hierarchy.get(user_role, 0)
    source_recipe_mode = SOURCE_RECIPE_MODE_DEFAULT
    target_mode = _normalize_target_mix_mode(target_mix_mode)

    style_norm = _normalize_style(style)
    mix_mode_norm = _normalize_mix_mode(mix_mode)

    # 3. Mode / nicotine access control (server-side hard stop)
    if mix_mode_norm == MIX_MODE_FULL and user_lvl < FULL_MIX_MIN_LEVEL:
        return False, {"message": "Access Denied: Full Mix requires Manager/Master clearance."}

    # Long Fill never allows nicotine steps (even if recipe includes it)
    if mix_mode_norm == MIX_MODE_LONGFILL:
        has_nicotine = False

    # 4. Workflow profile from style + mix type (long-fill vs full-mix).
    config = STYLE_CONFIG.get(style_norm) or STYLE_CONFIG["MTL"]
    workflow = _build_workflow_profile(style=style_norm, mix_mode=mix_mode_norm, vessel_size=vessel_size)
    bottles = int(workflow["units_produced"])
    if bottles <= 0:
        min_fill = float(workflow["lab_fill_per_unit_ml"] or 0.0)
        return False, {"message": f"Container too small for one long-fill unit. Minimum is {min_fill:.0f}ml."}

    total_batch_ml = float(workflow["total_mix_volume_ml"])
    fill_per_bottle_ml = float(workflow["lab_fill_per_unit_ml"])
    retail_bottle_ml = float(workflow["retail_bottle_size_ml"])
    used_volume_ml = float(workflow["used_volume_ml"])
    headroom_ml = float(workflow["headroom_ml"])

    nicotine_items = [i for i in items if bool(i.get("is_nicotine"))]
    flavor_items = [i for i in items if not bool(i.get("is_nicotine"))]
    flavor_items = sorted(flavor_items, key=lambda x: (str(x.get("name") or "").lower()))

    # --- Conversion preview: DTL -> MTL / Pod+Dispo (smart, rule-based) ---
    # We keep the original recipe untouched:
    # - original_pct = stored recipe formulation
    # - suggested_pct = algorithm conversion preview
    # - final_pct = suggested (or master-tuned) values used for batch math only
    items_by_id: dict[int, dict[str, Any]] = {}
    original_rows: list[dict[str, Any]] = []
    for it in flavor_items:
        rid = int(it["flavor_id"])
        items_by_id[rid] = dict(it)
        items_by_id[rid]["ingredient_id"] = rid
        items_by_id[rid]["name"] = str(it.get("name") or "")
        items_by_id[rid]["brand"] = str(it.get("brand") or "")

        original_pct = float(it["amount_percent"] or 0.0)
        original_rows.append(
            {
                "ingredient_id": rid,
                "name": str(it.get("name") or ""),
                "brand": str(it.get("brand") or ""),
                "is_nicotine": False,
                "original_pct": original_pct,
            }
        )

    db = get_db()
    analytics_by_ref = _load_flavor_recipe_analytics_bulk(db=db, reference_ids=[r["ingredient_id"] for r in original_rows])

    recipe_family_info = detect_recipe_family(rows=original_rows, items_by_id=items_by_id)
    _dom_fam = str(recipe_family_info.get("dominant_family") or "mixed")
    family_target_profile = get_family_target_profile(target_mode, _dom_fam)
    family_target_profile_key = get_family_target_profile_key(target_mode, _dom_fam)
    family_layer_note = preview_label_for_target_mode(target_mode)

    suggested_rows, conv_meta = _smart_convert_formula(
        mode=target_mode,
        original_rows=original_rows,
        items_by_id=items_by_id,
        analytics_by_ref=analytics_by_ref,
    )
    original_formula: List[Dict[str, Any]] = suggested_rows

    formula_origin = "original"
    if target_mode != TARGET_MODE_DTL:
        formula_origin = "algorithm_converted"
    if tuned_formula:
        if not allow_formula_tuning or user_lvl < MASTER_MIN_LEVEL:
            return False, {"message": "Fine-tuning is restricted to Master-level users."}
        for row in original_formula:
            key = str(row["ingredient_id"])
            if key in tuned_formula:
                try:
                    tuned_pct = max(0.0, float(tuned_formula[key]))
                    row["final_pct"] = round(tuned_pct, 4)
                    row["change_pct"] = round(float(row["final_pct"]) - float(row["original_pct"]), 4)
                except Exception:
                    pass
        formula_origin = "master_tuned"

    # Advisory tuning guardrails (for Old Goat / Master only). Non-blocking.
    suggested_total_pct = sum(float(r.get("suggested_pct") or 0.0) for r in original_formula)
    guardrails: list[str] = []
    if tuned_formula and user_lvl >= MASTER_MIN_LEVEL:
        guardrails = _compute_tuning_guardrails(
            final_rows=original_formula,
            items_by_id=items_by_id,
            analytics_by_ref=analytics_by_ref,
            suggested_total_pct=float(suggested_total_pct or 0.0),
        )

    def _need_ml(pct: float) -> float:
        return float(total_batch_ml) * (pct / 100.0)

    nic_carrier_norm = str(nic_carrier or "PG").strip().upper()
    if nic_carrier_norm not in {"PG", "VG"}:
        nic_carrier_norm = "PG"
    nic_target = max(0.0, float(nic_target_mg_per_ml or 0.0))
    nic_base_strength = float(nic_base_strength_mg_per_ml or 0.0)
    if mix_mode_norm == MIX_MODE_FULL and nic_base_strength <= 0:
        return False, {"message": "Nicotine base strength must be greater than 0 mg/ml for full mix."}
    if mix_mode_norm == MIX_MODE_FULL and nic_target > nic_base_strength:
        return False, {"message": "Target nicotine strength cannot exceed nicotine base strength."}

    nicotine_volume_ml = 0.0
    nicotine_pg_ml = 0.0
    nicotine_vg_ml = 0.0
    if mix_mode_norm == MIX_MODE_FULL:
        nicotine_volume_ml = (nic_target * float(total_batch_ml) / nic_base_strength) if nic_base_strength > 0 else 0.0
        if nicotine_volume_ml > float(total_batch_ml) + 1e-9:
            return False, {"message": "Calculated nicotine volume exceeds final bottle size."}
        if nic_carrier_norm == "PG":
            nicotine_pg_ml = nicotine_volume_ml
        else:
            nicotine_vg_ml = nicotine_volume_ml

    check_items: List[Dict[str, Any]] = []
    for row in original_formula:
        check_items.append({"name": row["name"], "amount_percent": row["final_pct"], "current_stock": next((float(i.get("current_stock") or 0.0) for i in flavor_items if int(i["flavor_id"]) == int(row["ingredient_id"])), 0.0)})

    # 5. Stock check per ingredient (excludes PG/VG drums). Old Goat may request override for lab testing.
    allow_stock_override = bool(is_old_goat and stock_override_requested)
    stock_shortfalls: List[Dict[str, Any]] = []
    for item in check_items:
        target_ml = _need_ml(float(item["amount_percent"]))
        current_stock = float(item.get("current_stock") or 0)
        if current_stock < target_ml:
            if allow_stock_override:
                stock_shortfalls.append(
                    {
                        "name": str(item.get("name") or "Unknown"),
                        "needed_ml": round(float(target_ml), 4),
                        "available_ml": round(float(current_stock), 4),
                        "kind": "missing_inventory" if current_stock <= 0.0001 else "low_stock",
                    }
                )
            else:
                return False, {"message": f"Stock Alert: {item['name']} (Need {target_ml:.1f}ml/g)"}

    stock_override_active = bool(allow_stock_override and stock_shortfalls)

    # 6. Build ingredient queue in strict order:
    #    nicotine (full mix only) -> flavors A..Z -> PG -> VG (full mix only)
    batch_num = f"OG-{datetime.now().strftime('%Y%m%d%H%M')}-{str(uuid.uuid4())[:4].upper()}"
    queue: List[Dict[str, Any]] = []
    sum_recipe_pct = 0.0

    def _append_item(item: Dict[str, Any]) -> None:
        nonlocal sum_recipe_pct
        pct = float(item["amount_percent"])
        sum_recipe_pct += pct
        target_g = _need_ml(pct)
        queue.append(
            {
                "ingredient_id": int(item["flavor_id"]),
                "name": item["name"],
                "brand": item.get("brand"),
                "is_nicotine": bool(item.get("is_nicotine")),
                "pct": pct,
                "target_grams": round(target_g, 2),
                "target_total_grams": 0.0,
                "actual_grams": 0.0,
                "actual_total_grams": 0.0,
                "delta_to_add": None,
                "status": "pending",
            }
        )

    if mix_mode_norm == MIX_MODE_FULL:
        queue.append(
            {
                "ingredient_id": -2,
                "name": f"Nicotine Base ({nic_carrier_norm})",
                "brand": None,
                "is_nicotine": True,
                "pct": None,
                "target_grams": round(float(nicotine_volume_ml), 2),
                "target_total_grams": 0.0,
                "actual_grams": 0.0,
                "actual_total_grams": 0.0,
                "delta_to_add": None,
                "status": "pending",
            }
        )
    by_id = {int(f["flavor_id"]): f for f in flavor_items}
    for row in original_formula:
        f = by_id.get(int(row["ingredient_id"]))
        if not f:
            continue
        f2 = dict(f)
        f2["amount_percent"] = float(row["final_pct"])
        _append_item(f2)

    flavor_ml_total = _need_ml(sum_recipe_pct)
    remainder_ml = max(0.0, float(total_batch_ml) - flavor_ml_total)
    if mix_mode_norm == MIX_MODE_LONGFILL:
        pg_ml = remainder_ml
        vg_ml = 0.0
    else:
        target_pg_ml = float(total_batch_ml) * float(config["pg_ratio"])
        target_vg_ml = float(total_batch_ml) * float(config["vg_ratio"])
        pg_ml = target_pg_ml - float(flavor_ml_total) - float(nicotine_pg_ml)
        vg_ml = target_vg_ml - float(nicotine_vg_ml)
        if pg_ml < -1e-6 or vg_ml < -1e-6:
            return False, {
                "message": "Flavor + nicotine exceed PG/VG ratio space for this full mix target.",
            }
        pg_ml = max(0.0, pg_ml)
        vg_ml = max(0.0, vg_ml)

    queue.append(
        {
            "ingredient_id": 0,
            "name": "PG",
            "brand": None,
            "is_nicotine": False,
            "pct": None,
            "target_grams": round(pg_ml, 2),
            "target_total_grams": 0.0,
            "actual_grams": 0.0,
            "actual_total_grams": 0.0,
            "delta_to_add": None,
            "status": "pending",
        }
    )

    if mix_mode_norm == MIX_MODE_FULL:
        queue.append(
            {
                "ingredient_id": -1,
                "name": "VG",
                "brand": None,
                "is_nicotine": False,
                "pct": None,
                "target_grams": round(vg_ml, 2),
                "target_total_grams": 0.0,
                "actual_grams": 0.0,
                "actual_total_grams": 0.0,
                "delta_to_add": None,
                "status": "pending",
            }
        )

    running = 0.0
    for step in queue:
        running += float(step.get("target_grams") or 0.0)
        step["target_total_grams"] = round(running, 2)

    flavor_rows_by_id = {int(r["ingredient_id"]): r for r in original_formula if int(r.get("ingredient_id") or 0) > 0}
    narrative_ingredients: list[dict[str, Any]] = []
    for q in queue:
        q_name = str(q.get("name") or "")
        q_category = "concentrate"
        qid = int(q.get("ingredient_id") or 0)
        if qid == -2 or "nicotine" in q_name.lower():
            q_category = "nicotine"
        elif qid == 0:
            q_category = "pg"
        elif qid == -1:
            q_category = "vg"
        row_meta = flavor_rows_by_id.get(qid) or {}
        narrative_ingredients.append(
            {
                "ingredient_id": qid,
                "name": q_name,
                "brand": q.get("brand") or "",
                "category": q_category,
                "target_grams": float(q.get("target_grams") or 0.0),
                "pct": q.get("pct"),
                "flavor_profile": row_meta.get("family") or row_meta.get("reason") or row_meta.get("confidence_note"),
            }
        )

    policy_profile = tvw_mix_policy()
    tolerance_profile = build_tolerance_profile(policy=policy_profile)
    guided_narrative_steps = build_guided_narrative_steps(
        tone_profile="tvw",
        ingredients=narrative_ingredients,
        policy_name=policy_profile.name,
        session_seed=f"{recipe_id}:{version_id}:{total_batch_ml:.3f}:{target_mode}:{formula_origin}",
    )

    batch_data = {
        "batch_number": batch_num,
        "recipe_id": recipe_id,
        "version_id": version_id,
        "mix_mode": mix_mode_norm,
        "style": style_norm,
        "vessel_size": vessel_size,
        "num_units": bottles,
        "retail_bottle_ml": retail_bottle_ml,
        "fill_per_bottle_ml": fill_per_bottle_ml,
        "retail_bottle_size_ml": retail_bottle_ml,
        "lab_fill_per_unit_ml": fill_per_bottle_ml,
        "units_produced": bottles,
        "used_volume_ml": used_volume_ml,
        "headroom_ml": headroom_ml,
        "headroom_applies": bool(workflow["headroom_applies"]),
        "decanting_enabled": bool(workflow["decanting_enabled"]),
        "include_vg_in_lab_mix": bool(workflow["include_vg_in_lab_mix"]),
        "total_volume": float(total_batch_ml),
        "has_nicotine": bool(mix_mode_norm == MIX_MODE_FULL and nic_target > 0),
        "nicotine": {
            "target_mg_per_ml": round(nic_target, 4),
            "base_strength_mg_per_ml": round(max(0.0, nic_base_strength), 4),
            "carrier": nic_carrier_norm,
            "nicotine_volume_ml": round(float(nicotine_volume_ml), 4),
            "nicotine_weight_g": round(float(nicotine_volume_ml), 4),
            "nicotine_pg_ml": round(float(nicotine_pg_ml), 4),
            "nicotine_vg_ml": round(float(nicotine_vg_ml), 4),
        },
        "ingredients": queue,
        "guided_narrative_steps": guided_narrative_steps,
        "stock_override_used": stock_override_active,
        "stock_override_reason": ("old_goat_test_mix" if stock_override_active else None),
        "stock_override_shortfalls": stock_shortfalls,
        "inventory_deduction_policy": ("skipped_when_override" if stock_override_active else "normal"),
        "source_recipe_mode": source_recipe_mode,
        "target_mix_mode": target_mode,
        "formula_origin": formula_origin,
        "tolerance_profile": tolerance_profile,
        "conversion_preview": {
            "source_recipe_mode": source_recipe_mode,
            "target_mix_mode": target_mode,
            "original_total_flavor_pct": round(sum(float(r["original_pct"]) for r in original_formula), 4),
            "suggested_total_flavor_pct": round(sum(float(r.get("suggested_pct") or 0.0) for r in original_formula), 4),
            "final_total_flavor_pct": round(sum(float(r["final_pct"]) for r in original_formula), 4),
            "pod_target_saturation_pct": conv_meta.get("pod_target_saturation_pct"),
            "pod_target_band_pct": conv_meta.get("pod_target_band_pct"),
            "pod_minimum_total_flavor_pct": conv_meta.get("pod_minimum_total_flavor_pct"),
            "algorithm_confidence": float(conv_meta.get("confidence_score") or 0.0),
            "confidence_label": str(conv_meta.get("confidence_label") or "Low"),
            "strengths": conv_meta.get("strengths") or [],
            # Traceability for how overpour recalculations behave during mixing.
            "overpour_reconcile_enabled": True,
            "overpour_threshold_multiplier": float(policy_profile.overpour_threshold_multiplier),
            "tolerance_profile": tolerance_profile,
            "warnings": conv_meta.get("warnings") or [],
            "tuning_guardrails": guardrails or [],
            "rows": original_formula,
            "dispo_fit_rating": conv_meta.get("dispo_fit_rating"),
            "dispo_fit_score": conv_meta.get("dispo_fit_score"),
            "dispo_subtype_key": conv_meta.get("dispo_subtype_key"),
            "dispo_subtype_label": conv_meta.get("dispo_subtype_label"),
            "dispo_fingerprint": conv_meta.get("dispo_fingerprint"),
            "dispo_fingerprint_after": conv_meta.get("dispo_fingerprint_after"),
            "dispo_key_weaknesses": conv_meta.get("dispo_key_weaknesses"),
            "dispo_key_strengths": conv_meta.get("dispo_key_strengths"),
            "dispo_suggestions": conv_meta.get("dispo_suggestions"),
            "dispo_advisories": conv_meta.get("dispo_advisories"),
            "dispo_change_summary": conv_meta.get("dispo_change_summary"),
            "dispo_commercial_targets": conv_meta.get("dispo_commercial_targets"),
            "dispo_why_changed": conv_meta.get("dispo_why_changed"),
            "target_total_flavor_pct": conv_meta.get("target_total_flavor_pct"),
            "extra_flavor_budget": conv_meta.get("extra_flavor_budget"),
            "adaptive_uplift_notes": conv_meta.get("adaptive_uplift_notes"),
            "conversion_engine": conv_meta.get("conversion_engine"),
            "recipe_family": recipe_family_info.get("dominant_family"),
            "recipe_family_secondary": recipe_family_info.get("secondary_family"),
            "recipe_family_confidence": recipe_family_info.get("family_confidence"),
            "recipe_family_mass_by_family": recipe_family_info.get("family_mass_by_family"),
            "recipe_family_reasoning": recipe_family_info.get("family_reasoning"),
            "target_family_profile_key": family_target_profile_key,
            "target_family_profile_name": family_target_profile.get("display_name"),
            "target_family_profile": conv_meta.get("family_target_profile_effective") or family_target_profile,
            "family_layer_note": family_layer_note,
            "diagnostics": conv_meta.get("diagnostics"),
            "fit_rating": conv_meta.get("fit_rating"),
            "dominant_clusters": conv_meta.get("dominant_clusters"),
            "cluster_preservation_notes": conv_meta.get("cluster_preservation_notes"),
            "adjustment_strategy_notes": conv_meta.get("adjustment_strategy_notes"),
            "restraint_applied": conv_meta.get("restraint_applied"),
            "weakness_summary": conv_meta.get("weakness_summary"),
            "adaptive_target_total_pre_restraint": conv_meta.get("adaptive_target_total_pre_restraint"),
            "restraint_factor": conv_meta.get("restraint_factor"),
            "perceptual_dominance_notes": conv_meta.get("perceptual_dominance_notes"),
            "pod_subtype": conv_meta.get("pod_subtype"),
            "pod_subtype_confidence": conv_meta.get("pod_subtype_confidence"),
            "pod_subtype_reasoning": conv_meta.get("pod_subtype_reasoning"),
            "pod_subtype_display_name": conv_meta.get("pod_subtype_display_name"),
            "pod_subtype_description": conv_meta.get("pod_subtype_description"),
            "pod_subtype_merge_key": conv_meta.get("pod_subtype_merge_key"),
            "pod_subtype_warnings": conv_meta.get("pod_subtype_warnings"),
        },
        # safety data is attached by the route layer
    }

    return True, {
        "batch": batch_data,
        "recipe_name": recipe_name,
        "has_nicotine": bool(batch_data.get("has_nicotine")),
    }


# ------------------------------------
# Reconciliation
# ------------------------------------

def reconcile_batch_plan(
    *,
    vessel_size: float,
    previously_poured: List[Dict[str, Any]],
    unit_count: int,
    version_id: int,
    style: str,
    mix_mode: str,
    nic_target_mg_per_ml: float = 0.0,
    nic_base_strength_mg_per_ml: float = 100.0,
    nic_carrier: str = "PG",
    formula_rows: List[Dict[str, Any]] | None = None,
    allow_continue_with_drift: bool = False,
    no_bigger_container: bool = False,
) -> Dict[str, Any]:
    """Recalculate targets after an overpour and build a reconciled queue."""
    policy = tvw_mix_policy()
    trigger = next(
        (
            p
            for p in reversed(previously_poured)
            if float(p.get("actual_ml") or p.get("measured_ml") or p.get("actual_grams") or 0) > 0
        ),
        None,
    )
    if not trigger or int(trigger.get("ingredient_id") or 0) in (0, -1):
        return {"status": "error", "message": "Cannot reconcile on PG/VG."}

    pct = None
    if formula_rows:
        for r in formula_rows:
            if int(r.get("ingredient_id") or 0) == int(trigger["ingredient_id"]):
                pct = float(r.get("final_pct") or r.get("amount_percent") or 0.0)
                break
    if pct is None:
        pct = recipes_repo.fetch_amount_percent_for_version_item(
            version_id=version_id,
            ingredient_id=int(trigger["ingredient_id"]),
        )
    if pct is None or pct == 0:
        return {"status": "error", "message": "Recipe item not found for reconciliation."}

    # Recipe percentages are volume-based. Prefer actual_ml (from grams/density) when provided.
    trigger_ml = None
    try:
        for key in ("actual_ml", "measured_ml"):
            val = trigger.get(key)
            if val is not None and float(val) > 0:
                trigger_ml = float(val)
                break
    except (TypeError, ValueError):
        trigger_ml = None
    if trigger_ml is None:
        try:
            dens = float(trigger.get("density") or 0.0)
        except (TypeError, ValueError):
            dens = 0.0
        try:
            grams = float(trigger.get("actual_grams") or 0.0)
        except (TypeError, ValueError):
            grams = 0.0
        if dens > 0 and grams > 0:
            trigger_ml = grams / dens
        else:
            # Legacy path: callers historically passed volume-as-ml under actual_grams.
            trigger_ml = grams
    if not trigger_ml or trigger_ml <= 0:
        return {"status": "error", "message": "Cannot reconcile without poured volume."}

    new_total_volume = float(trigger_ml) / (pct / 100.0)

    style_norm = _normalize_style(style)
    mix_mode_norm = _normalize_mix_mode(mix_mode)
    config = STYLE_CONFIG.get(style_norm) or STYLE_CONFIG["MTL"]

    initial_total_target = max(0.0001, float(unit_count or 1) * float(vessel_size or 0.0))
    overpour_decision = evaluate_overpour(
        poured_ml=float(new_total_volume),
        target_ml=float(initial_total_target),
        policy=policy,
    )
    if not overpour_decision.allowed:
        return {
            "status": "policy_blocked",
            "message": overpour_decision.hard_stops[0] if overpour_decision.hard_stops else "Policy blocked reconcile.",
            "policy": {
                "name": policy.name,
                "decision": overpour_decision.decision,
                "warnings": list(overpour_decision.warnings),
                "hard_stops": list(overpour_decision.hard_stops),
            },
        }

    if new_total_volume > float(vessel_size):
        if no_bigger_container and policy.overpour_hard_stop:
            return {
                "status": "scrap_required",
                "message": "No larger vessel available. Policy requires scrapping this batch with elevated authorization.",
                "required_action": "scrap_required",
                "required_roles": ["Manager", "Master", "Old Goat"],
                "policy": {
                    "name": policy.name,
                    "decision": "scrap_required",
                    "warnings": list(overpour_decision.warnings),
                    "hard_stops": list(overpour_decision.hard_stops),
                },
            }
        return {
            "status": "capacity_exceeded",
            "message": f"New volume ({new_total_volume:.1f}ml) exceeds vessel.",
            "required_vessel": math.ceil(new_total_volume / 10) * 10,
            "required_volume_ml": round(float(new_total_volume), 4),
            "policy": {
                "name": policy.name,
                "decision": overpour_decision.decision,
                "warnings": list(overpour_decision.warnings),
                "hard_stops": list(overpour_decision.hard_stops),
            },
        }

    fill_per_bottle_ml = float(config["longfill_fill_ml"]) if mix_mode_norm == MIX_MODE_LONGFILL else float(vessel_size)
    required_bottles = max(1, int(math.ceil(float(new_total_volume) / max(0.0001, float(fill_per_bottle_ml)))))
    adjusted_total_volume = required_bottles * float(fill_per_bottle_ml)

    drift_mode = bool(allow_continue_with_drift and no_bigger_container and not policy.overpour_hard_stop)
    if adjusted_total_volume > float(vessel_size):
        if drift_mode:
            # TVW degraded continuation: keep current vessel and rebalance around already-poured amounts.
            required_bottles = max(1, int(unit_count or 1))
            adjusted_total_volume = float(vessel_size)
        elif no_bigger_container and policy.overpour_hard_stop:
            return {
                "status": "scrap_required",
                "message": "No larger vessel available. Policy requires scrapping this batch with elevated authorization.",
                "required_action": "scrap_required",
                "required_roles": ["Manager", "Master", "Old Goat"],
                "policy": {
                    "name": policy.name,
                    "decision": "scrap_required",
                    "warnings": list(overpour_decision.warnings),
                    "hard_stops": list(overpour_decision.hard_stops),
                },
            }
        else:
            return {
                "status": "capacity_exceeded",
                "message": f"Adjusted volume ({adjusted_total_volume:.1f}ml) exceeds vessel.",
                "required_vessel": math.ceil(adjusted_total_volume / 10) * 10,
                "required_volume_ml": round(float(adjusted_total_volume), 4),
                "policy": {
                    "name": policy.name,
                    "decision": overpour_decision.decision,
                    "warnings": list(overpour_decision.warnings),
                    "hard_stops": list(overpour_decision.hard_stops),
                },
            }

    new_unit_vol = adjusted_total_volume / required_bottles

    if formula_rows:
        original_items = [
            {
                "ingredient_id": int(r.get("ingredient_id") or 0),
                "amount_percent": float(r.get("final_pct") or r.get("amount_percent") or 0.0),
                "name": str(r.get("name") or ""),
                "is_nicotine": bool(r.get("is_nicotine")),
            }
            for r in formula_rows
            if int(r.get("ingredient_id") or 0) > 0
        ]
    else:
        original_items = recipes_repo.fetch_version_items_basic(version_id)

    reconciled_steps: List[Dict[str, Any]] = []
    prev_actual_map: Dict[int, float] = {}
    for poured in previously_poured or []:
        try:
            iid = int(poured.get("ingredient_id") or 0)
        except (TypeError, ValueError):
            continue
        try:
            prev_actual_map[iid] = float(poured.get("actual_grams") or 0.0)
        except (TypeError, ValueError):
            prev_actual_map[iid] = 0.0
    sum_recipe_pct = 0.0

    flavor_items = [i for i in original_items if not bool(i.get("is_nicotine"))]
    flavor_items = sorted(flavor_items, key=lambda x: (str(x.get("name") or "").lower()))

    delta_tolerance = 0.01

    def _append(item: Dict[str, Any]) -> None:
        nonlocal sum_recipe_pct
        new_target = (new_unit_vol * (item["amount_percent"] / 100.0)) * required_bottles
        sum_recipe_pct += float(item["amount_percent"])
        actual = float(prev_actual_map.get(int(item["ingredient_id"]), 0.0) or 0.0)
        delta = max(0.0, float(new_target) - float(actual))
        satisfied = delta <= delta_tolerance

        reconciled_steps.append(
            {
                "ingredient_id": item["ingredient_id"],
                "name": item["name"],
                "is_nicotine": item["is_nicotine"],
                "target_grams": round(new_target, 2),
                "actual_grams": round(actual, 2),
                "delta_to_add": round(delta, 2) if not satisfied else 0,
                "new_target_grams": round(new_target, 2),
                "already_added_grams": round(actual, 2),
                "target_total_grams": 0.0,
                "actual_total_grams": 0.0,
                "status": "pending" if not satisfied else "skip",
            }
        )

    nic_carrier_norm = str(nic_carrier or "PG").strip().upper()
    if nic_carrier_norm not in {"PG", "VG"}:
        nic_carrier_norm = "PG"
    nic_target = max(0.0, float(nic_target_mg_per_ml or 0.0))
    nic_base_strength = max(0.0001, float(nic_base_strength_mg_per_ml or 0.0))
    nicotine_volume_ml = (nic_target * float(adjusted_total_volume) / nic_base_strength) if mix_mode_norm == MIX_MODE_FULL else 0.0
    nicotine_pg_ml = nicotine_volume_ml if (mix_mode_norm == MIX_MODE_FULL and nic_carrier_norm == "PG") else 0.0
    nicotine_vg_ml = nicotine_volume_ml if (mix_mode_norm == MIX_MODE_FULL and nic_carrier_norm == "VG") else 0.0

    if mix_mode_norm == MIX_MODE_FULL:
        nic_actual = float(prev_actual_map.get(-2, 0.0) or 0.0)
        nic_delta = max(0.0, float(nicotine_volume_ml) - float(nic_actual))
        nic_satisfied = nic_delta <= delta_tolerance
        reconciled_steps.append(
            {
                "ingredient_id": -2,
                "name": f"Nicotine Base ({nic_carrier_norm})",
                "is_nicotine": True,
                "target_grams": round(nicotine_volume_ml, 2),
                "actual_grams": round(nic_actual, 2),
                "delta_to_add": round(nic_delta, 2) if not nic_satisfied else 0,
                "new_target_grams": round(nicotine_volume_ml, 2),
                "already_added_grams": round(nic_actual, 2),
                "target_total_grams": 0.0,
                "actual_total_grams": 0.0,
                "status": "pending" if not nic_satisfied else "skip",
            }
        )
    for f in flavor_items:
        _append(f)

    total_ml = float(adjusted_total_volume)
    flavor_ml_total = total_ml * (sum_recipe_pct / 100.0)
    remainder_ml = max(0.0, total_ml - flavor_ml_total)

    if mix_mode_norm == MIX_MODE_LONGFILL:
        pg_target = remainder_ml
        vg_target = 0.0
    else:
        target_pg_ml = total_ml * float(config["pg_ratio"])
        target_vg_ml = total_ml * float(config["vg_ratio"])
        pg_target = max(0.0, target_pg_ml - flavor_ml_total - nicotine_pg_ml)
        vg_target = max(0.0, target_vg_ml - nicotine_vg_ml)

    pg_actual = float(prev_actual_map.get(0, 0.0) or 0.0)
    pg_delta = max(0.0, float(pg_target) - float(pg_actual))
    pg_satisfied = pg_delta <= delta_tolerance
    reconciled_steps.append(
        {
            "ingredient_id": 0,
            "name": "PG",
            "is_nicotine": False,
            "target_grams": round(pg_target, 2),
            "actual_grams": round(pg_actual, 2),
            "delta_to_add": round(pg_delta, 2) if not pg_satisfied else 0,
            "new_target_grams": round(pg_target, 2),
            "already_added_grams": round(pg_actual, 2),
            "target_total_grams": 0.0,
            "actual_total_grams": 0.0,
            "status": "pending" if not pg_satisfied else "skip",
        }
    )

    if mix_mode_norm == MIX_MODE_FULL:
        vg_actual = float(prev_actual_map.get(-1, 0.0) or 0.0)
        vg_delta = max(0.0, float(vg_target) - float(vg_actual))
        vg_satisfied = vg_delta <= delta_tolerance
        reconciled_steps.append(
            {
                "ingredient_id": -1,
                "name": "VG",
                "is_nicotine": False,
                "target_grams": round(vg_target, 2),
                "actual_grams": round(vg_actual, 2),
                "delta_to_add": round(vg_delta, 2) if not vg_satisfied else 0,
                "new_target_grams": round(vg_target, 2),
                "already_added_grams": round(vg_actual, 2),
                "target_total_grams": 0.0,
                "actual_total_grams": 0.0,
                "status": "pending" if not vg_satisfied else "skip",
            }
        )

    running = 0.0
    for step in reconciled_steps:
        running += float(step.get("target_grams") or 0.0)
        step["target_total_grams"] = round(running, 2)

    branch_status = "continue_with_drift" if drift_mode else "success"
    warnings = list(overpour_decision.warnings)
    if drift_mode:
        warnings.append("Degraded recovery mode: PG/balance was recalculated around the overpour. Taste outcome is expected to drift from the original recipe.")
    return {
        "status": branch_status,
        "reconciled_steps": reconciled_steps,
        "new_total_volume": round(float(adjusted_total_volume), 2),
        "new_unit_count": required_bottles,
        "reconcile_mode": True,
        "restarted_from_step": 1,
        "policy": {
            "name": policy.name,
            "decision": (branch_status if branch_status != "success" else overpour_decision.decision),
            "warnings": warnings,
            "hard_stops": list(overpour_decision.hard_stops),
        },
    }


# ------------------------------------
# Finalisation / logging / stock
# ------------------------------------

def finalize_batch_transaction(
    *,
    payload: Dict[str, Any],
    user_role: str,
    role_hierarchy: Dict[str, int],
    user_id: int,
    mix_token: str | None,
    safety_data: Dict[str, Any] | None,
    user_is_old_goat: bool = False,
) -> Tuple[Dict[str, Any], int]:
    """Complete a batch: safety, nicotine auth, logging, and stock movement.

    Old Goat test mixes may set stock_override_used (from init_batch_for_recipe); inventory deduction
    is skipped for those batches so stock is never corrupted — see inventory_deduction_policy on payload.

    Returns (response_payload, http_status_code) suitable for jsonify().
    """
    user_lvl = role_hierarchy.get(user_role, 0)

    stock_override_used = bool(payload.get("stock_override_used"))
    is_scrapped = bool(payload.get("scrapped"))
    if stock_override_used and not user_is_old_goat:
        return (
            {"status": "error", "message": "Stock override is only available to the Old Goat account."},
            403,
        )

    has_nicotine = payload.get("has_nicotine", False)
    supervisor_id = payload.get("supervisor_id")

    # 1. Nicotine authorization
    if has_nicotine and user_lvl < 4:
        if not supervisor_id:
            return (
                {
                    "status": "error",
                    "message": "Supervisor authorization required for Nicotine.",
                },
                401,
            )

        sup_role = users_repo.fetch_user_role(int(supervisor_id))
        if not sup_role or role_hierarchy.get(sup_role, 0) < 4:
            return (
                {"status": "error", "message": "Invalid supervisor clearance."},
                403,
            )

    safety = payload.get("safety_checks") or safety_data
    if not safety:
        return (
            {"status": "error", "message": "Safety record missing."},
            400,
        )

    try:
        db = get_db()
        db.execute("BEGIN")

        def _ensure_base_ref_id(base_name: str) -> int:
            ref_id = inventory_repo.fetch_reference_id_by_brand_name(brand="BASE", name=base_name)
            if ref_id:
                return int(ref_id)
            return int(inventory_repo.insert_reference(brand="BASE", name=base_name, category="Base"))

        pg_ref_id = _ensure_base_ref_id("PG")
        vg_ref_id = _ensure_base_ref_id("VG")

        nicotine_stock_ref_id = recipes_repo.fetch_version_primary_nicotine_reference_id(int(payload["version_id"]))

        # Compute total cost from actual grams * snapshot cost per gram.
        total_cost = 0.0
        for ing in payload.get("ingredients", []):
            actual = float(ing.get("actual_grams", 0) or 0)
            if actual <= 0:
                continue
            iid = int(ing.get("ingredient_id") or 0)
            if iid in (0, -1):
                continue
            if iid == -2:
                if not nicotine_stock_ref_id:
                    continue
                cost_ref_id = int(nicotine_stock_ref_id)
            else:
                cost_ref_id = iid
            cpg = float(inventory_repo.fetch_library_cost_per_g(int(cost_ref_id)) or 0.0)
            total_cost += actual * cpg

        override_detail = None
        if stock_override_used:
            override_detail = json.dumps(
                {
                    "reason": str(payload.get("stock_override_reason") or "old_goat_test_mix"),
                    "shortfalls": payload.get("stock_override_shortfalls") or [],
                    "policy": str(payload.get("inventory_deduction_policy") or "skipped_when_override"),
                }
            )

        batch_id = mix_logs_repo.insert_mix_history(
            batch_number=payload.get("batch_number"),
            recipe_id=int(payload["recipe_id"]),
            version_id=int(payload["version_id"]),
            user_id=int(user_id),
            supervisor_id=int(supervisor_id) if supervisor_id else None,
            total_volume_actual=float(payload["total_volume"]),
            style=payload.get("style"),
            session_token=mix_token,
            ready_for_sale=0 if (stock_override_used or is_scrapped) else 1,
            safety_json=json.dumps(safety),
            total_cost=round(float(total_cost), 4),
            vessel_size=float(payload.get("vessel_size")) if payload.get("vessel_size") is not None else None,
            mix_mode=str(payload.get("mix_mode") or ""),
            source_recipe_mode=str(payload.get("source_recipe_mode") or SOURCE_RECIPE_MODE_DEFAULT),
            target_mix_mode=str(payload.get("target_mix_mode") or TARGET_MODE_DTL),
            formula_origin=str(payload.get("formula_origin") or ("scrapped_after_overpour" if is_scrapped else "original")),
            formula_snapshot_json=json.dumps(payload.get("conversion_preview") or {}),
            stock_override_used=(1 if stock_override_used else 0),
            stock_override_reason=(str(payload.get("stock_override_reason") or "old_goat_test_mix") if stock_override_used else None),
            stock_override_detail_json=override_detail,
        )

        for ing in payload.get("ingredients", []):
            actual = float(ing.get("actual_grams", 0) or 0)
            if actual <= 0:
                continue

            ing_id = int(ing.get("ingredient_id") or 0)
            if ing_id == -2 and nicotine_stock_ref_id:
                log_ingredient_id = int(nicotine_stock_ref_id)
            else:
                log_ingredient_id = pg_ref_id if ing_id == 0 else vg_ref_id if ing_id == -1 else ing_id

            if ing_id == -2 and not nicotine_stock_ref_id:
                cost_snapshot = 0.0
            else:
                cost_snapshot = inventory_repo.fetch_library_cost_per_g(int(log_ingredient_id)) or 0.0

            mix_logs_repo.insert_mix_history_item(
                batch_id=int(batch_id),
                ingredient_id=int(log_ingredient_id),
                measured_grams=float(actual),
                target_grams=float(ing.get("target_grams")) if ing.get("target_grams") is not None else None,
                delta_grams=(
                    float(actual) - float(ing.get("target_grams"))
                    if ing.get("target_grams") is not None
                    else None
                ),
                cost_at_time=float(cost_snapshot),
            )

            if not stock_override_used:
                if ing_id == 0:
                    db.execute(
                        "UPDATE global_stock SET current_stock = current_stock - ? WHERE name = 'PG'",
                        (actual,),
                    )
                elif ing_id == -1:
                    db.execute(
                        "UPDATE global_stock SET current_stock = current_stock - ? WHERE name = 'VG'",
                        (actual,),
                    )
                elif ing_id == -2:
                    if nicotine_stock_ref_id:
                        density = (
                            inventory_repo.fetch_reference_density(int(nicotine_stock_ref_id))
                            or DEFAULT_DENSITY_G_PER_ML
                        )
                        delta_ml = -(float(actual) / float(density) if density else 0.0)
                        inventory_repo.update_library_stock_delta_ml(
                            reference_id=int(nicotine_stock_ref_id), delta_ml=delta_ml
                        )
                        inventory_repo.mark_empty_if_below(
                            reference_id=int(nicotine_stock_ref_id), threshold=0.05
                        )
                else:
                    density = inventory_repo.fetch_reference_density(int(ing_id)) or DEFAULT_DENSITY_G_PER_ML
                    delta_ml = -(float(actual) / float(density) if density else 0.0)
                    inventory_repo.update_library_stock_delta_ml(reference_id=int(ing_id), delta_ml=delta_ml)
                    inventory_repo.mark_empty_if_below(reference_id=int(ing_id), threshold=0.05)

        if stock_override_used and user_is_old_goat:
            audit_lines = []
            for sf in payload.get("stock_override_shortfalls") or []:
                k = sf.get("kind") or "unknown"
                audit_lines.append(f"{k}:{sf.get('name')}")
            users_repo.insert_audit_log(
                actor_id=int(user_id),
                action="MIX_STOCK_OVERRIDE",
                target_id=int(payload["recipe_id"]),
                details=json.dumps(
                    {
                        "batch_number": payload.get("batch_number"),
                        "shortfalls": payload.get("stock_override_shortfalls") or [],
                        "deduction": "skipped",
                        "mix_history_id": int(batch_id),
                        "kinds": audit_lines,
                    }
                ),
                timestamp=datetime.now(),
            )

        db.commit()
        return {"status": "success", "batch_id": batch_id}, 200
    except Exception as exc:  # pragma: no cover - safety net
        get_db().rollback()
        return {"status": "error", "message": str(exc)}, 500


def finalize_inventory_deduction(
    *,
    payload: Dict[str, Any],
    user_id: int,
) -> Tuple[Dict[str, Any], int]:
    """Legacy-style finalisation used by `/inventory/deduct-batch`.

    This is intentionally kept inside `mixing_engine` so **all** stock deduction and
    mix logging lives in one place.

    Expected payload keys:
      - recipe_id, version_id, total_volume, supervisor_id
      - ingredients[] where each item includes ingredient_id and poured_g (or actual_grams)

    Effects:
      - inserts into `mix_history`
      - deducts from `library_items.current_stock` (ml) using `flavor_reference.density`
      - writes `batch_ingredients`
    """
    if not payload:
        return {"status": "error", "message": "No data received"}, 400

    recipe_id = payload.get("recipe_id")
    version_id = payload.get("version_id")
    total_volume = payload.get("total_volume")
    supervisor_id = payload.get("supervisor_id")

    if not recipe_id or not version_id:
        return {"status": "error", "message": "recipe_id and version_id are required"}, 400

    ingredients: List[Dict[str, Any]] = payload.get("ingredients") or []

    try:
        db = get_db()
        db.execute("BEGIN")

        batch_id = mix_logs_repo.insert_mix_history(
            batch_number=None,
            recipe_id=int(recipe_id),
            version_id=int(version_id),
            user_id=int(user_id),
            supervisor_id=int(supervisor_id) if supervisor_id else None,
            total_volume_actual=float(total_volume or 0),
        )

        for item in ingredients:
            ingredient_id = item.get("ingredient_id")
            if not ingredient_id:
                continue

            poured_g = float(item.get("poured_g", item.get("actual_grams", 0)) or 0)
            if poured_g <= 0:
                continue

            density = inventory_repo.fetch_reference_density(int(ingredient_id)) or DEFAULT_DENSITY_G_PER_ML
            poured_ml = poured_g / density if density > 0 else 0.0

            inventory_repo.update_library_stock_delta_ml(reference_id=int(ingredient_id), delta_ml=-poured_ml)
            inventory_repo.mark_empty_if_below(reference_id=int(ingredient_id), threshold=0.05)
            mix_logs_repo.insert_batch_ingredient(
                batch_id=int(batch_id),
                reference_id=int(ingredient_id),
                actual_grams=float(poured_g),
            )

        db.commit()
        return {"status": "success", "message": "Inventory updated", "batch_id": batch_id}, 200
    except Exception as exc:
        db.rollback()
        return {"status": "error", "message": str(exc)}, 500

