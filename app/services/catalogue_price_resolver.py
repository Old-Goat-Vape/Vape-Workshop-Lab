from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# Preferred display labels; legacy AuNic manuals remain as fallback match targets only.
FREEBASE_NICOTINE_CATALOGUE = ("AuNic", "Freebase Nicotine VG - 1000ml")
SALT_NICOTINE_CATALOGUE = ("AuNic", "Salt Nicotine VG - 1000ml")
FREEBASE_NICOTINE_FALLBACKS = (
    ("AuNic", "Freebase Nicotine VG - 1000ml"),
    ("Gold-Nic", "Gold Nic"),
)
SALT_NICOTINE_FALLBACKS = (
    ("AuNic", "Salt Nicotine VG - 1000ml"),
    ("AuNic", "AuNic - Salt Nicotine"),
)

AUTO_SELECT_MIN_SCORE = 65
AMBIGUOUS_SCORE_GAP = 5

_BRAND_ALIAS_MAP: Dict[str, str] = {
    "inw": "inawera",
    "inawera": "inawera",
    "cap": "capella",
    "capella": "capella",
    "tfa": "the flavor apprentice",
    "tpa": "the perfumer's apprentice",
    "the flavor apprentice": "the flavor apprentice",
    "the perfumers apprentice": "the perfumer's apprentice",
    "the perfumer's apprentice": "the perfumer's apprentice",
    "fw": "flavor west",
    "flavor west": "flavor west",
    "fa": "flavourart",
    "flavourart": "flavourart",
    "flavor art": "flavourart",
    "flavour art": "flavourart",
    "flv": "flavorah",
    "flavorah": "flavorah",
    "wf": "wonder flavours",
    "wonder flavours": "wonder flavours",
    "ssa": "sobucky",
    "sobucky": "sobucky",
    "sobucky super aromas": "sobucky",
}

_NAME_NOISE = frozenset({"concentrate", "flavor", "flavour", "aroma", "essence"})


def get_brand_aliases() -> Dict[str, str]:
    return dict(_BRAND_ALIAS_MAP)


def normalise_brand(value: Any) -> str:
    raw = re.sub(r"[^\w\s']", " ", str(value or "").lower())
    compact = " ".join(raw.split()).strip()
    if not compact:
        return ""
    return _BRAND_ALIAS_MAP.get(compact, compact)


def normalise_flavour_name(value: Any) -> str:
    raw = re.sub(r"[^\w\s]", " ", str(value or "").lower())
    tokens = [t for t in raw.split() if t and t not in _NAME_NOISE]
    return " ".join(tokens).strip()


def _price_positive(price_per_ml: Any) -> bool:
    try:
        return float(price_per_ml or 0) > 0.0
    except (TypeError, ValueError):
        return False


def _catalogue_label(row: Dict[str, Any]) -> str:
    brand = str(row.get("brand") or "").strip()
    name = str(row.get("name") or "").strip()
    if brand and name:
        return f"{brand} / {name}"
    return name or brand or f"#{row.get('id')}"


def score_catalogue_match(
    *,
    recipe_brand: str,
    recipe_name: str,
    catalogue_brand: str,
    catalogue_name: str,
    price_per_ml: Any,
    reference_only: bool = False,
) -> int:
    score = 0
    rb = normalise_brand(recipe_brand)
    cb = normalise_brand(catalogue_brand)
    rn = normalise_flavour_name(recipe_name)
    cn = normalise_flavour_name(catalogue_name)

    if rb and cb and rb == cb:
        score += 40

    if rn and cn:
        if rn == cn:
            score += 40
        elif cn.startswith(rn):
            score += 25
        elif rn in cn:
            score += 20
        elif cn in rn:
            score += 15

    if _price_positive(price_per_ml):
        score += 25

    if reference_only:
        score -= 20

    return score


def _is_reference_only_row(row: Dict[str, Any]) -> bool:
    if int(row.get("active") or 1) != 1:
        return True
    cat = str(row.get("category") or "").strip().lower()
    return cat in {"reference", "placeholder"}


def _nicotine_kind(recipe_ingredient: Dict[str, Any]) -> Optional[str]:
    if int(recipe_ingredient.get("is_nicotine") or 0) != 1:
        return None
    text = f"{recipe_ingredient.get('brand', '')} {recipe_ingredient.get('name', '')}".lower()
    if "salt" in text or "nicsalt" in text:
        return "salt"
    return "freebase"


def _find_nicotine_catalogue_row(
    catalogue_rows: List[Dict[str, Any]],
    *,
    kind: str,
) -> Optional[Dict[str, Any]]:
    # Prefer live canonical BLCK variant pricing.
    try:
        from services.blck_canonical_costing_service import resolve_nicotine_price_per_ml

        entry = resolve_nicotine_price_per_ml(kind=kind)
        if entry:
            return {
                "id": None,
                "brand": str(entry.get("product_title") or "").split("(")[0].strip() or ("Gold-Nic" if kind == "freebase" else "AuNic"),
                "name": str(entry.get("label") or entry.get("variant_title") or ""),
                "price_per_ml": float(entry.get("price_per_ml") or 0),
                "source": "BLCK_VARIANT",
                "url": entry.get("product_url"),
                "last_scraped": entry.get("last_scraped"),
                "source_variant_id": entry.get("source_variant_id"),
                "pack_price": entry.get("pack_price"),
                "pack_volume_ml": entry.get("pack_volume_ml"),
                "active": 1,
                "category": "Nicotine",
                "_canonical_entry": entry,
            }
    except Exception:
        pass

    target = FREEBASE_NICOTINE_CATALOGUE if kind == "freebase" else SALT_NICOTINE_CATALOGUE
    fallbacks = FREEBASE_NICOTINE_FALLBACKS if kind == "freebase" else SALT_NICOTINE_FALLBACKS
    best: Optional[Dict[str, Any]] = None
    best_score = -1
    for target_brand_raw, target_name_raw in fallbacks:
        target_brand = normalise_brand(target_brand_raw)
        target_name = normalise_flavour_name(target_name_raw)
        for row in catalogue_rows:
            brand = str(row.get("brand") or "")
            name = str(row.get("name") or "")
            if normalise_brand(brand) != target_brand:
                continue
            cn = normalise_flavour_name(name)
            if target_name not in cn and cn not in target_name:
                continue
            sc = score_catalogue_match(
                recipe_brand=target_brand_raw,
                recipe_name=target_name_raw,
                catalogue_brand=brand,
                catalogue_name=name,
                price_per_ml=row.get("price_per_ml"),
                reference_only=_is_reference_only_row(row),
            )
            if sc > best_score:
                best_score = sc
                best = row
    return best


def resolve_price_item(
    recipe_ingredient: Dict[str, Any],
    catalogue_rows: List[Dict[str, Any]],
    *,
    override_catalogue_id: int | None = None,
) -> Dict[str, Any]:
    """Resolve a recipe ingredient to a catalogue price row."""
    ingredient_id = int(recipe_ingredient.get("ingredient_id") or 0)
    brand = str(recipe_ingredient.get("brand") or "")
    name = str(recipe_ingredient.get("name") or "")

    if override_catalogue_id:
        picked = next((r for r in catalogue_rows if int(r.get("id") or 0) == int(override_catalogue_id)), None)
        if picked:
            price = float(picked.get("price_per_ml") or 0)
            return {
                "status": "manual",
                "catalogue_id": int(picked["id"]),
                "catalogue_brand": str(picked.get("brand") or ""),
                "catalogue_name": str(picked.get("name") or ""),
                "resolved_label": _catalogue_label(picked),
                "price_per_ml": price if _price_positive(price) else None,
                "confidence_score": 100,
                "candidates": [],
                "missing_price": not _price_positive(price),
            }

    nic_kind = _nicotine_kind(recipe_ingredient)
    if nic_kind:
        nic_row = _find_nicotine_catalogue_row(catalogue_rows, kind=nic_kind)
        if nic_row:
            price = float(nic_row.get("price_per_ml") or 0)
            canon = nic_row.get("_canonical_entry") or {}
            cat_id = nic_row.get("id")
            return {
                "status": "resolved",
                "catalogue_id": int(cat_id) if cat_id is not None else None,
                "catalogue_brand": str(nic_row.get("brand") or ""),
                "catalogue_name": str(nic_row.get("name") or ""),
                "resolved_label": str(canon.get("label") or _catalogue_label(nic_row)),
                "price_per_ml": price if _price_positive(price) else None,
                "confidence_score": 100,
                "candidates": [],
                "missing_price": not _price_positive(price),
                "canonical_variant": canon or None,
                "price_source_detail": {
                    "source_variant_id": nic_row.get("source_variant_id") or canon.get("source_variant_id"),
                    "pack_price": nic_row.get("pack_price") or canon.get("pack_price"),
                    "pack_volume_ml": nic_row.get("pack_volume_ml") or canon.get("pack_volume_ml"),
                    "last_scraped": nic_row.get("last_scraped") or canon.get("last_scraped"),
                    "product_url": nic_row.get("url") or canon.get("product_url"),
                },
            }

    linked_id = ingredient_id
    linked = next((r for r in catalogue_rows if int(r.get("id") or 0) == linked_id), None)
    linked_price = float(linked.get("price_per_ml") or 0) if linked else 0.0
    if linked and _price_positive(linked_price):
        return {
            "status": "resolved",
            "catalogue_id": linked_id,
            "catalogue_brand": str(linked.get("brand") or brand),
            "catalogue_name": str(linked.get("name") or name),
            "resolved_label": _catalogue_label(linked),
            "price_per_ml": linked_price,
            "confidence_score": 100,
            "candidates": [],
            "missing_price": False,
        }

    scored: List[Tuple[int, Dict[str, Any]]] = []
    for row in catalogue_rows:
        sc = score_catalogue_match(
            recipe_brand=brand,
            recipe_name=name,
            catalogue_brand=str(row.get("brand") or ""),
            catalogue_name=str(row.get("name") or ""),
            price_per_ml=row.get("price_per_ml"),
            reference_only=_is_reference_only_row(row),
        )
        if sc > 0:
            scored.append((sc, row))

    scored.sort(key=lambda x: (-x[0], -float(x[1].get("price_per_ml") or 0), str(x[1].get("name") or "")))

    priced = [(s, r) for s, r in scored if _price_positive(r.get("price_per_ml"))]
    candidates = [
        {
            "catalogue_id": int(r["id"]),
            "brand": str(r.get("brand") or ""),
            "name": str(r.get("name") or ""),
            "price_per_ml": float(r.get("price_per_ml") or 0),
            "label": _catalogue_label(r),
            "score": s,
        }
        for s, r in scored[:8]
    ]

    if not priced:
        return {
            "status": "missing",
            "catalogue_id": linked_id or None,
            "catalogue_brand": brand,
            "catalogue_name": name,
            "resolved_label": "",
            "price_per_ml": None,
            "confidence_score": 0,
            "candidates": candidates,
            "missing_price": True,
        }

    top_score, top_row = priced[0]
    second_score = priced[1][0] if len(priced) > 1 else 0
    price = float(top_row.get("price_per_ml") or 0)

    if top_score >= AUTO_SELECT_MIN_SCORE and (top_score - second_score) >= AMBIGUOUS_SCORE_GAP:
        status = "resolved"
    elif len(priced) == 1:
        status = "resolved"
    else:
        status = "check_match"

    return {
        "status": status,
        "catalogue_id": int(top_row["id"]),
        "catalogue_brand": str(top_row.get("brand") or ""),
        "catalogue_name": str(top_row.get("name") or ""),
        "resolved_label": _catalogue_label(top_row),
        "price_per_ml": price,
        "confidence_score": top_score,
        "candidates": candidates,
        "missing_price": False,
    }


def search_catalogue_rows(
    catalogue_rows: List[Dict[str, Any]],
    *,
    query: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return catalogue_rows[:limit]
    out: List[Dict[str, Any]] = []
    for row in catalogue_rows:
        hay = f"{row.get('brand', '')} {row.get('name', '')}".lower()
        if q in hay:
            out.append(row)
            if len(out) >= limit:
                break
    return out
