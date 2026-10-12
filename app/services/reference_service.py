from __future__ import annotations

from typing import Any, Dict, List

from models import inventory_repo


def search_flavor_reference(query: str, *, limit: int = 250) -> List[Dict[str, Any]]:
    """Search the master catalog (`flavor_reference`) and include local in-stock status."""
    q = (query or "").strip()
    if not q:
        return []

    results: List[Dict[str, Any]] = []
    for r in inventory_repo.search_reference_with_in_stock(query=q, limit=int(limit)):
        results.append(
            {
                "id": r["id"],
                "name": r["name"],
                "brand": r["brand"],
                "category": r.get("display_category") or "Flavor",
                "avg_usage": r.get("avg_usage") or 0,  # catalog avg usage (reference only)
                "active_avg_usage": r.get("active_avg_usage") or 1.0,
                "recipe_avg_usage": r.get("recipe_avg_usage"),
                "price_10ml": r.get("price_10ml") or 0,
                "price_per_ml": r.get("price_per_ml") or 0,
                "url": r.get("url") or "#",
                "in_stock": r.get("in_stock_id") is not None,
                "in_stock_id": r.get("in_stock_id"),
            }
        )

    return results

