"""BLCK Shopify scraper — product + variant-aware pricing."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from config.canonical_library_brands import canonical_library_brand
from db import sqlite_retry
from scrapers.blck_variant_normalize import normalize_variant_fields
from scrapers.safe_print import safe_print


def _upsert_variant(db, *, product: Dict[str, Any], variant: Dict[str, Any], now: str) -> Dict[str, Any]:
    product_id = str(product.get("id") or "")
    variant_id = str(variant.get("id") or "")
    title = str(product.get("title") or "")
    vendor = str(product.get("vendor") or "")
    handle = str(product.get("handle") or "")
    product_type = str(product.get("product_type") or "")
    variant_title = str(variant.get("title") or "")
    option1 = str(variant.get("option1") or "")
    option2 = str(variant.get("option2") or "")
    option3 = str(variant.get("option3") or "")
    sku = str(variant.get("sku") or "") or None
    available = 1 if variant.get("available", True) else 0
    try:
        pack_price = float(variant.get("price") or 0.0)
    except (TypeError, ValueError):
        pack_price = 0.0

    fields = normalize_variant_fields(
        product_title=title,
        vendor=vendor,
        variant_title=variant_title,
        option1=option1,
        option2=option2,
        option3=option3,
    )
    volume = fields.get("pack_volume_ml")
    price_per_ml = (pack_price / float(volume)) if volume and float(volume) > 0 else 0.0
    product_url = f"https://blck.co.za/products/{handle}" if handle else None

    existing = db.execute(
        """
        SELECT pack_price, price_per_ml FROM blck_product_variants
        WHERE source = 'BLCK' AND source_variant_id = ?
        """,
        (variant_id,),
    ).fetchone()

    db.execute(
        """
        INSERT INTO blck_product_variants (
            source, source_product_id, source_variant_id,
            product_title, variant_title, handle, vendor, product_type,
            option1, option2, option3, sku, available,
            pack_volume_ml, nicotine_strength_mg_ml, carrier, nicotine_type,
            pack_price, price_per_ml, product_url, last_scraped, updated_at
        ) VALUES (
            'BLCK', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(source, source_variant_id) DO UPDATE SET
            source_product_id = excluded.source_product_id,
            product_title = excluded.product_title,
            variant_title = excluded.variant_title,
            handle = excluded.handle,
            vendor = excluded.vendor,
            product_type = excluded.product_type,
            option1 = excluded.option1,
            option2 = excluded.option2,
            option3 = excluded.option3,
            sku = excluded.sku,
            available = excluded.available,
            pack_volume_ml = excluded.pack_volume_ml,
            nicotine_strength_mg_ml = excluded.nicotine_strength_mg_ml,
            carrier = excluded.carrier,
            nicotine_type = excluded.nicotine_type,
            pack_price = excluded.pack_price,
            price_per_ml = excluded.price_per_ml,
            product_url = excluded.product_url,
            last_scraped = excluded.last_scraped,
            updated_at = excluded.updated_at
        """,
        (
            product_id,
            variant_id,
            title,
            variant_title,
            handle,
            vendor,
            product_type,
            option1 or None,
            option2 or None,
            option3 or None,
            sku,
            available,
            volume,
            fields.get("nicotine_strength_mg_ml"),
            fields.get("carrier"),
            fields.get("nicotine_type"),
            pack_price,
            price_per_ml,
            product_url,
            now,
            now,
        ),
    )

    price_changed = True
    if existing is not None:
        try:
            price_changed = abs(float(existing["pack_price"] or 0) - pack_price) > 0.0001
        except Exception:
            price_changed = True
    if price_changed:
        db.execute(
            """
            INSERT INTO blck_variant_price_history
                (source, source_variant_id, pack_price, price_per_ml, available, recorded_at)
            VALUES ('BLCK', ?, ?, ?, ?, ?)
            """,
            (variant_id, pack_price, price_per_ml, available, now),
        )

    return {
        "source_variant_id": variant_id,
        "pack_price": pack_price,
        "price_per_ml": price_per_ml,
        "pack_volume_ml": volume,
        "price_changed": price_changed,
    }


def _upsert_flavour_reference_from_primary_variant(
    db,
    *,
    product: Dict[str, Any],
    category: str,
    now: str,
    added: List[str],
    updated: List[str],
) -> None:
    """Keep legacy flavour_reference catalogue for concentrates (first volumetric variant preferred)."""
    variants = product.get("variants") or []
    if not variants:
        return
    title = str(product.get("title") or "")
    vendor = str(product.get("vendor") or "Unknown")
    handle = str(product.get("handle") or "")
    title_lower = title.lower()

    # Prefer a variant with explicit volume; else first variant.
    chosen = None
    for v in variants:
        fields = normalize_variant_fields(
            product_title=title,
            vendor=vendor,
            variant_title=str(v.get("title") or ""),
            option1=str(v.get("option1") or ""),
            option2=str(v.get("option2") or ""),
            option3=str(v.get("option3") or ""),
        )
        if fields.get("pack_volume_ml"):
            chosen = (v, fields)
            break
    if chosen is None:
        v0 = variants[0]
        fields = normalize_variant_fields(
            product_title=title,
            vendor=vendor,
            variant_title=str(v0.get("title") or ""),
            option1=str(v0.get("option1") or ""),
            option2=str(v0.get("option2") or ""),
            option3=str(v0.get("option3") or ""),
        )
        chosen = (v0, fields)

    variant, fields = chosen
    try:
        pack_price = float(variant.get("price") or 0.0)
    except (TypeError, ValueError):
        pack_price = 0.0
    volume = float(fields.get("pack_volume_ml") or 10.0)
    # Title-level volume fallback (legacy behaviour for concentrates without size options)
    if not fields.get("pack_volume_ml"):
        vol_match = re.search(r"(\d+)\s*(ml|l)\b", title_lower)
        if vol_match:
            num = float(vol_match.group(1))
            unit = vol_match.group(2)
            volume = num * 1000 if unit == "l" else num
    price_per_ml = pack_price / volume if volume > 0 else 0.0
    full_url = f"https://blck.co.za/products/{handle}"
    clean_name = title.replace(vendor, "").strip() or title
    canon_brand = canonical_library_brand(str(vendor or ""), str(clean_name or ""))

    existing = db.execute(
        "SELECT id, price_per_ml FROM flavor_reference WHERE brand = ? AND name = ?",
        (canon_brand, clean_name),
    ).fetchone()
    if existing:
        if abs(float(existing["price_per_ml"] or 0) - price_per_ml) > 0.01:
            db.execute(
                """
                INSERT INTO audit_logs (actor_id, action, target_table, target_id, details)
                VALUES (NULL, 'PRICE_UPDATE', 'flavor_reference', ?, ?)
                """,
                (
                    existing["id"],
                    f"Price Shift: R{float(existing['price_per_ml'] or 0):.4f} -> R{price_per_ml:.4f}/ml",
                ),
            )
        db.execute(
            """
            UPDATE flavor_reference
            SET category=?, price_per_ml=?, url=?, last_scraped=?, active=1,
                price_raw=?, unit_size_ml=?
            WHERE id=?
            """,
            (category, price_per_ml, full_url, now, pack_price, volume, existing["id"]),
        )
        updated.append(clean_name)
    else:
        db.execute(
            """
            INSERT INTO flavor_reference
                (brand, name, source, category, price_per_ml, url, last_scraped, active, price_raw, unit_size_ml)
            VALUES (?, ?, 'BLCK', ?, ?, ?, ?, 1, ?, ?)
            """,
            (canon_brand, clean_name, category, price_per_ml, full_url, now, pack_price, volume),
        )
        added.append(clean_name)


def scrape_vc_flavors(db, limit_pages=30):
    db.row_factory = sqlite3.Row

    added = 0
    updated = 0
    variants_upserted = 0
    variants_price_changed = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Legacy manual rows remain in DB for library history but are no longer
    # re-injected with hard-coded Rand prices (canonical costing uses Shopify variants).
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    safe_print("[BLCK] Syncing catalog + variants...")

    for page in range(1, limit_pages + 1):
        try:
            url = f"https://blck.co.za/products.json?limit=250&page={page}"
            res = requests.get(url, headers=headers, timeout=20)
            products_data = res.json().get("products", [])
            if not products_data:
                break
        except Exception as e:
            safe_print(f"[BLCK] Fetch error on page {page}: {e}")
            break

        def write_page() -> None:
            nonlocal added, updated, variants_upserted, variants_price_changed
            db.execute("BEGIN IMMEDIATE")
            try:
                for p in products_data:
                    title = p.get("title", "")
                    vendor = p.get("vendor", "Unknown")
                    p_type = str(p.get("product_type", "") or "").lower()
                    title_lower = title.lower()

                    category = "Flavor"
                    hw_keys = [
                        "bottle",
                        "cap",
                        "beaker",
                        "scale",
                        "syringe",
                        "magnetic",
                        "can",
                        "jerry",
                        "pet",
                        "glass",
                    ]
                    if any(x in title_lower or x in p_type for x in hw_keys):
                        category = "Hardware"
                    elif any(x in title_lower for x in ["nicotine", "nic salt", "freebase nic", "gold nic"]):
                        category = "Nicotine"
                    elif any(x in title_lower for x in ["vegetable glycerin", "propylene glycol", "vg (", "pg ("]):
                        if "flavour" not in title_lower or "glycer" in title_lower or "glycol" in title_lower:
                            category = "Base"

                    # Persist EVERY Shopify variant (source of truth for pack pricing).
                    for v in p.get("variants") or []:
                        meta = _upsert_variant(db, product=p, variant=v, now=now)
                        variants_upserted += 1
                        if meta.get("price_changed"):
                            variants_price_changed += 1

                    # Skip hardware / finished e-liquid from concentrate catalogue rows.
                    if any(x in p_type for x in ["mod", "kit", "tank", "coil", "e-liquid", "merchandise"]):
                        continue
                    # Base/Nicotine multi-variant parents: do not collapse to a single flavor_reference price.
                    if category in {"Base", "Nicotine"}:
                        continue

                    a_names: List[str] = []
                    u_names: List[str] = []
                    _upsert_flavour_reference_from_primary_variant(
                        db, product=p, category=category, now=now, added=a_names, updated=u_names
                    )
                    added += len(a_names)
                    updated += len(u_names)

                db.commit()
            except Exception:
                db.rollback()
                raise

        try:
            sqlite_retry(write_page, attempts=12, base_delay_s=0.05)
        except Exception as e:
            safe_print(f"[BLCK] Write error on page {page}: {e}")
            break

    canonical = {}
    try:
        from services.blck_canonical_costing_service import sync_canonical_bindings

        canonical = sync_canonical_bindings(db)
    except Exception as exc:
        safe_print(f"[BLCK] Canonical binding sync failed: {exc}")

    safe_print(
        f"[BLCK] Done. flavour added={added} updated={updated} "
        f"variants_upserted={variants_upserted} price_changes={variants_price_changed}"
    )
    return {
        "added": added,
        "updated": updated,
        "variants_upserted": variants_upserted,
        "variants_price_changed": variants_price_changed,
        "canonical": canonical,
    }
