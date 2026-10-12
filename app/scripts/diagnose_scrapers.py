"""Live diagnostic for Lab price scrapers — network + parse probes (no full crawl)."""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime

import requests

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html,*/*",
}


def _looks_like_challenge(text: str, status: int) -> str | None:
    low = (text or "")[:4000].lower()
    if status in (403, 429, 503):
        if "cloudflare" in low or "cf-ray" in low or "just a moment" in low:
            return f"HTTP {status} Cloudflare/challenge page"
        return f"HTTP {status}"
    if "just a moment" in low or "cf-browser-verification" in low:
        return "Cloudflare challenge HTML"
    if "attention required" in low and "cloudflare" in low:
        return "Cloudflare attention page"
    return None


def probe_blck() -> dict:
    url = "https://blck.co.za/products.json?limit=5&page=1"
    t0 = time.perf_counter()
    try:
        res = requests.get(url, headers=HEADERS_BROWSER, timeout=20, allow_redirects=True)
        body = res.text
        challenge = _looks_like_challenge(body, res.status_code)
        parsed = 0
        sample = None
        if res.ok and not challenge:
            try:
                data = res.json()
                products = data.get("products") or []
                parsed = len(products)
                if products:
                    p = products[0]
                    sample = {
                        "title": p.get("title"),
                        "vendor": p.get("vendor"),
                        "price": (p.get("variants") or [{}])[0].get("price"),
                        "handle": p.get("handle"),
                    }
            except Exception as exc:
                challenge = f"JSON parse failed: {exc}"
        return {
            "source": "blck",
            "url": url,
            "final_url": str(res.url),
            "status": res.status_code,
            "content_type": res.headers.get("Content-Type"),
            "content_length": len(body),
            "parsed_products": parsed,
            "challenge": challenge,
            "sample": sample,
            "ms": round((time.perf_counter() - t0) * 1000),
            "ok": bool(res.ok and not challenge and parsed > 0),
        }
    except Exception as exc:
        return {
            "source": "blck",
            "url": url,
            "status": None,
            "error": f"{type(exc).__name__}: {exc}",
            "ok": False,
            "ms": round((time.perf_counter() - t0) * 1000),
        }


def probe_clyrolinx() -> dict:
    url = "https://clyrolinx.co.za/wp-json/wc/store/products?per_page=5&page=1"
    t0 = time.perf_counter()
    headers = {
        **HEADERS_BROWSER,
        "User-Agent": "OldGoatLabScraper/1.0 (+https://clyrolinx.co.za/)",
    }
    try:
        res = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
        body = res.text
        challenge = _looks_like_challenge(body, res.status_code)
        parsed = 0
        sample = None
        if res.ok and not challenge:
            try:
                data = res.json()
                if isinstance(data, list):
                    parsed = len(data)
                    if data:
                        p = data[0]
                        prices = p.get("prices") or {}
                        sample = {
                            "name": p.get("name"),
                            "permalink": p.get("permalink"),
                            "price_minor": prices.get("price"),
                            "currency_minor_unit": prices.get("currency_minor_unit"),
                        }
                else:
                    challenge = f"Unexpected JSON type: {type(data).__name__}"
            except Exception as exc:
                challenge = f"JSON parse failed: {exc}; body_prefix={body[:200]!r}"
        return {
            "source": "clyrolinx",
            "url": url,
            "final_url": str(res.url),
            "status": res.status_code,
            "content_type": res.headers.get("Content-Type"),
            "content_length": len(body),
            "parsed_products": parsed,
            "challenge": challenge,
            "sample": sample,
            "ms": round((time.perf_counter() - t0) * 1000),
            "ok": bool(res.ok and not challenge and parsed > 0),
        }
    except Exception as exc:
        return {
            "source": "clyrolinx",
            "url": url,
            "status": None,
            "error": f"{type(exc).__name__}: {exc}",
            "ok": False,
            "ms": round((time.perf_counter() - t0) * 1000),
        }


def probe_vc_flavours() -> dict:
    url = "https://vceliquidrecipes.com/flavours"
    t0 = time.perf_counter()
    try:
        res = requests.get(url, headers=HEADERS_BROWSER, timeout=30, allow_redirects=True)
        body = res.text
        challenge = _looks_like_challenge(body, res.status_code)
        has_table = "datatableFlavours" in body or "datatable" in body.lower()
        ajax_hits = body.lower().count("ajax")
        return {
            "source": "vc_flavours",
            "url": url,
            "final_url": str(res.url),
            "status": res.status_code,
            "content_type": res.headers.get("Content-Type"),
            "content_length": len(body),
            "has_datatable_marker": has_table,
            "ajax_mentions": ajax_hits,
            "challenge": challenge,
            "ms": round((time.perf_counter() - t0) * 1000),
            "ok": bool(res.ok and not challenge and len(body) > 500),
        }
    except Exception as exc:
        return {
            "source": "vc_flavours",
            "url": url,
            "status": None,
            "error": f"{type(exc).__name__}: {exc}",
            "ok": False,
            "ms": round((time.perf_counter() - t0) * 1000),
        }


def probe_vc_dtl() -> dict:
    url = "https://vceliquidrecipes.com/recipes"
    t0 = time.perf_counter()
    try:
        res = requests.get(url, headers=HEADERS_BROWSER, timeout=30, allow_redirects=True)
        body = res.text
        challenge = _looks_like_challenge(body, res.status_code)
        return {
            "source": "vc_dtl",
            "url": url,
            "final_url": str(res.url),
            "status": res.status_code,
            "content_type": res.headers.get("Content-Type"),
            "content_length": len(body),
            "challenge": challenge,
            "ms": round((time.perf_counter() - t0) * 1000),
            "ok": bool(res.ok and not challenge and len(body) > 500),
        }
    except Exception as exc:
        return {
            "source": "vc_dtl",
            "url": url,
            "status": None,
            "error": f"{type(exc).__name__}: {exc}",
            "ok": False,
            "ms": round((time.perf_counter() - t0) * 1000),
        }


def main() -> int:
    print("=== Scraper network probes ===", datetime.now().isoformat(timespec="seconds"))
    results = [probe_blck(), probe_clyrolinx(), probe_vc_flavours(), probe_vc_dtl()]
    for r in results:
        print(json.dumps(r, indent=2, default=str))
        print("---")
    print("\nSummary table:")
    print(f"{'Source':12} {'OK':5} {'HTTP':6} {'Parsed':8} {'Failure'}")
    for r in results:
        print(
            f"{r.get('source','?'):12} "
            f"{str(r.get('ok')):5} "
            f"{str(r.get('status')):6} "
            f"{str(r.get('parsed_products', '-')):8} "
            f"{r.get('challenge') or r.get('error') or ''}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
