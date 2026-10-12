"""Normalize BLCK Shopify variant titles/options into volume, nic, carrier fields."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple


def parse_pack_volume_ml(*texts: str) -> Optional[float]:
    """Parse volumetric pack size. Ignores kg-only packs unless an explicit Litre is present."""
    blob = " ".join(str(t or "") for t in texts)
    if not blob.strip():
        return None
    low = blob.lower().replace(",", " ")

    # Prefer explicit litre / ml tokens; skip bare kg drums without litre.
    m = re.search(r"(\d+(?:\.\d+)?)\s*l(?:itre|iter)?\b", low)
    if m and "ml" not in low[max(0, m.start() - 1) : m.start() + 1]:
        # Avoid matching the 'l' inside 'ml' — handled below.
        pass
    # ml first
    m_ml = re.search(r"(\d+(?:\.\d+)?)\s*ml\b", low)
    if m_ml:
        return float(m_ml.group(1))

    m_l = re.search(r"(\d+(?:\.\d+)?)\s*(?:litre|liter|l)\b", low)
    if m_l:
        # Exclude accidental match of trailing 'l' in words; litre/liter/l with word boundary is OK.
        return float(m_l.group(1)) * 1000.0

    # Parenthetical litre inside kg titles, e.g. "30kg (24 Litre)"
    m_paren = re.search(r"\(\s*(\d+(?:\.\d+)?)\s*(?:litre|liter|l)\s*\)", low)
    if m_paren:
        return float(m_paren.group(1)) * 1000.0

    return None


def parse_nicotine_strength_mg_ml(*texts: str) -> Optional[float]:
    blob = " ".join(str(t or "") for t in texts).lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*mg\s*/\s*ml", blob)
    if m:
        return float(m.group(1))
    m2 = re.search(r"(\d+(?:\.\d+)?)\s*mg\b", blob)
    if m2 and "mg/ml" in blob.replace(" ", ""):
        return float(m2.group(1))
    return None


def parse_carrier(*texts: str) -> Optional[str]:
    blob = " ".join(str(t or "") for t in texts).lower()
    # Prefer explicit PG/VG tokens near strength or as standalone option.
    if re.search(r"\bpg\b", blob) and not re.search(r"\bvg\b", blob):
        return "PG"
    if re.search(r"\bvg\b", blob) and not re.search(r"\bpg\b", blob):
        return "VG"
    if re.search(r"\bpg\b", blob) and re.search(r"\bvg\b", blob):
        # Both present: take the one adjacent to mg/ml if possible.
        m = re.search(r"mg\s*/\s*ml\s+(pg|vg)\b", blob)
        if m:
            return m.group(1).upper()
        m2 = re.search(r"(pg|vg)\s+\d", blob)
        if m2:
            return m2.group(1).upper()
    return None


def parse_nicotine_type(product_title: str = "", vendor: str = "", *texts: str) -> Optional[str]:
    blob = " ".join([product_title, vendor, *[str(t or "") for t in texts]]).lower()
    if "salt" in blob:
        return "salt"
    if "freebase" in blob or "gold nic" in blob or "gold-nic" in blob:
        return "freebase"
    if "nicotine" in blob:
        return "freebase"
    return None


def normalize_variant_fields(
    *,
    product_title: str,
    vendor: str = "",
    variant_title: str = "",
    option1: str = "",
    option2: str = "",
    option3: str = "",
) -> Dict[str, Any]:
    texts = (product_title, variant_title, option1, option2, option3)
    return {
        "pack_volume_ml": parse_pack_volume_ml(*texts),
        "nicotine_strength_mg_ml": parse_nicotine_strength_mg_ml(*texts),
        "carrier": parse_carrier(*texts),
        "nicotine_type": parse_nicotine_type(product_title, vendor, variant_title, option1, option2, option3),
    }


def variant_matches_criteria(
    fields: Dict[str, Any],
    *,
    pack_volume_ml: Optional[float] = None,
    nicotine_strength_mg_ml: Optional[float] = None,
    carrier: Optional[str] = None,
    nicotine_type: Optional[str] = None,
) -> bool:
    if pack_volume_ml is not None:
        got = fields.get("pack_volume_ml")
        if got is None or abs(float(got) - float(pack_volume_ml)) > 0.01:
            return False
    if nicotine_strength_mg_ml is not None:
        got = fields.get("nicotine_strength_mg_ml")
        if got is None or abs(float(got) - float(nicotine_strength_mg_ml)) > 0.01:
            return False
    if carrier is not None:
        if str(fields.get("carrier") or "").upper() != str(carrier).upper():
            return False
    if nicotine_type is not None:
        if str(fields.get("nicotine_type") or "").lower() != str(nicotine_type).lower():
            return False
    return True
