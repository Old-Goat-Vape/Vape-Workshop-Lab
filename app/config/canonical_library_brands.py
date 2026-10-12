"""
Authoritative short canonical brands for `flavor_reference` storage and matching.

- Flavorah / FLV / flv -> FLV
- Capella family -> CAP, CAP (SL), or CAP (ES) (series must not collapse)
- The Flavor Apprentice / TFA / TPA / TPFA -> TFA
- Noise in raw brands (trailing %, \"Flavour\" suffix, etc.) is stripped before mapping
- Unknown brands: returned trimmed after sanitization (no guessing beyond aliases)

Matching keys must use `flavor_identity_match_key()` so scrapers, relink, repair, and
library dedupe stay aligned. Name cleanup for storage uses `preferred_flavor_storage_name()`.
"""

from __future__ import annotations

import re
from typing import Tuple


def _normTextVc(value: str) -> str:
    s = (value or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s&/+\-]", "", s)
    return s


def _strip_brand(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _combined_lower(brand: str, flavor: str) -> str:
    return f"{_strip_brand(brand)} {_strip_brand(flavor)}".lower()


def _strip_brand_punct_for_key(brand: str) -> str:
    """Apostrophes and similar break simple alias keys (Baker's vs Bakers)."""
    b = (brand or "").strip()
    b = re.sub(r"['\u2019\u2018`]", "", b)
    return _strip_brand(b)


def _alias_lookup_key(brand_raw: str) -> str:
    """Lowercased brand key with only %-suffix stripped — before dropping trailing 'Flavour', so 'Bakers Flavors' can match."""
    s = _strip_brand_punct_for_key(brand_raw)
    s = re.sub(r"(?i)\s+\d+(?:\.\d+)?\s*%?\s*$", "", s)
    s = _strip_brand(s)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s/+&-]", " ", s.lower())).strip()


_LINE_BRAND_ALIASES = {
    "flv": "FLV",
    "flavorah": "FLV",
    # Intentionally omit "cap"/"capella" here — series (CAP vs CAP (SL) vs CAP (ES)) needs flavour context.
    "tfa": "TFA",
    "tpfa": "TFA",
    "tpa": "TFA",
    "the flavor apprentice": "TFA",
    "the flavour apprentice": "TFA",
    "perfumers apprentice": "TFA",
    "the perfumers apprentice": "TFA",
    "blck": "BLCK",
    "blck flavour": "BLCK",
    "blck flavor": "BLCK",
    "bakers flavors": "BF",
    "bakers flavours": "BF",
    "bakers flavor": "BF",
    "bakers flavour": "BF",
    "baker flavors": "BF",
    "baker flavours": "BF",
}


def _sanitize_brand_noise(brand_raw: str) -> str:
    """
    Remove common import/entry junk from brand strings before alias resolution.
    Safe: only trailing percentage blobs and trailing flavour/flavor words.
    """
    s = _strip_brand_punct_for_key(brand_raw)
    if not s:
        return ""

    # Trailing strength like " 0.25 %" / " 3.50%" (user error or bad joins)
    s = re.sub(r"(?i)\s+\d+(?:\.\d+)?\s*%?\s*$", "", s)
    s = _strip_brand(s)

    # Do NOT strip generic trailing "Flavours/Flavor" — that mangles real names ("Wonder Flavours" -> "Wonder").
    # Vendor+flavour shortcuts (e.g. "BLCK Flavour") are handled via `_alias_lookup_key` / `_LINE_BRAND_ALIASES`.
    return _strip_brand(s)


def _is_capella_family(brand: str, combined_lower: str) -> bool:
    b = _strip_brand(brand).lower()
    if b in {"cap", "capella", "cap (sl)", "cap (es)"}:
        return True
    if "capella" in b:
        return True
    if "capella" in combined_lower:
        return True
    return False


def _capella_series_label(brand: str, flavor: str, combined_lower: str) -> str | None:
    """
    Return 'CAP (SL)' or 'CAP (ES)' when series is explicit; else None (plain CAP).
    Conservative: requires clear Silver Line / Euro Series signals.
    """
    text = _combined_lower(brand, flavor)
    hay = f"{combined_lower} {text}"

    if re.search(r"silver\s*line", hay) or re.search(r"capella\s+silver", hay):
        return "CAP (SL)"
    if "(sl)" in hay and ("cap" in hay or "capella" in hay):
        return "CAP (SL)"

    if re.search(r"euro\s*series", hay) or re.search(r"capella\s+euro", hay):
        return "CAP (ES)"
    if "(es)" in hay and "euro" in hay:
        return "CAP (ES)"

    return None


def canonical_library_brand(brand_raw: str, flavor_name_raw: str = "") -> str:
    """
    Canonical short brand label for storage and identity matching.

    Capella special cases:
    - Silver Line -> CAP (SL)
    - Euro Series -> CAP (ES)
    - Otherwise Capella -> CAP
    """
    ak = _alias_lookup_key(brand_raw)
    for alias_key in (ak, re.sub(r"^the\s+", "", ak)):
        if alias_key in _LINE_BRAND_ALIASES:
            return _LINE_BRAND_ALIASES[alias_key]

    b = _sanitize_brand_noise(brand_raw)
    n = _strip_brand(flavor_name_raw)
    combined_lower = _combined_lower(b, n)

    if _is_capella_family(b, combined_lower):
        sl = _capella_series_label(b, n, combined_lower)
        if sl:
            return sl
        return "CAP"

    key = re.sub(r"[^\w\s/+&-]", " ", b.lower())
    key = re.sub(r"\s+", " ", key).strip()

    for alias_key in (key, re.sub(r"^the\s+", "", key)):
        if alias_key in _LINE_BRAND_ALIASES:
            return _LINE_BRAND_ALIASES[alias_key]

    lk = key.lower()
    if "flavorah" in lk or lk == "flv":
        return "FLV"
    if "flavor apprentice" in lk or "flavour apprentice" in lk or lk in {"tfa", "tpa", "tpfa"}:
        return "TFA"
    if lk == "blck":
        return "BLCK"
    if "capella" in lk:
        sl = _capella_series_label(b, n, combined_lower)
        return sl if sl else "CAP"

    return b


def normalize_flavor_identity_name(name: str) -> str:
    """
    Stable normalized form for identity matching (lowercase, junk stripped).

    Strips empty parentheses, trailing \"concentrate\" (including \"concentrate**\"),
    collapses whitespace, and keeps phrase-friendly characters consistent with legacy VC rules.
    """
    s = (name or "").strip()
    if not s:
        return ""

    s = s.lower()
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    # VC/Capella imports often append stars or punctuation after "Concentrate"
    s = re.sub(r"(?i)\s*\bconcentrate\b[\s*.,;:+-]*$", "", s)
    s = re.sub(r"\s+", " ", s).strip()

    s = re.sub(r"[^\w\s()%/+&-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\(\s*", "(", s)
    s = re.sub(r"\s*\)", ")", s)
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[\s*.,;:+-]+$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fold_capella_brand_if_name_unmarked(canonical_brand: str, flavor_name: str) -> str:
    """
    For dedupe / storage alignment: CAP (SL) and CAP (ES) rows whose flavour name does not
    mention that series match plain CAP (e.g. BLCK imports omit \"Silver Line\").
    """
    b = (canonical_brand or "").strip()
    if b not in {"CAP (SL)", "CAP (ES)"}:
        return canonical_brand
    nk = normalize_flavor_identity_name(flavor_name or "")
    if b == "CAP (SL)" and not re.search(r"silver\s*line|\(sl\)", nk):
        return "CAP"
    if b == "CAP (ES)" and not re.search(r"euro\s*series|\(es\)|capella\s+euro", nk):
        return "CAP"
    return canonical_brand


def preferred_flavor_storage_name(raw: str) -> str:
    """
    Human-oriented flavor name for `flavor_reference.name` after cleaning obvious junk.
    Does not apply title case; picks shortest reasonable form when combining duplicates.
    """
    s = (raw or "").strip()
    if not s:
        return ""

    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"(?i)\s*\bconcentrate\b[\s*.,;:+-]*$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[\s*.,;:+-]+$", "", s)
    return s.strip()


def flavor_identity_match_key(brand_raw: str, flavor_name_raw: str) -> Tuple[str, str]:
    """Stable (brand_key, name_key) for dedupe, reference lookup, and identity resolution."""
    cb = canonical_library_brand(brand_raw or "", flavor_name_raw or "")
    cb = fold_capella_brand_if_name_unmarked(cb, flavor_name_raw or "")
    return (_normTextVc(cb), normalize_flavor_identity_name(flavor_name_raw or ""))
