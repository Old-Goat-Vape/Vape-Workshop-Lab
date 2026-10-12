from __future__ import annotations

from datetime import datetime
from functools import wraps
from typing import Any, Callable, Dict, List, Tuple

from db import get_db
from models import inventory_repo
from services import flavor_recipe_analytics_service as recipe_analytics
from utils.auth import ROLE_HIERARCHY

# Flask-only helpers used by rank_required
from flask import flash, redirect, session, url_for


ALLOWED_QUICK_ADD_ROLES = {"Old Goat", "Foreman", "Master"}
DEFAULT_DENSITY_G_PER_ML = 1.036


def quick_add_reference_to_inventory(
    *,
    reference_id: int | None,
    actor_role: str | None,
) -> Tuple[Dict[str, Any], int]:
    """Add a `flavor_reference` item to `library_items` if not present."""
    if actor_role not in ALLOWED_QUICK_ADD_ROLES:
        return {"success": False, "message": "Unauthorized"}, 403

    if not reference_id:
        return {"success": False, "message": "Reference ID missing"}, 400

    ref = inventory_repo.fetch_reference_basic(int(reference_id))
    if not ref:
        return {"success": False, "message": "Master reference item not found"}, 404

    existing_id = inventory_repo.find_active_library_item_by_reference(int(reference_id))
    if existing_id:
        return {"success": False, "message": "Already in your Lab inventory."}, 400

    try:
        inventory_repo.insert_library_item(
            reference_id=int(reference_id),
            current_stock=0.0,
            unit="ml",
            cost_per_unit=float(ref.get("price_per_ml") or 0.0),
        )
        get_db().commit()
        return {"success": True}, 200
    except Exception as exc:
        get_db().rollback()
        return {"success": False, "message": str(exc)}, 500


def add_from_reference_to_inventory(
    *,
    reference_id: int,
    initial_stock_ml: float = 10.0,
    low_stock_threshold_ml: float = 2.0,
) -> Tuple[Dict[str, Any], int]:
    """Create a physical stock entry in `library_items` for a reference flavor."""
    ref = inventory_repo.fetch_reference_basic(int(reference_id))
    if not ref:
        return {"status": "error", "message": "Reference flavor not found."}, 404

    existing_id = inventory_repo.find_active_library_item_by_reference(int(reference_id))
    if existing_id:
        return {
            "status": "error",
            "message": f"{ref['brand']} {ref['name']} is already in Lab Stock.",
        }, 400

    try:
        inventory_repo.insert_library_item(
            reference_id=int(reference_id),
            current_stock=float(initial_stock_ml),
            unit="ml",
            low_stock_threshold=float(low_stock_threshold_ml),
            status="In Use",
        )
        get_db().commit()
        return {
            "status": "success",
            "message": f"Success: {ref['brand']} {ref['name']} added to Lab Stock ({initial_stock_ml:.0f}ml).",
        }, 200
    except Exception as exc:
        get_db().rollback()
        return {"status": "error", "message": str(exc)}, 500


def low_stock_status(*, threshold_ml: float = 5.0) -> List[Dict[str, Any]]:
    """Return low stock items currently in use."""
    return [
        {
            "name": r["name"],
            "brand": r["brand"],
            "remaining": round(float(r.get("current_stock") or 0), 2),
        }
        for r in inventory_repo.select_low_stock(threshold_ml=float(threshold_ml))
    ]


# ------------------------------------------------------------------
# Classification + ranking + audit logging (extracted from library_main)
# ------------------------------------------------------------------

def classify_and_parse(name: str, brand: str = "") -> Dict[str, Any]:
    import re

    name_clean = (name or "").lower().strip()
    brand_clean = (brand or "").lower().strip() if brand else ""

    result: Dict[str, Any] = {
        "category": "Hardware",
        "unit": "unit",
        "volume": 1.0,
        "subtype": "",
        "nic_strength": 0.0,
    }

    liquid_markers = ["ml", "litre", "liter", "oz"]
    has_volume_marker = any(m in name_clean for m in liquid_markers)

    hardware_keywords = [
        "card",
        "coil",
        "battery",
        "stand",
        "tank",
        "wire",
        "tweezer",
        "glass",
        "cotton",
        "scale",
        "beaker",
        "bottle",
        "swab",
        "crystals",
        "disposable",
        "pod",
        "puff",
        "hoodie",
        "bat",
        "cricket",
        "set",
        "bag",
        "needle",
        "pouch",
        "shirt",
        "cap",
        "merch",
        "pump",
        "desk",
        "mat",
        "keyring",
        "gloves",
        "lanyard",
        "nozzle",
        "funnel",
        "strainer",
        "syringe",
        "band",
        "customization",
    ]

    protected_flavors = [
        "cardamom",
        "custard",
        "tobacco",
        "menthol",
        "ws-23",
        "ws23",
        "additive",
        "sweetener",
        "concentrate",
        "concentate",
    ]
    is_flavor_protected = any(p in name_clean for p in protected_flavors)

    if any(h in name_clean for h in hardware_keywords):
        if "recipe card" in name_clean or "cotton candy" not in name_clean:
            if not ("crystals" in name_clean and is_flavor_protected):
                return result

    nic_found = any(re.search(m, name_clean) for m in [r"\bnic\b", r"\bnicotine\b", r"\baunic\b", r"\b\d+mg\b"])
    salt_found = re.search(r"\bsalt\b(?![ey])", name_clean)
    if (nic_found or salt_found) and not any(food in name_clean for food in ["salted", "caramel", "taffy", "candy"]):
        result["category"] = "Nicotine"
        result["unit"] = "ml"
        result["volume"] = 10.0
        result["subtype"] = "Salt" if salt_found else "Freebase"
        str_match = re.search(r"(\d+)mg", name_clean)
        if str_match:
            result["nic_strength"] = float(str_match.group(1))
        vol_match = re.search(r"(\d+)\s*(ml|litre|l)", name_clean)
        if vol_match:
            result["volume"] = float(vol_match.group(1))
        return result

    base_markers = ["pg", "vg", "peg", "propylene glycol", "vegetable glycerine", "hybrid base"]
    if any(m in name_clean for m in base_markers):
        result["category"] = "Base"
        result["unit"] = "ml"
        result["volume"] = 1000.0
        result["subtype"] = "Hybrid" if "hybrid" in name_clean else ("PG" if "pg" in name_clean else "VG")
        vol_match = re.search(r"(\d+)\s*(ml|litre|l)", name_clean)
        if vol_match:
            result["volume"] = float(vol_match.group(1))
        return result

    flavor_markers = [
        "concentrate",
        "concentate",
        "additive",
        "enhancer",
        "sweetener",
        "flavorant",
        "erythritol",
        "ws-23",
        "menthol",
        "coolant",
    ]
    flavor_brands = [
        "capella",
        "cap",
        "tfa",
        "tpa",
        "the flavor apprentice",
        "fa",
        "flavourart",
        "fw",
        "flavor west",
        "inw",
        "inawera",
        "wf",
        "wonder flavours",
        "fe",
        "flavors express",
        "jf",
        "jungle flavors",
        "la",
        "lorann",
        "mol",
        "molinberry",
        "ooo",
        "one on one",
        "pur",
        "purilum",
        "rf",
        "real flavors",
        "vt",
        "vape train",
        "ssa",
        "sobucky",
        "blck",
    ]

    is_flavor_brand = brand_clean in flavor_brands or any(f" {b} " in f" {brand_clean} " for b in flavor_brands)
    if is_flavor_brand or any(m in name_clean for m in flavor_markers) or "unicorn" in name_clean or is_flavor_protected or has_volume_marker:
        result["category"] = "Flavor"
        result["unit"] = "ml"
        result["volume"] = 10.0
        if any(m in name_clean for m in ["sweetener", "enhancer", "additive", "ws-23", "menthol", "crystals"]):
            result["subtype"] = "Additive"
        vol_match = re.search(r"(\d+)\s*(ml|litre|l)", name_clean)
        if vol_match:
            val = float(vol_match.group(1))
            if vol_match.group(2).startswith("l"):
                val *= 1000
            result["volume"] = val
        return result

    return result


def effective_user_level(*, username: str | None, role: str | None) -> int:
    username_lower = (username or "").lower()
    if username_lower == "old goat":
        # Old Goat is absolute top authority.
        return 999_999
    return ROLE_HIERARCHY.get(role or "Kid", 0)


def rank_required(min_level: int):
    """Route decorator enforcing rank. Extracted from `routes/library_main.py`."""

    def decorator(f: Callable[..., Any]):
        @wraps(f)
        def decorated_function(*args, **kwargs):  # type: ignore[no-untyped-def]
            lvl = effective_user_level(username=session.get("username"), role=session.get("role"))
            if lvl < min_level:
                flash("Insufficient rank for this action.", "danger")
                return redirect(url_for("library.index"))
            return f(*args, **kwargs)

        return decorated_function

    return decorator


def log_change(
    *,
    actor_id: int | None,
    action: str,
    target_id: int,
    target_table: str = "library_items",
    details: str = "",
    qty_change: float = 0.0,
) -> None:
    # Keeping this in service for now; can be moved to an audit_repo later.
    db = get_db()
    db.execute(
        """
        INSERT INTO audit_logs (actor_id, action, target_table, target_id, details, qty_change, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (actor_id, action, target_table, target_id, details, qty_change, datetime.now()),
    )


# ------------------------------------------------------------------
# Stock / library operations (extracted from library_main routes)
# ------------------------------------------------------------------

def build_library_index_view(
    *,
    view_type: str,
    current_cat: str,
    search_query: str,
    user_level: int,
    is_old_goat: bool,
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    include_nicotine = user_level >= 3
    rows, total_count = inventory_repo.select_library_index_rows(
        view_type=view_type,
        current_cat=current_cat,
        search_query=search_query,
        include_nicotine=include_nicotine,
        page=page,
        page_size=page_size,
    )
    for item in rows:
        if not is_old_goat:
            item.pop("cost", None)
            item.pop("cost_per_g", None)
            item.pop("purchase_price", None)
            item.pop("supplier", None)

        intel = classify_and_parse(item.get("name", ""), item.get("brand", ""))
        item["ui_amount"] = intel["volume"] if view_type == "reference" else item.get("current_stock", 0)
        items.append(item)

    return {
        "items": items,
        "view_type": view_type,
        "current_cat": current_cat,
        "search": search_query,
        "user_level": user_level,
        "page": max(1, int(page or 1)),
        "page_size": max(1, int(page_size or 50)),
        "total_count": int(total_count),
        "total_pages": max(1, ((int(total_count) - 1) // max(1, int(page_size or 50))) + 1) if total_count else 1,
    }


def get_flavor_detail_view(*, reference_id: int, view_type: str) -> Tuple[Dict[str, Any], int]:
    include_inventory = (view_type or "").strip().lower() == "inventory"
    row = inventory_repo.select_flavor_detail(reference_id=int(reference_id), include_inventory=include_inventory)
    if not row:
        return {"status": "error", "message": "Flavor not found."}, 404
    item = dict(row)
    item["recipe_analytics"] = recipe_analytics.get_detail_attachment(int(reference_id))
    return {"status": "success", "item": item}, 200


def reclassify_all_references(*, actor_id: int | None) -> Tuple[Dict[str, Any], int]:
    try:
        updated_count = 0
        for item in inventory_repo.select_active_references_for_reclassify():
            intel = classify_and_parse(item["name"], item["brand"])
            new_cat = intel["category"]
            if new_cat != item["category"]:
                inventory_repo.update_reference_category(int(item["id"]), new_cat)
                updated_count += 1
        log_change(
            actor_id=actor_id,
            action="MASS_RECLASSIFY",
            target_id=0,
            details=f"Cleaned up {updated_count} items.",
        )
        get_db().commit()
        return {"status": "success", "updated_count": updated_count}, 200
    except Exception as exc:
        get_db().rollback()
        return {"status": "error", "message": str(exc)}, 500


def adjust_stock_with_reason(
    *,
    actor_id: int | None,
    actor_username: str | None,
    actor_role: str | None,
    item_id: int,
    qty_change: float,
    reason: str,
) -> Tuple[Dict[str, Any], int]:
    if not reason:
        return {"status": "error", "message": "Reason required for manual adjustment."}, 400

    item = inventory_repo.fetch_library_item_with_category(int(item_id))
    if not item:
        return {"status": "error", "message": "Item not found."}, 404

    user_level = effective_user_level(username=actor_username, role=actor_role)
    if item["category"] == "Nicotine" and user_level < 4:
        return {"status": "error", "message": "Nicotine adjustments require Ram level or higher."}, 403

    new_stock = float(item["current_stock"] or 0) + float(qty_change)
    if new_stock < 0 and (actor_username or "").lower() != "old goat":
        return {"status": "error", "message": "Only the Old Goat can authorize negative stock."}, 403

    inventory_repo.update_library_stock_set(item_id=int(item_id), new_stock=new_stock, unit="ml")
    log_change(
        actor_id=actor_id,
        action="STOCK_ADJUST",
        target_id=item_id,
        details=f"Reason: {reason}",
        qty_change=qty_change,
    )
    get_db().commit()
    return {"status": "success", "new_stock": new_stock}, 200


def manual_add_stock(
    *,
    actor_id: int | None,
    brand: str,
    name: str,
    amount: float,
    unit: str,
) -> Tuple[Dict[str, Any], int]:
    intel = classify_and_parse(name, brand)
    category = intel["category"]

    ref_id = inventory_repo.fetch_reference_id_by_brand_name(brand=brand, name=name)
    if ref_id:
        inventory_repo.update_reference_category(ref_id, category)
    else:
        ref_id = inventory_repo.insert_reference(brand=brand, name=name, category=category)

    existing_item_id = inventory_repo.find_active_library_item_by_reference(ref_id)
    if existing_item_id:
        inventory_repo.update_library_stock_delta_ml(reference_id=ref_id, delta_ml=float(amount))
        item_id = existing_item_id
    else:
        item_id = inventory_repo.insert_library_item(reference_id=ref_id, current_stock=float(amount), unit=unit)

    log_change(actor_id=actor_id, action="CREATE_ITEM", target_id=item_id, details=f"Initial Add: {brand} {name}")
    get_db().commit()
    return {"status": "success", "category": category, "item_id": item_id}, 200


def quick_add_stock(
    *,
    actor_id: int | None,
    ref_id: int,
    size: str | None,
) -> Tuple[Dict[str, Any], int]:
    ref = inventory_repo.fetch_reference_basic(int(ref_id))
    if not ref:
        return {"status": "error", "message": "Reference not found."}, 404

    intel = classify_and_parse(ref["name"], ref["brand"])
    user_size = (size or "").strip()
    amount = float(user_size) if user_size and float(user_size) > 0 else float(intel["volume"])

    existing_item_id = inventory_repo.find_active_library_item_by_reference(int(ref_id))
    if existing_item_id:
        inventory_repo.update_library_stock_delta_ml(reference_id=int(ref_id), delta_ml=float(amount))
        item_id = existing_item_id
    else:
        item_id = inventory_repo.insert_library_item(reference_id=int(ref_id), current_stock=float(amount), unit=intel["unit"])

    log_change(actor_id=actor_id, action="QUICK_ADD", target_id=item_id, details=f"Quick add {amount}", qty_change=amount)
    get_db().commit()
    return {"status": "success", "added_amount": amount}, 200


def get_edit_item_view(*, item_id: int) -> Optional[Dict[str, Any]]:
    return inventory_repo.select_edit_item_view(int(item_id))


def update_item_from_form(
    *,
    actor_id: int | None,
    actor_username: str | None,
    item_id: int,
    name: str,
    brand: str,
    category: str,
    stock: float,
    unit: str,
    cost_per_g: str | None,
) -> Tuple[Dict[str, Any], int]:
    try:
        ref_id = inventory_repo.fetch_library_item_reference_id(int(item_id))
        if not ref_id:
            return {"status": "error", "message": "Item not found."}, 404
        inventory_repo.update_reference_fields(reference_id=ref_id, name=name, brand=brand, category=category)
        inventory_repo.update_library_item_fields(item_id=int(item_id), current_stock=float(stock), unit=unit)

        if (actor_username or "").lower() == "old goat":
            if cost_per_g:
                inventory_repo.update_library_item_cost_per_g(item_id=int(item_id), cost_per_g=float(cost_per_g))

        log_change(actor_id=actor_id, action="EDIT", target_id=item_id, details=f"Updated by {actor_username or ''}")
        get_db().commit()
        return {"status": "success", "category": category}, 200
    except Exception as exc:
        get_db().rollback()
        return {"status": "error", "message": str(exc)}, 500


def merge_library_items(
    *,
    actor_id: int | None,
    source_id: int,
    target_id: int,
) -> Tuple[Dict[str, Any], int]:
    try:
        inventory_repo.merge_items(source_id=int(source_id), target_id=int(target_id))

        log_change(actor_id=actor_id, action="MERGE", target_id=target_id, details=f"Merged with item ID {source_id}")
        get_db().commit()
        return {"status": "success"}, 200
    except Exception as exc:
        get_db().rollback()
        return {"status": "error", "message": str(exc)}, 500


def archive_library_item(*, actor_id: int | None, item_id: int) -> Tuple[Dict[str, Any], int]:
    inventory_repo.archive_item(int(item_id))
    log_change(actor_id=actor_id, action="ARCHIVE", target_id=item_id, details="Deactivated")
    get_db().commit()
    return {"status": "success"}, 200

