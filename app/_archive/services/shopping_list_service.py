from __future__ import annotations

import logging
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from db import get_db


logger = logging.getLogger(__name__)


def _existing_columns(table: str) -> set[str]:
    db = get_db()
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _qty_col_for_library_items() -> str:
    """Support both legacy schemas and the requested 'quantity_ml' naming."""
    cols = _existing_columns("library_items")
    if "quantity_ml" in cols:
        return "quantity_ml"
    # Current app schema uses current_stock
    if "current_stock" in cols:
        return "current_stock"
    # Fallback: best-effort
    return "current_stock"


def fetch_low_stock_items(
    *,
    thresholds: Dict[str, float],
    default_threshold_ml: float = 10.0,
) -> List[Dict[str, Any]]:
    """Return low-stock inventory rows joined with flavor_reference metadata.

    thresholds: {category: threshold_ml}
    """
    db = get_db()
    qty_col = _qty_col_for_library_items()

    rows = db.execute(
        f"""
        SELECT
            l.id AS library_item_id,
            l.reference_id,
            l.{qty_col} AS quantity_ml,
            r.name,
            r.brand,
            r.category,
            COALESCE(r.product_url, r.url) AS product_url,
            r.price_raw,
            r.source
        FROM library_items l
        JOIN flavor_reference r ON r.id = l.reference_id
        WHERE l.active = 1
        """,
    ).fetchall()

    out: List[Dict[str, Any]] = []
    seen_ref: set[int] = set()
    for r in rows:
        d = dict(r)
        ref_id = int(d.get("reference_id") or 0)
        if not ref_id or ref_id in seen_ref:
            continue
        seen_ref.add(ref_id)

        cat = str(d.get("category") or "Flavor")
        thresh = float(thresholds.get(cat, default_threshold_ml))
        qty = float(d.get("quantity_ml") or 0.0)
        if qty < thresh:
            d["threshold_ml"] = thresh
            out.append(d)

    return out


def generate_shopping_list_table(
    *,
    low_stock_items: List[Dict[str, Any]],
    target_quantities: Optional[Dict[int, float]] = None,
) -> Any:
    """Return a shopping list table.

    - If pandas is available, returns a DataFrame.
    - Otherwise returns a list[dict].

    target_quantities: {reference_id: target_quantity_ml}
    """
    target_quantities = target_quantities or {}

    rows: List[Dict[str, Any]] = []
    for item in low_stock_items:
        ref_id = int(item.get("reference_id") or 0)
        cur_qty = float(item.get("quantity_ml") or 0.0)
        target_qty = float(target_quantities.get(ref_id, item.get("threshold_ml") or 10.0))

        rows.append(
            {
                "Flavor": item.get("name"),
                "Brand": item.get("brand"),
                "Category": item.get("category"),
                "Current Quantity": cur_qty,
                "Target Quantity": target_qty,
                "Price": float(item.get("price_raw") or 0.0),
                "Supplier URL": item.get("product_url") or "",
                "Supplier": item.get("brand") or "",
                "reference_id": ref_id,
            }
        )

    # Sort by supplier then ascending quantity
    rows.sort(key=lambda r: (str(r.get("Supplier") or ""), float(r.get("Current Quantity") or 0.0)))

    try:
        import pandas as pd  # type: ignore

        df = pd.DataFrame(rows)
        # keep reference_id for later actions, but move it to the end
        cols = [c for c in df.columns if c != "reference_id"] + ["reference_id"]
        return df[cols]
    except Exception:
        return rows


def open_product_url(url: str) -> bool:
    """Open a single product URL in the user's browser."""
    u = (url or "").strip()
    if not u:
        return False
    try:
        webbrowser.open_new_tab(u)
        return True
    except Exception:
        return False


def open_all_for_supplier(items: Iterable[Dict[str, Any]], supplier: str) -> int:
    """Open all URLs for a supplier in new tabs. Returns count opened."""
    count = 0
    for it in items:
        if str(it.get("Supplier") or it.get("brand") or "") != supplier:
            continue
        url = it.get("Supplier URL") or it.get("product_url") or ""
        if open_product_url(str(url)):
            count += 1
    return count


def _log_shopping_list_generated(*, user_id: int, count: int) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO audit_logs (actor_id, action, target_table, target_id, details, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            "SHOPPING_LIST_GENERATED",
            "library_items",
            0,
            f"Low-stock shopping list generated: {count} items",
            datetime.now(),
        ),
    )


def generate_shopping_list(
    user_id: int,
    thresholds: Dict[str, float],
    *,
    default_threshold_ml: float = 10.0,
    target_quantities: Optional[Dict[int, float]] = None,
) -> Any:
    """Main entrypoint.

    Returns a DataFrame (if pandas available) or list-of-dicts table.
    Logs to audit_logs.
    """
    low = fetch_low_stock_items(thresholds=thresholds, default_threshold_ml=default_threshold_ml)
    table = generate_shopping_list_table(low_stock_items=low, target_quantities=target_quantities)

    try:
        _log_shopping_list_generated(user_id=int(user_id), count=len(low))
        get_db().commit()
    except Exception as exc:
        get_db().rollback()
        logger.warning("Failed to log shopping list: %s", exc)

    return table

