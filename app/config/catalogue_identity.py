"""Conservative catalogue identities: spelling aliases are not different formulations."""

from functools import lru_cache
import json
import re
from config.canonical_library_brands import (
    canonical_library_brand,
    normalize_flavor_identity_name,
    preferred_flavor_storage_name,
)
from config.reference_category_infer import normalize_category_label

# Manufacturer-confirmed SilverLine products. Do not infer other unmarked lines.
# https://capellaflavors.com/products/27-fish (SilverLine)
_VERIFIED_SILVERLINE = {"27 fish", "27 bears"}
_SERIES_SUFFIX = re.compile(
    r"\s*(?:\(\s*(sl|es)\s*\)|(silver\s*line|euro\s*series)|\b(sl|es))\s*$", re.I
)


class CatalogueIdentityConflict(ValueError):
    """An edit would turn a separate ingredient into an existing flavour identity."""


@lru_cache(maxsize=40000)
def canonical_item(brand, name, category="Flavor"):
    brand = str(brand or "").strip()
    name = str(name or "").strip()
    kind = normalize_category_label(category)
    cap = (
        bool(re.fullmatch(r"cap\s*(?:\(\s*(?:sl|es)\s*\))?|csl", brand, re.I))
        or "capella" in brand.lower()
    )
    cb = canonical_library_brand(brand, name)
    clean = preferred_flavor_storage_name(name)
    if cap:
        explicit = re.search(r"\(\s*(sl|es)\s*\)", brand, re.I)
        brand_line = canonical_library_brand(brand, "")
        series = (
            explicit.group(1).upper()
            if explicit
            else (
                "SL"
                if brand_line == "CAP (SL)" or brand.lower() == "csl"
                else "ES" if brand_line == "CAP (ES)" else None
            )
        )
        match = _SERIES_SUFFIX.search(clean)
        if match:
            marker = re.sub(r"[^a-z]", "", match.group(0).lower())
            name_series = "SL" if marker in {"sl", "silverline"} else "ES"
            # Conflicting explicit labels stay separate for review.
            if not series or series == name_series:
                series = name_series
                clean = preferred_flavor_storage_name(clean[: match.start()])
        if (
            series is None
            and normalize_flavor_identity_name(clean) in _VERIFIED_SILVERLINE
        ):
            series = "SL"
        cb = f"CAP ({series})" if series else "CAP"
    normalized = normalize_flavor_identity_name(clean)
    key = (
        json.dumps([kind, cb.casefold(), normalized], separators=(",", ":"))
        if cb and normalized
        else None
    )
    return cb, clean, key


def reference_identity_index(db):
    """Build once per import batch; prefer priced references among legacy duplicates."""
    rows = db.execute(
        "SELECT id, brand, name, category, price_per_ml FROM flavor_reference "
        "WHERE COALESCE(active,1)=1 ORDER BY (COALESCE(price_per_ml,0)>0) DESC, id"
    ).fetchall()
    result = {}
    for row in rows:
        key = canonical_item(row["brand"], row["name"], row["category"])[2]
        if key is not None:
            result.setdefault(key, int(row["id"]))
    return result


def find_reference(db, *, brand, name, category="Flavor"):
    """Reuse an active identity, preferring a priced record; never merge holdings here."""
    key = canonical_item(brand, name, category)[2]
    return reference_identity_index(db).get(key) if key is not None else None


def register_catalogue_functions(db):
    from config.catalogue_product_policy import recipe_product_allowed

    db.create_function(
        "tvw_recipe_product_allowed", 3, recipe_product_allowed, deterministic=True
    )
    db.create_function(
        "tvw_catalogue_key",
        3,
        lambda b, n, c: canonical_item(b, n, c)[2],
        deterministic=True,
    )


def catalogue_flavor_match_key(brand, name):
    cb, clean, _ = canonical_item(brand, name, "Flavor")
    return cb.casefold(), normalize_flavor_identity_name(clean)


def flavour_category_sql(column):
    return f"LOWER(TRIM(COALESCE(NULLIF({column},''),'Flavor'))) IN ('flavor','flavour','flavors','flavours','concentrate','concentrates','additive')"


def recipe_flavour_sql(alias, name_column="name"):
    return f"({flavour_category_sql(alias+'.category')} AND tvw_recipe_product_allowed({alias}.brand,{alias}.{name_column},{alias}.category)=1)"


def visible_flavour_sql(alias="f"):
    return (
        f"COALESCE({alias}.merged_into_flavour_id,0)=0 AND COALESCE({alias}.is_hidden,0)=0 "
        f"AND COALESCE({alias}.is_invalid,0)=0 AND LOWER(COALESCE({alias}.moderation_status,'active'))='active' "
        f"AND {recipe_flavour_sql(alias,'canonical_name')} "
        f"AND NOT EXISTS (SELECT 1 FROM flavor_reference cr WHERE cr.id={alias}.source_flavor_ref_id "
        f"AND (NOT ({recipe_flavour_sql('cr')}) OR COALESCE(cr.is_nicotine,0)=1 OR COALESCE(cr.active,1)=0))"
    )
