"""Product eligibility for liquid flavour pickers, separate from legacy category labels.

Sources and unresolved supplier conflicts: docs/CATALOGUE_PRODUCT_RESEARCH_2026-11-11.md.
This classifies catalogue products; it is not an inhalation-safety certification.
"""

from functools import lru_cache
from html import unescape
import re

_HARDWARE_BRANDS = {
    "orca vape san dynasty",
    "uwell",
    "oxva",
    "nevoks",
    "hellvape",
    "vaporesso",
    "reload vapor usa",
    "voopoo",
    "elf bar",
    "geekvape",
    "lost vape",
    "moti",
    "vandy vape",
    "golisi",
    "vapjoy",
    "smok",
    "demon killer",
    "cotton bacon",
    "ipha",
    "ijoy",
    "joyetech",
    "suorin",
    "vapefly",
    "vapeman",
}
_HARDWARE_NAME = re.compile(
    r"\b(?:pod\s+(?:kit|system)|replacement\s+(?:mesh\s+)?(?:pods?|coils?|cartridges?)|"
    r"(?:pods?|coils?)\s+replacement|(?:usb|smart)\s+charger|(?:needle|ceramic)\s+tweezers|"
    r"bottle\s+opener|cleaning\s+brush|folding\s+scissors|wire\s+cutter|label\s+tape|"
    r"fused\s+clapton\s+wire|510\s+adapter|810\s+drip\s+tips?|unicorn\s+bottle)\b",
    re.I,
)


@lru_cache(maxsize=40000)
def product_kind(brand, name):
    brand = unescape(str(brand or "")).strip().casefold()
    name = unescape(str(name or "")).strip().casefold()
    if re.search(
        r"ready[\s-]*to[\s-]*vape|it['’]?s\s+(?:e[\s-]*liquid|nic\s*salt)", name
    ):
        return "Finished Liquid"
    if re.search(r"\be[\s-]*liquid\b", name) and not re.search(
        r"\bconcentrate\b", name
    ):
        return "Finished Liquid"
    if re.search(r'\b(?:clyro\s*nic|nicotine|freebase|nic\s*salt)\b',name):
        return 'Nicotine Product'
    if re.search(r"\bdisposable\b|\b\d+\s*puffs?\b", name) and not re.search(
        r"\bconcentrate\b", name
    ):
        return "Hardware"
    if re.search(r'\b\d+(?:[.,]\d+)?\s*mg(?:\s*/\s*ml)?\b',name):
        return 'Pending Verification'
    if re.search(r"\b(perfume|fragrance|eau\s+de\s+(?:parfum|toilette))\b", name):
        return "Fragrance"
    if brand in {
        "sa",
        "ssa",
        "super aromas",
        "sobucky",
        "sobucky super aromas",
    } and re.match(r"(fruit hard candy|scandinavian fruits)\b", name):
        return "Pending Verification"
    if re.sub(r'[^a-z0-9]','',brand) in {'supa','ssasupa','superaroma','superaromas','sobuckysuperaromas'} and re.match(r'(fruit hard candy|scandinavian fruits)\b',name):
        return 'Pending Verification'
    if re.search(r"\bsterili[sz]ing\s+fluid\b", name) or (
        brand == "lclc" and name.startswith("saline")
    ):
        return "Other Chemical"
    if re.search(
        r"\b(?:erythritol|ethyl\s*maltol|sucralose|ws[- ]?23|ws[- ]?5)\s*(?:ice\s*)?crystals?\b",
        name,
    ):
        return "Powder Additive"
    if re.search(r"\b(?:malic|citric|lactic)\s+acid\b", name) and re.search(
        r"\b\d+(?:[.,]\d+)?\s*(?:g|gm|grams?|kg)\b", name
    ):
        return "Powder Additive"
    if (
        brand in _HARDWARE_BRANDS
        or _HARDWARE_NAME.search(name)
        or (brand == "bewolk industries" and re.match(r"steamcraft\s+ak-47r?\b", name))
    ):
        return "Hardware"
    return None


def verified_liquid_concentrate(brand, name):
    """Narrow supplier-confirmed exceptions, not a mixed retailer-wide promotion."""
    return str(brand or "").strip().casefold() == "clyrolinx" and bool(
        re.fullmatch(
            r"cappuccino\s*[-–—]\s*(?:10|30|100)\s*ml",
            unescape(str(name or "")).strip(),
            re.I,
        )
    )


def recipe_product_allowed(brand, name, category=None):
    """Unknown liquid flavours retain legacy handling; positively excluded kinds do not."""
    return int(product_kind(brand, name) is None)
