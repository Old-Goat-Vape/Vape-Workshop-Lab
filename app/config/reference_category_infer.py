"""
Conservative inference of `flavor_reference.category` (Flavor / Hardware / Base / Nicotine).

Used by canonical library repair. Returns a new category only when signals clearly support a change.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from config.canonical_library_brands import canonical_library_brand

FLAVOR = "Flavor"
HARDWARE = "Hardware"
BASE = "Base"
NICOTINE = "Nicotine"

_VALID = {FLAVOR, HARDWARE, BASE, NICOTINE}


def normalize_category_label(raw: str | None) -> str:
    s = (raw or "").strip()
    if not s:
        return FLAVOR
    t = s.lower()
    if t in {"flavor", "flavours", "flavors"}:
        return FLAVOR
    if t == "hardware":
        return HARDWARE
    if t == "base":
        return BASE
    if t == "nicotine":
        return NICOTINE
    if s in _VALID:
        return s
    return FLAVOR


def _hay(brand: str, name: str) -> str:
    return f"{brand or ''} {name or ''}".lower()


def _vc_catalog_source(src: str | None) -> bool:
    """`vc_flavours_scraper` sets `flavor_reference.source` to 'VC' for catalog rows."""
    return (src or "").strip().upper() == "VC"


# Cart / shop meta SKUs — not concentrates; never promote to Flavor via VC or brand-line rules alone.
_ECOMM_SHOP_META_RX = re.compile(r"\bitem\s+customizations?\b", re.I)

_nic_rx = re.compile(
    r"\b(nicotine|nicsalt|nic\s*salt|salt\s*nic|salt\s*nicotine|freebase|nic\s*shot|nicotine\s*shot|"
    r"nic\s*base|nicotine\s*base)\b",
    re.I,
)
_pg_vg_rx = re.compile(
    r"\b(propylene\s*glycol|vegetable\s*glycerin|vegetable\s*glycerol|mono[- ]?propylene\s*glycol|"
    r"poly\s*ethylene\s*glycol|peg\s*\d+)\b|"
    r"\b99[,.]?\d?\s*%?\s*vg\b|\b99[,.]?\d?\s*%?\s*pg\b|\b100\s*%?\s*vg\b|\b100\s*%?\s*pg\b",
    re.I,
)
_pg_vg_short_rx = re.compile(
    r"(^|\b)(pg|vg)\s*(liquid|base|bottle)?\b|(^|\b)(vpg|pgvg|50\s*/\s*50|5050|70\s*/\s*30)\b",
    re.I,
)
_hw_rx = re.compile(
    r"\b("
    r"drip\s*tip|510\s*tip|replacement\s*glass|bubble\s*glass|glass\s*tube|"
    r"prebuilt\s*coil|mesh\s*coil|coil\s*head|atomizer\s*head|vape\s*coil|"
    r"18650|21700|battery\s*wrap|battery\s*case|\bmod\b|\btank\b|sub[\s-]?ohm\s*tank|rta|rdta|rda\b|"
    r"pod\s*cartridge|empty\s*pod|replacement\s*pod|"
    r"chubby\s*gorilla|\bchubby\b|\bldpe\b|pet\s*bottle|unicorn\s*bottle|mixing\s*bottle|jerry\s*cans?|"
    r"screw\s*cap|bottle\s*cap|"
    r"lanyards?|trucker\s*hat|\bhat\b.*collection|recipe\s*cards?|"
    r"gift\s*cards?|vouchers?|"
    r"\bnozzles?\b|"
    r"\bgloves?\b|"
    r"alcohol\s*swabs?|isopropyl\s*swabs?|prep\s*pad|"
    r"mop\s*caps?|hair\s*nets?|hairnets?|bouffants?|scrub\s*caps?|"
    r"\bstrainers?\b|"
    r"vape\s*tool|coil\s*tool|ohm\s*meter|wire\s*spool|"
    r"cotton\s*laces?|shoelace\s*cotton|vape\s*cotton|"
    r"item\s+customizations?"
    r")\b",
    re.I,
)
# Shop / lab equipment (often mis-filed under a concentrate vendor brand).
_HW_MERCH_RX = re.compile(
    r"\b("
    r"digital\s*scales?|bowl\s*scales?|square\s*type|chemical\s*pump|plastic\s*funnel|\bpumps?\b|"
    r"pipettes?|syringes?|needles?|glass\s*beakers?|\bdisposable\b|"
    r"baseball|cricket|cricket\s*set|desk\s*pad|pad\s*/\s*mat|"
    r"hoodie|neoprene|silicone|universal\s*band|erythritol\s*crystals|"
    r"gift\s*cards?|vouchers?|\bnozzles?\b|\bgloves?\b|"
    r"alcohol\s*swabs?|isopropyl\s*swabs?|prep\s*pads?|"
    r"mop\s*caps?|hair\s*nets?|hairnets?|bouffants?|scrub\s*caps?|"
    r"\bstrainers?\b|"
    r"trucker|cap\s*-\s*blck|"
    r"agri\s*bottles?|bottles?\s*/\s*jerry|"
    r"\bbag\b\s*\d{4}|\bbag\b\s*20\d{2}|\blitres?\b|\bliters?\b|"
    r"item\s+customizations?"
    r")\b",
    re.I,
)


def _name_reads_as_liquid_concentrate_or_additive(name: str) -> bool:
    """True when name is plausibly a bottled concentrate / known additive (allow Hardware→Flavor)."""
    n = (name or "").lower()
    if "concentrate" in n:
        return True
    if re.search(r"\(\s*\)", n):
        return True
    if re.search(
        r"\b(ws[- ]?\d|koolada|pyrazine|acetyl|super\s*sweet|sweetener|ethyl\s*maltol|"
        r"ice\s*crystals|cooling)\b",
        n,
    ):
        return True
    return False


_NON_FLAVOR_MERCH_STRICT_RX = re.compile(
    r"\b("
    r"baseball|bat\b|cricket|cricket\s*set|desk\s*pad|pad\s*/\s*mat|"
    r"pipettes?|syringes?|needles?|glass\s*beakers?|digital\s*scales?|bowl\s*scales?|"
    r"gift\s*cards?|vouchers?|nozzles?|gloves?|alcohol\s*swabs?|isopropyl\s*swabs?|prep\s*pads?|"
    r"mop\s*caps?|hair\s*nets?|hairnets?|bouffants?|scrub\s*caps?|strainers?|"
    r"item\s+customizations?"
    r")\b",
    re.I,
)


_KNOWN_CONCENTRATE_CANON = frozenset(
    {"FLV", "CAP", "CAP (SL)", "CAP (ES)", "TFA", "BLCK", "BF"},
)


def _is_concentrate_line_brand(brand: str, name: str) -> bool:
    cb = canonical_library_brand(brand or "", name or "")
    if cb in _KNOWN_CONCENTRATE_CANON:
        return True
    return bool(
        re.match(
            r"^(INW|FA|WF|VT|HS|OOO|PUR|SC|NIC\s*RIV|NR)\b",
            (brand or "").strip(),
            re.I,
        )
    )


def _nic_score(text: str, is_nicotine: int) -> int:
    s = 0
    if int(is_nicotine or 0) == 1:
        s += 4
    if _nic_rx.search(text):
        s += 3
    if re.search(r"\b\d{1,2}(?:\.\d+)?\s*mg(?:\s*/\s*ml)?\b", text) and (
        "nic" in text or "salt" in text or "freebase" in text
    ):
        s += 2
    return s


def _base_score(text: str, nic_score: int) -> int:
    if nic_score >= 3:
        return 0
    s = 0
    if _pg_vg_rx.search(text):
        s += 4
    if _pg_vg_short_rx.search(text):
        s += 3
    if re.search(r"\b(shortfill|shake\s*n\s*vape|longfill|hybrid\s*bases?)\b", text):
        s += 3
    if re.search(r"\b(diluent|carrier|base\s*liquid|mixing\s*base)\b", text) and "nic" not in text:
        s += 2
    return s


def _hw_score(text: str, brand: str) -> int:
    s = 0
    if _hw_rx.search(text):
        s += 4
    if _HW_MERCH_RX.search(text):
        s += 4
    if re.search(r"\b(coils?|atomizer|drip\s*tip)\b", text):
        s += 2
    b = (brand or "").strip().lower()
    if b in {"hardware", "accessories", "tools"}:
        s += 2
    return s


def merge_category_bucket(
    brand: str,
    name: str,
    category: str | None,
    is_nicotine: int,
) -> str:
    """Single bucket label so duplicate groups only merge when buckets match."""
    cur = normalize_category_label(category)
    text = _hay(brand, name)
    ns = _nic_score(text, is_nicotine)
    bs = _base_score(text, ns)
    hs = _hw_score(text, brand)
    flavor_line = _is_concentrate_line_brand(brand, name)

    # Hardware/base/nicotine signals beat vendor brand — shop uses one brand for concentrate + merch.
    if ns >= 3:
        return NICOTINE
    if bs >= 3:
        return BASE
    if hs >= 3:
        return HARDWARE
    if flavor_line and ns < 2:
        return FLAVOR
    return cur


def suggest_category_correction(
    brand: str,
    name: str,
    current: str | None,
    is_nicotine: int,
    source: str | None = None,
) -> Tuple[Optional[str], Optional[int], str]:
    """
    (new_category or None, new_is_nicotine or None, reason).

    When new_category is None, keep the existing category string unless caller applies
    a label-casing fix via normalize_category_label.

    Rows from the VC flavour scraper (`source == 'VC'`) are catalog concentrates: when
    still filed as Hardware, promote to Flavor unless nicotine/base signals are strong.
    """
    raw_cat = (current or "").strip()
    cur = normalize_category_label(current)
    text = _hay(brand, name)
    ns = _nic_score(text, is_nicotine)
    bs = _base_score(text, ns)
    hs = _hw_score(text, brand)
    flavor_line = _is_concentrate_line_brand(brand, name)

    if ns >= 3:
        target = NICOTINE
        t_reason = "nicotine_signal"
    elif bs >= 3:
        target = BASE
        t_reason = "base_signal"
    elif hs >= 3:
        target = HARDWARE
        t_reason = "hardware_signal"
    elif flavor_line and ns < 2:
        target = FLAVOR
        t_reason = "flavor_brand_line"
    else:
        target = cur
        t_reason = "no_strong_signal"

    vc = _vc_catalog_source(source)
    if (
        vc
        and cur == HARDWARE
        and ns < 3
        and bs < 3
        and not _ECOMM_SHOP_META_RX.search(name or "")
    ):
        if target == HARDWARE or t_reason == "no_strong_signal":
            target = FLAVOR
            t_reason = "vc_source_catalog"

    def _non_concentrate_merch_blocks_flavor_flip(nm: str) -> bool:
        """Keep Hardware when the row name is clearly merch/lab, not a liquid concentrate line."""
        if _name_reads_as_liquid_concentrate_or_additive(nm):
            return False
        return bool(_NON_FLAVOR_MERCH_STRICT_RX.search(nm or ""))

    new_cat: Optional[str] = None
    reason = "unchanged"

    if raw_cat and cur != raw_cat and target == cur:
        new_cat = cur
        reason = "normalized_label_only"
    elif target != cur:
        weak = (
            (target == HARDWARE and hs < 4)
            or (target == BASE and bs < 4)
            or (target == NICOTINE and ns < 3)
            or (target == FLAVOR and cur == HARDWARE and hs >= 3 and not vc)
            or (target == FLAVOR and cur == HARDWARE and t_reason == "flavor_brand_line" and hs >= 2 and not vc)
            or (
                target == FLAVOR
                and cur == HARDWARE
                and t_reason == "flavor_brand_line"
                and _non_concentrate_merch_blocks_flavor_flip(name)
                and not vc
            )
            or (target == FLAVOR and cur == BASE and bs >= 3)
            or (target == HARDWARE and cur == BASE and bs >= 4)
        )
        if weak:
            reason = "weak_signal_keeping_current"
        else:
            new_cat = target
            reason = t_reason

    nic_new: Optional[int] = None
    eff = new_cat if new_cat is not None else cur
    if eff == NICOTINE and int(is_nicotine or 0) != 1:
        nic_new = 1
        if reason == "unchanged" or reason.startswith("weak"):
            reason = "sync_is_nicotine"
    elif eff != NICOTINE and int(is_nicotine or 0) == 1 and ns < 2 and not _nic_rx.search(text):
        nic_new = 0
        if reason == "unchanged":
            reason = "clear_false_nic_flag"

    if new_cat is None and nic_new is None:
        return None, None, reason
    return new_cat, nic_new, reason
