"""Configurable production-pay rates (finished bottles). Source of truth."""

from __future__ import annotations

from typing import Any

from db import get_db

# Fallback defaults only if the rates table is empty / unavailable.
DEFAULT_FINISHED_BOTTLE_RATES = {
    120.0: 5.0,
    30.0: 3.0,
}


def list_finished_bottle_rates() -> list[dict[str, Any]]:
    db = get_db()
    try:
        rows = db.execute(
            "SELECT bottle_size_ml, rate_zar, updated_at FROM production_pay_rates ORDER BY bottle_size_ml"
        ).fetchall()
        if rows:
            return [
                {
                    "bottle_size_ml": float(r["bottle_size_ml"]),
                    "rate_zar": float(r["rate_zar"]),
                    "updated_at": r["updated_at"],
                }
                for r in rows
            ]
    except Exception:
        pass
    return [
        {"bottle_size_ml": size, "rate_zar": rate, "updated_at": None}
        for size, rate in sorted(DEFAULT_FINISHED_BOTTLE_RATES.items())
    ]


def rate_for_bottle_size_ml(bottle_size_ml: float) -> float | None:
    """Exact size match against configured rates. None if no rate configured."""
    size = float(bottle_size_ml)
    for row in list_finished_bottle_rates():
        if abs(float(row["bottle_size_ml"]) - size) < 1e-6:
            return float(row["rate_zar"])
    return None


def upsert_finished_bottle_rate(*, bottle_size_ml: float, rate_zar: float) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO production_pay_rates (bottle_size_ml, rate_zar, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(bottle_size_ml) DO UPDATE SET
            rate_zar = excluded.rate_zar,
            updated_at = datetime('now')
        """,
        (float(bottle_size_ml), float(rate_zar)),
    )
    db.commit()
