from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

from db import get_db

from config.canonical_library_brands import canonical_library_brand

from config.flavor_classification_brands import get_brand_flavor_trait_defaults

CLASSIFICATION_VERSION = "v1.0.0"
CLASSIFICATION_SOURCE = "rules_engine"

PROFILE_DEFAULTS = {
    "fruit": {"role": "main_note", "potency": "medium", "volatility": "high", "body_weight": "medium", "pod_suitability": "excellent"},
    "cream": {"role": "body", "potency": "low", "volatility": "low", "body_weight": "dense", "pod_suitability": "good"},
    "bakery": {"role": "body", "potency": "low", "volatility": "low", "body_weight": "dense", "pod_suitability": "fair"},
    "candy": {"role": "main_note", "potency": "medium", "volatility": "medium", "body_weight": "medium", "pod_suitability": "excellent"},
    "cooling": {"role": "cooling", "potency": "high", "volatility": "high", "body_weight": "thin", "pod_suitability": "excellent"},
    "sweetener": {"role": "sweetener_support", "potency": "high", "volatility": "low", "body_weight": "thin", "pod_suitability": "good"},
    "sour": {"role": "accent", "potency": "high", "volatility": "high", "body_weight": "thin", "pod_suitability": "good"},
    "dark_support": {"role": "body", "potency": "medium", "volatility": "low", "body_weight": "dense", "pod_suitability": "fair"},
    "beverage": {"role": "accent", "potency": "medium", "volatility": "high", "body_weight": "thin", "pod_suitability": "good"},
    "unknown": {"role": "support", "potency": "medium", "volatility": "medium", "body_weight": "medium", "pod_suitability": "fair"},
}

PHRASE_KEYWORDS = [
    ("ice cream", {"hard_profile": "cream"}),
    ("sweetener", {"primary": "sweetener", "modifier": "sweetener"}),
    ("ws-23", {"hard_profile": "cooling", "modifier": "cooling"}),
    ("ws23", {"hard_profile": "cooling", "modifier": "cooling"}),
    ("cooling", {"hard_profile": "cooling", "modifier": "cooling"}),
    ("coolada", {"hard_profile": "cooling", "modifier": "cooling"}),
    ("sour", {"primary": "sour", "modifier": "sour"}),
    ("cheesecake", {"primary": "cream", "secondary": "bakery", "modifier": "body"}),
    ("custard", {"hard_profile": "cream", "modifier": "body"}),
    ("candy", {"primary": "candy", "modifier": "candy"}),
    ("cola", {"primary": "beverage", "modifier": "beverage"}),
    ("coffee", {"primary": "dark_support", "modifier": "dark_support"}),
]

TOKEN_KEYWORDS = {
    "lemon": {"primary": "fruit", "secondary": "citrus", "modifier": "brightness"},
    "lime": {"primary": "fruit", "secondary": "citrus", "modifier": "brightness"},
    "orange": {"primary": "fruit", "secondary": "citrus", "modifier": "brightness"},
    "berry": {"primary": "fruit", "secondary": "berry"},
    "strawberry": {"primary": "fruit", "secondary": "berry"},
    "blueberry": {"primary": "fruit", "secondary": "berry"},
    "grape": {"primary": "fruit"},
    "watermelon": {"primary": "fruit", "secondary": "dispo_fruit"},
    "mango": {"primary": "fruit", "secondary": "tropical", "modifier": "brightness"},
    "pineapple": {"primary": "fruit", "secondary": "tropical", "modifier": "brightness"},
    "apple": {"primary": "fruit", "secondary": "citrus"},
    "banana": {"primary": "fruit", "secondary": "tropical"},
    "cream": {"primary": "cream", "modifier": "body"},
    "bakery": {"primary": "bakery", "modifier": "body"},
    "cake": {"primary": "bakery", "modifier": "body"},
    "cookie": {"primary": "bakery", "modifier": "body"},
    "donut": {"primary": "bakery", "modifier": "body"},
    "ice": {"primary": "cooling", "modifier": "cooling"},
    "menthol": {"primary": "cooling", "modifier": "cooling"},
    "sweet": {"modifier": "sweetener"},
    "sucralose": {"primary": "sweetener", "modifier": "sweetener"},
}

BIAS_BY_TAG = {
    "citrus": {"brightness_bias": 2, "harshness_bias": 1},
    "berry": {"sweetness_bias": 1},
    "tropical": {"sweetness_bias": 1, "brightness_bias": 1},
    "dispo_fruit": {"sweetness_bias": 1},
    "sweetener": {"sweetness_bias": 1, "coil_risk": 1},
    "sour": {"brightness_bias": 1, "harshness_bias": 1},
    "cream": {"sweetness_bias": 1, "coil_risk": 1},
    "bakery": {"sweetness_bias": 1, "coil_risk": 2},
    "candy": {"sweetness_bias": 2, "coil_risk": 1},
    "cooling": {"harshness_bias": 1},
    "dark_support": {"coil_risk": 2},
    "beverage": {"brightness_bias": 1},
}


def normalize_text(text: str) -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"[^\w\s/+&-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_brand(brand: str) -> str:
    """Resolve stored brand to short canonical library brand (does not write to DB)."""
    return canonical_library_brand(brand or "", "")


def tokenize_name(name: str) -> Tuple[str, List[str]]:
    normalized = normalize_text(name)
    return normalized, normalized.split()


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def detect_keywords(name: str) -> Dict[str, Any]:
    normalized, tokens = tokenize_name(name)
    primary = ""
    secondary: List[str] = []
    modifiers: List[str] = []
    hard_profile = ""

    for phrase, info in sorted(PHRASE_KEYWORDS, key=lambda x: len(x[0]), reverse=True):
        if not _contains_phrase(normalized, phrase):
            continue
        if info.get("hard_profile"):
            hard_profile = str(info["hard_profile"])
        if info.get("primary") and not primary:
            primary = str(info["primary"])
        if info.get("secondary"):
            secondary.append(str(info["secondary"]))
        if info.get("modifier"):
            modifiers.append(str(info["modifier"]))

    for t in tokens:
        info = TOKEN_KEYWORDS.get(t)
        if not info:
            continue
        if info.get("primary") and not primary:
            primary = str(info["primary"])
        if info.get("secondary"):
            secondary.append(str(info["secondary"]))
        if info.get("modifier"):
            modifiers.append(str(info["modifier"]))

    secondary = sorted(set([s for s in secondary if s]))
    modifiers = sorted(set([m for m in modifiers if m]))
    return {
        "keyword_primary": primary or None,
        "keyword_secondary": secondary,
        "keyword_modifiers": modifiers,
        "hard_profile": hard_profile or None,
    }


def apply_hard_overrides(reference: Dict[str, Any], detected: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(detected)
    if detected.get("hard_profile"):
        out["keyword_primary"] = detected.get("hard_profile")
    # Keep fruit as primary unless a hard override exists.
    if not detected.get("hard_profile") and out.get("keyword_primary") in ("cream", "bakery", "candy", "sour", "cooling", "sweetener"):
        tokens = tokenize_name(str(reference.get("name") or ""))[1]
        if any(t in tokens for t in ("lemon", "lime", "orange", "berry", "strawberry", "blueberry", "grape", "watermelon", "mango", "pineapple", "apple", "banana")):
            out["keyword_primary"] = "fruit"
    return out


def apply_soft_modifiers(detected: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(detected)
    primary = out.get("keyword_primary")
    mods = list(out.get("keyword_modifiers") or [])
    if primary == "fruit":
        # Soft rules may enrich fruit, but should not blindly replace it.
        if "cooling" in mods and "cooling" not in (out.get("keyword_secondary") or []):
            out["keyword_secondary"] = list(out.get("keyword_secondary") or []) + ["cooling"]
    return out


def resolve_profile(row: Dict[str, Any], detected: Dict[str, Any]) -> str:
    if detected.get("hard_profile"):
        return str(detected["hard_profile"])
    if row.get("profile"):
        return str(row["profile"]).strip().lower()
    cat = str(row.get("category") or "").strip().lower()
    category_to_profile = {
        "flavor": "fruit",
        "base": "dark_support",
        "nicotine": "dark_support",
        "hardware": "unknown",
        "fruit": "fruit",
        "cream": "cream",
        "bakery": "bakery",
        "candy": "candy",
        "beverage": "beverage",
    }
    if cat in category_to_profile:
        return category_to_profile[cat]
    if detected.get("keyword_primary"):
        return str(detected["keyword_primary"])
    return "unknown"


def apply_brand_bias(resolved: Dict[str, Any], brand: str) -> None:
    trait_defaults = get_brand_flavor_trait_defaults(brand or "")
    if not trait_defaults:
        return
    for k, v in trait_defaults.items():
        if not resolved.get(k):
            resolved[k] = v


def calculate_bias(resolved_profile: str, secondary: List[str], modifiers: List[str]) -> Dict[str, int]:
    out = {"sweetness_bias": 0, "brightness_bias": 0, "harshness_bias": 0, "coil_risk": 0}
    tags = [resolved_profile] + secondary + modifiers
    for t in tags:
        b = BIAS_BY_TAG.get(t)
        if not b:
            continue
        for k, v in b.items():
            out[k] += int(v)
    # hard minimums
    if resolved_profile == "sweetener":
        out["sweetness_bias"] = max(out["sweetness_bias"], 2)
        out["coil_risk"] = max(out["coil_risk"], 2)
    if resolved_profile == "cooling":
        out["harshness_bias"] = max(out["harshness_bias"], 1)
    if resolved_profile == "bakery":
        out["coil_risk"] = max(out["coil_risk"], 2)
    if resolved_profile == "candy":
        out["sweetness_bias"] = max(out["sweetness_bias"], 2)
    return out


def assign_confidence(resolved_profile: str, detected: Dict[str, Any], manual_override: bool) -> str:
    if manual_override:
        return "high"
    if detected.get("hard_profile"):
        return "high"
    if detected.get("keyword_primary"):
        return "medium"
    if resolved_profile != "unknown":
        return "medium"
    return "low"


def build_override_reason(*, manual: bool, hard: bool, soft_count: int, source_profile: str) -> str:
    if manual:
        return "manual_override"
    if hard:
        return "hard_override"
    if soft_count > 0:
        return "soft_modifiers"
    if source_profile and source_profile != "unknown":
        return "profile_default"
    return "brand_or_unknown_default"


def _parse_manual_payload(payload: Any) -> Dict[str, Any]:
    if not payload:
        return {}
    try:
        if isinstance(payload, dict):
            return payload
        return json.loads(str(payload))
    except Exception:
        return {}


def classify_flavor(reference: Dict[str, Any]) -> Dict[str, Any]:
    manual_override = bool(int(reference.get("manual_override") or 0))
    detected = detect_keywords(str(reference.get("name") or ""))
    detected = apply_hard_overrides(reference, detected)
    detected = apply_soft_modifiers(detected)
    resolved_profile = resolve_profile(reference, detected)
    defaults = PROFILE_DEFAULTS.get(resolved_profile, PROFILE_DEFAULTS["unknown"]).copy()
    apply_brand_bias(defaults, str(reference.get("brand") or ""))

    if manual_override:
        payload = _parse_manual_payload(reference.get("manual_override_payload"))
        for k in ("resolved_profile", "resolved_role", "resolved_potency", "resolved_volatility", "resolved_body_weight", "resolved_pod_suitability"):
            if payload.get(k) not in (None, ""):
                defaults[k.replace("resolved_", "")] = payload.get(k)
        if payload.get("keyword_primary"):
            detected["keyword_primary"] = payload.get("keyword_primary")
        if isinstance(payload.get("keyword_secondary"), list):
            detected["keyword_secondary"] = payload.get("keyword_secondary")
        if isinstance(payload.get("keyword_modifiers"), list):
            detected["keyword_modifiers"] = payload.get("keyword_modifiers")

    biases = calculate_bias(
        resolved_profile=resolved_profile,
        secondary=detected.get("keyword_secondary", []),
        modifiers=detected.get("keyword_modifiers", []),
    )
    confidence = assign_confidence(resolved_profile, detected, manual_override)
    reason = build_override_reason(
        manual=manual_override,
        hard=bool(detected.get("hard_profile")),
        soft_count=len(detected.get("keyword_modifiers") or []),
        source_profile=str(reference.get("profile") or ""),
    )
    needs_review = 1 if (confidence == "low" or resolved_profile == "unknown") else 0

    return {
        "resolved_profile": resolved_profile,
        "resolved_role": defaults.get("role"),
        "resolved_potency": defaults.get("potency"),
        "resolved_volatility": defaults.get("volatility"),
        "resolved_body_weight": defaults.get("body_weight"),
        "resolved_pod_suitability": defaults.get("pod_suitability"),
        "keyword_primary": detected.get("keyword_primary"),
        "keyword_secondary": json.dumps(detected.get("keyword_secondary") or []),
        "keyword_modifiers": json.dumps(detected.get("keyword_modifiers") or []),
        "sweetness_bias": int(biases.get("sweetness_bias", 0)),
        "brightness_bias": int(biases.get("brightness_bias", 0)),
        "harshness_bias": int(biases.get("harshness_bias", 0)),
        "coil_risk": int(biases.get("coil_risk", 0)),
        "confidence": confidence,
        "override_reason": reason,
        "classification_version": CLASSIFICATION_VERSION,
        "classification_source": CLASSIFICATION_SOURCE,
        "last_classified_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "needs_review": needs_review,
    }


def _fetch_references(*, flavor_id: int | None = None) -> List[Dict[str, Any]]:
    db = get_db()
    query = "SELECT * FROM flavor_reference WHERE active = 1"
    params: List[Any] = []
    if flavor_id is not None:
        query += " AND id = ?"
        params.append(int(flavor_id))
    rows = db.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def _apply_classification(flavor_id: int, payload: Dict[str, Any]) -> None:
    db = get_db()
    db.execute(
        """
        UPDATE flavor_reference
        SET resolved_profile = ?,
            resolved_role = ?,
            resolved_potency = ?,
            resolved_volatility = ?,
            resolved_body_weight = ?,
            resolved_pod_suitability = ?,
            keyword_primary = ?,
            keyword_secondary = ?,
            keyword_modifiers = ?,
            sweetness_bias = ?,
            brightness_bias = ?,
            harshness_bias = ?,
            coil_risk = ?,
            confidence = ?,
            override_reason = ?,
            classification_version = ?,
            classification_source = ?,
            last_classified_at = ?,
            needs_review = ?
        WHERE id = ?
        """,
        (
            payload.get("resolved_profile"),
            payload.get("resolved_role"),
            payload.get("resolved_potency"),
            payload.get("resolved_volatility"),
            payload.get("resolved_body_weight"),
            payload.get("resolved_pod_suitability"),
            payload.get("keyword_primary"),
            payload.get("keyword_secondary"),
            payload.get("keyword_modifiers"),
            payload.get("sweetness_bias"),
            payload.get("brightness_bias"),
            payload.get("harshness_bias"),
            payload.get("coil_risk"),
            payload.get("confidence"),
            payload.get("override_reason"),
            payload.get("classification_version"),
            payload.get("classification_source"),
            payload.get("last_classified_at"),
            payload.get("needs_review"),
            int(flavor_id),
        ),
    )


def classify_single_flavor(*, flavor_id: int, apply: bool) -> Dict[str, Any]:
    rows = _fetch_references(flavor_id=int(flavor_id))
    if not rows:
        return {"status": "error", "message": "Flavor not found."}
    row = rows[0]
    result = classify_flavor(row)
    if apply and not bool(int(row.get("manual_override") or 0)):
        _apply_classification(int(row["id"]), result)
        get_db().commit()
    return {"status": "success", "flavor_id": int(row["id"]), "name": row.get("name"), "result": result, "applied": bool(apply)}


def classify_all_flavors(*, apply: bool, limit: int | None = None) -> Dict[str, Any]:
    rows = _fetch_references()
    if limit is not None and int(limit) > 0:
        rows = rows[: int(limit)]
    scanned = len(rows)
    changed = 0
    skipped_manual = 0
    by_profile: Dict[str, int] = {}
    by_reason: Dict[str, int] = {}
    needs_review = 0
    sample: List[Dict[str, Any]] = []

    for idx, row in enumerate(rows):
        is_manual = bool(int(row.get("manual_override") or 0))
        if is_manual:
            skipped_manual += 1
            continue
        result = classify_flavor(row)
        by_profile[result["resolved_profile"]] = by_profile.get(result["resolved_profile"], 0) + 1
        by_reason[result["override_reason"]] = by_reason.get(result["override_reason"], 0) + 1
        if int(result.get("needs_review") or 0) == 1:
            needs_review += 1
        if apply:
            _apply_classification(int(row["id"]), result)
            changed += 1
        if len(sample) < 5:
            sample.append({"id": row["id"], "name": row.get("name"), "resolved_profile": result["resolved_profile"], "reason": result["override_reason"]})

    if apply:
        get_db().commit()
    return {
        "status": "success",
        "mode": "apply" if apply else "dry_run",
        "total_scanned": scanned,
        "total_changed": changed if apply else scanned - skipped_manual,
        "skipped_manual_overrides": skipped_manual,
        "counts_by_profile": by_profile,
        "counts_by_override_reason": by_reason,
        "needs_review_count": needs_review,
        "classification_version": CLASSIFICATION_VERSION,
        "sample": sample,
    }
