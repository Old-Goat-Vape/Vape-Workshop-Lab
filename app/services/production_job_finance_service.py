"""Production Job costs, purchases with spend, and profitability."""

from __future__ import annotations

from typing import Any

from db import get_db
from services import production_job_service as pjs
from services import production_pay_service as pay


def _row(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    return {k: row[k] for k in row.keys()}


def update_job_revenue(
    *,
    job_id: int,
    revenue_mode: str,
    revenue_amount: float,
    revenue_note: str = "",
) -> None:
    mode = (revenue_mode or "none").strip().lower()
    if mode not in ("none", "estimated", "actual", "customer_order"):
        mode = "none"
    db = get_db()
    db.execute(
        """
        UPDATE production_jobs
        SET revenue_mode = ?, revenue_amount = ?, revenue_note = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (mode, float(revenue_amount or 0), (revenue_note or "").strip() or None, int(job_id)),
    )
    db.commit()


def update_job_packaging_overrides(
    *,
    job_id: int,
    bottle_cost_override: float | None,
    label_cost_override: float | None,
) -> None:
    db = get_db()
    db.execute(
        """
        UPDATE production_jobs
        SET bottle_cost_override = ?, label_cost_override = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (
            float(bottle_cost_override) if bottle_cost_override is not None else None,
            float(label_cost_override) if label_cost_override is not None else None,
            int(job_id),
        ),
    )
    db.commit()


def update_job_referral(
    *,
    job_id: int,
    referral_label: str,
    referral_mode: str,
    referral_value: float,
) -> None:
    mode = (referral_mode or "none").strip().lower()
    if mode not in ("none", "fixed", "percent"):
        mode = "none"
    db = get_db()
    db.execute(
        """
        UPDATE production_jobs
        SET referral_label = ?, referral_mode = ?, referral_value = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (
            (referral_label or "").strip() or None,
            mode,
            float(referral_value or 0),
            int(job_id),
        ),
    )
    db.commit()


def add_job_expense(
    *,
    job_id: int,
    category: str,
    description: str,
    amount: float,
    note: str = "",
    recorded_by_user_id: int | None,
) -> int:
    amt = float(amount)
    if amt <= 0:
        raise ValueError("Expense amount must be positive.")
    cat = (category or "other").strip().lower() or "other"
    desc = (description or "").strip()
    if not desc:
        raise ValueError("Expense description is required.")
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO production_job_expenses (
            production_job_id, category, description, amount, expense_date, note, recorded_by_user_id, created_at
        ) VALUES (?, ?, ?, ?, datetime('now'), ?, ?, datetime('now'))
        """,
        (
            int(job_id),
            cat,
            desc,
            amt,
            (note or "").strip() or None,
            int(recorded_by_user_id) if recorded_by_user_id is not None else None,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def list_job_expenses(job_id: int) -> list[dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT e.*, u.username AS recorded_by_username
        FROM production_job_expenses e
        LEFT JOIN users u ON u.id = e.recorded_by_user_id
        WHERE e.production_job_id = ?
        ORDER BY e.expense_date DESC, e.id DESC
        """,
        (int(job_id),),
    ).fetchall()
    return [_row(r) for r in rows]


def record_job_purchase(
    *,
    job_id: int,
    inventory_id: int,
    quantity_ml: float,
    recorded_by_user_id: int | None,
    pack_count: float | None = None,
    pack_size_ml: float | None = None,
    pack_price: float | None = None,
    total_paid: float | None = None,
    source: str = "",
    note: str = "",
    pack_info: str = "",
) -> None:
    qty = float(quantity_ml)
    if qty <= 0:
        raise ValueError("Purchase quantity must be positive.")
    paid = float(total_paid) if total_paid is not None else 0.0
    if paid <= 0 and pack_count and pack_price:
        paid = float(pack_count) * float(pack_price)
    db = get_db()
    inv = db.execute(
        "SELECT * FROM production_job_inventory WHERE id = ? AND production_job_id = ?",
        (int(inventory_id), int(job_id)),
    ).fetchone()
    if not inv:
        raise ValueError("Inventory row not found for this job.")
    db.execute(
        """
        INSERT INTO production_job_inventory_purchases (
            production_job_id, inventory_id, quantity_ml, note, pack_info,
            recorded_by_user_id, created_at,
            pack_count, pack_size_ml, pack_price, total_paid, source, purchased_at
        ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            int(job_id),
            int(inventory_id),
            qty,
            (note or "").strip() or None,
            (pack_info or "").strip() or None,
            int(recorded_by_user_id) if recorded_by_user_id is not None else None,
            float(pack_count) if pack_count is not None else None,
            float(pack_size_ml) if pack_size_ml is not None else None,
            float(pack_price) if pack_price is not None else None,
            paid,
            (source or "").strip() or None,
        ),
    )
    db.execute(
        """
        UPDATE production_job_inventory
        SET purchased_ml = purchased_ml + ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (qty, int(inventory_id)),
    )
    db.commit()


def set_planned_purchase_ml(*, inventory_id: int, planned_ml: float) -> None:
    db = get_db()
    db.execute(
        """
        UPDATE production_job_inventory
        SET planned_purchase_ml = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (float(planned_ml or 0), int(inventory_id)),
    )
    db.commit()


def list_purchase_transactions(job_id: int) -> list[dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT p.*, i.display_name, i.ingredient_type, u.username AS recorded_by_username
        FROM production_job_inventory_purchases p
        JOIN production_job_inventory i ON i.id = p.inventory_id
        LEFT JOIN users u ON u.id = p.recorded_by_user_id
        WHERE p.production_job_id = ?
        ORDER BY COALESCE(p.purchased_at, p.created_at) DESC, p.id DESC
        """,
        (int(job_id),),
    ).fetchall()
    return [_row(r) for r in rows]


def _effective_cost_per_ml(inventory_id: int) -> float:
    """Average R/ml from actual purchase spend on this inventory row."""
    db = get_db()
    row = db.execute(
        """
        SELECT COALESCE(SUM(total_paid), 0) AS spend,
               COALESCE(SUM(quantity_ml), 0) AS qty
        FROM production_job_inventory_purchases
        WHERE inventory_id = ?
        """,
        (int(inventory_id),),
    ).fetchone()
    spend = float(row["spend"] or 0)
    qty = float(row["qty"] or 0)
    if qty <= 0 or spend <= 0:
        return 0.0
    return spend / qty


def ingredients_consumed_cost(job_id: int) -> dict[str, Any]:
    inventory = pjs.list_job_inventory(job_id)
    lines = []
    total = 0.0
    cash_spend = 0.0
    remaining_value = 0.0
    for inv in inventory:
        rate = _effective_cost_per_ml(int(inv["id"]))
        used = float(inv.get("used_ml") or 0)
        purchased = float(inv.get("purchased_ml") or 0)
        remaining = max(purchased - used, 0.0)
        used_cost = used * rate
        rem_value = remaining * rate
        # Cash spend from purchase transactions
        db = get_db()
        spend_row = db.execute(
            "SELECT COALESCE(SUM(total_paid), 0) AS s FROM production_job_inventory_purchases WHERE inventory_id = ?",
            (int(inv["id"]),),
        ).fetchone()
        spend = float(spend_row["s"] or 0)
        cash_spend += spend
        remaining_value += rem_value
        total += used_cost
        lines.append(
            {
                **inv,
                "cost_per_ml": rate,
                "consumed_cost": used_cost,
                "cash_spend": spend,
                "remaining_stock_value": rem_value,
            }
        )
    return {
        "lines": lines,
        "ingredients_consumed_cost": total,
        "cash_spent_on_inventory": cash_spend,
        "remaining_job_stock_value": remaining_value,
    }


def packaging_costs(job_id: int) -> dict[str, Any]:
    job = pjs.get_job(job_id) or {}
    recipes = pjs.list_job_recipes(job_id)
    completed_bottles = sum(float(r.get("completed_bottles") or 0) for r in recipes)
    # Defaults align with BatchCostInputs.bottle_cost / label_cost field defaults.
    default_bottle = 5.0
    default_label = 4.0
    bottle_unit = (
        float(job["bottle_cost_override"])
        if job.get("bottle_cost_override") is not None
        else default_bottle
    )
    label_unit = (
        float(job["label_cost_override"])
        if job.get("label_cost_override") is not None
        else default_label
    )
    bottles_cost = completed_bottles * bottle_unit
    labels_cost = completed_bottles * label_unit
    return {
        "completed_bottles": completed_bottles,
        "bottle_unit_cost": bottle_unit,
        "label_unit_cost": label_unit,
        "bottles_cost": bottles_cost,
        "labels_cost": labels_cost,
    }


def referral_cost(job_id: int, *, revenue: float) -> dict[str, Any]:
    job = pjs.get_job(job_id) or {}
    mode = str(job.get("referral_mode") or "none").lower()
    value = float(job.get("referral_value") or 0)
    label = job.get("referral_label") or ""
    amount = 0.0
    if mode == "fixed":
        amount = value
    elif mode == "percent":
        amount = float(revenue) * (value / 100.0)
    return {"label": label, "mode": mode, "value": value, "amount": amount}


def job_financial_summary(job_id: int) -> dict[str, Any]:
    job = pjs.get_job(job_id) or {}
    revenue = float(job.get("revenue_amount") or 0) if str(job.get("revenue_mode") or "none") != "none" else 0.0
    ingredients = ingredients_consumed_cost(job_id)
    packaging = packaging_costs(job_id)
    pay_sum = pay.job_production_pay_summary(job_id)
    referral = referral_cost(job_id, revenue=revenue)
    expenses = list_job_expenses(job_id)
    expense_by_cat: dict[str, float] = {}
    other_total = 0.0
    shipping_packing = 0.0
    for e in expenses:
        cat = str(e.get("category") or "other").lower()
        amt = float(e.get("amount") or 0)
        expense_by_cat[cat] = expense_by_cat.get(cat, 0.0) + amt
        if cat in ("shipping", "packing", "courier", "cartons"):
            shipping_packing += amt
        else:
            other_total += amt

    production_pay = float(pay_sum.get("profit_production_pay") or 0)
    fulfilment = (
        float(ingredients["ingredients_consumed_cost"])
        + float(packaging["bottles_cost"])
        + float(packaging["labels_cost"])
        + production_pay
        + float(referral["amount"])
        + shipping_packing
        + other_total
    )
    profit = revenue - fulfilment
    return {
        "job": job,
        "revenue": revenue,
        "revenue_mode": job.get("revenue_mode") or "none",
        "ingredients_consumed": float(ingredients["ingredients_consumed_cost"]),
        "ingredient_lines": ingredients["lines"],
        "bottles_cost": float(packaging["bottles_cost"]),
        "labels_cost": float(packaging["labels_cost"]),
        "packaging": packaging,
        "production_pay": production_pay,
        "owner_labour": float(pay_sum.get("owner_labour") or 0),
        "referral": referral,
        "shipping_packing": shipping_packing,
        "other_costs": other_total,
        "expenses": expenses,
        "expense_by_category": expense_by_cat,
        "total_fulfilment_cost": fulfilment,
        "old_goat_profit": profit,
        "cash_spent_on_inventory": float(ingredients["cash_spent_on_inventory"]),
        "remaining_job_stock_value": float(ingredients["remaining_job_stock_value"]),
        "production_pay_detail": pay_sum,
    }
