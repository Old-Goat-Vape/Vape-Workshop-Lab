from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config.canonical_library_brands import (
    canonical_library_brand,
    flavor_identity_match_key,
    preferred_flavor_storage_name,
)


VC_FLAVOURS_URL = "https://vceliquidrecipes.com/flavours"
logger = logging.getLogger(__name__)


def normalize_brand(name: str, flavor_name: str = "") -> str:
    """Storage-oriented canonical short brand (FLV, CAP, TFA, …)."""
    return canonical_library_brand(name or "", flavor_name or "")


def normalize_flavor_name(name: str) -> str:
    """Legacy helper; identity matching uses `flavor_identity_match_key` / `normalize_flavor_identity_name`."""
    s = (name or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s&/+-]", "", s)
    return s


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("%", "")
    s = re.sub(r"[^0-9.\-]", "", s)
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _strip_html(text: Any) -> str:
    if text is None:
        return ""
    s = str(text).strip()
    if "<" not in s:
        return s.strip()
    return BeautifulSoup(s, "html.parser").get_text(" ", strip=True)


def _extract_href(html_or_text: Any) -> str:
    s = str(html_or_text or "").strip()
    if not s:
        return ""
    if "<a" in s.lower():
        soup = BeautifulSoup(s, "html.parser")
        a = soup.select_one("a[href]")
        if a and a.get("href"):
            return str(a.get("href")).strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return ""


def _discover_ajax_endpoint(page_html: str, *, include_fallbacks: bool = True) -> List[Tuple[str, str]]:
    """Return candidate (method, endpoint) pairs from DataTable config."""
    out: List[Tuple[str, str]] = []
    # Typical datatable config: "ajax": {"type":"POST","url":"..."}
    patterns = [
        r"'ajax'\s*:\s*\{.*?'type'\s*:\s*'([A-Z]+)'.*?'url'\s*:\s*'([^']+)'.*?\}",
        r'"ajax"\s*:\s*\{.*?"type"\s*:\s*"([A-Z]+)".*?"url"\s*:\s*"([^"]+)".*?\}',
        r"'ajax'\s*:\s*\{.*?'url'\s*:\s*'([^']+)'.*?\}",
        r'"ajax"\s*:\s*\{.*?"url"\s*:\s*"([^"]+)".*?\}',
    ]
    for p in patterns:
        for m in re.finditer(p, page_html, flags=re.IGNORECASE | re.DOTALL):
            if len(m.groups()) == 2:
                method = (m.group(1) or "GET").upper()
                url = m.group(2)
            else:
                method = "GET"
                url = m.group(1)
            if not url:
                continue
            full = urljoin(VC_FLAVOURS_URL, url)
            pair = (method, full)
            if pair not in out:
                out.append(pair)

    # Extra endpoint discovery: generic $.ajax({url:'..', type:'POST'}) blocks.
    ajax_block = re.compile(r"\$\.ajax\(\s*\{(.*?)\}\s*\)", flags=re.IGNORECASE | re.DOTALL)
    for block in ajax_block.findall(page_html):
        url_match = re.search(r"(?:url)\s*:\s*['\"]([^'\"]+)['\"]", block, flags=re.IGNORECASE)
        type_match = re.search(r"(?:type|method)\s*:\s*['\"]([^'\"]+)['\"]", block, flags=re.IGNORECASE)
        if not url_match:
            continue
        method = (type_match.group(1) if type_match else "GET").upper()
        full = urljoin(VC_FLAVOURS_URL, url_match.group(1))
        pair = (method, full)
        if pair not in out:
            out.append(pair)

    # Generic URL literals in scripts likely tied to flavour/datatable requests.
    for m in re.finditer(r"['\"](/[^'\"]*(?:flavour|datatable|ajax|api)[^'\"]*)['\"]", page_html, flags=re.IGNORECASE):
        full = urljoin(VC_FLAVOURS_URL, m.group(1))
        for meth in ("POST", "GET"):
            pair = (meth, full)
            if pair not in out:
                out.append(pair)
    if include_fallbacks:
        # conservative fallback guesses
        fallbacks = [
            ("POST", VC_FLAVOURS_URL),
            ("GET", VC_FLAVOURS_URL),
        ]
        for f in fallbacks:
            if f not in out:
                out.append(f)
    return out


def _extract_rows_from_json(data: Any) -> Optional[List[Any]]:
    """Support multiple payload wrappers returned by DataTables-like endpoints."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return None
    for k in ("data", "aaData", "results", "rows"):
        v = data.get(k)
        if isinstance(v, list):
            return v
    return None


def _parse_html_table_rows(html: str) -> List[Dict[str, Any]]:
    """Fallback parser if rows are server-rendered in HTML."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#datatableFlavours")
    if not table:
        return []
    body_rows = table.select("tbody tr")
    out: List[Dict[str, Any]] = []
    for tr in body_rows:
        cols = tr.select("td")
        if len(cols) < 10:
            continue
        row = [c.decode_contents() for c in cols]
        parsed = parse_vc_flavour_row(row)
        if parsed:
            out.append(parsed)
    return out


def parse_vc_flavour_row(row: Any) -> Optional[Dict[str, Any]]:
    """Parse one DataTables row (list or dict) into normalized VC item."""
    name = ""
    manufacturer = ""
    flavour_url = ""
    description = ""
    rating: Optional[float] = None
    avg_usage: Optional[float] = None

    if isinstance(row, dict):
        name = _strip_html(row.get("name") or row.get("NAME") or row.get("flavour"))
        manufacturer = _strip_html(row.get("manufacturer") or row.get("MANUFACTURER") or row.get("brand"))
        flavour_url = _extract_href(row.get("url") or row.get("URL"))
        description = _strip_html(row.get("description") or row.get("DESCRIPTION"))
        rating = _to_float(row.get("rating") or row.get("RATING"))
        avg_usage = _to_float(row.get("avg flavour usage %") or row.get("avg_usage") or row.get("AVG FLAVOUR USAGE %"))
    elif isinstance(row, list):
        # Expected order per site table:
        # ID, NAME, MANUFACTURER, URL, DESCRIPTION, USED IN, USED IN RECIPE, DATE ADDED, RATING, AVG FLAVOUR USAGE %, ACTION
        name = _strip_html(row[1] if len(row) > 1 else "")
        manufacturer = _strip_html(row[2] if len(row) > 2 else "")
        flavour_url = _extract_href(row[3] if len(row) > 3 else "")
        description = _strip_html(row[4] if len(row) > 4 else "")
        rating = _to_float(row[8] if len(row) > 8 else None)
        avg_usage = _to_float(row[9] if len(row) > 9 else None)
    else:
        return None

    if not name or not manufacturer:
        return None

    return {
        "name": name.strip(),
        "brand": manufacturer.strip(),
        "vc_url": flavour_url.strip(),
        "description": description.strip(),
        "rating": rating,
        "avg_usage": avg_usage,
        "source": "VC",
    }


def fetch_vc_flavour_list(
    *, delay_s: float = 1.0, max_pages: int = 300
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """Fetch VC flavours using DataTables ajax feed."""
    errors: List[str] = []
    debug: List[str] = []
    meta: Dict[str, Any] = {"attempted_endpoints": [], "loading_method": "", "sample_row": None}
    session = requests.Session()
    headers = {
        "User-Agent": "OldGoatLabScraper/1.0",
        "Referer": VC_FLAVOURS_URL,
    }

    try:
        page = session.get(VC_FLAVOURS_URL, headers=headers, timeout=30)
        page.raise_for_status()
        html = page.text
        debug.append(f"page_fetch_ok status={page.status_code} len={len(html)} content_type={page.headers.get('Content-Type','')}")
    except Exception as exc:
        return [], [f"page_fetch_failed: {exc}"], meta

    # Debug script tag inventory.
    soup = BeautifulSoup(html, "html.parser")
    script_tags = soup.select("script")
    debug.append(f"script_tags={len(script_tags)}")
    for idx, s in enumerate(script_tags[:25], start=1):
        src = (s.get("src") or "").strip()
        if src:
            debug.append(f"script[{idx}] src={src}")

    endpoints_discovered_only = _discover_ajax_endpoint(html, include_fallbacks=False)
    endpoints = _discover_ajax_endpoint(html, include_fallbacks=True)
    if not endpoints_discovered_only:
        errors.append("endpoint_discovery_failed: no explicit ajax/datatable endpoint found in page JS")
    debug.append(f"discovered_endpoints={len(endpoints)}")
    for method, endpoint in endpoints[:50]:
        debug.append(f"endpoint {method} {endpoint}")

    all_items: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    # If inline table rows exist, parse them as fallback path.
    html_rows = _parse_html_table_rows(html)
    if html_rows:
        debug.append(f"html_fallback_rows={len(html_rows)}")
        meta["loading_method"] = "html_table_fallback"
        for parsed in html_rows:
            key = flavor_identity_match_key(parsed["brand"], parsed["name"])
            if key in seen:
                continue
            seen.add(key)
            all_items.append(parsed)

    for method, endpoint in endpoints:
        try:
            draw = 1
            found_any = False
            for page_idx in range(max_pages):
                start = page_idx * 100
                payload = {
                    "draw": draw,
                    "start": start,
                    "length": 100,
                    "search[value]": "",
                    "search[regex]": "false",
                    "getFlavours": "true",
                    "company": "all",
                    "manufacturer": "all",
                    "flavour-company-ML": "",
                    "userID": "0",
                }
                time.sleep(max(delay_s, 0.25))
                if method == "POST":
                    r = session.post(
                        endpoint,
                        data=payload,
                        headers={**headers, "X-Requested-With": "XMLHttpRequest"},
                        timeout=30,
                    )
                else:
                    r = session.get(endpoint, params=payload, headers=headers, timeout=30)
                meta["attempted_endpoints"].append(f"{method} {endpoint}")
                body_preview = (r.text or "")[:220].replace("\n", " ").replace("\r", " ")
                debug.append(
                    f"attempt endpoint={endpoint} method={method} page_idx={page_idx} "
                    f"status={r.status_code} ctype={r.headers.get('Content-Type','')} body={body_preview}"
                )
                if r.status_code >= 300:
                    errors.append(f"endpoint_http_error {method} {endpoint} status={r.status_code}")
                    break
                try:
                    data = r.json()
                except Exception:
                    # Not a JSON endpoint; stop trying this candidate.
                    errors.append(f"endpoint_non_json {method} {endpoint}")
                    break
                rows = _extract_rows_from_json(data)
                debug.append(
                    f"endpoint_rows method={method} endpoint={endpoint} page_idx={page_idx} "
                    f"row_count={(len(rows) if isinstance(rows,list) else 'none')}"
                )
                if not isinstance(rows, list) or not rows:
                    if page_idx == 0:
                        errors.append(f"endpoint_empty_data {method} {endpoint}")
                    break
                found_any = True
                for raw in rows:
                    parsed = parse_vc_flavour_row(raw)
                    if not parsed:
                        continue
                    key = flavor_identity_match_key(parsed["brand"], parsed["name"])
                    if key in seen:
                        continue
                    seen.add(key)
                    all_items.append(parsed)
                draw += 1
            if found_any:
                meta["loading_method"] = f"datatable_ajax:{method} {endpoint}"
                if all_items:
                    sample = all_items[0]
                    meta["sample_row"] = sample
                    debug.append(
                        "sample_row "
                        + f"name={sample.get('name')} brand={sample.get('brand')} "
                        + f"rating={sample.get('rating')} avg_usage={sample.get('avg_usage')} vc_url={sample.get('vc_url')}"
                    )
                logger.info("VC scraper debug: %s", " | ".join(debug[:60]))
                return all_items, errors + debug[:40], meta
        except Exception as exc:
            errors.append(f"{method} {endpoint}: {exc}")
            continue

    if not all_items:
        # Last-resort fallback: hit main flavours endpoint with DataTables params as both GET and POST.
        for meth in ("POST", "GET"):
            try:
                payload = {
                    "draw": 1,
                    "start": 0,
                    "length": 100,
                    "getFlavours": "true",
                    "company": "all",
                    "manufacturer": "all",
                    "flavour-company-ML": "",
                    "userID": "0",
                }
                r = session.post(VC_FLAVOURS_URL, data=payload, headers=headers, timeout=30) if meth == "POST" else session.get(
                    VC_FLAVOURS_URL, params=payload, headers=headers, timeout=30
                )
                debug.append(f"last_resort {meth} status={r.status_code} ctype={r.headers.get('Content-Type','')}")
                data = r.json()
                rows = _extract_rows_from_json(data)
                if isinstance(rows, list) and rows:
                    meta["loading_method"] = f"last_resort:{meth} {VC_FLAVOURS_URL}"
                    for raw in rows:
                        parsed = parse_vc_flavour_row(raw)
                        if parsed:
                            key = flavor_identity_match_key(parsed["brand"], parsed["name"])
                            if key not in seen:
                                seen.add(key)
                                all_items.append(parsed)
                    if all_items:
                        meta["sample_row"] = all_items[0]
                    if all_items:
                        break
            except Exception as exc:
                errors.append(f"last_resort_{meth}_failed: {exc}")

    if not all_items:
        if not errors:
            errors.append("endpoint_returned_empty_data: no rows in any discovered endpoint")
        errors.append("parser_failed_or_no_rows: No VC flavour rows parsed from discovered endpoints.")
    logger.info("VC scraper debug: %s", " | ".join(debug[:120]))
    return all_items, errors + debug[:60], meta


def find_existing_reference(
    db,
    *,
    brand: str,
    flavor_name: str,
    identity_index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    target_key = flavor_identity_match_key(brand, flavor_name)
    if not target_key:
        return None
    if identity_index is not None:
        return identity_index.get(target_key)

    rows = db.execute(
        """
        SELECT *
        FROM flavor_reference
        WHERE active = 1
        """,
    ).fetchall()
    for r in rows:
        d = dict(r)
        rk = flavor_identity_match_key(str(d.get("brand") or ""), str(d.get("name") or ""))
        if rk == target_key:
            return d
    return None


def build_identity_index(db) -> Dict[str, Dict[str, Any]]:
    """One-pass brand/name identity map for a scraper run (avoids O(n^2) scans)."""
    rows = db.execute(
        """
        SELECT *
        FROM flavor_reference
        WHERE active = 1
        """,
    ).fetchall()
    index: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        key = flavor_identity_match_key(str(d.get("brand") or ""), str(d.get("name") or ""))
        if key and key not in index:
            index[key] = d
    return index


def merge_vc_into_reference(db, *, existing: Dict[str, Any], vc_item: Dict[str, Any], now_ts: str) -> bool:
    """Enrichment-only merge; protects commercial/shop fields."""
    sets: List[str] = []
    vals: List[Any] = []

    # NEVER touch price fields or shop URLs here.
    avg_usage = vc_item.get("avg_usage")
    if avg_usage is not None:
        sets.append("avg_usage = ?")
        vals.append(float(avg_usage))

    vc_url = (vc_item.get("vc_url") or "").strip()
    if vc_url:
        sets.append("vc_url = ?")
        vals.append(vc_url)
        # Only fill shared url if currently blank.
        if not (existing.get("url") or "").strip():
            sets.append("url = ?")
            vals.append(vc_url)

    rating = vc_item.get("rating")
    if rating is not None:
        sets.append("rating = ?")
        vals.append(float(rating))

    desc = (vc_item.get("description") or "").strip()
    if desc:
        # Update description + usage_notes (enrichment fields only).
        sets.append("description = ?")
        vals.append(desc)
        if not (existing.get("usage_notes") or "").strip():
            sets.append("usage_notes = ?")
            vals.append(desc)

    # Keep source unless empty.
    if not (existing.get("source") or "").strip():
        sets.append("source = ?")
        vals.append("VC")

    sets.append("last_scraped = ?")
    vals.append(now_ts)

    if not sets:
        return False

    sql = f"UPDATE flavor_reference SET {', '.join(sets)} WHERE id = ?"
    vals.append(int(existing["id"]))
    db.execute(sql, vals)
    return True


def insert_new_vc_reference(db, *, vc_item: Dict[str, Any], now_ts: str) -> None:
    vc_url = (vc_item.get("vc_url") or "").strip()
    desc = (vc_item.get("description") or "").strip()
    raw_name = str(vc_item.get("name") or "")
    canon_brand = canonical_library_brand(str(vc_item.get("brand") or ""), raw_name)
    storage_name = preferred_flavor_storage_name(raw_name) or raw_name.strip()
    db.execute(
        """
        INSERT INTO flavor_reference (
            brand, name, category, source, active,
            avg_usage, description, usage_notes, rating,
            vc_url, url,
            price_per_ml, price_10ml, last_scraped
        )
        VALUES (?, ?, 'Flavor', 'VC', 1, ?, ?, ?, ?, ?, ?, 0.0, 0.0, ?)
        """,
        (
            canon_brand,
            storage_name,
            float(vc_item["avg_usage"]) if vc_item.get("avg_usage") is not None else 0.0,
            desc or None,
            desc or None,
            float(vc_item["rating"]) if vc_item.get("rating") is not None else 0.0,
            vc_url or None,
            vc_url or None,
            now_ts,
        ),
    )


def upsert_vc_reference_item(
    db,
    *,
    vc_item: Dict[str, Any],
    now_ts: str,
    identity_index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    existing = find_existing_reference(
        db,
        brand=vc_item["brand"],
        flavor_name=vc_item["name"],
        identity_index=identity_index,
    )
    if existing:
        changed = merge_vc_into_reference(db, existing=existing, vc_item=vc_item, now_ts=now_ts)
        return "enriched" if changed else "skipped"
    insert_new_vc_reference(db, vc_item=vc_item, now_ts=now_ts)
    if identity_index is not None:
        raw_name = str(vc_item.get("name") or "")
        canon_brand = canonical_library_brand(str(vc_item.get("brand") or ""), raw_name)
        storage_name = preferred_flavor_storage_name(raw_name) or raw_name.strip()
        key = flavor_identity_match_key(canon_brand, storage_name)
        if key:
            identity_index[key] = {
                "brand": canon_brand,
                "name": storage_name,
                "id": db.execute("SELECT last_insert_rowid()").fetchone()[0],
                "url": "",
                "usage_notes": "",
                "source": "VC",
            }
    return "added"


def run_vc_scraper(db, *, delay_s: float = 0.5, commit_batch: int = 200) -> Dict[str, Any]:
    """VC enrichment sync.

    Protections:
    - never overwrite commercial pricing fields
    - never replace existing shop URL with VC URL
    - only merge enrichment fields when VC has real values
    """
    added = 0
    enriched = 0
    skipped = 0
    errors = 0
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    items, fetch_errors, fetch_meta = fetch_vc_flavour_list(delay_s=delay_s)
    errors += len([e for e in fetch_errors if not str(e).startswith("page_fetch_ok") and "script[" not in str(e)])
    identity_index = build_identity_index(db)

    for idx, item in enumerate(items, 1):
        try:
            outcome = upsert_vc_reference_item(
                db, vc_item=item, now_ts=now_ts, identity_index=identity_index
            )
            if outcome == "added":
                added += 1
            elif outcome == "enriched":
                enriched += 1
            else:
                skipped += 1
            if idx % max(1, int(commit_batch)) == 0:
                db.commit()
        except Exception:
            errors += 1

    db.commit()
    return {
        "status": "success",
        "added": added,
        "enriched": enriched,
        "skipped": skipped,
        "errors": errors,
        "fetched": len(items),
        "fetch_errors": fetch_errors[:10],
        "attempted_endpoints": fetch_meta.get("attempted_endpoints", [])[:12],
        "loading_method": fetch_meta.get("loading_method", ""),
        "sample_row": fetch_meta.get("sample_row"),
    }

