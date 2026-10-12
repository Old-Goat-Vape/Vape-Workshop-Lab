"""Production pay credits and payment ledger."""

from __future__ import annotations

from typing import Any

from config.production_pay_rates import rate_for_bottle_size_ml
from db import get_db


def _row(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    return {k: row[k] for k in row.keys()}


def labour_kind_for_user(user_id: int) -> str:
    """Owner labour vs helper labour based on existing user identity."""
    db = get_db()
    row = db.execute(
        "SELECT username, role FROM users WHERE id = ?",
        (int(user_id),),
    ).fetchone()
    if not row:
        return "helper"
    username = str(row["username"] or "").strip().lower()
    role = str(row["role"] or "").strip()
    if username in ("old goat", "wynand") or role == "Old Goat":
        return "owner"
    return "helper"


def post_finished_bottle_credit_for_batch(*, batch_id: int) -> dict[str, Any] | None:
    """Idempotent: one finished_bottle credit per batch+user. Returns credit row or None."""
    db = get_db()
    batch = db.execute(
        "SELECT * FROM production_job_batches WHERE id = ?",
        (int(batch_id),),
    ).fetchone()
    if not batch:
        return None
    if str(batch["status"]) != "completed":
        return None
    if int(batch["credit_eligible"] if "credit_eligible" in batch.keys() else 1) != 1:
        return None

    bottles = float(batch["completed_bottles"] or 0)
    if bottles <= 0:
        return None

    # Bottle size from locked job recipe.
    jr = db.execute(
        "SELECT target_bottle_size_ml FROM production_job_recipes WHERE id = ?",
        (int(batch["production_job_recipe_id"]),),
    ).fetchone()
    if not jr:
        return None
    size = float(jr["target_bottle_size_ml"] or 0)
    rate = rate_for_bottle_size_ml(size)
    if rate is None:
        return None

    user_id = int(batch["operator_user_id"] or 0)
    if user_id <= 0:
        return None

    existing = db.execute(
        """
        SELECT * FROM production_job_credits
        WHERE production_job_batch_id = ?
          AND credit_type = 'finished_bottle'
          AND user_id = ?
        LIMIT 1
        """,
        (int(batch_id), user_id),
    ).fetchone()
    if existing:
        return _row(existing)

    amount = bottles * float(rate)
    kind = labour_kind_for_user(user_id)
    cur = db.execute(
        """
        INSERT INTO production_job_credits (
            production_job_id, production_job_batch_id, user_id,
            credit_type, quantity, unit, rate, amount, status,
            labour_kind, credit_eligible, created_at, notes
        ) VALUES (?, ?, ?, 'finished_bottle', ?, 'bottle', ?, ?, 'earned', ?, 1, datetime('now'), ?)
        """,
        (
            int(batch["production_job_id"]),
            int(batch_id),
            user_id,
            bottles,
            float(rate),
            amount,
            kind,
            f"{bottles:g} × {size:g} ml @ R{rate:g}",
        ),
    )
    db.commit()
    return _row(
        db.execute(
            "SELECT * FROM production_job_credits WHERE id = ?",
            (int(cur.lastrowid),),
        ).fetchone()
    )


def record_payment(
    *,
    user_id: int,
    amount: float,
    recorded_by_user_id: int | None,
    production_job_id: int | None = None,
    note: str = "",
) -> int:
    amt = float(amount)
    if amt <= 0:
        raise ValueError("Payment amount must be positive.")
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO production_payments (
            user_id, production_job_id, amount, paid_at, recorded_by_user_id, note, created_at
        ) VALUES (?, ?, ?, datetime('now'), ?, ?, datetime('now'))
        """,
        (
            int(user_id),
            int(production_job_id) if production_job_id is not None else None,
            amt,
            int(recorded_by_user_id) if recorded_by_user_id is not None else None,
            (note or "").strip() or None,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def helper_earned_total(*, user_id: int, job_id: int | None = None) -> float:
    """Helper labour credits only (owner labour tracked separately)."""
    db = get_db()
    sql = """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM production_job_credits
        WHERE user_id = ?
          AND credit_eligible = 1
          AND labour_kind = 'helper'
          AND status != 'void'
    """
    args: list[Any] = [int(user_id)]
    if job_id is not None:
        sql += " AND production_job_id = ?"
        args.append(int(job_id))
    return float(db.execute(sql, tuple(args)).fetchone()["total"] or 0)


def paid_total(*, user_id: int, job_id: int | None = None) -> float:
    db = get_db()
    if job_id is None:
        row = db.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM production_payments WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
    else:
        # Job-specific payments + general payments apply to overall owed;
        # for per-job display, only job-tagged payments.
        row = db.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM production_payments
            WHERE user_id = ? AND production_job_id = ?
            """,
            (int(user_id), int(job_id)),
        ).fetchone()
    return float(row["total"] or 0)


def earnings_summary_for_user(*, user_id: int) -> dict[str, Any]:
    db = get_db()
    by_job = db.execute(
        """
        SELECT c.production_job_id AS job_id,
               j.name AS job_name,
               COALESCE(SUM(c.amount), 0) AS earned
        FROM production_job_credits c
        JOIN production_jobs j ON j.id = c.production_job_id
        WHERE c.user_id = ?
          AND c.credit_eligible = 1
          AND c.labour_kind = 'helper'
          AND c.status != 'void'
        GROUP BY c.production_job_id
        ORDER BY j.name COLLATE NOCASE
        """,
        (int(user_id),),
    ).fetchall()
    earned = helper_earned_total(user_id=user_id)
    paid = paid_total(user_id=user_id)
    owed = max(earned - paid, 0.0)
    history = db.execute(
        """
        SELECT c.*,
               j.name AS job_name,
               b.target_ml,
               b.completed_bottles AS batch_bottles
        FROM production_job_credits c
        JOIN production_jobs j ON j.id = c.production_job_id
        LEFT JOIN production_job_batches b ON b.id = c.production_job_batch_id
        WHERE c.user_id = ?
          AND c.labour_kind = 'helper'
        ORDER BY c.created_at DESC, c.id DESC
        """,
        (int(user_id),),
    ).fetchall()
    payments = db.execute(
        """
        SELECT p.*, j.name AS job_name, u.username AS recorded_by_username
        FROM production_payments p
        LEFT JOIN production_jobs j ON j.id = p.production_job_id
        LEFT JOIN users u ON u.id = p.recorded_by_user_id
        WHERE p.user_id = ?
        ORDER BY p.paid_at DESC, p.id DESC
        """,
        (int(user_id),),
    ).fetchall()
    return {
        "user_id": int(user_id),
        "by_job": [_row(r) for r in by_job],
        "total_earned": earned,
        "total_paid": paid,
        "total_owed": owed,
        "credits": [_row(r) for r in history],
        "payments": [_row(r) for r in payments],
    }


def job_production_pay_summary(job_id: int) -> dict[str, Any]:
    db = get_db()
    rows = db.execute(
        """
        SELECT c.user_id,
               u.username,
               c.labour_kind,
               COALESCE(SUM(c.amount), 0) AS earned,
               COALESCE(SUM(c.quantity), 0) AS bottles
        FROM production_job_credits c
        JOIN users u ON u.id = c.user_id
        WHERE c.production_job_id = ?
          AND c.credit_type = 'finished_bottle'
          AND c.credit_eligible = 1
          AND c.status != 'void'
        GROUP BY c.user_id
        ORDER BY u.username COLLATE NOCASE
        """,
        (int(job_id),),
    ).fetchall()
    helper_total = 0.0
    owner_total = 0.0
    operators = []
    for r in rows:
        d = _row(r)
        operators.append(d)
        if str(d.get("labour_kind")) == "owner":
            owner_total += float(d["earned"] or 0)
        else:
            helper_total += float(d["earned"] or 0)
    # Owed for helpers on this job: job-tagged payments only (conservative).
    paid_on_job = 0.0
    for op in operators:
        if str(op.get("labour_kind")) == "helper":
            paid_on_job += paid_total(user_id=int(op["user_id"]), job_id=job_id)
    # Better: sum payments for all helpers against this job
    paid_row = db.execute(
        """
        SELECT COALESCE(SUM(p.amount), 0) AS total
        FROM production_payments p
        JOIN users u ON u.id = p.user_id
        WHERE p.production_job_id = ?
        """,
        (int(job_id),),
    ).fetchone()
    paid_on_job = float(paid_row["total"] or 0)
    return {
        "operators": operators,
        "helper_earned": helper_total,
        "owner_labour": owner_total,
        "helper_paid_on_job": paid_on_job,
        "helper_owed_on_job": max(helper_total - paid_on_job, 0.0),
        # Profit uses helper production pay only.
        "profit_production_pay": helper_total,
    }


def operator_bottle_breakdown(job_recipe_id: int) -> list[dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT b.operator_user_id AS user_id,
               u.username,
               COALESCE(SUM(CASE WHEN b.status = 'completed' THEN COALESCE(b.completed_bottles, 0) ELSE 0 END), 0) AS bottles,
               COALESCE(SUM(CASE WHEN c.id IS NOT NULL THEN c.amount ELSE 0 END), 0) AS pay_amount
        FROM production_job_batches b
        LEFT JOIN users u ON u.id = b.operator_user_id
        LEFT JOIN production_job_credits c
          ON c.production_job_batch_id = b.id
         AND c.credit_type = 'finished_bottle'
         AND c.credit_eligible = 1
        WHERE b.production_job_recipe_id = ?
        GROUP BY b.operator_user_id
        HAVING bottles > 0
        ORDER BY u.username COLLATE NOCASE
        """,
        (int(job_recipe_id),),
    ).fetchall()
    return [_row(r) for r in rows]
