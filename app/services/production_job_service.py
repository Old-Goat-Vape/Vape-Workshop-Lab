"""Lab Production Jobs — named manufacturing projects with explicit job-linked mixes."""

from __future__ import annotations

from typing import Any

from db import get_db
from models import recipes_repo
from services import tvw_mix_service

JOB_TYPES = ("customer_order", "event_stock", "internal", "other")
JOB_STATUSES = ("planning", "ready", "in_production", "complete", "cancelled")
BATCH_STATUSES = ("planned", "in_progress", "completed", "cancelled")

ING_FLAVOR = "flavor"
ING_VG = "vg"
ING_PG = "pg"
ING_NIC = "nicotine"


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    return {k: row[k] for k in row.keys()}


def _parse_vg_pg_ratio(raw: str | None) -> tuple[float, float]:
    text = str(raw or "70/30").strip()
    parts = text.replace(":", "/").split("/")
    try:
        if len(parts) >= 2:
            a = float(parts[0])
            b = float(parts[1])
            # Recipes store VG/PG; guided mix uses pg_ratio/vg_ratio as percentages totaling 100.
            if a + b > 0:
                # Heuristic: if labelled like 70/30 and first >= second, treat as VG/PG.
                return float(b), float(a)  # pg, vg when stored as VG/PG
    except (TypeError, ValueError):
        pass
    return 30.0, 70.0


def create_job(
    *,
    name: str,
    job_type: str = "internal",
    description: str = "",
    notes: str = "",
    reference_label: str = "",
    created_by_user_id: int | None,
    status: str = "planning",
) -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("Job name is required.")
    jt = (job_type or "internal").strip().lower()
    if jt not in JOB_TYPES:
        jt = "other"
    st = (status or "planning").strip().lower()
    if st not in JOB_STATUSES:
        st = "planning"
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO production_jobs (
            name, description, job_type, status, reference_label,
            created_by_user_id, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            name,
            (description or "").strip() or None,
            jt,
            st,
            (reference_label or "").strip() or None,
            int(created_by_user_id) if created_by_user_id is not None else None,
            (notes or "").strip() or None,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_job_status(*, job_id: int, status: str) -> None:
    st = (status or "").strip().lower()
    if st not in JOB_STATUSES:
        raise ValueError("Invalid job status.")
    db = get_db()
    extra = ""
    if st == "in_production":
        extra = ", started_at = COALESCE(started_at, datetime('now'))"
    elif st == "complete":
        extra = ", completed_at = datetime('now')"
    db.execute(
        f"""
        UPDATE production_jobs
        SET status = ?, updated_at = datetime('now'){extra}
        WHERE id = ?
        """,
        (st, int(job_id)),
    )
    db.commit()


def list_jobs(*, include_cancelled: bool = True) -> list[dict[str, Any]]:
    db = get_db()
    where = "" if include_cancelled else "WHERE status != 'cancelled'"
    rows = db.execute(
        f"""
        SELECT j.*,
               u.username AS created_by_username,
               (SELECT COUNT(1) FROM production_job_recipes r WHERE r.production_job_id = j.id) AS recipe_count,
               (SELECT COALESCE(SUM(r.target_total_ml), 0) FROM production_job_recipes r WHERE r.production_job_id = j.id) AS target_total_ml,
               (SELECT COALESCE(SUM(r.completed_ml), 0) FROM production_job_recipes r WHERE r.production_job_id = j.id) AS completed_ml,
               (SELECT COALESCE(SUM(r.in_progress_ml), 0) FROM production_job_recipes r WHERE r.production_job_id = j.id) AS in_progress_ml,
               (SELECT COALESCE(SUM(r.target_bottle_count), 0) FROM production_job_recipes r WHERE r.production_job_id = j.id) AS target_bottles,
               (SELECT COALESCE(SUM(r.completed_bottles), 0) FROM production_job_recipes r WHERE r.production_job_id = j.id) AS completed_bottles
        FROM production_jobs j
        LEFT JOIN users u ON u.id = j.created_by_user_id
        {where}
        ORDER BY j.updated_at DESC, j.id DESC
        """
    ).fetchall()
    out = []
    for row in rows:
        d = _row_dict(row)
        d.update(_progress_fields(d))
        out.append(d)
    return out


def get_job(job_id: int) -> dict[str, Any] | None:
    db = get_db()
    row = db.execute(
        """
        SELECT j.*, u.username AS created_by_username
        FROM production_jobs j
        LEFT JOIN users u ON u.id = j.created_by_user_id
        WHERE j.id = ?
        """,
        (int(job_id),),
    ).fetchone()
    return _row_dict(row) if row else None


def _progress_fields(agg: dict[str, Any]) -> dict[str, Any]:
    target_ml = float(agg.get("target_total_ml") or 0)
    completed_ml = float(agg.get("completed_ml") or 0)
    in_progress_ml = float(agg.get("in_progress_ml") or 0)
    target_bottles = float(agg.get("target_bottles") or agg.get("target_bottle_count") or 0)
    completed_bottles = float(agg.get("completed_bottles") or 0)
    remaining_ml = max(target_ml - completed_ml, 0.0)
    remaining_bottles = max(target_bottles - completed_bottles, 0.0)
    over_ml = max(completed_ml - target_ml, 0.0)
    over_bottles = max(completed_bottles - target_bottles, 0.0)
    pct = (completed_ml / target_ml * 100.0) if target_ml > 0 else 0.0
    return {
        "target_total_ml": target_ml,
        "completed_ml": completed_ml,
        "in_progress_ml": in_progress_ml,
        "remaining_ml": remaining_ml,
        "over_ml": over_ml,
        "target_bottles": target_bottles,
        "completed_bottles": completed_bottles,
        "remaining_bottles": remaining_bottles,
        "over_bottles": over_bottles,
        "progress_pct": pct,
        "progress_bar_pct": min(pct, 100.0),
    }


def list_job_recipes(job_id: int) -> list[dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT pjr.*,
               r.name AS recipe_name,
               r.slug AS recipe_slug,
               rv.version_number
        FROM production_job_recipes pjr
        JOIN recipes r ON r.id = pjr.recipe_id
        JOIN recipe_versions rv ON rv.id = pjr.recipe_version_id
        WHERE pjr.production_job_id = ?
        ORDER BY pjr.id ASC
        """,
        (int(job_id),),
    ).fetchall()
    out = []
    for row in rows:
        d = _row_dict(row)
        d.update(
            _progress_fields(
                {
                    "target_total_ml": d.get("target_total_ml"),
                    "completed_ml": d.get("completed_ml"),
                    "in_progress_ml": d.get("in_progress_ml"),
                    "target_bottles": d.get("target_bottle_count"),
                    "completed_bottles": d.get("completed_bottles"),
                }
            )
        )
        out.append(d)
    return out


def get_job_recipe(job_recipe_id: int) -> dict[str, Any] | None:
    db = get_db()
    row = db.execute(
        """
        SELECT pjr.*,
               r.name AS recipe_name,
               r.slug AS recipe_slug,
               rv.version_number,
               j.name AS job_name,
               j.status AS job_status
        FROM production_job_recipes pjr
        JOIN recipes r ON r.id = pjr.recipe_id
        JOIN recipe_versions rv ON rv.id = pjr.recipe_version_id
        JOIN production_jobs j ON j.id = pjr.production_job_id
        WHERE pjr.id = ?
        """,
        (int(job_recipe_id),),
    ).fetchone()
    return _row_dict(row) if row else None


def add_recipe_to_job(
    *,
    job_id: int,
    recipe_id: int,
    recipe_version_id: int,
    target_bottle_size_ml: float,
    target_bottle_count: int,
    mix_target_nic_mg_ml: float = 0.0,
    mix_pg_ratio: float | None = None,
    mix_vg_ratio: float | None = None,
    mix_nic_base_mg_ml: float = 100.0,
    mix_nicotine_carrier: str = "PG",
) -> int:
    job = get_job(job_id)
    if not job:
        raise ValueError("Production job not found.")
    version = recipes_repo.fetch_version(int(recipe_version_id))
    if not version or int(version.get("recipe_id") or 0) != int(recipe_id):
        raise ValueError("Recipe version does not belong to the selected recipe.")
    size = float(target_bottle_size_ml)
    count = int(target_bottle_count)
    if size <= 0 or count <= 0:
        raise ValueError("Bottle size and count must be positive.")
    target_total = size * count
    if mix_pg_ratio is None or mix_vg_ratio is None:
        pg, vg = _parse_vg_pg_ratio(version.get("target_vg_pg_ratio"))
        mix_pg_ratio = pg if mix_pg_ratio is None else mix_pg_ratio
        mix_vg_ratio = vg if mix_vg_ratio is None else mix_vg_ratio
    carrier = str(mix_nicotine_carrier or "PG").upper()
    if carrier not in ("PG", "VG"):
        carrier = "PG"
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO production_job_recipes (
            production_job_id, recipe_id, recipe_version_id,
            target_bottle_size_ml, target_bottle_count, target_total_ml,
            mix_target_nic_mg_ml, mix_pg_ratio, mix_vg_ratio,
            mix_nic_base_mg_ml, mix_nicotine_carrier,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            int(job_id),
            int(recipe_id),
            int(recipe_version_id),
            size,
            count,
            target_total,
            float(mix_target_nic_mg_ml or 0),
            float(mix_pg_ratio),
            float(mix_vg_ratio),
            float(mix_nic_base_mg_ml or 100),
            carrier,
        ),
    )
    job_recipe_id = int(cur.lastrowid)
    db.execute(
        "UPDATE production_jobs SET updated_at = datetime('now') WHERE id = ?",
        (int(job_id),),
    )
    db.commit()
    sync_job_inventory_requirements(job_id)
    return job_recipe_id


def update_job_recipe_version(*, job_recipe_id: int, new_version_id: int) -> None:
    """Explicit version update only — never automatic."""
    jr = get_job_recipe(job_recipe_id)
    if not jr:
        raise ValueError("Job recipe not found.")
    version = recipes_repo.fetch_version(int(new_version_id))
    if not version or int(version.get("recipe_id") or 0) != int(jr["recipe_id"]):
        raise ValueError("New version does not belong to this recipe.")
    db = get_db()
    db.execute(
        """
        UPDATE production_job_recipes
        SET recipe_version_id = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (int(new_version_id), int(job_recipe_id)),
    )
    db.commit()
    sync_job_inventory_requirements(int(jr["production_job_id"]))


def job_overview(job_id: int) -> dict[str, Any]:
    job = get_job(job_id)
    if not job:
        raise ValueError("Production job not found.")
    recipes = list_job_recipes(job_id)
    from services import production_pay_service as pay
    from services import production_job_finance_service as finance

    for r in recipes:
        r["operators"] = pay.operator_bottle_breakdown(int(r["id"]))
        r["production_pay_amount"] = sum(float(o.get("pay_amount") or 0) for o in r["operators"])
    agg = {
        "target_total_ml": sum(float(r["target_total_ml"]) for r in recipes),
        "completed_ml": sum(float(r["completed_ml"]) for r in recipes),
        "in_progress_ml": sum(float(r["in_progress_ml"]) for r in recipes),
        "target_bottles": sum(float(r["target_bottle_count"]) for r in recipes),
        "completed_bottles": sum(float(r["completed_bottles"]) for r in recipes),
    }
    progress = _progress_fields(agg)
    inv = list_job_inventory(job_id)
    short_count = sum(1 for i in inv if i.get("stock_status") in ("short", "not_purchased"))
    enough = all(i.get("stock_status") in ("enough", "excess") for i in inv) if inv else False
    pay_sum = pay.job_production_pay_summary(job_id)
    # Lightweight finance cards — avoid full summary recursion into overview.
    ingredients = finance.ingredients_consumed_cost(job_id)
    revenue = float(job.get("revenue_amount") or 0) if str(job.get("revenue_mode") or "none") != "none" else 0.0
    packaging = finance.packaging_costs(job_id)
    referral = finance.referral_cost(job_id, revenue=revenue)
    expenses = finance.list_job_expenses(job_id)
    shipping_packing = sum(
        float(e.get("amount") or 0)
        for e in expenses
        if str(e.get("category") or "").lower() in ("shipping", "packing", "courier", "cartons")
    )
    other_total = sum(
        float(e.get("amount") or 0)
        for e in expenses
        if str(e.get("category") or "").lower() not in ("shipping", "packing", "courier", "cartons")
    )
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
    return {
        "job": job,
        "recipes": recipes,
        "recipe_count": len(recipes),
        **progress,
        "ingredient_readiness": "ready" if enough and inv else ("short" if short_count else "empty"),
        "ingredients_short_count": short_count,
        "production_pay_owed": float(pay_sum.get("helper_owed_on_job") or 0),
        "purchase_spend": float(ingredients["cash_spent_on_inventory"]),
        "revenue": revenue,
        "estimated_profit": revenue - fulfilment,
    }


def refresh_job_recipe_totals(job_recipe_id: int) -> None:
    """Recalculate completed / in-progress from batches (idempotent source of truth)."""
    db = get_db()
    row = db.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN status = 'completed' THEN COALESCE(actual_ml, target_ml) ELSE 0 END), 0) AS completed_ml,
            COALESCE(SUM(CASE WHEN status = 'completed' THEN COALESCE(completed_bottles, target_bottles) ELSE 0 END), 0) AS completed_bottles,
            COALESCE(SUM(CASE WHEN status = 'in_progress' THEN target_ml ELSE 0 END), 0) AS in_progress_ml
        FROM production_job_batches
        WHERE production_job_recipe_id = ?
        """,
        (int(job_recipe_id),),
    ).fetchone()
    db.execute(
        """
        UPDATE production_job_recipes
        SET completed_ml = ?, completed_bottles = ?, in_progress_ml = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (
            float(row["completed_ml"] or 0),
            float(row["completed_bottles"] or 0),
            float(row["in_progress_ml"] or 0),
            int(job_recipe_id),
        ),
    )
    parent = db.execute(
        "SELECT production_job_id FROM production_job_recipes WHERE id = ?",
        (int(job_recipe_id),),
    ).fetchone()
    if parent:
        db.execute(
            "UPDATE production_jobs SET updated_at = datetime('now') WHERE id = ?",
            (int(parent["production_job_id"]),),
        )
    db.commit()


def start_job_linked_mix(
    *,
    job_recipe_id: int,
    operator_user_id: int,
    bottle_count: float,
    target_ml: float | None = None,
) -> dict[str, Any]:
    """Create guided mix session + production batch with explicit job linkage."""
    from services import guided_mix_session_service

    jr = get_job_recipe(job_recipe_id)
    if not jr:
        raise ValueError("Job recipe not found.")
    if str(jr.get("job_status") or "") == "cancelled":
        raise ValueError("Cannot mix for a cancelled production job.")
    bottles = float(bottle_count)
    if bottles <= 0:
        raise ValueError("Bottle count must be positive.")
    size = float(jr["target_bottle_size_ml"])
    ml = float(target_ml) if target_ml is not None else size * bottles
    if ml <= 0:
        raise ValueError("Target volume must be positive.")

    session_id, resolved_version_id, steps = guided_mix_session_service.create_guided_mix_session(
        user_id=int(operator_user_id),
        recipe_id=int(jr["recipe_id"]),
        requested_version_id=int(jr["recipe_version_id"]),
        target_ml=ml,
        target_nic_mg_ml=float(jr.get("mix_target_nic_mg_ml") or 0),
        nic_base_mg_ml=float(jr.get("mix_nic_base_mg_ml") or 100),
        pg_ratio=float(jr.get("mix_pg_ratio") or 30),
        vg_ratio=float(jr.get("mix_vg_ratio") or 70),
        nicotine_carrier=str(jr.get("mix_nicotine_carrier") or "PG"),
        production_job_id=int(jr["production_job_id"]),
        production_job_recipe_id=int(jr["id"]),
    )
    if int(resolved_version_id) != int(jr["recipe_version_id"]):
        # Locked version must win; refuse silent drift.
        raise ValueError("Locked job recipe version could not be resolved for mixing.")

    db = get_db()
    cur = db.execute(
        """
        INSERT INTO production_job_batches (
            production_job_id, production_job_recipe_id, guided_mix_session_id,
            recipe_id, recipe_version_id, target_ml, actual_ml,
            target_bottles, completed_bottles, operator_user_id, status,
            started_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 0, ?, 'in_progress', datetime('now'), datetime('now'))
        """,
        (
            int(jr["production_job_id"]),
            int(jr["id"]),
            int(session_id),
            int(jr["recipe_id"]),
            int(jr["recipe_version_id"]),
            ml,
            bottles,
            int(operator_user_id),
        ),
    )
    batch_id = int(cur.lastrowid)
    # Promote job status when production starts.
    if str(jr.get("job_status") or "") in ("planning", "ready"):
        db.execute(
            """
            UPDATE production_jobs
            SET status = 'in_production',
                started_at = COALESCE(started_at, datetime('now')),
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (int(jr["production_job_id"]),),
        )
    db.commit()
    refresh_job_recipe_totals(int(jr["id"]))
    return {
        "batch_id": batch_id,
        "mix_session_id": session_id,
        "target_ml": ml,
        "target_bottles": bottles,
        "step_count": len(steps),
    }


def cancel_batch(*, batch_id: int, cancelled_by_user_id: int | None = None) -> None:
    """Cancel an in-progress/planned batch. Never posts inventory or production pay."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM production_job_batches WHERE id = ?",
        (int(batch_id),),
    ).fetchone()
    if not row:
        raise ValueError("Batch not found.")
    status = str(row["status"] or "")
    if status == "completed":
        raise ValueError("Completed batches cannot be cancelled.")
    if status == "cancelled":
        return
    if status not in ("planned", "in_progress"):
        raise ValueError(f"Cannot cancel batch in status '{status}'.")
    db.execute(
        """
        UPDATE production_job_batches
        SET status = 'cancelled',
            cancelled_at = datetime('now'),
            cancelled_by_user_id = ?,
            completed_at = COALESCE(completed_at, datetime('now'))
        WHERE id = ? AND status IN ('planned', 'in_progress')
        """,
        (
            int(cancelled_by_user_id) if cancelled_by_user_id is not None else None,
            int(batch_id),
        ),
    )
    db.commit()
    refresh_job_recipe_totals(int(row["production_job_recipe_id"]))


def cancel_batch_for_guided_session(
    *,
    mix_session_id: int,
    cancelled_by_user_id: int | None = None,
) -> dict[str, Any] | None:
    db = get_db()
    batch = db.execute(
        "SELECT * FROM production_job_batches WHERE guided_mix_session_id = ?",
        (int(mix_session_id),),
    ).fetchone()
    if not batch:
        return None
    cancel_batch(batch_id=int(batch["id"]), cancelled_by_user_id=cancelled_by_user_id)
    return {
        "batch_id": int(batch["id"]),
        "production_job_id": int(batch["production_job_id"]),
        "status": "cancelled",
    }


def list_job_batches(job_id: int) -> list[dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT b.*,
               r.name AS recipe_name,
               rv.version_number,
               u.username AS operator_username
        FROM production_job_batches b
        JOIN recipes r ON r.id = b.recipe_id
        JOIN recipe_versions rv ON rv.id = b.recipe_version_id
        LEFT JOIN users u ON u.id = b.operator_user_id
        WHERE b.production_job_id = ?
        ORDER BY b.created_at DESC, b.id DESC
        """,
        (int(job_id),),
    ).fetchall()
    return [_row_dict(r) for r in rows]


def _ingredient_key(ingredient_type: str, ingredient_id: int | None) -> tuple[str, int]:
    return (str(ingredient_type), int(ingredient_id or 0))


def compute_job_requirement_lines(job_id: int) -> list[dict[str, Any]]:
    """Roll up required ml across locked job recipe versions (canonical ingredient ids)."""
    recipes = list_job_recipes(job_id)
    totals: dict[tuple[str, int], dict[str, Any]] = {}

    def add_line(*, ingredient_type: str, ingredient_id: int | None, display_name: str, ml: float) -> None:
        if ml <= 0:
            return
        key = _ingredient_key(ingredient_type, ingredient_id)
        if key not in totals:
            totals[key] = {
                "ingredient_type": ingredient_type,
                "ingredient_id": int(ingredient_id) if ingredient_id else None,
                "display_name": display_name,
                "required_ml": 0.0,
            }
        totals[key]["required_ml"] += float(ml)

    for jr in recipes:
        target_ml = float(jr["target_total_ml"])
        items = recipes_repo.fetch_version_items_with_stock(int(jr["recipe_version_id"]))
        for item in items:
            pct = float(item.get("amount_percent") or 0)
            req = target_ml * (pct / 100.0)
            add_line(
                ingredient_type=ING_FLAVOR,
                ingredient_id=int(item.get("flavor_id") or item.get("id") or item.get("ingredient_id") or 0),
                display_name=f"{item.get('brand') or ''} {item.get('name') or ''}".strip() or "Flavour",
                ml=req,
            )
        mix = tvw_mix_service.quick_mix_for_recipe_version(
            recipe_id=int(jr["recipe_id"]),
            version_id=int(jr["recipe_version_id"]),
            target_ml=target_ml,
            target_nic_mg_ml=float(jr.get("mix_target_nic_mg_ml") or 0),
            nic_base_mg_ml=float(jr.get("mix_nic_base_mg_ml") or 100),
            pg_ratio=float(jr.get("mix_pg_ratio") or 30),
            vg_ratio=float(jr.get("mix_vg_ratio") or 70),
            nicotine_carrier=str(jr.get("mix_nicotine_carrier") or "PG"),
        )
        totals_m = mix.get("totals") or {}
        add_line(ingredient_type=ING_VG, ingredient_id=None, display_name="VG", ml=float(totals_m.get("vg_ml") or 0))
        add_line(ingredient_type=ING_PG, ingredient_id=None, display_name="PG", ml=float(totals_m.get("pg_ml") or 0))
        add_line(
            ingredient_type=ING_NIC,
            ingredient_id=None,
            display_name=f"Nicotine ({jr.get('mix_nicotine_carrier') or 'PG'})",
            ml=float(totals_m.get("nicotine_ml") or 0),
        )

    return sorted(totals.values(), key=lambda x: (x["ingredient_type"], x["display_name"].lower()))


def sync_job_inventory_requirements(job_id: int) -> None:
    """Upsert required_ml on job inventory from current locked recipes; preserve purchased/used."""
    lines = compute_job_requirement_lines(job_id)
    db = get_db()
    seen: set[tuple[str, int]] = set()
    for line in lines:
        key = _ingredient_key(line["ingredient_type"], line.get("ingredient_id"))
        seen.add(key)
        existing = db.execute(
            """
            SELECT id FROM production_job_inventory
            WHERE production_job_id = ? AND ingredient_type = ?
              AND COALESCE(ingredient_id, 0) = ?
            """,
            (int(job_id), line["ingredient_type"], int(line.get("ingredient_id") or 0)),
        ).fetchone()
        if existing:
            db.execute(
                """
                UPDATE production_job_inventory
                SET required_ml = ?, display_name = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (float(line["required_ml"]), line["display_name"], int(existing["id"])),
            )
        else:
            db.execute(
                """
                INSERT INTO production_job_inventory (
                    production_job_id, ingredient_type, ingredient_id, display_name,
                    required_ml, purchased_ml, used_ml, adjustment_ml, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, 0, 0, datetime('now'), datetime('now'))
                """,
                (
                    int(job_id),
                    line["ingredient_type"],
                    int(line["ingredient_id"]) if line.get("ingredient_id") else None,
                    line["display_name"],
                    float(line["required_ml"]),
                ),
            )
    # Zero required for rows no longer needed (keep purchase history).
    rows = db.execute(
        "SELECT id, ingredient_type, ingredient_id FROM production_job_inventory WHERE production_job_id = ?",
        (int(job_id),),
    ).fetchall()
    for row in rows:
        key = _ingredient_key(row["ingredient_type"], row["ingredient_id"])
        if key not in seen:
            db.execute(
                "UPDATE production_job_inventory SET required_ml = 0, updated_at = datetime('now') WHERE id = ?",
                (int(row["id"]),),
            )
    db.commit()


def list_job_inventory(job_id: int) -> list[dict[str, Any]]:
    sync_job_inventory_requirements(job_id)
    db = get_db()
    rows = db.execute(
        """
        SELECT * FROM production_job_inventory
        WHERE production_job_id = ?
        ORDER BY
          CASE ingredient_type WHEN 'flavor' THEN 1 WHEN 'vg' THEN 2 WHEN 'pg' THEN 3 WHEN 'nicotine' THEN 4 ELSE 5 END,
          display_name COLLATE NOCASE
        """,
        (int(job_id),),
    ).fetchall()
    out = []
    for row in rows:
        d = _row_dict(row)
        required = float(d.get("required_ml") or 0)
        purchased = float(d.get("purchased_ml") or 0)
        used = float(d.get("used_ml") or 0)
        remaining = purchased - used
        still_to_buy = max(required - purchased, 0.0)
        if purchased <= 0 and required > 0:
            status = "not_purchased"
        elif purchased + 1e-9 < required:
            status = "short"
        elif remaining > required * 0.05 and purchased > required:
            status = "excess"
        else:
            status = "enough"
        d.update(
            {
                "remaining_ml": remaining,
                "still_to_buy_ml": still_to_buy,
                "stock_status": status,
            }
        )
        out.append(d)
    return out


def record_purchase(
    *,
    job_id: int,
    inventory_id: int,
    quantity_ml: float,
    note: str = "",
    pack_info: str = "",
    recorded_by_user_id: int | None,
) -> None:
    qty = float(quantity_ml)
    if qty <= 0:
        raise ValueError("Purchase quantity must be positive.")
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
            production_job_id, inventory_id, quantity_ml, note, pack_info, recorded_by_user_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            int(job_id),
            int(inventory_id),
            qty,
            (note or "").strip() or None,
            (pack_info or "").strip() or None,
            int(recorded_by_user_id) if recorded_by_user_id is not None else None,
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


def _usage_lines_for_volume(*, job_recipe: dict[str, Any], volume_ml: float) -> list[dict[str, Any]]:
    """Ingredient ml consumed by a completed volume of a locked job recipe."""
    lines: list[dict[str, Any]] = []
    items = recipes_repo.fetch_version_items_with_stock(int(job_recipe["recipe_version_id"]))
    for item in items:
        pct = float(item.get("amount_percent") or 0)
        lines.append(
            {
                "ingredient_type": ING_FLAVOR,
                "ingredient_id": int(item.get("flavor_id") or item.get("id") or item.get("ingredient_id") or 0),
                "ml": volume_ml * (pct / 100.0),
            }
        )
    mix = tvw_mix_service.quick_mix_for_recipe_version(
        recipe_id=int(job_recipe["recipe_id"]),
        version_id=int(job_recipe["recipe_version_id"]),
        target_ml=float(volume_ml),
        target_nic_mg_ml=float(job_recipe.get("mix_target_nic_mg_ml") or 0),
        nic_base_mg_ml=float(job_recipe.get("mix_nic_base_mg_ml") or 100),
        pg_ratio=float(job_recipe.get("mix_pg_ratio") or 30),
        vg_ratio=float(job_recipe.get("mix_vg_ratio") or 70),
        nicotine_carrier=str(job_recipe.get("mix_nicotine_carrier") or "PG"),
    )
    totals_m = mix.get("totals") or {}
    lines.append({"ingredient_type": ING_VG, "ingredient_id": None, "ml": float(totals_m.get("vg_ml") or 0)})
    lines.append({"ingredient_type": ING_PG, "ingredient_id": None, "ml": float(totals_m.get("pg_ml") or 0)})
    lines.append({"ingredient_type": ING_NIC, "ingredient_id": None, "ml": float(totals_m.get("nicotine_ml") or 0)})
    return lines


def complete_batch_for_guided_session(*, mix_session_id: int) -> dict[str, Any] | None:
    """Idempotent: mark linked batch completed, refresh progress, post inventory + pay once."""
    from services import production_pay_service as pay

    db = get_db()
    batch = db.execute(
        "SELECT * FROM production_job_batches WHERE guided_mix_session_id = ?",
        (int(mix_session_id),),
    ).fetchone()
    if not batch:
        return None
    batch_d = _row_dict(batch)
    status = str(batch_d.get("status") or "")
    if status == "cancelled":
        return {"batch_id": int(batch_d["id"]), "status": "cancelled", "posted": False}
    if status == "completed" and int(batch_d.get("inventory_posted") or 0) == 1:
        credit = pay.post_finished_bottle_credit_for_batch(batch_id=int(batch_d["id"]))
        return {
            "batch_id": int(batch_d["id"]),
            "status": "completed",
            "posted": False,
            "already_done": True,
            "credit": credit,
        }
    if status not in ("planned", "in_progress", "completed"):
        raise ValueError(f"Cannot complete batch in status '{status}'.")

    session_row = db.execute(
        "SELECT target_ml, completed_at FROM mix_sessions WHERE id = ?",
        (int(mix_session_id),),
    ).fetchone()
    actual_ml = float((session_row["target_ml"] if session_row else None) or batch_d.get("target_ml") or 0)
    size = 0.0
    jr = get_job_recipe(int(batch_d["production_job_recipe_id"]))
    if jr:
        size = float(jr.get("target_bottle_size_ml") or 0)
    completed_bottles = (actual_ml / size) if size > 0 else float(batch_d.get("target_bottles") or 0)

    db.execute(
        """
        UPDATE production_job_batches
        SET status = 'completed',
            actual_ml = ?,
            completed_bottles = ?,
            completed_at = COALESCE(completed_at, datetime('now'))
        WHERE id = ? AND status IN ('planned', 'in_progress', 'completed')
        """,
        (actual_ml, completed_bottles, int(batch_d["id"])),
    )

    posted = False
    if int(batch_d.get("inventory_posted") or 0) != 1 and jr:
        for line in _usage_lines_for_volume(job_recipe=jr, volume_ml=actual_ml):
            ml = float(line["ml"] or 0)
            if ml <= 0:
                continue
            inv = db.execute(
                """
                SELECT id FROM production_job_inventory
                WHERE production_job_id = ? AND ingredient_type = ?
                  AND COALESCE(ingredient_id, 0) = ?
                """,
                (
                    int(batch_d["production_job_id"]),
                    line["ingredient_type"],
                    int(line.get("ingredient_id") or 0),
                ),
            ).fetchone()
            if inv:
                db.execute(
                    """
                    UPDATE production_job_inventory
                    SET used_ml = used_ml + ?, updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (ml, int(inv["id"])),
                )
        db.execute(
            "UPDATE production_job_batches SET inventory_posted = 1 WHERE id = ?",
            (int(batch_d["id"]),),
        )
        posted = True

    db.commit()
    refresh_job_recipe_totals(int(batch_d["production_job_recipe_id"]))
    credit = pay.post_finished_bottle_credit_for_batch(batch_id=int(batch_d["id"]))
    return {
        "batch_id": int(batch_d["id"]),
        "status": "completed",
        "posted": posted,
        "actual_ml": actual_ml,
        "credit": credit,
    }


def production_context_for_session(mix_session_id: int) -> dict[str, Any] | None:
    db = get_db()
    row = db.execute(
        """
        SELECT b.id AS batch_id,
               b.status AS batch_status,
               b.production_job_id,
               b.production_job_recipe_id,
               b.target_ml,
               b.target_bottles,
               b.operator_user_id,
               j.name AS job_name,
               r.name AS recipe_name,
               rv.version_number
        FROM production_job_batches b
        JOIN production_jobs j ON j.id = b.production_job_id
        JOIN recipes r ON r.id = b.recipe_id
        JOIN recipe_versions rv ON rv.id = b.recipe_version_id
        WHERE b.guided_mix_session_id = ?
        """,
        (int(mix_session_id),),
    ).fetchone()
    return _row_dict(row) if row else None
