"""
Brand maps for flavor classification trait hints.

Storage canonical short brands (FLV, CAP, TFA, …) live in
`config.canonical_library_brands`. This module maps those (and legacy long
names) to trait-default keys.

Two separate maps:
1. BRAND_ALIAS_TO_CANONICAL — legacy alias normalization (long names) for bias lookup.
2. CANONICAL_BRAND_FLAVOR_DEFAULTS — potency / volatility / body_weight keyed by trait family name.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

from config.canonical_library_brands import canonical_library_brand

# ---------------------------------------------------------------------------
# 1) Aliases: normalized lowercase key -> canonical brand label
#    Keys must match normalize_text(brand) output (see _normalize_key).
# ---------------------------------------------------------------------------
BRAND_ALIAS_TO_CANONICAL: Dict[str, str] = {
    "flv": "Flavorah",
    "flavorah": "Flavorah",
    "cap": "Capella",
    "capella": "Capella",
    "tfa": "The Flavor Apprentice",
    "tpfa": "The Flavor Apprentice",
    "the flavor apprentice": "The Flavor Apprentice",
}

# ---------------------------------------------------------------------------
# 2) Flavor trait defaults: canonical brand -> fields merged into resolved_* pipeline
#    Only potency / volatility / body_weight (same semantics as before refactor).
# ---------------------------------------------------------------------------
CANONICAL_BRAND_FLAVOR_DEFAULTS: Dict[str, Dict[str, str]] = {
    "Flavorah": {"potency": "high", "volatility": "high", "body_weight": "medium"},
    "Capella": {"potency": "medium", "volatility": "medium", "body_weight": "medium"},
    "The Flavor Apprentice": {"potency": "medium", "volatility": "medium", "body_weight": "medium"},
}

# Short brands from `canonical_library_brand` -> trait family key in CANONICAL_BRAND_FLAVOR_DEFAULTS
LIBRARY_SHORT_TO_TRAIT_FAMILY: Dict[str, str] = {
    "FLV": "Flavorah",
    "CAP": "Capella",
    "CAP (SL)": "Capella",
    "CAP (ES)": "Capella",
    "TFA": "The Flavor Apprentice",
}


def _normalize_key(text: str) -> str:
    """Same rules as flavor_classification_service.normalize_text (keep in sync)."""
    s = (text or "").strip().lower()
    s = re.sub(r"[^\w\s/+&-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def resolve_canonical_brand(brand: str) -> str:
    """
    Map a stored brand string to a canonical label used for legacy bias lookup.

    Prefer library short brands when unambiguous, otherwise fall back to long
    trait-family names from the alias tables.
    """
    raw = (brand or "").strip()
    short = canonical_library_brand(raw, "")
    if short in LIBRARY_SHORT_TO_TRAIT_FAMILY:
        return LIBRARY_SHORT_TO_TRAIT_FAMILY[short]
    key = _normalize_key(raw)
    if key in BRAND_ALIAS_TO_CANONonical:
        return BRAND_ALIAS_TO_CANONICAL[key]
    for canon in CANONICAL_BRAND_FLAVOR_DEFAULTS:
        if _normalize_key(canon) == key:
            return canon
    return raw


def classification_trait_family(brand: str, flavor_name: str = "") -> str:
    """Resolve stored/import brand to CANONICAL_BRAND_FLAVOR_DEFAULTS key (long family name)."""
    short = canonical_library_brand(brand or "", flavor_name or "")
    if short in LIBRARY_SHORT_TO_TRAIT_FAMILY:
        return LIBRARY_SHORT_TO_TRAIT_FAMILY[short]
    return resolve_canonical_brand(brand or "")


def get_brand_flavor_trait_defaults(brand: str, flavor_name: str = "") -> Optional[Dict[str, str]]:
    """
    Return brand-level potency/volatility/body_weight hints, or None if unknown.

    Accepts both short library brands (FLV, CAP, …) and legacy long names.
    """
    if not (brand or "").strip():
        return None
    fam = classification_trait_family(brand or "", flavor_name or "")
    return CANONICAL_BRAND_FLAVOR_DEFAULTS.get(fam)
