"""Canonical BLCK purchase selections — identities only, never prices."""

from __future__ import annotations

from typing import Any, Dict, List

# Stable Shopify variant IDs observed on blck.co.za (identity, not price).
# Scraper refreshes pack_price / price_per_ml for these IDs; costing never hard-codes Rand.
CANONICAL_PURCHASES: List[Dict[str, Any]] = [
    {
        "role": "vg",
        "label": "VG (Vegetable Glycerine) · 5 Litre",
        "preferred_source_variant_id": "365231013902",
        "preferred_source_product_id": "65639972878",
        "match": {
            "product_title_contains": ["VG (Vegetable Glycerine)", "Vegetable Glycerine"],
            "pack_volume_ml": 5000.0,
        },
    },
    {
        "role": "pg",
        "label": "PG (Propylene Glycol) · 5 Litre",
        "preferred_source_variant_id": "365087391758",
        "preferred_source_product_id": "65596653582",
        "match": {
            "product_title_contains": ["PG (Propylene Glycol)", "Propylene Glycol"],
            "pack_volume_ml": 5000.0,
        },
    },
    {
        "role": "freebase_nic",
        "label": "Gold Nic · 100mg/ml PG · 1 Litre",
        "preferred_source_variant_id": "188698296334",
        "preferred_source_product_id": "10698750222",
        "match": {
            "product_title_contains": ["Gold Nic"],
            "pack_volume_ml": 1000.0,
            "nicotine_strength_mg_ml": 100.0,
            "carrier": "PG",
            "nicotine_type": "freebase",
        },
    },
    {
        "role": "salt_nic",
        "label": "AuNic Salt · 100mg/ml VG · 1 Litre",
        "preferred_source_variant_id": "41133687078973",
        "preferred_source_product_id": "7264583614525",
        "match": {
            "product_title_contains": ["AuNic - Salt Nicotine", "Salt Nicotine"],
            "pack_volume_ml": 1000.0,
            "nicotine_strength_mg_ml": 100.0,
            "carrier": "VG",
            "nicotine_type": "salt",
        },
    },
]


def purchases_by_role() -> Dict[str, Dict[str, Any]]:
    return {str(p["role"]): p for p in CANONICAL_PURCHASES}
