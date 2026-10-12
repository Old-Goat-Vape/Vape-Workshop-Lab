from __future__ import annotations

import json
import re
import time
import traceback
from collections import defaultdict
from datetime import datetime
from statistics import median
from typing import Any, Dict, List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config.vc_dtl_scraper_config import VC_DTL_CONFIG
from config.canonical_library_brands import (
    canonical_library_brand,
    flavor_identity_match_key,
    normalize_flavor_identity_name,
)
from config.flavor_name_aliases import FLAVOR_NAME_ALIASES
from scrapers.vc_shared import (
    compute_content_hash,
    detect_has_any_token,
    normalize_flavor_name,
    normalize_text,
    parse_steep,
    to_float,
)

VC_RECIPES_URL = "https://vceliquidrecipes.com/recipes"
ANALYTICS_VERSION = "vc_dtl_bench_v1"
FLAVOUR_IDENTITY_RESOLVER_VERSION = 2


def _profile_normalize(raw_tags: list[str], title: str) -> str:
    t = " ".join(raw_tags + [title]).lower()
    if any(k in t for k in ("fruit", "berry", "citrus", "apple", "mango", "grape", "melon")):
        if any(k in t for k in ("ice", "cool", "menthol", "ws-23", "ws23")):
            return "fruit_ice"
        if any(k in t for k in ("candy", "sweet", "gummy")):
            return "fruit_candy"
        return "fruit"
    if any(k in t for k in ("dessert", "custard", "cream", "cheesecake")):
        if any(k in t for k in ("cake", "cookie", "bakery", "pie", "donut")):
            return "dessert_bakery"
        return "dessert"
    if any(k in t for k in ("cola", "beverage", "drink", "soda", "tea", "coffee")):
        return "beverage"
    if "tobacco" in t:
        return "tobacco"
    return "other"


def _extract_recipe_links(list_html: str) -> list[str]:
    soup = BeautifulSoup(list_html, "html.parser")
    links: list[str] = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        href_l = href.lower()
        is_recipe_path = (
            ("/recipe/" in href_l)
            or ("/recipes/" in href_l)
            or bool(re.match(r"^recipe/\d+", href_l))
            or bool(re.match(r"^recipes/\d+", href_l))
        )
        if not is_recipe_path:
            continue
        if href_l.rstrip("/") in {"/recipes", "/recipe"}:
            continue
        full = urljoin(VC_RECIPES_URL, href).split("#")[0]
        if full not in links:
            links.append(full)
    # Some VC pages hydrate links via inline scripts.
    for m in re.finditer(r"['\"](/?recipe/\d+|/?recipes/\d+)['\"]", list_html, flags=re.IGNORECASE):
        full = urljoin(VC_RECIPES_URL, m.group(1)).split("#")[0]
        if full not in links:
            links.append(full)
    return links


def _discover_ajax_endpoints(page_html: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    patterns = [
        r"'ajax'\s*:\s*\{.*?'type'\s*:\s*'([A-Z]+)'.*?'url'\s*:\s*'([^']+)'.*?\}",
        r'"ajax"\s*:\s*\{.*?"type"\s*:\s*"([A-Z]+)".*?"url"\s*:\s*"([^"]+)".*?\}',
        r"(?:url)\s*:\s*['\"]([^'\"]*(?:recipe|recipes|released|datatable|api)[^'\"]*)['\"]",
    ]
    for p in patterns:
        for m in re.finditer(p, page_html, flags=re.IGNORECASE | re.DOTALL):
            if len(m.groups()) == 2:
                method = (m.group(1) or "GET").upper()
                raw_url = m.group(2)
            else:
                method = "GET"
                raw_url = m.group(1)
            if not raw_url:
                continue
            full = urljoin(VC_RECIPES_URL, raw_url)
            pair = (method, full)
            if pair not in out:
                out.append(pair)
    return out


def _json_extract_recipe_links(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        # Capture structured ID fields from API payloads.
        for k, v in node.items():
            key = str(k or "").strip().lower().replace("_", "")
            if key in {"recipeid", "sourcerecipeid", "id"}:
                sval = str(v or "").strip()
                if sval.isdigit():
                    full = f"https://vceliquidrecipes.com/recipe/{sval}"
                    if full not in out:
                        out.append(full)
        for v in node.values():
            _json_extract_recipe_links(v, out)
        return
    if isinstance(node, list):
        for v in node:
            _json_extract_recipe_links(v, out)
        return
    if not isinstance(node, str):
        return
    text = node
    # JSON strings sometimes contain escaped link forms.
    text = text.replace("\\/", "/")
    for m in re.finditer(r"(https?://vceliquidrecipes\.com/recipe/\d+|/?recipe/\d+)", text, flags=re.IGNORECASE):
        full = urljoin(VC_RECIPES_URL, m.group(1))
        if full not in out:
            out.append(full)
    # Also support query-style IDs frequently returned by controllers.
    for m in re.finditer(r"(?:recipeid|sourcerecipeid|id)\s*[:=]\s*['\"]?(\d{2,8})", text, flags=re.IGNORECASE):
        full = f"https://vceliquidrecipes.com/recipe/{m.group(1)}"
        if full not in out:
            out.append(full)


def _fetch_links_via_ajax(session: requests.Session, headers: dict[str, str], page_html: str, max_recipes: int) -> list[str]:
    links: list[str] = []
    endpoints = _discover_ajax_endpoints(page_html)
    if not endpoints:
        return links
    payload = {
        "draw": 1,
        "start": 0,
        "length": 100,
        "search[value]": "",
        "search[regex]": "false",
        # request the broad released list (no type narrowing)
        "getRecipes": "true",
        "released": "1",
        "year": "all",
        "month": "all",
        "homogenisationPeriod": "all",
        "homogenisationUnit": "all",
        "recipeProfile": "all",
        "hideAdapted": "0",
        "hideSingleFlavour": "0",
        "showOnlyPodRecipes": "0",
        "totalFlavourPercent": "all",
    }
    for method, endpoint in endpoints:
        for page_idx in range(0, 60):
            payload["start"] = page_idx * 100
            try:
                if method == "POST":
                    r = session.post(endpoint, data=payload, headers={**headers, "X-Requested-With": "XMLHttpRequest"}, timeout=30)
                else:
                    r = session.get(endpoint, params=payload, headers=headers, timeout=30)
                if r.status_code >= 300:
                    break
                try:
                    data = r.json()
                    before = len(links)
                    _json_extract_recipe_links(data, links)
                    if len(links) == before and page_idx > 1:
                        break
                except Exception:
                    found = _extract_recipe_links(r.text)
                    if not found and page_idx > 0:
                        break
                    for f in found:
                        if f not in links:
                            links.append(f)
                if len(links) >= max_recipes:
                    return links[:max_recipes]
            except Exception:
                break
    return links[:max_recipes]


def _fetch_links_via_sitemap(session: requests.Session, headers: dict[str, str], max_recipes: int) -> list[str]:
    links: list[str] = []
    candidates = [
        "https://vceliquidrecipes.com/sitemap.xml",
        "https://vceliquidrecipes.com/sitemap_index.xml",
    ]
    for sm in candidates:
        try:
            r = session.get(sm, headers=headers, timeout=30)
            if r.status_code >= 300:
                continue
            xml = r.text or ""
            locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", xml, flags=re.IGNORECASE)
            # If sitemap index, recursively fetch child sitemaps likely containing recipes.
            child_maps = [u for u in locs if "sitemap" in u.lower() and u.lower().endswith(".xml")]
            pages = [xml]
            for cm in child_maps[:40]:
                try:
                    rc = session.get(cm, headers=headers, timeout=30)
                    if rc.status_code < 300:
                        pages.append(rc.text or "")
                except Exception:
                    continue
            for page in pages:
                for m in re.finditer(r"<loc>\s*(https?://vceliquidrecipes\.com/recipe/\d+)\s*</loc>", page, flags=re.IGNORECASE):
                    u = m.group(1).strip()
                    if u not in links:
                        links.append(u)
                    if len(links) >= max_recipes:
                        return links[:max_recipes]
        except Exception:
            continue
    return links[:max_recipes]


def _fetch_links_via_public_controller(
    session: requests.Session, headers: dict[str, str], max_recipes: int, start_page: int = 1
) -> list[str]:
    """
    VC released recipes controller discovered in page scripts:
    controllers/publicRecipesPageControl.php
    """
    endpoint = urljoin(VC_RECIPES_URL, "controllers/publicRecipesPageControl.php")
    links: list[str] = []
    for page in range(max(1, int(start_page)), 250):
        try:
            r = session.get(
                endpoint,
                params={"page": page, "search": ""},
                headers={**headers, "X-Requested-With": "XMLHttpRequest"},
                timeout=30,
            )
            if r.status_code >= 300:
                break
            body = r.text or ""
            extracted: list[str] = []
            try:
                data = r.json()
                # Typical VC payload includes HTML field.
                html_blob = str(data.get("html") or data.get("recipes") or "")
                if html_blob:
                    extracted = _extract_recipe_links(html_blob)
                _json_extract_recipe_links(data, extracted)
            except Exception:
                extracted = _extract_recipe_links(body)
            before = len(links)
            for lk in extracted:
                if lk not in links:
                    links.append(lk)
            if len(links) >= max_recipes:
                return links[:max_recipes]
            if page > 1 and len(links) == before:
                break
        except Exception:
            break
    return links[:max_recipes]


def _fetch_links_for_user_profile(
    session: requests.Session,
    headers: dict[str, str],
    *,
    user_id: str,
    max_recipes: int,
    start_page: int = 1,
) -> list[str]:
    links: list[str] = []
    base = f"https://vceliquidrecipes.com/user/{user_id}"
    for page in range(max(1, int(start_page)), 220):
        try:
            url = base if page == 1 else f"{base}?page={page}"
            r = session.get(url, headers=headers, timeout=30)
            if r.status_code >= 300:
                break
            found = _extract_recipe_links(r.text or "")
            before = len(links)
            for lk in found:
                if lk not in links:
                    links.append(lk)
            if len(links) >= max_recipes:
                return links[:max_recipes]
            if page > 1 and len(links) == before:
                break
        except Exception:
            break
    return links[:max_recipes]


def _fetch_links_via_id_scan(
    session: requests.Session,
    headers: dict[str, str],
    *,
    max_recipes: int,
    start_id: int = 1,
    end_id: int = 120000,
    request_budget: int = 400,
    max_consecutive_misses: int = 120,
    timeout_seconds: int = 8,
) -> list[str]:
    """
    Reliable fallback: probe canonical /recipe/<id> pages directly.
    Stops once enough valid public pages are found.
    """
    links: list[str] = []
    requests_used = 0
    consecutive_misses = 0
    private_markers = ["this recipe is private", "private recipe", "members only", "sign in", "login"]
    for rid in range(end_id, start_id - 1, -1):
        if len(links) >= max_recipes or requests_used >= request_budget:
            break
        url = f"https://vceliquidrecipes.com/recipe/{rid}"
        try:
            # Use GET (not HEAD) so we can reject private/login bodies that still return 200.
            g = session.get(url, headers=headers, timeout=timeout_seconds, allow_redirects=True)
            requests_used += 1
            if g.status_code >= 300:
                consecutive_misses += 1
                if consecutive_misses >= max_consecutive_misses:
                    break
                continue
            body = (g.text or "").lower()
            if any(marker in body for marker in private_markers):
                consecutive_misses += 1
                if consecutive_misses >= max_consecutive_misses:
                    break
                continue
            consecutive_misses = 0
            if url not in links:
                links.append(url)
        except Exception:
            requests_used += 1
            consecutive_misses += 1
            if consecutive_misses >= max_consecutive_misses:
                break
    return links[:max_recipes]


def _fetch_links_near_seed_ids(
    session: requests.Session,
    headers: dict[str, str],
    *,
    seed_ids: list[int],
    max_recipes: int,
    scan_radius: int = 1200,
    request_budget: int = 220,
    timeout_seconds: int = 6,
) -> list[str]:
    """
    Fast fallback: scan around already discovered recipe IDs so we avoid
    spending the entire discovery budget on unrelated private high-ID ranges.
    """
    private_markers = ["this recipe is private", "private recipe", "members only", "sign in", "login"]
    links: list[str] = []
    tested: set[int] = set()
    requests_used = 0
    for seed in sorted(set([int(x) for x in seed_ids if int(x) > 0]), reverse=True):
        if requests_used >= request_budget or len(links) >= max_recipes:
            break
        start = max(1, seed - scan_radius)
        end = seed + scan_radius
        for rid in range(end, start - 1, -1):
            if requests_used >= request_budget or len(links) >= max_recipes:
                break
            if rid in tested:
                continue
            tested.add(rid)
            url = f"https://vceliquidrecipes.com/recipe/{rid}"
            try:
                g = session.get(url, headers=headers, timeout=timeout_seconds, allow_redirects=True)
                requests_used += 1
                if g.status_code >= 300:
                    continue
                body = (g.text or "").lower()
                if any(marker in body for marker in private_markers):
                    continue
                if url not in links:
                    links.append(url)
            except Exception:
                requests_used += 1
                continue
    return links[:max_recipes]


def _extract_recipe_id(url: str) -> str:
    m = re.search(r"/recipe[s]?/(\d+)", (url or "").lower())
    return m.group(1) if m else normalize_text(url).replace(" ", "_")[:80]


def _canonical_detail_url(url_or_id: str) -> str:
    rid = _extract_recipe_id(url_or_id)
    if rid.isdigit():
        return f"https://vceliquidrecipes.com/recipe/{rid}"
    return ""


def _split_flavor_brand(raw: str) -> tuple[str, str]:
    txt = (raw or "").strip()
    if not txt:
        return "", ""
    # If combined like "Greek Yogurt - The Flavor Apprentice", split on LAST " - ".
    if " - " in txt:
        left, right = txt.rsplit(" - ", 1)
        return left.strip(), right.strip()
    m = re.match(r"(.+?)\s*\((.+?)\)\s*$", txt)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return txt, ""


def _extract_positive_pct(text: str) -> float | None:
    """
    Extract percentage as an absolute positive value from row text.
    Prevents '-' separators in labels from being interpreted as negative percentages.
    """
    s = str(text or "")
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*%", s)
    if matches:
        # VC rows can contain concentrate-strength markers inside names
        # (e.g. "WS-23 (30%)"). The row percentage is typically the trailing token.
        val = to_float(matches[-1])
        return abs(float(val)) if val is not None else None
    val = to_float(s)
    return abs(float(val)) if val is not None else None


def _is_summary_or_meta_row(text: str) -> bool:
    s = normalize_text(text or "")
    if not s:
        return True
    blocked_tokens = [
        "total flavour",
        "total flavor",
        "total %",
        "steep",
        "notes",
        "description",
        "author",
        "created",
        "profile",
    ]
    return any(tok in s for tok in blocked_tokens)


def _normalize_flavour_for_identity(name: str) -> str:
    return normalize_flavor_identity_name(name)


def _repair_existing_vc_import_signs(db) -> dict[str, int]:
    """
    One-time style repair for legacy bad VC import mappings:
    - make VCREF recipe_items non-negative
    - remove synthetic ingredient row named exactly 'VC Imported'
    - recalculate recipe_versions.total_flavor_percent from ingredient sum
    """
    repaired_negative = db.execute(
        """
        UPDATE recipe_items
        SET amount_percent = ABS(amount_percent)
        WHERE amount_percent < 0
          AND version_id IN (
            SELECT v.id
            FROM recipe_versions v
            JOIN recipes r ON r.id = v.recipe_id
            WHERE r.recipe_code LIKE 'VCREF-%'
          )
        """
    ).rowcount or 0

    removed_synthetic = db.execute(
        """
        DELETE FROM recipe_items
        WHERE ingredient_id IN (
            SELECT fr.id
            FROM flavor_reference fr
            WHERE LOWER(COALESCE(fr.name, '')) = 'vc imported'
        )
          AND version_id IN (
            SELECT v.id
            FROM recipe_versions v
            JOIN recipes r ON r.id = v.recipe_id
            WHERE r.recipe_code LIKE 'VCREF-%'
          )
        """
    ).rowcount or 0

    removed_fake_total_rows = db.execute(
        """
        DELETE FROM recipe_reference_flavours
        WHERE recipe_reference_id IN (
            SELECT id FROM recipe_references WHERE source = 'vc' AND source_type = 'vc_dtl'
        )
          AND (
            LOWER(COALESCE(flavour_name_raw, '')) LIKE '%total flavour%'
            OR LOWER(COALESCE(flavour_name_raw, '')) LIKE '%total flavor%'
            OR LOWER(COALESCE(flavour_name_normalized, '')) LIKE '%total flavour%'
            OR LOWER(COALESCE(flavour_name_normalized, '')) LIKE '%total flavor%'
          )
        """
    ).rowcount or 0

    db.execute(
        """
        UPDATE recipe_versions
        SET total_flavor_percent = (
            SELECT COALESCE(SUM(ABS(ri.amount_percent)), 0.0)
            FROM recipe_items ri
            WHERE ri.version_id = recipe_versions.id
              AND ri.ingredient_id > 0
        )
        WHERE recipe_id IN (SELECT id FROM recipes WHERE recipe_code LIKE 'VCREF-%')
        """
    )
    return {
        "repaired_negative_rows": int(repaired_negative),
        "removed_synthetic_rows": int(removed_synthetic),
        "removed_fake_total_rows": int(removed_fake_total_rows),
    }


def _backfill_flavour_identity_matches(db, *, resolver_ctx: dict[str, Any], limit: int = 2000) -> dict[str, int]:
    rows = db.execute(
        """
        SELECT id, brand_raw, flavour_name_raw
        FROM recipe_reference_flavours
        WHERE (matched_flavor_id IS NULL OR COALESCE(match_method, '') = '')
        ORDER BY id ASC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    updated = 0
    unresolved = 0
    for r in rows:
        ident = _resolve_imported_flavour_identity(
            brand_raw=str(r["brand_raw"] or ""),
            flavour_name_raw=str(r["flavour_name_raw"] or ""),
            resolver_ctx=resolver_ctx,
        )
        if not ident.get("matched_flavor_id"):
            unresolved += 1
        db.execute(
            """
            UPDATE recipe_reference_flavours
            SET brand_canonical = ?,
                flavour_name_normalized = ?,
                matched_flavor_id = ?,
                matched_flavor_name = ?,
                match_method = ?,
                match_confidence = ?,
                needs_review = ?
            WHERE id = ?
            """,
            (
                ident.get("brand_canonical"),
                ident.get("flavour_name_normalized"),
                ident.get("matched_flavor_id"),
                ident.get("matched_flavor_name"),
                ident.get("match_method"),
                ident.get("match_confidence"),
                int(ident.get("needs_review") or 0),
                int(r["id"]),
            ),
        )
        updated += 1
    return {"identity_backfill_rows": updated, "identity_backfill_unresolved": unresolved}


def _resync_imported_recipe_library_links(db, *, reference_lookup: dict[tuple[str, str], int]) -> dict[str, int]:
    rows = db.execute(
        """
        SELECT id, recipe_id, source_recipe_id, title
        FROM recipe_references
        WHERE source='vc' AND source_type='vc_dtl' AND recipe_id IS NOT NULL
        """
    ).fetchall()
    recipes_updated = 0
    items_written = 0
    for rr in rows:
        rv = db.execute(
            "SELECT id FROM recipe_versions WHERE recipe_id = ? ORDER BY id DESC LIMIT 1",
            (int(rr["recipe_id"]),),
        ).fetchone()
        if not rv:
            continue
        version_id = int(rv["id"])
        db.execute("DELETE FROM recipe_items WHERE version_id = ?", (version_id,))
        frs = db.execute(
            """
            SELECT brand_raw, flavour_name_raw, percentage, matched_flavor_id
            FROM recipe_reference_flavours
            WHERE recipe_reference_id = ?
            ORDER BY position ASC, id ASC
            """,
            (int(rr["id"]),),
        ).fetchall()
        wrote = 0
        for fr in frs:
            ref_id = int(fr["matched_flavor_id"]) if str(fr["matched_flavor_id"] or "").isdigit() else None
            if not ref_id:
                ref_id = _ensure_reference_for_flavor(
                    db,
                    reference_lookup=reference_lookup,
                    brand_raw=str(fr["brand_raw"] or ""),
                    flavor_raw=str(fr["flavour_name_raw"] or ""),
                )
            if not ref_id:
                continue
            db.execute(
                "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (?, ?, ?)",
                (version_id, int(ref_id), abs(float(fr["percentage"] or 0.0))),
            )
            wrote += 1
        if wrote > 0:
            recipes_updated += 1
            items_written += wrote
    return {"resynced_recipes": recipes_updated, "resynced_recipe_items": items_written}


def backfill_all_imported_flavour_identity_matches(
    db,
    *,
    force_rematch: bool = False,
    unresolved_only: bool = False,
    chunk_size: int = 250,
) -> dict[str, Any]:
    _ensure_reference_tables(db)
    resolver_ctx = _build_identity_resolver_context(db)
    scanned_rows = 0
    updated_rows = 0
    exact = 0
    alias = 0
    fuzzy = 0
    unresolved = 0
    needs_review = 0
    chunk_count = 0
    db_commit_count = 0
    db_rollback_count = 0
    skipped_already_current_version = 0
    first_error = ""
    flavour_match_sample: list[dict[str, Any]] = []
    mode = "force_rematch" if force_rematch else ("unresolved_only" if unresolved_only else "normal")

    while True:
        where = [
            "rr.source='vc'",
            "rr.source_type='vc_dtl'",
        ]
        if force_rematch:
            pass
        elif unresolved_only:
            where.append("(rf.matched_flavor_id IS NULL OR COALESCE(rf.match_method,'')='' OR COALESCE(rf.needs_review,0)=1)")
        else:
            where.append("(rf.matched_flavor_id IS NULL OR COALESCE(rf.match_method,'')='' OR COALESCE(rf.needs_review,0)=1 OR COALESCE(rf.identity_match_version,0) < ?)")

        sql = f"""
            SELECT rf.id, rf.recipe_reference_id, rr.source_recipe_id, rf.brand_raw, rf.flavour_name_raw, rf.identity_match_version
            FROM recipe_reference_flavours rf
            JOIN recipe_references rr ON rr.id = rf.recipe_reference_id
            WHERE {' AND '.join(where)}
            ORDER BY rf.id ASC
            LIMIT ?
        """
        params: list[Any] = []
        if (not force_rematch) and (not unresolved_only):
            params.append(FLAVOUR_IDENTITY_RESOLVER_VERSION)
        params.append(int(chunk_size))
        rows = db.execute(sql, params).fetchall()
        if not rows:
            break
        chunk_count += 1
        for r in rows:
            scanned_rows += 1
            if (not force_rematch) and int(r["identity_match_version"] or 0) >= FLAVOUR_IDENTITY_RESOLVER_VERSION and not unresolved_only:
                skipped_already_current_version += 1
                continue
            try:
                ident = _resolve_imported_flavour_identity(
                    brand_raw=str(r["brand_raw"] or ""),
                    flavour_name_raw=str(r["flavour_name_raw"] or ""),
                    resolver_ctx=resolver_ctx,
                )
                db.execute(
                    """
                    UPDATE recipe_reference_flavours
                    SET brand_canonical = ?,
                        flavour_name_normalized = ?,
                        matched_flavor_id = ?,
                        matched_flavor_name = ?,
                        match_method = ?,
                        match_confidence = ?,
                        needs_review = ?,
                        identity_matched_at = ?,
                        identity_match_version = ?
                    WHERE id = ?
                    """,
                    (
                        ident.get("brand_canonical"),
                        ident.get("flavour_name_normalized"),
                        ident.get("matched_flavor_id"),
                        ident.get("matched_flavor_name"),
                        ident.get("match_method"),
                        ident.get("match_confidence"),
                        int(ident.get("needs_review") or 0),
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        FLAVOUR_IDENTITY_RESOLVER_VERSION,
                        int(r["id"]),
                    ),
                )
                updated_rows += 1
                mm = str(ident.get("match_method") or "")
                if mm == "exact_brand_name":
                    exact += 1
                elif mm == "alias_map":
                    alias += 1
                elif mm == "fuzzy":
                    fuzzy += 1
                if int(ident.get("needs_review") or 0) == 1:
                    needs_review += 1
                    unresolved += 1
                if len(flavour_match_sample) < 20:
                    flavour_match_sample.append(
                        {
                            "recipe_reference_id": int(r["recipe_reference_id"]),
                            "source_recipe_id": str(r["source_recipe_id"] or ""),
                            "brand_raw": str(r["brand_raw"] or ""),
                            "flavour_name_raw": str(r["flavour_name_raw"] or ""),
                            "brand_canonical": str(ident.get("brand_canonical") or ""),
                            "flavour_name_normalized": str(ident.get("flavour_name_normalized") or ""),
                            "matched_flavor_id": ident.get("matched_flavor_id"),
                            "matched_flavor_name": str(ident.get("matched_flavor_name") or ""),
                            "match_method": mm,
                            "match_confidence": float(ident.get("match_confidence") or 0.0),
                            "needs_review": int(ident.get("needs_review") or 0),
                        }
                    )
            except Exception as exc:
                if not first_error:
                    first_error = f"{type(exc).__name__}: {exc}"
        try:
            db.commit()
            db_commit_count += 1
        except Exception:
            db.rollback()
            db_rollback_count += 1
            if not first_error:
                first_error = "chunk_commit_failed"
            break

    reference_lookup = _build_reference_lookup(db)
    resync_stats = _resync_imported_recipe_library_links(db, reference_lookup=reference_lookup)
    db.commit()
    db_commit_count += 1

    unresolved_grouped = db.execute(
        """
        SELECT COALESCE(brand_canonical, '') AS brand_canonical,
               COALESCE(flavour_name_normalized, '') AS flavour_name_normalized,
               COUNT(1) AS occurrence_count,
               MIN(COALESCE(flavour_name_raw, '')) AS sample_raw_name
        FROM recipe_reference_flavours
        WHERE COALESCE(matched_flavor_id, 0) = 0
           OR COALESCE(needs_review, 0) = 1
        GROUP BY COALESCE(brand_canonical, ''), COALESCE(flavour_name_normalized, '')
        ORDER BY occurrence_count DESC
        LIMIT 25
        """
    ).fetchall()
    unresolved_grouped_sample = [
        {
            "brand_canonical": str(r["brand_canonical"] or ""),
            "flavour_name_normalized": str(r["flavour_name_normalized"] or ""),
            "occurrence_count": int(r["occurrence_count"] or 0),
            "sample_raw_names": [str(r["sample_raw_name"] or "")],
            "candidate_library_matches": [],
        }
        for r in unresolved_grouped
    ]

    totals = db.execute(
        """
        SELECT
          COUNT(1) AS total_rows,
          SUM(CASE WHEN COALESCE(matched_flavor_id,0) > 0 THEN 1 ELSE 0 END) AS matched_rows,
          SUM(CASE WHEN COALESCE(matched_flavor_id,0) = 0 OR COALESCE(needs_review,0)=1 THEN 1 ELSE 0 END) AS unresolved_rows
        FROM recipe_reference_flavours rf
        JOIN recipe_references rr ON rr.id = rf.recipe_reference_id
        WHERE rr.source='vc' AND rr.source_type='vc_dtl'
        """
    ).fetchone()
    total_rows = int(totals["total_rows"] or 0)
    matched_rows = int(totals["matched_rows"] or 0)
    unresolved_total = int(totals["unresolved_rows"] or 0)
    match_rate_percent = round((100.0 * matched_rows / total_rows), 2) if total_rows else 0.0
    recipe_linkability = db.execute(
        """
        SELECT
          SUM(CASE WHEN unresolved_cnt = 0 THEN 1 ELSE 0 END) AS fully_linkable_recipes,
          SUM(CASE WHEN unresolved_cnt > 0 THEN 1 ELSE 0 END) AS recipes_with_unresolved
        FROM (
          SELECT rr.id,
                 SUM(CASE WHEN COALESCE(rf.matched_flavor_id,0)=0 OR COALESCE(rf.needs_review,0)=1 THEN 1 ELSE 0 END) AS unresolved_cnt
          FROM recipe_references rr
          LEFT JOIN recipe_reference_flavours rf ON rf.recipe_reference_id = rr.id
          WHERE rr.source='vc' AND rr.source_type='vc_dtl'
          GROUP BY rr.id
        ) t
        """
    ).fetchone()

    return {
        "status": "success",
        "backfill_mode": mode,
        "resolver_version": FLAVOUR_IDENTITY_RESOLVER_VERSION,
        "scanned_rows": scanned_rows,
        "updated_rows": updated_rows,
        "rematched_rows": updated_rows,
        "exact_matched_rows": exact,
        "alias_matched_rows": alias,
        "fuzzy_matched_rows": fuzzy,
        "unresolved_rows": unresolved,
        "needs_review_rows": needs_review,
        "chunk_count": chunk_count,
        "db_commit_count": db_commit_count,
        "db_rollback_count": db_rollback_count,
        "skipped_already_current_version": skipped_already_current_version,
        "first_error": first_error,
        "flavour_match_sample": flavour_match_sample[:20],
        "unresolved_grouped_sample": unresolved_grouped_sample,
        "resync_stats": resync_stats,
        "total_imported_flavour_rows": total_rows,
        "total_rows_with_matched_flavor_id": matched_rows,
        "total_rows_unresolved": unresolved_total,
        "match_rate_percent": match_rate_percent,
        "fully_linkable_imported_recipes": int(recipe_linkability["fully_linkable_recipes"] or 0) if recipe_linkability else 0,
        "imported_recipes_with_unresolved_rows": int(recipe_linkability["recipes_with_unresolved"] or 0) if recipe_linkability else 0,
    }


def _has_column(db, table: str, col: str) -> bool:
    try:
        rows = db.execute(f"PRAGMA table_info({table})").fetchall()
        return any(str(r["name"]) == col for r in rows)
    except Exception:
        return False


def _ensure_reference_tables(db) -> None:
    # Defensive guard so scraper can run even if app restart/migrations lagged.
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS recipe_references (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_type TEXT NOT NULL,
            device_type TEXT NOT NULL,
            source_url TEXT,
            source_recipe_id TEXT NOT NULL,
            title TEXT,
            author TEXT,
            total_flavour_pct REAL,
            steep_value REAL,
            steep_unit TEXT,
            is_public INTEGER DEFAULT 1,
            is_adapted INTEGER DEFAULT 0,
            is_single_flavour INTEGER DEFAULT 0,
            profile_raw TEXT,
            profile_normalized TEXT,
            scraped_at DATETIME,
            content_hash TEXT,
            benchmark_eligible INTEGER DEFAULT 0,
            recipe_id INTEGER,
            parse_failed INTEGER DEFAULT 0,
            parse_error TEXT,
            needs_review INTEGER DEFAULT 0,
            UNIQUE(source, source_type, source_recipe_id)
        );
        CREATE TABLE IF NOT EXISTS recipe_reference_flavours (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_reference_id INTEGER NOT NULL,
            brand_raw TEXT,
            brand_canonical TEXT,
            flavour_name_raw TEXT,
            flavour_name_normalized TEXT,
            percentage REAL,
            position INTEGER,
            matched_flavor_id INTEGER,
            matched_flavor_name TEXT,
            match_method TEXT,
            match_confidence REAL,
            needs_review INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS recipe_reference_benchmark_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            device_type TEXT NOT NULL,
            profile_normalized TEXT NOT NULL,
            recipe_count INTEGER,
            avg_total_flavour_pct REAL,
            median_total_flavour_pct REAL,
            avg_flavour_count REAL,
            avg_pct_per_flavour REAL,
            sweetener_usage_rate REAL,
            cooling_usage_rate REAL,
            analytics_version TEXT,
            updated_at DATETIME,
            UNIQUE(source_type, device_type, profile_normalized)
        );
        CREATE TABLE IF NOT EXISTS vc_import_checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            source_user_id TEXT,
            cursor_page INTEGER DEFAULT 1,
            last_recipe_id TEXT,
            total_discovered INTEGER DEFAULT 0,
            total_imported INTEGER DEFAULT 0,
            no_new_ids_runs_in_a_row INTEGER DEFAULT 0,
            last_new_id_count INTEGER DEFAULT 0,
            updated_at DATETIME,
            UNIQUE(mode, source_user_id)
        );
        CREATE TABLE IF NOT EXISTS recipe_reference_provenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_reference_id INTEGER NOT NULL,
            vc_source_scope TEXT NOT NULL,
            vc_source_user_id TEXT,
            vc_source_user_url TEXT,
            vc_discovered_via TEXT,
            created_at DATETIME,
            UNIQUE(recipe_reference_id, vc_source_scope, vc_source_user_id, vc_discovered_via)
        );
        """
    )
    # Additive columns for older installs.
    for col_sql in [
        "ALTER TABLE recipe_reference_flavours ADD COLUMN matched_flavor_id INTEGER",
        "ALTER TABLE recipe_reference_flavours ADD COLUMN matched_flavor_name TEXT",
        "ALTER TABLE recipe_reference_flavours ADD COLUMN match_method TEXT",
        "ALTER TABLE recipe_reference_flavours ADD COLUMN match_confidence REAL",
        "ALTER TABLE recipe_reference_flavours ADD COLUMN needs_review INTEGER DEFAULT 0",
        "ALTER TABLE recipe_reference_flavours ADD COLUMN identity_matched_at DATETIME",
        "ALTER TABLE recipe_reference_flavours ADD COLUMN identity_match_version INTEGER",
    ]:
        try:
            db.execute(col_sql)
        except Exception:
            pass


def _load_checkpoint(db, *, mode: str, source_user_id: str = "") -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM vc_import_checkpoints WHERE mode = ? AND COALESCE(source_user_id, '') = ?",
        (mode, str(source_user_id or "")),
    ).fetchone()
    return dict(row) if row else {}


def _save_checkpoint(
    db,
    *,
    mode: str,
    source_user_id: str = "",
    cursor_page: int = 1,
    last_recipe_id: str = "",
    total_discovered: int = 0,
    total_imported: int = 0,
    no_new_ids_runs_in_a_row: int = 0,
    last_new_id_count: int = 0,
) -> None:
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        """
        INSERT INTO vc_import_checkpoints
        (mode, source_user_id, cursor_page, last_recipe_id, total_discovered, total_imported, no_new_ids_runs_in_a_row, last_new_id_count, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(mode, source_user_id)
        DO UPDATE SET
            cursor_page=excluded.cursor_page,
            last_recipe_id=excluded.last_recipe_id,
            total_discovered=excluded.total_discovered,
            total_imported=excluded.total_imported,
            no_new_ids_runs_in_a_row=excluded.no_new_ids_runs_in_a_row,
            last_new_id_count=excluded.last_new_id_count,
            updated_at=excluded.updated_at
        """,
        (
            mode,
            str(source_user_id or ""),
            int(cursor_page),
            str(last_recipe_id or ""),
            int(total_discovered),
            int(total_imported),
            int(no_new_ids_runs_in_a_row),
            int(last_new_id_count),
            now_ts,
        ),
    )


def _record_provenance(
    db,
    *,
    recipe_reference_id: int,
    scope: str,
    source_user_id: str = "",
    source_user_url: str = "",
    discovered_via: str = "",
) -> None:
    db.execute(
        """
        INSERT OR IGNORE INTO recipe_reference_provenance
        (recipe_reference_id, vc_source_scope, vc_source_user_id, vc_source_user_url, vc_discovered_via, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            int(recipe_reference_id),
            scope,
            str(source_user_id or ""),
            str(source_user_url or ""),
            str(discovered_via or ""),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )


def _build_reference_lookup(db) -> dict[tuple[str, str], int]:
    rows = db.execute("SELECT id, brand, name FROM flavor_reference WHERE active = 1").fetchall()
    out: dict[tuple[str, str], int] = {}
    for r in rows:
        key = flavor_identity_match_key(str(r["brand"] or ""), str(r["name"] or ""))
        if key not in out:
            out[key] = int(r["id"])
    return out


def _build_identity_resolver_context(db) -> dict[str, Any]:
    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS flavor_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alias_raw TEXT NOT NULL,
                alias_normalized TEXT NOT NULL,
                brand_canonical TEXT,
                target_flavor_id INTEGER NOT NULL,
                notes TEXT,
                confidence_override REAL,
                source TEXT,
                created_at DATETIME,
                created_by INTEGER,
                UNIQUE(alias_normalized, COALESCE(brand_canonical, ''))
            )
            """
        )
    except Exception:
        pass
    rows = db.execute("SELECT id, brand, name FROM flavor_reference WHERE active = 1").fetchall()
    by_brand_name: dict[tuple[str, str], dict[str, Any]] = {}
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        fid = int(r["id"])
        brand_raw = str(r["brand"] or "")
        name_raw = str(r["name"] or "")
        canon_brand = canonical_library_brand(brand_raw, name_raw)
        norm_name = _normalize_flavour_for_identity(name_raw)
        rec = {"id": fid, "name": name_raw, "brand_canonical": canon_brand, "name_normalized": norm_name}
        by_brand_name[(canon_brand, norm_name)] = rec
        by_name[norm_name].append(rec)
    alias_map: dict[tuple[str, str], dict[str, Any]] = {}
    for a in FLAVOR_NAME_ALIASES:
        alias_norm = _normalize_flavour_for_identity(str(a.get("alias_raw") or ""))
        if not alias_norm:
            continue
        alias_brand = canonical_library_brand(str(a.get("brand_canonical") or ""), "")
        target_name_norm = _normalize_flavour_for_identity(str(a.get("target_flavor_name") or ""))
        target = by_brand_name.get((alias_brand, target_name_norm))
        if target:
            alias_map[(alias_brand, alias_norm)] = {
                "target_id": target["id"],
                "target_name": target["name"],
                "confidence": float(a.get("confidence_override") or 0.9),
            }
    # DB aliases (admin-curated) override config aliases.
    try:
        db_alias_rows = db.execute(
            """
            SELECT fa.alias_raw, fa.alias_normalized, fa.brand_canonical, fa.target_flavor_id,
                   fa.confidence_override, fr.name AS target_flavor_name
            FROM flavor_aliases fa
            JOIN flavor_reference fr ON fr.id = fa.target_flavor_id
            """
        ).fetchall()
        for r in db_alias_rows:
            alias_norm = str(r["alias_normalized"] or "").strip()
            if not alias_norm:
                continue
            alias_brand = canonical_library_brand(str(r["brand_canonical"] or ""), "")
            alias_map[(alias_brand, alias_norm)] = {
                "target_id": int(r["target_flavor_id"]),
                "target_name": str(r["target_flavor_name"] or ""),
                "confidence": float(r["confidence_override"] or 0.95),
            }
    except Exception:
        pass
    return {"by_brand_name": by_brand_name, "by_name": by_name, "alias_map": alias_map}


def _resolve_imported_flavour_identity(
    *,
    brand_raw: str,
    flavour_name_raw: str,
    resolver_ctx: dict[str, Any],
) -> dict[str, Any]:
    brand_canonical = canonical_library_brand(brand_raw or "", flavour_name_raw or "")
    name_norm = _normalize_flavour_for_identity(flavour_name_raw or "")
    by_brand_name = resolver_ctx.get("by_brand_name") or {}
    by_name = resolver_ctx.get("by_name") or {}
    alias_map = resolver_ctx.get("alias_map") or {}

    exact = by_brand_name.get((brand_canonical, name_norm))
    if exact:
        return {
            "brand_canonical": brand_canonical,
            "flavour_name_normalized": name_norm,
            "matched_flavor_id": int(exact["id"]),
            "matched_flavor_name": exact["name"],
            "match_method": "exact_brand_name",
            "match_confidence": 1.0,
            "needs_review": 0,
        }

    alias = alias_map.get((brand_canonical, name_norm))
    if alias:
        return {
            "brand_canonical": brand_canonical,
            "flavour_name_normalized": name_norm,
            "matched_flavor_id": int(alias["target_id"]),
            "matched_flavor_name": str(alias["target_name"]),
            "match_method": "alias_map",
            "match_confidence": float(alias["confidence"]),
            "needs_review": 0,
        }

    candidates = by_name.get(name_norm) or []
    if len(candidates) == 1:
        c = candidates[0]
        return {
            "brand_canonical": brand_canonical,
            "flavour_name_normalized": name_norm,
            "matched_flavor_id": int(c["id"]),
            "matched_flavor_name": c["name"],
            "match_method": "normalized_name_unique",
            "match_confidence": 0.86,
            "needs_review": 0,
        }

    return {
        "brand_canonical": brand_canonical,
        "flavour_name_normalized": name_norm,
        "matched_flavor_id": None,
        "matched_flavor_name": "",
        "match_method": "unresolved",
        "match_confidence": 0.0,
        "needs_review": 1,
    }


def _ensure_reference_for_flavor(
    db,
    *,
    reference_lookup: dict[tuple[str, str], int],
    brand_raw: str,
    flavor_raw: str,
) -> int | None:
    brand = (brand_raw or "").strip() or "VC Imported"
    flavor = (flavor_raw or "").strip()
    if not flavor:
        return None
    canon_b = canonical_library_brand(brand, flavor)
    key = flavor_identity_match_key(brand, flavor)
    existing = reference_lookup.get(key)
    if existing:
        return int(existing)

    row = db.execute(
        "SELECT id FROM flavor_reference WHERE brand = ? AND name = ? LIMIT 1",
        (canon_b, flavor),
    ).fetchone()
    if row:
        ref_id = int(row["id"])
        reference_lookup[key] = ref_id
        return ref_id

    db.execute(
        """
        INSERT OR IGNORE INTO flavor_reference
        (brand, name, category, source, active, avg_usage, price_per_ml, price_10ml)
        VALUES (?, ?, 'Flavor', 'VC Recipe Import', 1, 0.0, 0.0, 0.0)
        """,
        (canon_b, flavor),
    )
    row2 = db.execute(
        "SELECT id FROM flavor_reference WHERE brand = ? AND name = ? LIMIT 1",
        (canon_b, flavor),
    ).fetchone()
    if not row2:
        return None
    ref_id = int(row2["id"])
    reference_lookup[key] = ref_id
    return ref_id


def _sync_into_recipe_library(
    db,
    recipe: dict[str, Any],
    *,
    reference_lookup: dict[tuple[str, str], int],
    actor_id: int | None = None,
) -> dict[str, Any]:
    # Additive import into internal recipe library for visibility/benchmarking.
    recipe_code = f"VCREF-{recipe['source_recipe_id']}"
    existing = db.execute("SELECT id FROM recipes WHERE recipe_code = ?", (recipe_code,)).fetchone()
    # Cleanup guard for accidental prior duplicates by same recipe_code.
    dupes = db.execute("SELECT id FROM recipes WHERE recipe_code = ? ORDER BY id ASC", (recipe_code,)).fetchall()
    if len(dupes) > 1:
        keep = int(dupes[0]["id"])
        for d in dupes[1:]:
            db.execute("UPDATE recipes SET active = 0 WHERE id = ?", (int(d["id"]),))
        existing = {"id": keep}
    if existing:
        recipe_id = int(existing["id"])
        db.execute(
            "UPDATE recipes SET name = ?, status = ?, active = 1 WHERE id = ?",
            (recipe.get("title") or recipe_code, "Reference", recipe_id),
        )
    else:
        cur = db.execute(
            "INSERT INTO recipes (name, recipe_code, status, created_by, active) VALUES (?, ?, ?, ?, 1)",
            (recipe.get("title") or recipe_code, recipe_code, "Reference", (int(actor_id) if actor_id else None)),
        )
        recipe_id = int(cur.lastrowid)

    # Replace latest reference version each run to keep deterministic snapshot.
    row_v = db.execute("SELECT id FROM recipe_versions WHERE recipe_id = ? ORDER BY id DESC LIMIT 1", (recipe_id,)).fetchone()
    if row_v:
        db.execute("DELETE FROM recipe_items WHERE version_id = ?", (int(row_v["id"]),))
        db.execute("DELETE FROM recipe_versions WHERE id = ?", (int(row_v["id"]),))

    cur_v = db.execute(
        """
        INSERT INTO recipe_versions (recipe_id, version_number, target_vg_pg_ratio, total_flavor_percent, notes, is_locked, created_at)
        VALUES (?, '1.0', '70/30', ?, ?, 1, ?)
        """,
        (
            recipe_id,
            abs(float(recipe.get("total_flavour_pct") or 0.0)),
            f"Imported VC DTL reference ({recipe.get('source_url')})",
            datetime.now(),
        ),
    )
    version_id = int(cur_v.lastrowid)
    attempted_items = 0
    written_items = 0
    for fl in recipe.get("flavours") or []:
        attempted_items += 1
        matched_id = fl.get("matched_flavor_id")
        ref_id = int(matched_id) if str(matched_id or "").isdigit() else None
        if not ref_id:
            ref_id = _ensure_reference_for_flavor(
                db,
                reference_lookup=reference_lookup,
                brand_raw=str(fl.get("brand_raw") or ""),
                flavor_raw=str(fl.get("flavour_name_raw") or ""),
            )
        if not ref_id:
            continue
        db.execute(
            "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (?, ?, ?)",
            (version_id, int(ref_id), abs(float(fl.get("percentage") or 0.0))),
        )
        written_items += 1
    return {
        "recipe_id": recipe_id,
        "attempted_items": attempted_items,
        "written_items": written_items,
    }


def _parse_recipe_page(url: str, html: str) -> dict[str, Any] | None:
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    if soup.select_one("h1"):
        title = soup.select_one("h1").get_text(" ", strip=True)
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    title = title.strip()
    if not title:
        return None

    visible_text = soup.get_text(" ", strip=True)
    author = ""
    m_author = re.search(r"(?:author|by)\s*[:\-]?\s*([A-Za-z0-9 _\-\.]{2,60})", visible_text, re.IGNORECASE)
    if m_author:
        author = m_author.group(1).strip()
    m_created = re.search(r"(?:created|posted|published)\s*[:\-]?\s*([A-Za-z0-9,\-\/ ]{4,40})", visible_text, re.IGNORECASE)
    created_text = m_created.group(1).strip() if m_created else ""

    total_pct = None
    m_total = re.search(r"(?:total\s+flavo[u]?r\s*%?|flavo[u]?r\s*total)\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)\s*%", visible_text, re.IGNORECASE)
    if m_total:
        total_pct = to_float(m_total.group(1))

    steep_value, steep_unit = parse_steep(visible_text)

    tags: list[str] = []
    for el in soup.select(".badge, .tag, .label, a[href*='profile'], a[href*='tag']"):
        t = el.get_text(" ", strip=True)
        if t and len(t) < 50:
            tags.append(t)
    tags = list(dict.fromkeys(tags))
    profile_norm = _profile_normalize(tags, title)

    lowered = visible_text.lower()
    is_adapted = detect_has_any_token([title, lowered], ["adapted", "adaptation", "clone of"])
    is_public = not detect_has_any_token([lowered], ["private recipe", "members only", "restricted"])

    flavor_rows: list[dict[str, Any]] = []
    excluded_summary_row_count = 0
    excluded_rows_sample: list[str] = []
    for tr in soup.select("table tr"):
        row_txt = tr.get_text(" ", strip=True)
        if "%" not in row_txt:
            continue
        if _is_summary_or_meta_row(row_txt):
            excluded_summary_row_count += 1
            if len(excluded_rows_sample) < 8:
                excluded_rows_sample.append(row_txt[:180])
            continue
        pct = _extract_positive_pct(row_txt)
        if pct is None:
            continue
        cells = [c.get_text(" ", strip=True) for c in tr.select("td,th")]
        candidate = " ".join(cells).strip()
        candidate = re.sub(r"\b\d+(?:\.\d+)?\s*%\b", "", candidate).strip(" -:")
        if len(candidate) < 2:
            continue
        if _is_summary_or_meta_row(candidate):
            excluded_summary_row_count += 1
            if len(excluded_rows_sample) < 8:
                excluded_rows_sample.append(candidate[:180])
            continue
        flavor, brand = _split_flavor_brand(candidate)
        if _is_summary_or_meta_row(flavor):
            excluded_summary_row_count += 1
            if len(excluded_rows_sample) < 8:
                excluded_rows_sample.append(flavor[:180])
            continue
        flavor_rows.append(
            {
                "brand_raw": brand,
                "flavour_name_raw": flavor,
                "percentage": pct,
            }
        )

    if not flavor_rows:
        # light fallback from bullet-like lines
        for line in visible_text.split("  "):
            if "%" not in line:
                continue
            if _is_summary_or_meta_row(line):
                excluded_summary_row_count += 1
                if len(excluded_rows_sample) < 8:
                    excluded_rows_sample.append(line[:180])
                continue
            pct = _extract_positive_pct(line)
            if pct is None:
                continue
            n = re.sub(r"\b\d+(?:\.\d+)?\s*%\b", "", line).strip(" -:")
            if len(n) < 2:
                continue
            if _is_summary_or_meta_row(n):
                excluded_summary_row_count += 1
                if len(excluded_rows_sample) < 8:
                    excluded_rows_sample.append(n[:180])
                continue
            flavor_rows.append({"brand_raw": "", "flavour_name_raw": n, "percentage": pct})

    if not flavor_rows:
        return None

    # Avoid false positives from global UI text like "Hide Single Flavour".
    # Treat as single only when the recipe clearly indicates it, or when it truly has one flavor line.
    single_markers = [
        "single flavour recipe",
        "single-flavour recipe",
        "single flavor recipe",
        "one shot recipe",
        "one-shot recipe",
    ]
    is_single = (len(flavor_rows) == 1) or detect_has_any_token([title, lowered], single_markers)

    # Keep total consistent with parsed rows to avoid offset-style mapping errors.
    sum_pct = sum([abs(float(r["percentage"] or 0.0)) for r in flavor_rows])
    if total_pct is None:
        total_pct = sum_pct
    else:
        total_pct = abs(float(total_pct))

    # Optional notes/description slice for debugging/auditing.
    description = ""
    for sel in (".recipe-description", ".description", ".notes", "#description"):
        el = soup.select_one(sel)
        if el:
            description = el.get_text(" ", strip=True)
            break

    return {
        "source": "vc",
        "source_type": "vc_dtl",
        "device_type": "DTL",
        "source_url": url,
        "source_recipe_id": _extract_recipe_id(url),
        "title": title,
        "author": author,
        "total_flavour_pct": total_pct,
        "steep_value": steep_value,
        "steep_unit": steep_unit,
        "is_public": 1 if is_public else 0,
        "is_adapted": 1 if is_adapted else 0,
        "is_single_flavour": 1 if is_single else 0,
        "profile_raw": json.dumps(tags),
        "profile_normalized": profile_norm,
        "created_text": created_text,
        "description_notes": description,
        "flavours": flavor_rows,
        "excluded_summary_row_count": excluded_summary_row_count,
        "excluded_rows_sample": excluded_rows_sample[:8],
        "summed_real_flavour_pct": round(sum_pct, 4),
        "sum_mismatch": abs(sum_pct - float(total_pct or 0.0)) > 0.25,
    }


def _is_benchmark_eligible(recipe: dict[str, Any], *, include_adapted: bool) -> int:
    thresholds = VC_DTL_CONFIG.get("thresholds", {})
    hard_min = float(thresholds.get("hard_min_total_flavour_pct", 3.0))
    hard_max = float(thresholds.get("hard_max_total_flavour_pct", 30.0))
    total = float(recipe.get("total_flavour_pct") or 0.0)
    flavors = recipe.get("flavours") or []
    # DTL benchmark policy: keep raw rows but only mark clean rows as benchmark-eligible.
    if int(recipe.get("is_public") or 0) != 1:
        return 0
    if int(recipe.get("is_single_flavour") or 0) == 1:
        return 0
    if not include_adapted and int(recipe.get("is_adapted") or 0) == 1:
        return 0
    if total < hard_min or total > hard_max:  # hard outlier cut
        return 0
    if len(flavors) < 2:
        return 0
    return 1


def _upsert_recipe_reference(db, recipe: dict[str, Any], scraped_at: str, content_hash: str, benchmark_eligible: int) -> int:
    existing = db.execute(
        """
        SELECT id FROM recipe_references
        WHERE source = ? AND source_type = ? AND source_recipe_id = ?
        """,
        (recipe["source"], recipe["source_type"], recipe["source_recipe_id"]),
    ).fetchone()

    payload = (
        recipe["device_type"],
        recipe["source_url"],
        recipe["title"],
        recipe.get("author"),
        recipe.get("total_flavour_pct"),
        recipe.get("steep_value"),
        recipe.get("steep_unit"),
        int(recipe.get("is_public") or 0),
        int(recipe.get("is_adapted") or 0),
        int(recipe.get("is_single_flavour") or 0),
        recipe.get("profile_raw"),
        recipe.get("profile_normalized"),
        scraped_at,
        content_hash,
        int(benchmark_eligible),
        int(recipe.get("parse_failed") or 0),
        recipe.get("parse_error"),
        int(recipe.get("needs_review") or 0),
    )
    if existing:
        db.execute(
            """
            UPDATE recipe_references
            SET device_type = ?, source_url = ?, title = ?, author = ?, total_flavour_pct = ?,
                steep_value = ?, steep_unit = ?, is_public = ?, is_adapted = ?, is_single_flavour = ?,
                profile_raw = ?, profile_normalized = ?, scraped_at = ?, content_hash = ?, benchmark_eligible = ?,
                parse_failed = ?, parse_error = ?, needs_review = ?
            WHERE id = ?
            """,
            (*payload, int(existing["id"])),
        )
        return int(existing["id"])

    cur = db.execute(
        """
        INSERT INTO recipe_references (
            source, source_type, device_type, source_url, source_recipe_id, title, author,
            total_flavour_pct, steep_value, steep_unit, is_public, is_adapted, is_single_flavour,
            profile_raw, profile_normalized, scraped_at, content_hash, benchmark_eligible,
            parse_failed, parse_error, needs_review
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            recipe["source"],
            recipe["source_type"],
            recipe["device_type"],
            recipe["source_url"],
            recipe["source_recipe_id"],
            recipe["title"],
            recipe.get("author"),
            recipe.get("total_flavour_pct"),
            recipe.get("steep_value"),
            recipe.get("steep_unit"),
            int(recipe.get("is_public") or 0),
            int(recipe.get("is_adapted") or 0),
            int(recipe.get("is_single_flavour") or 0),
            recipe.get("profile_raw"),
            recipe.get("profile_normalized"),
            scraped_at,
            content_hash,
            int(benchmark_eligible),
            int(recipe.get("parse_failed") or 0),
            recipe.get("parse_error"),
            int(recipe.get("needs_review") or 0),
        ),
    )
    return int(cur.lastrowid)


def _replace_recipe_flavours(db, recipe_reference_id: int, flavors: list[dict[str, Any]], *, resolver_ctx: dict[str, Any]) -> dict[str, int]:
    db.execute("DELETE FROM recipe_reference_flavours WHERE recipe_reference_id = ?", (int(recipe_reference_id),))
    m_exact = 0
    m_alias = 0
    m_norm = 0
    m_fuzzy = 0
    m_unresolved = 0
    for idx, row in enumerate(flavors, start=1):
        brand_raw = (row.get("brand_raw") or "").strip()
        flavor_raw = (row.get("flavour_name_raw") or "").strip()
        ident = _resolve_imported_flavour_identity(
            brand_raw=brand_raw,
            flavour_name_raw=flavor_raw,
            resolver_ctx=resolver_ctx,
        )
        meth = str(ident.get("match_method") or "")
        if meth == "exact_brand_name":
            m_exact += 1
        elif meth == "alias_map":
            m_alias += 1
        elif meth == "normalized_name_unique":
            m_norm += 1
        elif meth == "fuzzy":
            m_fuzzy += 1
        else:
            m_unresolved += 1
        db.execute(
            """
            INSERT INTO recipe_reference_flavours (
                recipe_reference_id, brand_raw, brand_canonical, flavour_name_raw,
                flavour_name_normalized, percentage, position,
                matched_flavor_id, matched_flavor_name, match_method, match_confidence, needs_review,
                identity_matched_at, identity_match_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(recipe_reference_id),
                brand_raw or None,
                ident.get("brand_canonical"),
                flavor_raw,
                ident.get("flavour_name_normalized"),
                float(row.get("percentage") or 0.0),
                idx,
                ident.get("matched_flavor_id"),
                ident.get("matched_flavor_name"),
                ident.get("match_method"),
                ident.get("match_confidence"),
                int(ident.get("needs_review") or 0),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                FLAVOUR_IDENTITY_RESOLVER_VERSION,
            ),
        )
        row["brand_canonical"] = ident.get("brand_canonical")
        row["flavour_name_normalized"] = ident.get("flavour_name_normalized")
        row["matched_flavor_id"] = ident.get("matched_flavor_id")
        row["matched_flavor_name"] = ident.get("matched_flavor_name")
        row["match_method"] = ident.get("match_method")
        row["match_confidence"] = ident.get("match_confidence")
        row["needs_review"] = int(ident.get("needs_review") or 0)
    return {
        "exact_matched_rows": m_exact,
        "alias_matched_rows": m_alias,
        "normalized_matched_rows": m_norm,
        "fuzzy_matched_rows": m_fuzzy,
        "unresolved_rows": m_unresolved,
    }


def resync_imported_recipe_display_names(db) -> dict[str, int]:
    """Rebuild recipe-library-facing imported rows from latest identity matches."""
    reference_lookup = _build_reference_lookup(db)
    stats = _resync_imported_recipe_library_links(db, reference_lookup=reference_lookup)
    db.commit()
    return stats


def fix_recipe_flavor_links_safe(
    db,
    *,
    chunk_size: int = 250,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Critical data-integrity workflow.

    Goal: fix imported recipe ingredient links without "inventing" new flavor_reference entries for unresolved cases.
    Steps:
      1) Safely backfill identity matches for imported recipe_reference_flavours (no recipe_items resync).
      2) Safely relink recipe_items.ingredient_id -> recipe_reference_flavours.matched_flavor_id
         only when the recipe_reference_flavours row order matches recipe_items row order with matching percentages.
    """
    _ensure_reference_tables(db)
    resolver_ctx = _build_identity_resolver_context(db)
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    eps = 0.0006

    # ---------------------------
    # Step 1: resolve identity
    # ---------------------------
    scanned_rows = 0
    updated_rows = 0
    exact = 0
    alias = 0
    normalized_unique = 0
    unresolved = 0
    needs_review = 0
    chunk_count = 0
    db_commit_count = 0
    db_rollback_count = 0
    first_error = ""

    while True:
        rows = db.execute(
            """
            SELECT
                rf.id,
                rf.recipe_reference_id,
                rr.source_recipe_id,
                rf.brand_raw,
                rf.flavour_name_raw,
                COALESCE(rf.identity_match_version, 0) AS identity_match_version
            FROM recipe_reference_flavours rf
            JOIN recipe_references rr ON rr.id = rf.recipe_reference_id
            WHERE rr.source='vc'
              AND rr.source_type='vc_dtl'
              AND (
                rf.matched_flavor_id IS NULL
                OR COALESCE(rf.match_method,'') = ''
                OR COALESCE(rf.needs_review,0) = 1
                OR COALESCE(rf.identity_match_version,0) < ?
              )
            ORDER BY rf.id ASC
            LIMIT ?
            """,
            (int(FLAVOUR_IDENTITY_RESOLVER_VERSION), int(chunk_size)),
        ).fetchall()

        if not rows:
            break

        chunk_count += 1
        for r in rows:
            scanned_rows += 1
            try:
                ident = _resolve_imported_flavour_identity(
                    brand_raw=str(r["brand_raw"] or ""),
                    flavour_name_raw=str(r["flavour_name_raw"] or ""),
                    resolver_ctx=resolver_ctx,
                )
                matched_id = ident.get("matched_flavor_id")
                mm = str(ident.get("match_method") or "")

                if mm == "exact_brand_name":
                    exact += 1
                elif mm == "alias_map":
                    alias += 1
                elif mm == "normalized_name_unique":
                    normalized_unique += 1

                if int(ident.get("needs_review") or 0) == 1 or not matched_id:
                    unresolved += 1
                    needs_review += 1

                if dry_run:
                    continue

                db.execute(
                    """
                    UPDATE recipe_reference_flavours
                    SET brand_canonical = ?,
                        flavour_name_normalized = ?,
                        matched_flavor_id = ?,
                        matched_flavor_name = ?,
                        match_method = ?,
                        match_confidence = ?,
                        needs_review = ?,
                        identity_matched_at = ?,
                        identity_match_version = ?
                    WHERE id = ?
                    """,
                    (
                        ident.get("brand_canonical"),
                        ident.get("flavour_name_normalized"),
                        ident.get("matched_flavor_id"),
                        ident.get("matched_flavor_name"),
                        ident.get("match_method"),
                        ident.get("match_confidence"),
                        int(ident.get("needs_review") or 0),
                        now_ts,
                        FLAVOUR_IDENTITY_RESOLVER_VERSION,
                        int(r["id"]),
                    ),
                )
                updated_rows += 1
            except Exception as exc:
                if not first_error:
                    first_error = f"{type(exc).__name__}: {exc}"
                # Keep scanning. This should not take the whole maintenance workflow down.
                continue

        if dry_run:
            continue
        try:
            db.commit()
            db_commit_count += 1
        except Exception:
            db.rollback()
            db_rollback_count += 1
            if not first_error:
                first_error = "identity_chunk_commit_failed"
            break

    # ---------------------------
    # Step 2: relink recipe_items
    # ---------------------------
    relinked = 0
    already_correct = 0
    unresolved_rows_aligned = 0
    ambiguous_recipes = 0
    re_linked_from_ambiguous = 0
    ingredients_scanned = 0
    relink_sample: list[dict[str, Any]] = []
    ambiguous_samples: list[dict[str, Any]] = []

    last_rr_id = 0
    processed_recipes = 0
    while True:
        rr_rows = db.execute(
            """
            SELECT id, recipe_id
            FROM recipe_references
            WHERE source='vc'
              AND source_type='vc_dtl'
              AND recipe_id IS NOT NULL
              AND id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (int(last_rr_id), int(chunk_size)),
        ).fetchall()
        if not rr_rows:
            break

        last_rr_id = int(rr_rows[-1]["id"])
        processed_recipes += len(rr_rows)

        recipe_ids = [int(x["recipe_id"]) for x in rr_rows]
        placeholders = ",".join(["?"] * len(recipe_ids))

        latest_map: dict[int, int] = {}
        # Latest version_id per recipe_id for this chunk.
        latest_rows = db.execute(
            f"""
            SELECT v.recipe_id, v.id AS version_id
            FROM recipe_versions v
            JOIN (
                SELECT recipe_id, MAX(id) AS max_id
                FROM recipe_versions
                WHERE recipe_id IN ({placeholders})
                GROUP BY recipe_id
            ) m ON m.recipe_id = v.recipe_id AND m.max_id = v.id
            """,
            recipe_ids,
        ).fetchall()
        for lr in latest_rows:
            latest_map[int(lr["recipe_id"])] = int(lr["version_id"])

        for rr in rr_rows:
            rid = int(rr["recipe_id"])
            version_id = latest_map.get(rid)
            if not version_id:
                continue

            rf_rows = db.execute(
                """
                SELECT
                    id,
                    position,
                    matched_flavor_id,
                    matched_flavor_name,
                    brand_raw,
                    flavour_name_raw,
                    percentage
                FROM recipe_reference_flavours
                WHERE recipe_reference_id = ?
                ORDER BY position ASC, id ASC
                """,
                (int(rr["id"]),),
            ).fetchall()

            item_rows = db.execute(
                """
                SELECT id, ingredient_id, amount_percent
                FROM recipe_items
                WHERE version_id = ?
                ORDER BY id ASC
                """,
                (int(version_id),),
            ).fetchall()

            if len(rf_rows) != len(item_rows):
                ambiguous_recipes += 1
                if len(ambiguous_samples) < 5:
                    ambiguous_samples.append(
                        {
                            "recipe_reference_id": int(rr["id"]),
                            "recipe_id": rid,
                            "version_id": int(version_id),
                            "reason": "count_mismatch",
                            "rf_count": len(rf_rows),
                            "item_count": len(item_rows),
                        }
                    )
                continue

            aligned = True
            for a, b in zip(rf_rows, item_rows, strict=False):  # type: ignore[arg-type]
                rf_pct = float(a["percentage"] or 0.0)
                item_pct = float(b["amount_percent"] or 0.0)
                # Reference rows may store flavour % with opposite sign to recipe_items.amount_percent.
                if abs(abs(rf_pct) - item_pct) > eps:
                    aligned = False
                    break

            if not aligned:
                ambiguous_recipes += 1

                # Conservative fallback: relink by percentage “buckets” when row ordering differs.
                # Only relink when all recipe_reference_flavours rows that share the same %
                # resolve to the same canonical matched_flavor_id.
                try:
                    rf_all_resolved = all(
                        (r["matched_flavor_id"] is not None and int(r["matched_flavor_id"] or 0) > 0)
                        for r in rf_rows
                    )
                    if not rf_all_resolved:
                        continue

                    def _pct_key(x: float) -> float:
                        return round(float(x), 4)

                    rf_by_key: dict[float, list[Dict[str, Any]]] = defaultdict(list)
                    it_by_key: dict[float, list[Dict[str, Any]]] = defaultdict(list)
                    for rf in rf_rows:
                        rf_by_key[_pct_key(abs(float(rf["percentage"] or 0.0)))].append(dict(rf))
                    for it in item_rows:
                        it_by_key[_pct_key(float(it["amount_percent"] or 0.0))].append(dict(it))

                    if set(rf_by_key.keys()) != set(it_by_key.keys()):
                        if len(ambiguous_samples) < 5:
                            ambiguous_samples.append(
                                {
                                    "recipe_reference_id": int(rr["id"]),
                                    "recipe_id": rid,
                                    "version_id": int(version_id),
                                    "reason": "bucket_mismatch_keys",
                                    "rf_keys": sorted(list(rf_by_key.keys()))[:10],
                                    "item_keys": sorted(list(it_by_key.keys()))[:10],
                                }
                            )
                        continue

                    # Ensure counts match per bucket.
                    if any(len(rf_by_key[k]) != len(it_by_key[k]) for k in rf_by_key.keys()):
                        if len(ambiguous_samples) < 5:
                            k = next(iter(rf_by_key.keys()))
                            ambiguous_samples.append(
                                {
                                    "recipe_reference_id": int(rr["id"]),
                                    "recipe_id": rid,
                                    "version_id": int(version_id),
                                    "reason": "bucket_count_mismatch",
                                    "sample_bucket": float(k),
                                    "rf_count": len(rf_by_key[k]),
                                    "item_count": len(it_by_key[k]),
                                }
                            )
                        continue

                    can_relink_all_buckets = True
                    planned_updates: list[tuple[int, int]] = []  # (recipe_item_id, target_flavor_id)

                    for k in rf_by_key.keys():
                        rf_group = rf_by_key[k]
                        target_ids = {int(x.get("matched_flavor_id") or 0) for x in rf_group if x.get("matched_flavor_id")}
                        if len(target_ids) != 1:
                            can_relink_all_buckets = False
                            break
                        target_id = next(iter(target_ids))
                        for it in it_by_key.get(k, []):
                            planned_updates.append((int(it["id"]), int(target_id)))

                    if not can_relink_all_buckets:
                        continue

                    if not dry_run:
                        for it_id, target_id in planned_updates:
                            # Update only when different (idempotent).
                            db.execute(
                                "UPDATE recipe_items SET ingredient_id = ? WHERE id = ? AND COALESCE(ingredient_id,0) != ?",
                                (int(target_id), int(it_id), int(target_id)),
                            )
                            re_linked_from_ambiguous += 1
                except Exception:
                    if len(ambiguous_samples) < 5:
                        ambiguous_samples.append(
                            {
                                "recipe_reference_id": int(rr["id"]),
                                "recipe_id": rid,
                                "version_id": int(version_id),
                                "reason": "ambiguous_fallback_exception",
                            }
                        )
                continue

            for rf, it in zip(rf_rows, item_rows, strict=False):  # type: ignore[arg-type]
                ingredients_scanned += 1
                matched_id = int(rf["matched_flavor_id"]) if rf["matched_flavor_id"] else None
                current_ing_id = int(it["ingredient_id"] or 0)
                if matched_id:
                    if current_ing_id == matched_id:
                        already_correct += 1
                    else:
                        if not dry_run:
                            db.execute(
                                "UPDATE recipe_items SET ingredient_id = ? WHERE id = ?",
                                (int(matched_id), int(it["id"])),
                            )
                        relinked += 1
                        if len(relink_sample) < 8:
                            relink_sample.append(
                                {
                                    "recipe_reference_id": int(rr["id"]),
                                    "recipe_id": rid,
                                    "version_id": int(version_id),
                                    "position": int(rf["position"] or 0),
                                    "brand_raw": str(rf["brand_raw"] or ""),
                                    "flavour_name_raw": str(rf["flavour_name_raw"] or ""),
                                    "percentage": float(rf["percentage"] or 0.0),
                                    "before_ingredient_id": current_ing_id,
                                    "after_ingredient_id": int(matched_id),
                                    "matched_flavor_name": str(rf["matched_flavor_name"] or ""),
                                }
                            )
                else:
                    unresolved_rows_aligned += 1

        if dry_run:
            continue

        # Commit per rr chunk so progress is durable.
        try:
            db.commit()
            db_commit_count += 1
        except Exception:
            db.rollback()
            db_rollback_count += 1
            if not first_error:
                first_error = "relink_chunk_commit_failed"
            break

    # ---------------------------
    # Unresolved summary (queue-like)
    # ---------------------------
    unresolved_grouped = db.execute(
        """
        SELECT
            COALESCE(brand_canonical, '') AS brand_canonical,
            COALESCE(flavour_name_normalized, '') AS flavour_name_normalized,
            COUNT(1) AS occurrence_count,
            MIN(COALESCE(flavour_name_raw, '')) AS sample_raw_name
        FROM recipe_reference_flavours rf
        JOIN recipe_references rr ON rr.id = rf.recipe_reference_id
        WHERE rr.source='vc'
          AND rr.source_type='vc_dtl'
          AND (COALESCE(rf.matched_flavor_id,0) = 0 OR COALESCE(rf.needs_review,0)=1)
        GROUP BY COALESCE(brand_canonical, ''), COALESCE(flavour_name_normalized, '')
        ORDER BY occurrence_count DESC
        LIMIT 25
        """,
    ).fetchall()

    unresolved_grouped_sample = [
        {
            "brand_canonical": str(r["brand_canonical"] or ""),
            "flavour_name_normalized": str(r["flavour_name_normalized"] or ""),
            "occurrence_count": int(r["occurrence_count"] or 0),
            "sample_raw_name": str(r["sample_raw_name"] or ""),
        }
        for r in unresolved_grouped
    ]

    return {
        "status": "success",
        "scraper": "recipe_flavor_relink",
        "dry_run": bool(dry_run),
        "chunk_size": int(chunk_size),
        "message": (
            f"Relink complete. identity_scanned={scanned_rows} identity_updated={updated_rows} "
            f"relinked_recipe_items={relinked} already_correct={already_correct} "
            f"aligned_unresolved_rows={unresolved_rows_aligned} ambiguous_recipe_versions={ambiguous_recipes} "
            f"relinked_from_ambiguous={re_linked_from_ambiguous} sample_relinks_count={len(relink_sample)}"
        ),
        "identity_backfill": {
            "scanned_rows": scanned_rows,
            "updated_rows": updated_rows,
            "exact_matched_rows": exact,
            "alias_matched_rows": alias,
            "normalized_matched_rows": normalized_unique,
            "unresolved_rows": unresolved,
            "needs_review_rows": needs_review,
            "chunk_count": chunk_count,
            "db_commit_count": db_commit_count,
            "db_rollback_count": db_rollback_count,
            "first_error": first_error or "",
        },
        "relink_recipe_items": {
            "recipe_references_processed": processed_recipes,
            "ingredients_scanned": ingredients_scanned,
            "already_correct": already_correct,
            "relinked": relinked,
            "unresolved_rows_aligned": unresolved_rows_aligned,
            "ambiguous_recipe_versions": ambiguous_recipes,
            "relinked_from_ambiguous": re_linked_from_ambiguous,
            "sample_relinks": relink_sample,
            "ambiguous_samples": ambiguous_samples,
        },
        "unresolved_grouped_sample": unresolved_grouped_sample,
        "analytics_recompute_recommended": True,
        "analytics_recompute_tables": ["recipe_items", "flavor_recipe_analytics", "flavor_pairings"],
    }


def recompute_vc_dtl_benchmark_stats(db) -> dict[str, Any]:
    rows = db.execute(
        """
        SELECT id, profile_normalized, total_flavour_pct
        FROM recipe_references
        WHERE source = 'vc'
          AND source_type = 'vc_dtl'
          AND device_type = 'DTL'
          AND benchmark_eligible = 1
        """
    ).fetchall()

    by_profile: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_profile[str(r["profile_normalized"] or "other")].append(dict(r))

    db.execute("DELETE FROM recipe_reference_benchmark_stats WHERE source_type = 'vc_dtl' AND device_type = 'DTL'")
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0
    for profile, recipes in by_profile.items():
        ids = [int(r["id"]) for r in recipes]
        totals = [float(r["total_flavour_pct"] or 0.0) for r in recipes]
        if not ids:
            continue
        q_marks = ",".join(["?"] * len(ids))
        flavors = db.execute(
            f"""
            SELECT recipe_reference_id, flavour_name_normalized, percentage
            FROM recipe_reference_flavours
            WHERE recipe_reference_id IN ({q_marks})
            """,
            ids,
        ).fetchall()

        flavor_count_per_recipe: dict[int, int] = defaultdict(int)
        pct_per_flavor: list[float] = []
        sweetener_hits = set()
        cooling_hits = set()
        for fr in flavors:
            rid = int(fr["recipe_reference_id"])
            flavor_count_per_recipe[rid] += 1
            pct = float(fr["percentage"] or 0.0)
            pct_per_flavor.append(pct)
            fname = str(fr["flavour_name_normalized"] or "")
            if detect_has_any_token([fname], ["sweetener", "sucralose", "super sweet"]):
                sweetener_hits.add(rid)
            if detect_has_any_token([fname], ["ice", "ws23", "ws-23", "menthol", "coolada", "koolada"]):
                cooling_hits.add(rid)

        rc = len(ids)
        avg_total = (sum(totals) / rc) if rc else 0.0
        med_total = float(median(totals)) if totals else 0.0
        avg_flavor_count = (sum(flavor_count_per_recipe.values()) / rc) if rc else 0.0
        avg_pct_pf = (sum(pct_per_flavor) / len(pct_per_flavor)) if pct_per_flavor else 0.0
        sweet_rate = (len(sweetener_hits) / rc) if rc else 0.0
        cool_rate = (len(cooling_hits) / rc) if rc else 0.0
        db.execute(
            """
            INSERT INTO recipe_reference_benchmark_stats (
                source_type, device_type, profile_normalized, recipe_count,
                avg_total_flavour_pct, median_total_flavour_pct, avg_flavour_count,
                avg_pct_per_flavour, sweetener_usage_rate, cooling_usage_rate,
                analytics_version, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "vc_dtl",
                "DTL",
                profile,
                rc,
                round(avg_total, 3),
                round(med_total, 3),
                round(avg_flavor_count, 3),
                round(avg_pct_pf, 3),
                round(sweet_rate, 4),
                round(cool_rate, 4),
                ANALYTICS_VERSION,
                now_ts,
            ),
        )
        inserted += 1
    return {"profiles": inserted, "eligible_recipes": len(rows)}


def _run_vc_dtl_scraper_impl(
    db,
    *,
    mode: str = "vc_public_all",
    source_user_id: str = "",
    target_ids: list[str] | None = None,
    resume: bool = True,
    reset_checkpoint: bool = False,
    include_adapted: bool = False,
    delay_s: float = 0.5,
    max_pages: int = 20,
    max_recipes: int = 300,
) -> dict[str, Any]:
    _ensure_reference_tables(db)
    repair_stats = _repair_existing_vc_import_signs(db)
    resolver_ctx_bootstrap = _build_identity_resolver_context(db)
    identity_repair_stats = _backfill_flavour_identity_matches(db, resolver_ctx=resolver_ctx_bootstrap, limit=5000)
    db.commit()
    crawl_cfg = VC_DTL_CONFIG.get("crawl", {})
    mode = (mode or "vc_public_all").strip().lower()
    source_user_id = str(source_user_id or "").strip()
    include_adapted = bool(VC_DTL_CONFIG.get("include_adapted_recipes", include_adapted))
    delay_s = float(crawl_cfg.get("delay_s", delay_s))
    max_pages = int(crawl_cfg.get("max_pages", max_pages))
    max_recipes = int(crawl_cfg.get("max_recipes", max_recipes))
    run_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    started_ts = time.time()
    max_runtime_seconds = int(crawl_cfg.get("max_runtime_seconds", 90))
    discovery_runtime_budget_seconds = int(crawl_cfg.get("discovery_runtime_budget_seconds", 35))
    detail_runtime_budget_seconds = int(crawl_cfg.get("detail_runtime_budget_seconds", 55))
    min_detail_attempts_before_runtime_stop = int(crawl_cfg.get("min_detail_attempts_before_runtime_stop", 10))
    runtime_guard_disabled = bool(crawl_cfg.get("vc_dtl_disable_runtime_guard", False))
    ignore_completed_filter = bool(crawl_cfg.get("vc_dtl_ignore_completed_filter", False))
    reset_discovery_cursor = bool(crawl_cfg.get("vc_dtl_reset_discovery_cursor", False))
    discovery_started_ts = time.time()
    session = requests.Session()
    headers = {"User-Agent": "OldGoatLabScraper/1.0", "Referer": VC_RECIPES_URL}
    scraped_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    discovered: list[str] = []
    page_errors: list[str] = []
    imported_to_library = 0
    reference_lookup = _build_reference_lookup(db)
    resolver_ctx = _build_identity_resolver_context(db)
    can_link_recipe_ref = _has_column(db, "recipe_references", "recipe_id")
    processing_errors: list[str] = []
    stage_debug: list[str] = []
    discovery_trace_sample: list[dict[str, Any]] = []
    discovery_pages_visited = 0
    next_page_detected_count = 0
    next_page_followed_count = 0
    discovery_stop_reason = ""
    discovery_cursor_state = {"mode": "fresh_full_scan", "cursor": None, "resumed": False}
    discovery_cursor_reset_used = reset_discovery_cursor
    source_discovery_counts: dict[str, int] = {
        "listing_pages": 0,
        "public_controller": 0,
        "ajax": 0,
        "sitemap": 0,
        "id_scan": 0,
    }
    known_completed_numeric_ids: list[int] = []
    checkpoint_key_mode = mode
    checkpoint_key_user = source_user_id if mode == "vc_user_profile" else ""
    checkpoint_used = False
    checkpoint = _load_checkpoint(db, mode=checkpoint_key_mode, source_user_id=checkpoint_key_user)
    if reset_checkpoint:
        db.execute(
            "DELETE FROM vc_import_checkpoints WHERE mode = ? AND COALESCE(source_user_id, '') = ?",
            (checkpoint_key_mode, checkpoint_key_user),
        )
        checkpoint = {}
    if resume and checkpoint:
        checkpoint_used = True
    try:
        rows_known = db.execute(
            """
            SELECT source_recipe_id
            FROM recipe_references
            WHERE source = 'vc' AND source_type = 'vc_dtl'
            """
        ).fetchall()
        for rk in rows_known:
            sid = str(rk["source_recipe_id"] or "").strip()
            if sid.isdigit():
                known_completed_numeric_ids.append(int(sid))
    except Exception:
        known_completed_numeric_ids = []

    if mode == "vc_targeted_ids":
        for rid in (target_ids or []):
            rid_s = str(rid or "").strip()
            if rid_s.isdigit():
                discovered.append(f"https://vceliquidrecipes.com/recipe/{rid_s}")
        source_discovery_counts["listing_pages"] = len(discovered)
        discovery_stop_reason = "targeted_ids_supplied"
    elif mode == "vc_user_profile" and source_user_id:
        start_page = int(checkpoint.get("cursor_page") or 1) if checkpoint_used else 1
        links = _fetch_links_for_user_profile(
            session, headers, user_id=source_user_id, max_recipes=max_recipes, start_page=start_page
        )
        for lk in links:
            if lk not in discovered:
                discovered.append(lk)
        source_discovery_counts["public_controller"] = len(discovered)
        discovery_pages_visited = max(1, len(links) // 10)
        discovery_stop_reason = "user_profile_discovery_complete" if links else "user_profile_no_links"
    else:
        empty_pages_streak = 0
        for page in range(1, max_pages + 1):
            if (not runtime_guard_disabled) and ((time.time() - discovery_started_ts) > discovery_runtime_budget_seconds):
                discovery_stop_reason = "discovery_runtime_budget_reached"
                break
            try:
                time.sleep(max(0.1, delay_s))
                url = VC_RECIPES_URL if page == 1 else f"{VC_RECIPES_URL}?page={page}"
                res = session.get(url, headers=headers, timeout=35)
                discovery_pages_visited += 1
                if res.status_code >= 300:
                    continue
                links = _extract_recipe_links(res.text)
                for lk in links:
                    if lk not in discovered:
                        discovered.append(lk)
                source_discovery_counts["listing_pages"] = len(discovered)
                if not links:
                    empty_pages_streak += 1
                    if empty_pages_streak >= 3 and page > 1:
                        discovery_stop_reason = "listing_three_consecutive_empty_pages"
                        break
                else:
                    empty_pages_streak = 0
                if len(discovered) >= max_recipes:
                    discovery_stop_reason = "max_recipes_reached_from_listing"
                    break
            except Exception as exc:
                page_errors.append(f"list_page_{page}: {exc}")

    if mode == "vc_public_all" and len(discovered) < max_recipes and (runtime_guard_disabled or (time.time() - discovery_started_ts) <= discovery_runtime_budget_seconds):
        before_len = len(discovered)
        controller_links = _fetch_links_via_public_controller(session, headers, max_recipes=max_recipes)
        for lk in controller_links:
            if lk not in discovered:
                discovered.append(lk)
        source_discovery_counts["public_controller"] = max(0, len(discovered) - before_len)
        if source_discovery_counts["public_controller"] > 0:
            stage_debug.append(f"controller_discovery_added={source_discovery_counts['public_controller']}")

    if mode == "vc_public_all" and len(discovered) < max_recipes and (runtime_guard_disabled or (time.time() - discovery_started_ts) <= discovery_runtime_budget_seconds):
        # Ajax/datatable fallback for full released recipe feed.
        try:
            before_len = len(discovered)
            base_html = session.get(VC_RECIPES_URL, headers=headers, timeout=35).text
            ajax_links = _fetch_links_via_ajax(session, headers, base_html, max_recipes=max_recipes)
            for lk in ajax_links:
                if lk not in discovered:
                    discovered.append(lk)
            source_discovery_counts["ajax"] = max(0, len(discovered) - before_len)
        except Exception as exc:
            page_errors.append(f"ajax_discovery: {exc}")

    if mode == "vc_public_all" and len(discovered) < max_recipes and (runtime_guard_disabled or (time.time() - discovery_started_ts) <= discovery_runtime_budget_seconds):
        before_len = len(discovered)
        sitemap_links = _fetch_links_via_sitemap(session, headers, max_recipes=max_recipes)
        for lk in sitemap_links:
            if lk not in discovered:
                discovered.append(lk)
        source_discovery_counts["sitemap"] = max(0, len(discovered) - before_len)

    if mode == "vc_public_all" and len(discovered) < max_recipes and (runtime_guard_disabled or (time.time() - discovery_started_ts) <= discovery_runtime_budget_seconds):
        id_scan_cfg = crawl_cfg.get("id_scan", {}) if isinstance(crawl_cfg.get("id_scan"), dict) else {}
        if bool(id_scan_cfg.get("enabled", True)):
            configured_end = int(id_scan_cfg.get("end_id", 120000))
            if known_completed_numeric_ids:
                hinted_end = max(known_completed_numeric_ids) + int(crawl_cfg.get("id_scan_end_hint_offset", 20000))
                dynamic_end = min(configured_end, hinted_end)
            else:
                dynamic_end = configured_end
            before_len = len(discovered)
            scan_links = _fetch_links_via_id_scan(
                session,
                headers,
                max_recipes=max_recipes,
                start_id=int(id_scan_cfg.get("start_id", 1)),
                end_id=max(1, int(dynamic_end)),
                request_budget=int(id_scan_cfg.get("request_budget", 400)),
                max_consecutive_misses=int(id_scan_cfg.get("max_consecutive_misses", 120)),
                timeout_seconds=int(id_scan_cfg.get("timeout_seconds", 8)),
            )
            for lk in scan_links:
                if lk not in discovered:
                    discovered.append(lk)
            source_discovery_counts["id_scan"] = max(0, len(discovered) - before_len)

    # Seed-neighborhood fallback runs last and only with remaining discovery budget.
    if (
        mode == "vc_public_all"
        and
        len(discovered) < max_recipes
        and len(discovered) > 0
        and (runtime_guard_disabled or (time.time() - discovery_started_ts) <= discovery_runtime_budget_seconds)
    ):
        seed_ids = []
        for lk in discovered[:120]:
            rid = _extract_recipe_id(lk)
            if rid.isdigit():
                seed_ids.append(int(rid))
        if seed_ids:
            before_len = len(discovered)
            near_links = _fetch_links_near_seed_ids(
                session,
                headers,
                seed_ids=seed_ids,
                max_recipes=max_recipes,
                scan_radius=int(crawl_cfg.get("seed_scan_radius", 1200)),
                request_budget=int(crawl_cfg.get("seed_scan_request_budget", 220)),
                timeout_seconds=int(crawl_cfg.get("seed_scan_timeout_seconds", 6)),
            )
            for lk in near_links:
                if lk not in discovered:
                    discovered.append(lk)
            source_discovery_counts["id_scan"] += max(0, len(discovered) - before_len)

    discovered = discovered[:max_recipes]
    discovered_recipe_links_total = len(discovered)
    canonical_urls: list[str] = []
    seen_ids: set[str] = set()
    first_20_detail_urls: list[str] = []
    first_20_recipe_ids: list[str] = []
    normalized_queue: list[dict[str, str]] = []
    invalid_queued_items = 0
    invalid_queue_samples: list[dict[str, Any]] = []
    for raw in discovered:
        url = _canonical_detail_url(raw)
        rid = _extract_recipe_id(raw)
        if not url or not rid:
            invalid_queued_items += 1
            if len(invalid_queue_samples) < 20:
                invalid_queue_samples.append({"raw": raw, "reason": "missing_recipe_id_or_url"})
            continue
        if rid in seen_ids:
            continue
        seen_ids.add(rid)
        canonical_urls.append(url)
        normalized_queue.append({"source_recipe_id": rid, "detail_url": url})
        if len(first_20_detail_urls) < 20:
            first_20_detail_urls.append(url)
        if len(first_20_recipe_ids) < 20:
            first_20_recipe_ids.append(rid)

    unique_ids = len(seen_ids)
    queued_detail_pages = len(normalized_queue)
    # Progression guard for partial runs:
    # avoid reprocessing already-successful VC DTL recipes every run.
    completed_ids = {
        str(r["source_recipe_id"])
        for r in db.execute(
            """
            SELECT source_recipe_id
            FROM recipe_references
            WHERE source = 'vc'
              AND source_type = 'vc_dtl'
              AND COALESCE(parse_failed, 0) = 0
              AND COALESCE(needs_review, 0) = 0
            """
        ).fetchall()
        if r["source_recipe_id"] is not None
    }
    completed_ids_loaded = len(completed_ids)
    skipped_already_completed = 0
    discovered_recipe_links_existing = sum(1 for q in normalized_queue if str(q.get("source_recipe_id") or "") in completed_ids)
    discovered_recipe_links_new = max(0, len(normalized_queue) - discovered_recipe_links_existing)
    if completed_ids and not ignore_completed_filter:
        pre_len = len(normalized_queue)
        normalized_queue = [q for q in normalized_queue if str(q.get("source_recipe_id") or "") not in completed_ids]
        skipped_already_completed = max(0, pre_len - len(normalized_queue))
        first_20_detail_urls = [q.get("detail_url", "") for q in normalized_queue[:20]]
        first_20_recipe_ids = [str(q.get("source_recipe_id") or "") for q in normalized_queue[:20]]
    queued_detail_pages = len(normalized_queue)
    queued_after_completed_filter = queued_detail_pages
    if not discovery_stop_reason:
        if discovered_recipe_links_total <= 10:
            discovery_stop_reason = "small_discovery_set_detected"
        else:
            discovery_stop_reason = "discovery_complete"
    stage_debug.append(f"discovered={len(discovered)} unique_ids={unique_ids} queued={queued_detail_pages}")
    discovery_elapsed_seconds = round(time.time() - discovery_started_ts, 3)
    detail_started_ts = time.time()
    detail_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    remaining_budget_at_detail_start = max(0.0, float(detail_runtime_budget_seconds) - 0.0)

    parsed_ok = 0
    broken = 0
    parse_failed = 0
    zero_flavour = 0
    excluded_pod = 0
    excluded_single = 0
    stored = 0
    benchmark_yes = 0
    per_recipe_flavour_rows: list[dict[str, Any]] = []
    validated_recipes = 0
    stored_flavour_rows = 0
    reference_upsert_failures = 0
    flavour_upsert_failures = 0
    library_import_failures = 0
    first_library_import_error = ""
    library_import_attempted = 0
    library_import_skipped = 0
    db_commit_count = 0
    db_rollback_count = 0
    persistence_trace_sample: list[dict[str, Any]] = []
    successful_recipe_ids: list[str] = []
    import_sum_check_sample: list[dict[str, Any]] = []
    post_store_verification_sample: list[dict[str, Any]] = []
    imported_flavour_rows = 0
    exact_matched_rows = 0
    alias_matched_rows = 0
    fuzzy_matched_rows = 0
    unresolved_rows = 0
    needs_review_rows = 0
    flavour_match_sample: list[dict[str, Any]] = []

    # Stage-B counters (hard contract)
    detail_attempted = 0
    detail_succeeded = 0
    detail_failed_before_parse = 0
    detail_skipped_due_to_global_stop = 0
    detail_http_errors = 0
    runtime_stop_triggered_at_index = 0
    pre_parse_skip_reason_counts: dict[str, int] = defaultdict(int)
    detail_trace_sample: list[dict[str, Any]] = []
    stop_reason = ""
    fatal_stage = ""

    stage_debug.append(f"detail_loop_start queued={queued_detail_pages}")
    for idx, q in enumerate(normalized_queue, start=1):
        rid = str(q.get("source_recipe_id") or "").strip()
        link = str(q.get("detail_url") or "").strip()
        trace_ref: dict[str, Any] | None = None

        # First-20 progressive trace object.
        if len(detail_trace_sample) < 20:
            trace_ref = {
                "index": idx,
                "source_recipe_id": rid,
                "detail_url": link,
                "loop_entered": True,
                "pre_parse_validation_passed": False,
                "pre_parse_skip_reason": "",
                "detail_attempt_counter_after_increment": detail_attempted,
                "fetch_started": False,
                "parser_entered": False,
                "http_status": "",
                "final_url": "",
                "body_len": "",
                "body_empty": "",
                "flavour_rows_found": "",
                "reason": "",
                "last_step": "TRACE_CREATED",
                "exception_type": "",
                "exception_message": "",
            }
            detail_trace_sample.append(trace_ref)

        # Per-item pre-parse validation only.
        pre_reason = ""
        if not rid:
            pre_reason = "missing_recipe_id"
        elif not link:
            pre_reason = "missing_detail_url"
        elif not link.startswith("https://vceliquidrecipes.com/recipe/"):
            pre_reason = "malformed_detail_url"

        if pre_reason:
            detail_failed_before_parse += 1
            pre_parse_skip_reason_counts[pre_reason] += 1
            if trace_ref is not None:
                trace_ref["pre_parse_validation_passed"] = False
                trace_ref["pre_parse_skip_reason"] = pre_reason
                trace_ref["reason"] = pre_reason
                trace_ref["last_step"] = "PRE_PARSE_REJECTED"
            continue

        # Item is structurally valid; count attempt immediately.
        detail_attempted += 1
        if trace_ref is not None:
            trace_ref["pre_parse_validation_passed"] = True
            trace_ref["detail_attempt_counter_after_increment"] = detail_attempted
            trace_ref["last_step"] = "ATTEMPT_INCREMENTED"

        # Global stop is not treated as per-item failure fanout.
        detail_elapsed = (time.time() - detail_started_ts)
        if (
            (not runtime_guard_disabled)
            and detail_attempted >= max(1, min_detail_attempts_before_runtime_stop)
            and detail_elapsed > float(detail_runtime_budget_seconds)
        ):
            detail_skipped_due_to_global_stop = max(0, queued_detail_pages - (idx - 1))
            runtime_stop_triggered_at_index = idx
            if not stop_reason:
                stop_reason = "runtime_stop_after_partial_detail_batch"
            if trace_ref is not None:
                trace_ref["pre_parse_skip_reason"] = "runtime_stop"
                trace_ref["reason"] = "runtime_stop_after_partial_detail_batch"
                trace_ref["last_step"] = "RUNTIME_STOP"
            break

        parser_entered = False
        try:
            time.sleep(max(0.1, delay_s))
            if trace_ref is not None:
                trace_ref["fetch_started"] = True
                trace_ref["last_step"] = "FETCH_STARTING"
            res = session.get(link, headers=headers, timeout=35)
            http_status = int(res.status_code)
            body = res.text or ""
            body_len = len(body)
            if trace_ref is not None:
                trace_ref["http_status"] = http_status
                trace_ref["final_url"] = str(res.url)
                trace_ref["body_len"] = body_len
                trace_ref["body_empty"] = (body_len == 0)
                trace_ref["last_step"] = "FETCH_RETURNED"
            if http_status >= 300:
                detail_http_errors += 1
                broken += 1
                parse_failed += 1
                _upsert_recipe_reference(
                    db,
                    {
                        "source": "vc",
                        "source_type": "vc_dtl",
                        "device_type": "DTL",
                        "source_url": link,
                        "source_recipe_id": rid,
                        "title": f"VC Recipe {rid}",
                        "author": "",
                        "total_flavour_pct": None,
                        "steep_value": None,
                        "steep_unit": None,
                        "is_public": 1,
                        "is_adapted": 0,
                        "is_single_flavour": 0,
                        "profile_raw": "[]",
                        "profile_normalized": "other",
                        "parse_failed": 1,
                        "parse_error": f"http_{http_status}",
                        "needs_review": 1,
                    },
                    scraped_at,
                    compute_content_hash({"id": rid, "url": link, "parse_error": f"http_{http_status}"}),
                    0,
                )
                if len(detail_trace_sample) < 20:
                    if trace_ref is not None:
                        trace_ref["parser_entered"] = parser_entered
                        trace_ref["body_snippet"] = body[:300]
                        trace_ref["flavour_rows_found"] = 0
                        trace_ref["reason"] = "http_error"
                        trace_ref["last_step"] = "HTTP_ERROR"
                continue

            parser_entered = True
            if trace_ref is not None:
                trace_ref["parser_entered"] = True
                trace_ref["last_step"] = "PARSER_STARTING"
            parsed = _parse_recipe_page(link, body)
            if trace_ref is not None:
                trace_ref["last_step"] = "PARSER_RETURNED"
            if not parsed:
                broken += 1
                parse_failed += 1
                _upsert_recipe_reference(
                    db,
                    {
                        "source": "vc",
                        "source_type": "vc_dtl",
                        "device_type": "DTL",
                        "source_url": link,
                        "source_recipe_id": rid,
                        "title": f"VC Recipe {rid}",
                        "author": "",
                        "total_flavour_pct": None,
                        "steep_value": None,
                        "steep_unit": None,
                        "is_public": 1,
                        "is_adapted": 0,
                        "is_single_flavour": 0,
                        "profile_raw": "[]",
                        "profile_normalized": "other",
                        "parse_failed": 1,
                        "parse_error": "detail_parse_failed",
                        "needs_review": 1,
                    },
                    scraped_at,
                    compute_content_hash({"id": rid, "url": link, "parse_error": "detail_parse_failed"}),
                    0,
                )
                if len(detail_trace_sample) < 20:
                    if trace_ref is not None:
                        trace_ref["body_snippet"] = body[:300]
                        trace_ref["flavour_rows_found"] = 0
                        trace_ref["reason"] = "parse_returned_none"
                continue

            flavor_count = len(parsed.get("flavours") or [])
            per_recipe_flavour_rows.append({"source_recipe_id": parsed.get("source_recipe_id"), "flavour_rows": flavor_count})
            if trace_ref is not None:
                trace_ref["flavour_rows_found"] = flavor_count
            if flavor_count == 0:
                zero_flavour += 1
                parse_failed += 1
                parsed["parse_failed"] = 1
                parsed["parse_error"] = "zero_flavour_rows"
                parsed["needs_review"] = 1
                _upsert_recipe_reference(
                    db,
                    parsed,
                    scraped_at,
                    compute_content_hash({"id": parsed.get("source_recipe_id"), "title": parsed.get("title"), "parse_error": "zero_flavour_rows"}),
                    0,
                )
                if len(detail_trace_sample) < 20:
                    if trace_ref is not None:
                        trace_ref["body_snippet"] = body[:300]
                        trace_ref["reason"] = "zero_flavour_rows"
                continue

            if not parsed.get("title") or parsed.get("total_flavour_pct") in (None, ""):
                parse_failed += 1
                parsed["parse_failed"] = 1
                parsed["parse_error"] = "missing_required_fields"
                parsed["needs_review"] = 1
                _upsert_recipe_reference(
                    db,
                    parsed,
                    scraped_at,
                    compute_content_hash({"id": parsed.get("source_recipe_id"), "title": parsed.get("title"), "parse_error": "missing_required_fields"}),
                    0,
                )
                if len(detail_trace_sample) < 20:
                    if trace_ref is not None:
                        trace_ref["body_snippet"] = body[:300]
                        trace_ref["reason"] = "missing_required_fields"
                continue

            validated_recipes += 1
            parsed_ok += 1
            detail_succeeded += 1

            # DTL filter AFTER detail parsing (reliable stage)
            blob = " ".join([parsed.get("title", ""), parsed.get("profile_raw", "")]).lower()
            if detect_has_any_token([blob], ["pod", "dispo", "salt nic pod"]):
                excluded_pod += 1
                continue
            if int(parsed.get("is_single_flavour") or 0) == 1:
                excluded_single += 1
                continue

            content_hash = compute_content_hash(
                {
                    "id": parsed.get("source_recipe_id"),
                    "title": parsed.get("title"),
                    "author": parsed.get("author"),
                    "total_flavour_pct": parsed.get("total_flavour_pct"),
                    "profile_normalized": parsed.get("profile_normalized"),
                    "flavours": parsed.get("flavours"),
                    "created_text": parsed.get("created_text"),
                    "description_notes": parsed.get("description_notes"),
                }
            )
            eligible = _is_benchmark_eligible(parsed, include_adapted=include_adapted)
            if eligible == 1:
                benchmark_yes += 1

            p_trace: dict[str, Any] = {
                "source_recipe_id": str(parsed.get("source_recipe_id") or rid),
                "title": parsed.get("title") or "",
                "validated": True,
                "reference_upserted": False,
                "flavour_rows_written": 0,
                "library_import_attempted": True,
                "library_imported": False,
                "commit_completed": False,
                "error": "",
            }
            try:
                rrid = _upsert_recipe_reference(db, parsed, scraped_at, content_hash, eligible)
                p_trace["reference_upserted"] = True
                _record_provenance(
                    db,
                    recipe_reference_id=int(rrid),
                    scope=("user_profile" if mode == "vc_user_profile" else "public_all"),
                    source_user_id=source_user_id if mode == "vc_user_profile" else "",
                    source_user_url=(f"https://vceliquidrecipes.com/user/{source_user_id}" if mode == "vc_user_profile" and source_user_id else ""),
                    discovered_via=("user_profile_page" if mode == "vc_user_profile" else "public_controller"),
                )
            except Exception as exc:
                reference_upsert_failures += 1
                p_trace["error"] = f"reference_upsert_failed: {type(exc).__name__}: {exc}"
                if len(processing_errors) < 30:
                    processing_errors.append(f"{link}: {p_trace['error']}")
                if len(persistence_trace_sample) < 10:
                    persistence_trace_sample.append(p_trace)
                continue

            try:
                match_stats = _replace_recipe_flavours(db, rrid, parsed.get("flavours") or [], resolver_ctx=resolver_ctx)
                flavour_rows_written = len(parsed.get("flavours") or [])
                p_trace["flavour_rows_written"] = flavour_rows_written
                stored_flavour_rows += flavour_rows_written
                imported_flavour_rows += flavour_rows_written
                exact_matched_rows += int(match_stats.get("exact_matched_rows") or 0)
                alias_matched_rows += int(match_stats.get("alias_matched_rows") or 0)
                fuzzy_matched_rows += int(match_stats.get("fuzzy_matched_rows") or 0)
                unresolved_rows += int(match_stats.get("unresolved_rows") or 0)
                needs_review_rows += sum([int(x.get("needs_review") or 0) for x in (parsed.get("flavours") or [])])
                for fr in (parsed.get("flavours") or []):
                    if len(flavour_match_sample) >= 12:
                        break
                    flavour_match_sample.append(
                        {
                            "source_recipe_id": str(parsed.get("source_recipe_id") or rid),
                            "brand_raw": str(fr.get("brand_raw") or ""),
                            "flavour_name_raw": str(fr.get("flavour_name_raw") or ""),
                            "brand_canonical": str(fr.get("brand_canonical") or ""),
                            "flavour_name_normalized": str(fr.get("flavour_name_normalized") or ""),
                            "matched_flavor_id": fr.get("matched_flavor_id"),
                            "matched_flavor_name": str(fr.get("matched_flavor_name") or ""),
                            "match_method": str(fr.get("match_method") or ""),
                            "match_confidence": float(fr.get("match_confidence") or 0.0),
                            "needs_review": int(fr.get("needs_review") or 0),
                        }
                    )
            except Exception as exc:
                flavour_upsert_failures += 1
                p_trace["error"] = f"flavour_upsert_failed: {type(exc).__name__}: {exc}"
                if len(processing_errors) < 30:
                    processing_errors.append(f"{link}: {p_trace['error']}")
                try:
                    db.rollback()
                    db_rollback_count += 1
                except Exception:
                    pass
                if len(persistence_trace_sample) < 10:
                    persistence_trace_sample.append(p_trace)
                continue

            # protect library import: only valid parsed recipes
            try:
                library_import_attempted += 1
                lib_sync = _sync_into_recipe_library(db, parsed, reference_lookup=reference_lookup, actor_id=None)
                lib_recipe_id = int(lib_sync.get("recipe_id") or 0)
                # More accurate count of persisted recipe_items in library versions.
                p_trace["flavour_rows_written"] = int(lib_sync.get("written_items") or p_trace["flavour_rows_written"])
                if lib_recipe_id:
                    imported_to_library += 1
                    p_trace["library_imported"] = True
                    if can_link_recipe_ref:
                        db.execute("UPDATE recipe_references SET recipe_id = ? WHERE id = ?", (lib_recipe_id, int(rrid)))
                else:
                    library_import_skipped += 1
                    library_import_failures += 1
                    if not first_library_import_error:
                        first_library_import_error = "library_import_returned_empty_id"
                    p_trace["error"] = first_library_import_error
            except Exception as exc:
                library_import_failures += 1
                if not first_library_import_error:
                    first_library_import_error = f"{type(exc).__name__}: {exc}"
                p_trace["error"] = f"library_import_failed: {type(exc).__name__}: {exc}"
                if len(processing_errors) < 30:
                    processing_errors.append(f"{link}: {p_trace['error']}")
                try:
                    db.rollback()
                    db_rollback_count += 1
                except Exception:
                    pass
                if len(persistence_trace_sample) < 10:
                    persistence_trace_sample.append(p_trace)
                continue

            # Invariant: recipe item percentages must be non-negative.
            if int(p_trace.get("flavour_rows_written") or 0) > 0:
                neg_row = db.execute(
                    """
                    SELECT COUNT(1) AS c
                    FROM recipe_items ri
                    JOIN recipe_versions v ON v.id = ri.version_id
                    JOIN recipes r ON r.id = v.recipe_id
                    WHERE r.recipe_code = ?
                      AND ri.amount_percent < 0
                    """,
                    (f"VCREF-{parsed.get('source_recipe_id')}",),
                ).fetchone()
                if int((neg_row["c"] if neg_row else 0) or 0) > 0:
                    parsed["needs_review"] = 1
                    parsed["parse_error"] = "negative_percent_detected"
                    _upsert_recipe_reference(
                        db,
                        parsed,
                        scraped_at,
                        content_hash,
                        0,
                    )
                    p_trace["error"] = "negative_percent_detected"

            row_sum = sum([abs(float(x.get("percentage") or 0.0)) for x in (parsed.get("flavours") or [])])
            total_stored = abs(float(parsed.get("total_flavour_pct") or 0.0))
            if abs(row_sum - total_stored) > 0.25:
                parsed["needs_review"] = 1
                parsed["parse_error"] = "sum_total_mismatch"
                _upsert_recipe_reference(
                    db,
                    parsed,
                    scraped_at,
                    content_hash,
                    0,
                )
            if len(import_sum_check_sample) < 10:
                import_sum_check_sample.append(
                    {
                        "source_recipe_id": str(parsed.get("source_recipe_id") or rid),
                        "flavours": [
                            {
                                "name": str(x.get("flavour_name_raw") or ""),
                                "brand": str(x.get("brand_raw") or ""),
                                "percentage": abs(float(x.get("percentage") or 0.0)),
                            }
                            for x in (parsed.get("flavours") or [])[:15]
                        ],
                        "sum_pct": round(row_sum, 4),
                        "stored_total_flavour_pct": round(total_stored, 4),
                        "sum_mismatch": abs(row_sum - total_stored) > 0.25,
                    }
                )
                import_sum_check_sample[-1]["parsed_real_flavour_row_count"] = int(len(parsed.get("flavours") or []))
                import_sum_check_sample[-1]["excluded_summary_row_count"] = int(parsed.get("excluded_summary_row_count") or 0)
                import_sum_check_sample[-1]["excluded_rows_sample"] = list(parsed.get("excluded_rows_sample") or [])[:6]

            try:
                db.commit()
                db_commit_count += 1
                p_trace["commit_completed"] = True
                stored += 1
                successful_recipe_ids.append(str(parsed.get("source_recipe_id") or rid))
                if len(post_store_verification_sample) < 10:
                    vrow = {
                        "source_recipe_id": str(parsed.get("source_recipe_id") or rid),
                        "reference_exists": False,
                        "flavour_row_count": 0,
                        "fake_total_row_present": False,
                        "library_entry_exists": False,
                        "library_entry_title": "",
                        "verification_error": "",
                    }
                    try:
                        rr_chk = db.execute(
                            "SELECT id, recipe_id, title FROM recipe_references WHERE source='vc' AND source_type='vc_dtl' AND source_recipe_id = ?",
                            (str(parsed.get("source_recipe_id") or rid),),
                        ).fetchone()
                        if rr_chk:
                            vrow["reference_exists"] = True
                            fr_cnt = db.execute(
                                "SELECT COUNT(1) AS c FROM recipe_reference_flavours WHERE recipe_reference_id = ?",
                                (int(rr_chk["id"]),),
                            ).fetchone()
                            vrow["flavour_row_count"] = int((fr_cnt["c"] if fr_cnt else 0) or 0)
                            fake = db.execute(
                                """
                                SELECT COUNT(1) AS c
                                FROM recipe_reference_flavours
                                WHERE recipe_reference_id = ?
                                  AND (LOWER(COALESCE(flavour_name_raw, '')) LIKE '%total flavour%'
                                       OR LOWER(COALESCE(flavour_name_raw, '')) LIKE '%total flavor%')
                                """,
                                (int(rr_chk["id"]),),
                            ).fetchone()
                            vrow["fake_total_row_present"] = int((fake["c"] if fake else 0) or 0) > 0
                            if rr_chk["recipe_id"]:
                                lib_chk = db.execute("SELECT id, name FROM recipes WHERE id = ?", (int(rr_chk["recipe_id"]),)).fetchone()
                                if lib_chk:
                                    vrow["library_entry_exists"] = True
                                    vrow["library_entry_title"] = lib_chk["name"] or ""
                    except Exception as vex:
                        vrow["verification_error"] = f"{type(vex).__name__}: {vex}"
                    post_store_verification_sample.append(vrow)
            except Exception as exc:
                p_trace["error"] = f"commit_failed: {type(exc).__name__}: {exc}"
                if len(processing_errors) < 30:
                    processing_errors.append(f"{link}: {p_trace['error']}")
                try:
                    db.rollback()
                    db_rollback_count += 1
                except Exception:
                    pass
                if len(persistence_trace_sample) < 10:
                    persistence_trace_sample.append(p_trace)
                continue

            if len(persistence_trace_sample) < 10:
                persistence_trace_sample.append(p_trace)

            if trace_ref is not None:
                trace_ref["body_snippet"] = body[:300]
                trace_ref["reason"] = "ok"
                trace_ref["last_step"] = "ITEM_COMPLETED"

        except Exception as exc:
            broken += 1
            parse_failed += 1
            detail_http_errors += 1
            if len(processing_errors) < 30:
                processing_errors.append(f"{link}: {exc}")
            pre_parse_skip_reason_counts["per_item_exception_before_fetch"] += 1
            if trace_ref is not None:
                trace_ref["reason"] = "per_item_exception_before_fetch"
                trace_ref["exception_type"] = type(exc).__name__
                trace_ref["exception_message"] = str(exc)
                trace_ref["last_step"] = "ITEM_EXCEPTION"

    stats = recompute_vc_dtl_benchmark_stats(db)
    stage_debug.append(f"parsed_ok={parsed_ok} parse_failed={parse_failed} zero_flavour={zero_flavour} stored={stored}")

    # Run invariants / fatal stage checks
    partial_run = False
    run_status = "success"
    if queued_detail_pages > 0 and detail_attempted == 0:
        run_status = "error"
        fatal_stage = "detail_stage"
        stop_reason = stop_reason or "detail_stage_deadline_expired_before_fetch"
        processing_errors.append("Detail parsing never executed after queue creation")
    elif queued_detail_pages > 0 and parsed_ok == 0:
        # not fatal, but should be explicit
        if not stop_reason:
            stop_reason = "no_successful_detail_parses"

    if detail_skipped_due_to_global_stop > 0:
        partial_run = True
        if run_status != "error":
            run_status = "partial"

    if stored == 0 and imported_to_library == 0 and detail_succeeded == 0:
        if stop_reason.startswith("runtime_stop") or stop_reason.startswith("detail_stage_deadline"):
            run_status = "error"
            if not fatal_stage:
                fatal_stage = "detail_stage"
            if detail_attempted <= 1 and stop_reason == "runtime_stop_after_partial_detail_batch":
                stop_reason = "runtime_stop_before_first_fetch"
    if queued_detail_pages == 0 and discovered_recipe_links_total > 0 and skipped_already_completed == discovered_recipe_links_total:
        run_status = "no_new_work"
        if not stop_reason:
            stop_reason = "all_discovered_ids_already_completed"
    if queued_detail_pages > 0 and detail_attempted == 0 and detail_failed_before_parse == queued_detail_pages:
        stage_debug.append("All queued recipes skipped before fetch; inspect pre_parse_skip_reason distribution")
        pre_parse_skip_reason_counts["unknown_pre_parse_skip"] += 1
    total_completed_ids = int(
        (
            db.execute(
                "SELECT COUNT(DISTINCT source_recipe_id) AS c FROM recipe_references WHERE source = 'vc' AND source_type = 'vc_dtl' AND COALESCE(parse_failed,0)=0"
            ).fetchone()
            or {"c": 0}
        )["c"]
        or 0
    )
    newly_discovered_ids_this_run = max(0, discovered_recipe_links_new)
    no_new_ids_runs_in_a_row = int(checkpoint.get("no_new_ids_runs_in_a_row") or 0)
    if newly_discovered_ids_this_run <= 0:
        no_new_ids_runs_in_a_row += 1
    else:
        no_new_ids_runs_in_a_row = 0
    _save_checkpoint(
        db,
        mode=checkpoint_key_mode,
        source_user_id=checkpoint_key_user,
        cursor_page=int(checkpoint.get("cursor_page") or 1) + 1,
        last_recipe_id=(first_20_recipe_ids[0] if first_20_recipe_ids else ""),
        total_discovered=int(discovered_recipe_links_total),
        total_imported=total_completed_ids,
        no_new_ids_runs_in_a_row=no_new_ids_runs_in_a_row,
        last_new_id_count=newly_discovered_ids_this_run,
    )
    db.commit()
    db_commit_count += 1
    detail_elapsed_seconds = round(time.time() - detail_started_ts, 3)
    runtime_budget_seconds = max_runtime_seconds if not runtime_guard_disabled else 0
    post_run_verification_sample: list[dict[str, Any]] = []
    for sid in successful_recipe_ids[:5]:
        row = {
            "source_recipe_id": sid,
            "recipe_reference_exists": False,
            "recipe_reference_flavour_count": 0,
            "recipe_library_entry_exists": False,
            "recipe_library_title": "",
            "verification_error": "",
        }
        try:
            rr = db.execute(
                "SELECT id, recipe_id, title FROM recipe_references WHERE source = 'vc' AND source_type = 'vc_dtl' AND source_recipe_id = ?",
                (sid,),
            ).fetchone()
            if rr:
                row["recipe_reference_exists"] = True
                row["recipe_library_title"] = rr["title"] or ""
                cnt = db.execute(
                    "SELECT COUNT(1) AS c FROM recipe_reference_flavours WHERE recipe_reference_id = ?",
                    (int(rr["id"]),),
                ).fetchone()
                row["recipe_reference_flavour_count"] = int((cnt["c"] if cnt else 0) or 0)
                lib_row = db.execute("SELECT id, name FROM recipes WHERE id = ?", (int(rr["recipe_id"] or 0),)).fetchone() if rr["recipe_id"] else None
                if lib_row:
                    row["recipe_library_entry_exists"] = True
                    row["recipe_library_title"] = lib_row["name"] or row["recipe_library_title"]
        except Exception as exc:
            row["verification_error"] = f"{type(exc).__name__}: {exc}"
        post_run_verification_sample.append(row)
    return {
        "status": run_status,
        "source": "vc",
        "source_type": "vc_dtl",
        "device_type": "DTL",
        "discovered_recipe_links": len(discovered),
        "discovered_recipe_links_total": discovered_recipe_links_total,
        "discovered_recipe_links_existing": discovered_recipe_links_existing,
        "discovered_recipe_links_new": discovered_recipe_links_new,
        "parsed_recipes": parsed_ok,
        "validated_recipes": validated_recipes,
        "parse_failed": parse_failed,
        "zero_flavour": zero_flavour,
        "queued_detail_pages": queued_detail_pages,
        "queued_after_completed_filter": queued_after_completed_filter,
        "unique_recipe_ids": unique_ids,
        "stored_recipes": stored,
        "stored_flavour_rows": stored_flavour_rows,
        "imported_flavour_rows": imported_flavour_rows,
        "exact_matched_rows": exact_matched_rows,
        "alias_matched_rows": alias_matched_rows,
        "fuzzy_matched_rows": fuzzy_matched_rows,
        "unresolved_rows": unresolved_rows,
        "needs_review_rows": needs_review_rows,
        "imported_to_recipe_library": imported_to_library,
        "library_import_attempted": library_import_attempted,
        "library_import_skipped": library_import_skipped,
        "library_import_failures": library_import_failures,
        "first_library_import_error": first_library_import_error,
        "reference_upsert_failures": reference_upsert_failures,
        "flavour_upsert_failures": flavour_upsert_failures,
        "db_commit_count": db_commit_count,
        "db_rollback_count": db_rollback_count,
        "detail_attempted": detail_attempted,
        "detail_succeeded": detail_succeeded,
        "detail_failed_before_parse": detail_failed_before_parse,
        "detail_skipped_due_to_global_stop": detail_skipped_due_to_global_stop,
        "detail_http_errors": detail_http_errors,
        "benchmark_eligible": benchmark_yes,
        "excluded_pod": excluded_pod,
        "excluded_single_flavour": excluded_single,
        "broken_or_incomplete": broken,
        "include_adapted": bool(include_adapted),
        "hard_thresholds": {
            "min_total_pct": VC_DTL_CONFIG.get("thresholds", {}).get("hard_min_total_flavour_pct", 3.0),
            "max_total_pct": VC_DTL_CONFIG.get("thresholds", {}).get("hard_max_total_flavour_pct", 30.0),
            "preferred_clean_range": list(VC_DTL_CONFIG.get("thresholds", {}).get("preferred_clean_range", (6.0, 20.0))),
        },
        "benchmark_stats": stats,
        "errors": (page_errors + processing_errors)[:30],
        "debug": stage_debug[:20],
        "invalid_queued_items": invalid_queued_items,
        "skipped_already_completed": skipped_already_completed,
        "completed_ids_loaded": completed_ids_loaded,
        "discovery_pages_visited": discovery_pages_visited,
        "discovery_trace_sample": discovery_trace_sample[:12],
        "discovery_stop_reason": discovery_stop_reason,
        "next_page_detected_count": next_page_detected_count,
        "next_page_followed_count": next_page_followed_count,
        "source_discovery_counts": source_discovery_counts,
        "discovery_cursor_state": discovery_cursor_state,
        "discovery_cursor_reset_used": discovery_cursor_reset_used,
        "ignore_completed_filter": ignore_completed_filter,
        "invalid_queue_samples": invalid_queue_samples[:20],
        "pre_parse_skip_reason_counts": dict(pre_parse_skip_reason_counts),
        "per_recipe_flavour_rows": per_recipe_flavour_rows[:100],
        "first_20_detail_urls": first_20_detail_urls,
        "first_20_recipe_ids": first_20_recipe_ids,
        "detail_trace_sample": detail_trace_sample[:20],
        "persistence_trace_sample": persistence_trace_sample[:10],
        "post_run_verification_sample": post_run_verification_sample[:5],
        "stop_reason": stop_reason,
        "fatal_stage": fatal_stage,
        "partial_run": partial_run,
        "run_started_at": run_started_at,
        "discovery_started_at": datetime.fromtimestamp(discovery_started_ts).strftime("%Y-%m-%d %H:%M:%S"),
        "detail_started_at": detail_started_at,
        "runtime_budget_seconds": runtime_budget_seconds,
        "discovery_elapsed_seconds": discovery_elapsed_seconds,
        "detail_elapsed_seconds": detail_elapsed_seconds,
        "remaining_budget_at_detail_start": remaining_budget_at_detail_start,
        "runtime_stop_triggered_at_index": runtime_stop_triggered_at_index,
        "min_detail_attempts_before_runtime_stop": min_detail_attempts_before_runtime_stop,
        "runtime_guard_disabled": runtime_guard_disabled,
        "repair_stats": repair_stats,
        "identity_repair_stats": identity_repair_stats,
        "import_sum_check_sample": import_sum_check_sample[:10],
        "post_store_verification_sample": post_store_verification_sample[:10],
        "flavour_match_sample": flavour_match_sample[:12],
        "mode": mode,
        "source_user_id": source_user_id,
        "checkpoint_used": checkpoint_used,
        "checkpoint_reset_used": reset_checkpoint,
        "total_known_recipe_ids": discovered_recipe_links_total,
        "newly_discovered_ids_this_run": newly_discovered_ids_this_run,
        "total_completed_ids": total_completed_ids,
        "queued_this_run": queued_detail_pages,
        "attempted_this_run": detail_attempted,
        "succeeded_this_run": detail_succeeded,
        "stored_this_run": stored,
        "imported_to_library_this_run": imported_to_library,
        "cumulative_imported_total": total_completed_ids,
        "remaining_estimated_ids": max(0, discovered_recipe_links_total - total_completed_ids),
        "no_new_ids_runs_in_a_row": no_new_ids_runs_in_a_row,
        "last_new_id_count": newly_discovered_ids_this_run,
        "saturation_likely": no_new_ids_runs_in_a_row >= 3,
        "runtime_seconds": round(time.time() - started_ts, 2),
    }


def run_vc_dtl_scraper(
    db,
    *,
    mode: str = "vc_public_all",
    source_user_id: str = "",
    target_ids: list[str] | None = None,
    resume: bool = True,
    reset_checkpoint: bool = False,
    include_adapted: bool = False,
    delay_s: float = 0.5,
    max_pages: int = 20,
    max_recipes: int = 300,
) -> dict[str, Any]:
    try:
        return _run_vc_dtl_scraper_impl(
            db,
            mode=mode,
            source_user_id=source_user_id,
            target_ids=target_ids,
            resume=resume,
            reset_checkpoint=reset_checkpoint,
            include_adapted=include_adapted,
            delay_s=delay_s,
            max_pages=max_pages,
            max_recipes=max_recipes,
        )
    except Exception as exc:
        tb = traceback.extract_tb(exc.__traceback__)
        frame = tb[-1] if tb else None
        loc = f"{frame.filename}:{frame.lineno}" if frame else "unknown"
        return {
            "status": "error",
            "scraper": "vc_dtl",
            "fatal_stage": "top_level_exception",
            "stop_reason": str(exc),
            "errors": [f"{type(exc).__name__}: {exc}"],
            "debug": [f"trace={loc}"],
            "detail_attempted": 0,
            "detail_succeeded": 0,
            "detail_failed_before_parse": 0,
            "detail_http_errors": 0,
            "queued_detail_pages": 0,
            "parsed_recipes": 0,
            "parse_failed": 0,
            "zero_flavour": 0,
            "stored_recipes": 0,
            "imported_to_recipe_library": 0,
            "first_20_detail_urls": [],
            "first_20_recipe_ids": [],
            "detail_trace_sample": [],
            "runtime_seconds": 0.0,
            "source": "vc",
            "source_type": "vc_dtl",
            "device_type": "DTL",
        }
