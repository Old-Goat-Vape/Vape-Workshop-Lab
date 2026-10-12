from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from config.canonical_library_brands import canonical_library_brand
from scrapers.safe_print import safe_print


BASE_URL = "https://clyrolinx.co.za/"
BRAND = "Clyrolinx"
SOURCE = "clyrolinx_scraper"
STORE_API = "https://clyrolinx.co.za/wp-json/wc/store/products"


@dataclass(frozen=True)
class ScrapeFlavor:
    brand: str
    name: str
    category: str
    usage_notes: str
    product_url: str
    price_raw: float
    unit_size_ml: Optional[float]


def normalize_name(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"\(.*?\)", " ", s)  # drop bracketed notes
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _norm(text: str) -> str:
    """Light normalization for keyword scanning."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _parse_price(text: str) -> float:
    """Parse 'R120.00' -> 120.00"""
    t = (text or "").strip()
    # Keep digits, dot, comma.
    t = re.sub(r"[^0-9.,]", "", t)
    if not t:
        return 0.0
    # If comma used as thousands separator, drop it.
    if t.count(",") and t.count("."):
        t = t.replace(",", "")
    # If comma used as decimal separator, normalize.
    elif t.count(",") and not t.count("."):
        t = t.replace(",", ".")
    try:
        return float(t)
    except Exception:
        return 0.0


def _parse_unit_size_ml(text: str) -> Optional[float]:
    """Try to parse a unit size like '10ml', '30 ml', '0.1L'."""
    t = (text or "").strip().lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ml|l)\b", t)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2)
    return num * 1000.0 if unit == "l" else num


def _robots_ok(url: str, user_agent: str = "*") -> bool:
    try:
        rp = RobotFileParser()
        rp.set_url(urljoin(BASE_URL, "/robots.txt"))
        rp.read()
        return rp.can_fetch(user_agent, url)
    except Exception:
        # Fail open but still polite with delay.
        return True


def fetch_page(url: str, *, session: requests.Session, delay_s: float = 10.0) -> Optional[str]:
    if not _robots_ok(url):
        safe_print(f"[Clyrolinx] robots.txt disallows: {url}")
        return None

    time.sleep(max(0.0, float(delay_s)))
    headers = {"User-Agent": "OldGoatLabScraper/1.0 (+https://clyrolinx.co.za/)"}
    try:
        res = session.get(url, headers=headers, timeout=20)
        if res.status_code >= 300:
            safe_print(f"[Clyrolinx] HTTP {res.status_code} for {url}")
            return None
        return res.text
    except Exception as exc:
        safe_print(f"[Clyrolinx] Request failed for {url}: {exc}")
        return None


def fetch_store_api_page(
    *,
    session: requests.Session,
    page: int,
    per_page: int = 100,
    delay_s: float = 10.0,
) -> List[Dict[str, Any]]:
    """Fetch a page of products from WooCommerce Store API (public)."""
    url = f"{STORE_API}?per_page={int(per_page)}&page={int(page)}"
    if not _robots_ok(url):
        safe_print(f"[Clyrolinx] robots.txt disallows: {url}")
        return []

    time.sleep(max(0.0, float(delay_s)))
    headers = {"User-Agent": "OldGoatLabScraper/1.0 (+https://clyrolinx.co.za/)"}
    try:
        res = session.get(url, headers=headers, timeout=30)
        if res.status_code >= 300:
            safe_print(f"[Clyrolinx] HTTP {res.status_code} for {url}")
            return []
        data = res.json()
        return data if isinstance(data, list) else []
    except Exception as exc:
        safe_print(f"[Clyrolinx] Store API request failed: {exc}")
        return []


def _classify_category_from_slugs(slugs: List[str], name: str) -> str:
    blob = " ".join([s.lower() for s in slugs] + [(_norm(name) or "")])
    if "nicotine" in blob or "clyronic" in blob or "nic" in blob:
        return "Nicotine"
    if "vg" in blob or "pg" in blob or "vegetable" in blob or "propylene" in blob:
        return "Base"
    if "bottles" in blob or "syringes" in blob or "bottle" in blob:
        return "Hardware"
    return "Flavor"


def parse_flavor_data(product: Dict[str, Any]) -> Optional[ScrapeFlavor]:
    name = (product.get("name") or "").strip()
    if not name:
        return None

    permalink = (product.get("permalink") or "").strip()
    if not permalink:
        return None

    categories = product.get("categories") or []
    slugs = [c.get("slug", "") for c in categories if isinstance(c, dict)]
    cat = _classify_category_from_slugs(slugs, name)

    # Skip perfumes/fragrances entirely (not relevant to lab flavors)
    name_l = name.lower()
    if "perfume" in name_l or "fragrance" in name_l:
        return None
    if any("perfume" in (s or "").lower() for s in slugs) or any(
        "perfume" in (c.get("name", "").lower()) or "fragrance" in (c.get("name", "").lower())
        for c in categories
        if isinstance(c, dict)
    ):
        return None

    usage_notes = ""
    short = (product.get("short_description") or "").strip()
    if short:
        # Strip HTML tags quickly via BeautifulSoup.
        usage_notes = BeautifulSoup(short, "html.parser").get_text(" ", strip=True)
    usage_notes = usage_notes[:1000]

    prices = product.get("prices") or {}
    price_minor = str(prices.get("price") or "").strip()
    # price is in minor units; currency_minor_unit indicates decimal places.
    minor_unit = int(prices.get("currency_minor_unit") or 2)
    try:
        price_raw = float(price_minor) / (10 ** minor_unit) if price_minor else 0.0
    except Exception:
        price_raw = 0.0

    unit_size_ml = _parse_unit_size_ml(name)

    return ScrapeFlavor(
        brand=BRAND,
        name=name,
        category=cat,
        usage_notes=usage_notes,
        product_url=permalink,
        price_raw=price_raw,
        unit_size_ml=unit_size_ml,
    )


def _iter_listing_pages() -> Iterable[str]:
    # WooCommerce default shop + pagination: /shop/page/2/
    # We crawl from /shop/ and follow /page/N/ until empty.
    yield urljoin(BASE_URL, "/shop/")


def upsert_flavor(
    db,
    flavor: ScrapeFlavor,
    *,
    existing_by_norm: Dict[str, Dict[str, Any]],
    now: str,
) -> str:
    """Insert new or update prices on existing. Returns 'added'|'updated'|'skipped'."""
    norm = normalize_name(flavor.name)
    price_raw = float(flavor.price_raw or 0.0)
    unit_size_ml = float(flavor.unit_size_ml) if flavor.unit_size_ml is not None else None
    price_per_ml = (price_raw / unit_size_ml) if unit_size_ml else 0.0
    canon_brand = canonical_library_brand(str(flavor.brand or ""), str(flavor.name or ""))

    existing = existing_by_norm.get(norm)
    if existing:
        old_ppm = float(existing.get("price_per_ml") or 0.0)
        old_raw = float(existing.get("price_raw") or 0.0)
        changed = (
            abs(old_ppm - price_per_ml) > 0.0001
            or abs(old_raw - price_raw) > 0.0001
            or str(existing.get("product_url") or "") != str(flavor.product_url or "")
            or str(existing.get("url") or "") != str(flavor.product_url or "")
        )
        if not changed:
            db.execute(
                """
                UPDATE flavor_reference
                SET last_scraped = ?, active = 1, source = ?
                WHERE id = ?
                """,
                (now, SOURCE, int(existing["id"])),
            )
            existing["last_scraped"] = now
            return "skipped"
        db.execute(
            """
            UPDATE flavor_reference
            SET brand = ?,
                category = ?,
                usage_notes = COALESCE(NULLIF(?, ''), usage_notes),
                url = ?,
                product_url = ?,
                price_raw = ?,
                unit_size_ml = ?,
                price_per_ml = ?,
                last_scraped = ?,
                active = 1,
                source = ?
            WHERE id = ?
            """,
            (
                canon_brand,
                flavor.category,
                flavor.usage_notes,
                flavor.product_url,
                flavor.product_url,
                price_raw,
                unit_size_ml,
                price_per_ml,
                now,
                SOURCE,
                int(existing["id"]),
            ),
        )
        existing.update(
            {
                "brand": canon_brand,
                "price_raw": price_raw,
                "unit_size_ml": unit_size_ml,
                "price_per_ml": price_per_ml,
                "url": flavor.product_url,
                "product_url": flavor.product_url,
                "last_scraped": now,
            }
        )
        return "updated"

    db.execute(
        """
        INSERT INTO flavor_reference (
            brand, name, source, category,
            usage_notes, url, product_url,
            price_raw, unit_size_ml,
            price_per_ml,
            last_scraped, active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            canon_brand,
            flavor.name,
            SOURCE,
            flavor.category,
            flavor.usage_notes,
            flavor.product_url,
            flavor.product_url,
            price_raw,
            unit_size_ml,
            price_per_ml,
            now,
        ),
    )
    row_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    existing_by_norm[norm] = {
        "id": int(row_id),
        "brand": canon_brand,
        "name": flavor.name,
        "price_raw": price_raw,
        "unit_size_ml": unit_size_ml,
        "price_per_ml": price_per_ml,
        "url": flavor.product_url,
        "product_url": flavor.product_url,
        "last_scraped": now,
    }
    return "added"


def run_clyrolinx_scraper(
    db, *, max_pages: int = 200, delay_s: float = 10.0
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Scrape Clyrolinx and upsert prices into flavor_reference.

    Returns (new_flavors_added_details, skipped_unchanged_count, updated_count)
    """
    added: List[Dict[str, Any]] = []
    skipped = 0
    updated = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows = db.execute(
        """
        SELECT id, brand, name, price_raw, unit_size_ml, price_per_ml, url, product_url, last_scraped
        FROM flavor_reference
        WHERE source = ? OR brand = ?
        """,
        (SOURCE, BRAND),
    ).fetchall()
    existing_by_norm: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        d = dict(r) if not isinstance(r, dict) else r
        existing_by_norm[normalize_name(str(d.get("name") or ""))] = d

    sess = requests.Session()

    safe_print("[Clyrolinx] Syncing catalog (polite mode)...")

    # Use WooCommerce Store API (public) instead of HTML, because /shop/ is JS-driven.
    per_page = 100
    for page in range(1, max_pages + 1):
        products = fetch_store_api_page(session=sess, page=page, per_page=per_page, delay_s=delay_s)
        if not products:
            break
        safe_print(f"[Clyrolinx] API page {page}: {len(products)} products")

        for p in products:
            flav = parse_flavor_data(p)
            if not flav:
                continue
            try:
                outcome = upsert_flavor(db, flav, existing_by_norm=existing_by_norm, now=now)
                if outcome == "added":
                    added.append(
                        {
                            "brand": flav.brand,
                            "name": flav.name,
                            "category": flav.category,
                            "product_url": flav.product_url,
                            "price_raw": flav.price_raw,
                            "unit_size_ml": flav.unit_size_ml,
                        }
                    )
                elif outcome == "updated":
                    updated += 1
                else:
                    skipped += 1
            except Exception as exc:
                safe_print(f"[Clyrolinx] DB write failed for {flav.name}: {exc}")
                raise

        db.commit()

    safe_print(f"[Clyrolinx] Done. Added: {len(added)}, Updated: {updated}, Unchanged: {skipped}")
    return added, skipped, updated


# Back-compat alias used by older call sites / tests.
def insert_or_skip(db, flavor: ScrapeFlavor, *, existing_norm_names: set[str], now: str) -> bool:
    """Legacy insert-only helper. Prefer upsert_flavor."""
    norm = normalize_name(flavor.name)
    if norm in existing_norm_names:
        return False
    outcome = upsert_flavor(db, flavor, existing_by_norm={}, now=now)
    if outcome == "added":
        existing_norm_names.add(norm)
        return True
    return False

