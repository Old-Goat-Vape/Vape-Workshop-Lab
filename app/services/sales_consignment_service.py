from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from db import get_db
from services.master_admin_console_service import _log_admin_action


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(default)


def _rowdicts(rows) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except Exception:
        return default


def _sync_legacy_finished_goods_delta(*, fg_row: Dict[str, Any], qty_delta: int) -> None:
    """Keep legacy finished_goods_inventory aligned with new finished_goods_stock deltas."""
    if int(qty_delta or 0) == 0:
        return
    db = get_db()
    legacy = db.execute(
        """
        SELECT id, quantity_on_hand
        FROM finished_goods_inventory
        WHERE production_batch_id = ? AND product_name = ? AND bottle_size_ml = ? AND active = 1
        LIMIT 1
        """,
        (
            int(fg_row.get("source_batch_id") or 0),
            str(fg_row.get("product_name_snapshot") or ""),
            float(fg_row.get("bottle_size_ml") or 0.0),
        ),
    ).fetchone()
    if not legacy:
        return
    new_qty = max(0, int(_to_int(legacy["quantity_on_hand"])) + int(qty_delta))
    db.execute(
        "UPDATE finished_goods_inventory SET quantity_on_hand = ? WHERE id = ?",
        (new_qty, int(legacy["id"])),
    )


def _line_state_columns() -> Dict[str, str]:
    return {
        "sold": "qty_reported_sold",
        "returned": "qty_returned",
        "damaged": "qty_damaged",
        "remaining": "qty_remaining_with_client",
    }


def _state_pair_for_correction(correction_type: str) -> tuple[str, str]:
    t = str(correction_type or "").strip().lower()
    mapping = {
        "sold_to_remaining": ("sold", "remaining"),
        "sold_to_returned": ("sold", "returned"),
        "damaged_to_returned": ("damaged", "returned"),
        "returned_to_remaining": ("returned", "remaining"),
        "wrong_line_reversal": ("sold", "remaining"),
        "admin_adjustment": ("remaining", "returned"),
    }
    if t not in mapping:
        raise ValueError("Unsupported correction type.")
    return mapping[t]


def _insert_stock_movement(
    *,
    movement_type: str,
    actor_id: int,
    notes: str = "",
    recipe_id: int | None = None,
    recipe_version_id: int | None = None,
    source_batch_id: int | None = None,
    decant_event_id: int | None = None,
    finished_goods_stock_id: int | None = None,
    client_id: int | None = None,
    delivery_id: int | None = None,
    delivery_line_id: int | None = None,
    qty_bottles: int = 0,
    qty_ml: float = 0.0,
    amount_value: float = 0.0,
) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO stock_movements (
            movement_type, movement_date, recipe_id, recipe_version_id, source_batch_id, decant_event_id,
            finished_goods_stock_id, client_id, delivery_id, delivery_line_id, qty_bottles, qty_ml,
            amount_value, notes, created_by_user_id, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            movement_type,
            datetime.now(),
            recipe_id,
            recipe_version_id,
            source_batch_id,
            decant_event_id,
            finished_goods_stock_id,
            client_id,
            delivery_id,
            delivery_line_id,
            int(qty_bottles or 0),
            float(qty_ml or 0.0),
            float(amount_value or 0.0),
            notes,
            int(actor_id or 0),
            datetime.now(),
        ),
    )


def _refresh_finished_goods_at_clients(finished_goods_stock_id: int) -> None:
    db = get_db()
    r = db.execute(
        """
        SELECT COALESCE(SUM(qty_remaining_with_client), 0.0) AS remaining_qty
        FROM client_delivery_lines
        WHERE finished_goods_stock_id = ?
        """,
        (int(finished_goods_stock_id),),
    ).fetchone()
    remaining = int(_to_float(_row_value(r, "remaining_qty", 0.0), 0.0))
    db.execute(
        "UPDATE finished_goods_stock SET quantity_at_clients = ?, updated_at = ? WHERE id = ?",
        (remaining, datetime.now(), int(finished_goods_stock_id)),
    )


def _delivery_totals(delivery_id: int) -> Dict[str, float]:
    return _delivery_totals_as_of(delivery_id=delivery_id, as_of_date=None)


def _delivery_totals_as_of(*, delivery_id: int, as_of_date: str | None) -> Dict[str, float]:
    db = get_db()
    h = db.execute(
        """
        SELECT delivery_type
        FROM client_deliveries
        WHERE id = ?
        """,
        (int(delivery_id),),
    ).fetchone()
    if not h:
        return {"sent_value": 0.0, "sold_value": 0.0, "paid_value": 0.0, "due_value": 0.0}
    t = str(h["delivery_type"] or "consignment")
    r = db.execute(
        """
        SELECT
            COALESCE(SUM(total_value_snapshot), 0.0) AS sent_value,
            COALESCE(SUM(qty_reported_sold * unit_price_snapshot), 0.0) AS sold_value
        FROM client_delivery_lines
        WHERE delivery_id = ?
        """,
        (int(delivery_id),),
    ).fetchone()
    if as_of_date:
        p = db.execute(
            """
            SELECT COALESCE(SUM(a.amount_allocated), 0.0) AS paid_value
            FROM client_payment_allocations a
            JOIN client_payments p ON p.id = a.payment_id
            WHERE a.delivery_id = ? AND date(p.payment_date) <= date(?)
            """,
            (int(delivery_id), str(as_of_date)),
        ).fetchone()
    else:
        p = db.execute(
            "SELECT COALESCE(SUM(amount_allocated), 0.0) AS paid_value FROM client_payment_allocations WHERE delivery_id = ?",
            (int(delivery_id),),
        ).fetchone()
    sent = float(_row_value(r, "sent_value", 0.0) or 0.0)
    sold = float(_row_value(r, "sold_value", 0.0) or 0.0)
    paid = float(_row_value(p, "paid_value", 0.0) or 0.0)
    due_base = sent if t == "direct_sale" else sold
    return {"sent_value": sent, "sold_value": sold, "paid_value": paid, "due_value": max(0.0, due_base - paid)}


def _normalize_date(v: str | None, fallback: str) -> str:
    s = str(v or "").strip()
    try:
        if s:
            return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
    except Exception:
        pass
    return fallback


def _update_delivery_status(delivery_id: int) -> None:
    db = get_db()
    row = db.execute(
        """
        SELECT
            d.delivery_type,
            COALESCE(SUM(l.qty_sent),0) AS sent_qty,
            COALESCE(SUM(l.qty_reported_sold + l.qty_returned + l.qty_damaged),0) AS reconciled_qty,
            COALESCE(SUM(l.qty_remaining_with_client),0) AS remaining_qty
        FROM client_deliveries d
        LEFT JOIN client_delivery_lines l ON l.delivery_id = d.id
        WHERE d.id = ?
        GROUP BY d.id
        """,
        (int(delivery_id),),
    ).fetchone()
    if not row:
        return
    totals = _delivery_totals(delivery_id)
    sent_qty = int(row["sent_qty"] or 0)
    reconciled_qty = int(row["reconciled_qty"] or 0)
    remaining_qty = int(row["remaining_qty"] or 0)
    due = float(totals["due_value"])
    paid = float(totals["paid_value"])
    status = "open"
    if sent_qty > 0 and reconciled_qty > 0 and remaining_qty > 0:
        status = "partially_reconciled"
    if sent_qty > 0 and remaining_qty == 0:
        status = "reconciled"
    if paid > 0 and due > 0:
        status = "partially_paid"
    if due <= 0 and sent_qty > 0:
        status = "paid" if status in {"partially_reconciled", "reconciled", "partially_paid"} else "closed"
    if status == "paid" and remaining_qty == 0:
        status = "closed"
    db.execute(
        "UPDATE client_deliveries SET status = ?, updated_at = ? WHERE id = ?",
        (status, datetime.now(), int(delivery_id)),
    )


def _next_delivery_number() -> str:
    db = get_db()
    row = db.execute("SELECT COALESCE(MAX(id), 0) AS m FROM client_deliveries").fetchone()
    n = int(_row_value(row, "m", 0) or 0) + 1
    return f"DLV-{datetime.now().strftime('%Y%m')}-{n:04d}"


def list_clients() -> List[Dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT c.*,
               (
                 SELECT MAX(delivery_date) FROM client_deliveries d WHERE d.client_id = c.id
               ) AS last_delivery_date,
               (
                 SELECT MAX(payment_date) FROM client_payments p WHERE p.client_id = c.id
               ) AS last_payment_date
        FROM clients c
        WHERE c.is_active = 1
        ORDER BY c.name COLLATE NOCASE
        """
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        totals = client_financial_summary(int(d["id"]))
        d.update(totals)
        out.append(d)
    return out


def create_client(*, actor_id: int, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    db = get_db()
    name = str(payload.get("name") or "").strip()
    if not name:
        return {"status": "error", "message": "Client name is required."}, 400
    db.execute(
        """
        INSERT INTO clients (name, contact_person, phone, email, client_type, payment_terms_days, notes, is_active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            name,
            str(payload.get("contact_person") or "").strip(),
            str(payload.get("phone") or "").strip(),
            str(payload.get("email") or "").strip(),
            str(payload.get("client_type") or "consignment").strip() or "consignment",
            int(_to_int(payload.get("payment_terms_days"), 0)),
            str(payload.get("notes") or "").strip(),
            datetime.now(),
            datetime.now(),
        ),
    )
    cid = int(db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
    _log_admin_action(actor_id=actor_id, action="CLIENT_CREATE", details=f"client_id={cid} name={name}")
    db.commit()
    return {"status": "success", "client_id": cid}, 200


def list_finished_goods_stock(*, search: str = "") -> List[Dict[str, Any]]:
    db = get_db()
    q = str(search or "").strip()
    if q:
        rows = db.execute(
            """
            SELECT fgs.*, pb.batch_name
            FROM finished_goods_stock fgs
            LEFT JOIN production_batches pb ON pb.id = fgs.source_batch_id
            WHERE fgs.location_status != 'archived'
              AND (fgs.product_name_snapshot LIKE ? OR CAST(fgs.bottle_size_ml AS TEXT) LIKE ?)
            ORDER BY fgs.product_name_snapshot COLLATE NOCASE, fgs.bottle_size_ml
            """,
            (f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT fgs.*, pb.batch_name
            FROM finished_goods_stock fgs
            LEFT JOIN production_batches pb ON pb.id = fgs.source_batch_id
            WHERE fgs.location_status != 'archived'
            ORDER BY fgs.product_name_snapshot COLLATE NOCASE, fgs.bottle_size_ml
            """
        ).fetchall()
    return _rowdicts(rows)


def create_decant_event(
    *,
    actor_id: int,
    source_batch_id: int,
    recipe_id: int | None,
    recipe_version_id: int | None,
    batch_number_snapshot: str,
    product_name_snapshot: str,
    bottle_size_ml: float,
    bottles_created: int,
    cost_total_snapshot: float,
    default_wholesale_price: float,
    default_retail_reference_price: float,
    notes: str = "",
) -> Tuple[Dict[str, Any], int]:
    db = get_db()
    bottle_size = max(0.0, _to_float(bottle_size_ml))
    qty = max(0, _to_int(bottles_created))
    if bottle_size <= 0 or qty <= 0:
        return {"status": "error", "message": "Bottle size and quantity must be greater than zero."}, 400
    batch = db.execute(
        "SELECT id, remaining_volume_ml FROM production_batches WHERE id = ?",
        (int(source_batch_id),),
    ).fetchone()
    if not batch:
        return {"status": "error", "message": "Source batch not found."}, 404
    total_ml = bottle_size * qty
    remaining = _to_float(batch["remaining_volume_ml"])
    if total_ml > remaining + 1e-9:
        return {"status": "error", "message": "Not enough remaining bulk volume for this decant."}, 400
    unit_cost = float(cost_total_snapshot) / float(qty) if qty > 0 else 0.0
    db.execute(
        """
        INSERT INTO decant_events (
            source_batch_id, recipe_id, recipe_version_id, batch_number_snapshot, product_name_snapshot,
            bottle_size_ml, bottles_created, total_ml_decanted, cost_total_snapshot, cost_per_bottle_snapshot,
            created_by_user_id, created_at, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(source_batch_id),
            int(recipe_id) if recipe_id else None,
            int(recipe_version_id) if recipe_version_id else None,
            str(batch_number_snapshot or ""),
            str(product_name_snapshot or "").strip(),
            bottle_size,
            qty,
            total_ml,
            float(cost_total_snapshot or 0.0),
            unit_cost,
            int(actor_id or 0),
            datetime.now(),
            str(notes or ""),
        ),
    )
    decant_event_id = int(db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

    new_remaining = max(0.0, remaining - total_ml)
    cur = db.execute(
        """
        UPDATE production_batches
        SET remaining_volume_ml = ?, batch_status = ?, notes = COALESCE(notes,'')
        WHERE id = ? AND remaining_volume_ml >= ?
        """,
        (new_remaining, "open" if new_remaining > 0.01 else "depleted", int(source_batch_id), float(total_ml)),
    )
    if int(getattr(cur, "rowcount", 0) or 0) <= 0:
        return {"status": "error", "message": "Batch volume changed; retry decant with fresh values."}, 409

    fg = db.execute(
        """
        SELECT id FROM finished_goods_stock
        WHERE source_batch_id = ? AND product_name_snapshot = ? AND bottle_size_ml = ? AND location_status = 'lab'
        LIMIT 1
        """,
        (int(source_batch_id), str(product_name_snapshot or "").strip(), bottle_size),
    ).fetchone()
    if fg:
        fg_id = int(fg["id"])
        db.execute(
            """
            UPDATE finished_goods_stock
            SET quantity_on_hand = quantity_on_hand + ?, cost_per_bottle = ?, default_wholesale_price = ?,
                default_retail_reference_price = ?, updated_at = ?
            WHERE id = ?
            """,
            (qty, unit_cost, float(default_wholesale_price or 0.0), float(default_retail_reference_price or 0.0), datetime.now(), fg_id),
        )
    else:
        db.execute(
            """
            INSERT INTO finished_goods_stock (
                recipe_id, recipe_version_id, source_batch_id, decant_event_id, product_name_snapshot,
                bottle_size_ml, quantity_on_hand, quantity_reserved, quantity_at_clients, cost_per_bottle,
                default_wholesale_price, default_retail_reference_price, location_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, 'lab', ?, ?)
            """,
            (
                int(recipe_id) if recipe_id else None,
                int(recipe_version_id) if recipe_version_id else None,
                int(source_batch_id),
                int(decant_event_id),
                str(product_name_snapshot or "").strip(),
                bottle_size,
                qty,
                unit_cost,
                float(default_wholesale_price or 0.0),
                float(default_retail_reference_price or 0.0),
                datetime.now(),
                datetime.now(),
            ),
        )
        fg_id = int(db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

    # Keep legacy finished_goods_inventory in sync for compatibility.
    legacy = db.execute(
        """
        SELECT id FROM finished_goods_inventory
        WHERE production_batch_id = ? AND product_name = ? AND bottle_size_ml = ? AND active = 1
        LIMIT 1
        """,
        (int(source_batch_id), str(product_name_snapshot or "").strip(), bottle_size),
    ).fetchone()
    if legacy:
        db.execute(
            "UPDATE finished_goods_inventory SET quantity_on_hand = quantity_on_hand + ? WHERE id = ?",
            (qty, int(legacy["id"])),
        )
    else:
        db.execute(
            """
            INSERT INTO finished_goods_inventory (production_batch_id, recipe_id, product_name, bottle_size_ml, quantity_on_hand, active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (int(source_batch_id), int(recipe_id) if recipe_id else None, str(product_name_snapshot or "").strip(), bottle_size, qty),
        )

    _insert_stock_movement(
        movement_type="decanted_out_of_bulk",
        actor_id=actor_id,
        source_batch_id=int(source_batch_id),
        decant_event_id=int(decant_event_id),
        qty_ml=float(total_ml),
        notes=str(notes or ""),
    )
    _insert_stock_movement(
        movement_type="decanted_into_finished_goods",
        actor_id=actor_id,
        source_batch_id=int(source_batch_id),
        decant_event_id=int(decant_event_id),
        finished_goods_stock_id=int(fg_id),
        qty_bottles=int(qty),
        qty_ml=float(total_ml),
        amount_value=float(cost_total_snapshot or 0.0),
        notes=str(notes or ""),
    )
    _log_admin_action(
        actor_id=actor_id,
        action="DECANT_EVENT_CREATE",
        details=f"decant_event_id={decant_event_id} source_batch_id={source_batch_id} bottles={qty}",
    )
    db.commit()
    return {"status": "success", "decant_event_id": decant_event_id, "finished_goods_stock_id": fg_id}, 200


def create_delivery(
    *,
    actor_id: int,
    client_id: int,
    delivery_type: str,
    delivery_date: str,
    notes: str,
    lines: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], int]:
    db = get_db()
    client = db.execute("SELECT id, name FROM clients WHERE id = ? AND is_active = 1", (int(client_id),)).fetchone()
    if not client:
        return {"status": "error", "message": "Client not found."}, 404
    dtype = str(delivery_type or "consignment").strip().lower()
    if dtype not in {"consignment", "direct_sale"}:
        dtype = "consignment"
    prepared: List[Dict[str, Any]] = []
    requested_by_fg: Dict[int, int] = {}
    for ln in lines:
        fg_id = _to_int(ln.get("finished_goods_stock_id"))
        qty_sent = _to_int(ln.get("qty_sent"))
        unit_price = _to_float(ln.get("unit_price_snapshot"))
        if fg_id <= 0 or qty_sent <= 0:
            continue
        fg = db.execute("SELECT * FROM finished_goods_stock WHERE id = ?", (fg_id,)).fetchone()
        if not fg:
            return {"status": "error", "message": f"Finished goods item #{fg_id} not found."}, 404
        on_hand = _to_int(fg["quantity_on_hand"])
        if qty_sent > on_hand:
            return {"status": "error", "message": f"Insufficient stock for {fg['product_name_snapshot']} ({on_hand} on hand)."}, 400
        if unit_price < 0:
            return {"status": "error", "message": "Unit price cannot be negative."}, 400
        requested_by_fg[fg_id] = requested_by_fg.get(fg_id, 0) + qty_sent
        prepared.append({"fg": dict(fg), "qty_sent": qty_sent, "unit_price": unit_price})
    for fg_id, qty_total in requested_by_fg.items():
        fg = next((x["fg"] for x in prepared if int(x["fg"]["id"]) == int(fg_id)), None)
        if fg is None:
            continue
        on_hand = _to_int(fg["quantity_on_hand"])
        if qty_total > on_hand:
            return {"status": "error", "message": f"Total qty for item #{fg_id} exceeds on-hand ({on_hand})."}, 400
    if not prepared:
        return {"status": "error", "message": "Add at least one valid delivery line."}, 400

    dnum = _next_delivery_number()
    db.execute(
        """
        INSERT INTO client_deliveries (client_id, delivery_number, delivery_date, delivery_type, status, notes, created_by_user_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?)
        """,
        (
            int(client_id),
            dnum,
            delivery_date if delivery_date else datetime.now().strftime("%Y-%m-%d"),
            dtype,
            str(notes or ""),
            int(actor_id or 0),
            datetime.now(),
            datetime.now(),
        ),
    )
    delivery_id = int(db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
    for ln in prepared:
        fg = ln["fg"]
        qty_sent = int(ln["qty_sent"])
        unit_price = float(ln["unit_price"])
        total = qty_sent * unit_price
        db.execute(
            """
            INSERT INTO client_delivery_lines (
                delivery_id, finished_goods_stock_id, source_batch_id, product_name_snapshot, bottle_size_ml,
                qty_sent, unit_price_snapshot, total_value_snapshot, qty_reported_sold, qty_returned, qty_damaged,
                qty_remaining_with_client, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?)
            """,
            (
                int(delivery_id),
                int(fg["id"]),
                fg.get("source_batch_id"),
                fg.get("product_name_snapshot"),
                float(fg.get("bottle_size_ml") or 0.0),
                qty_sent,
                unit_price,
                total,
                qty_sent,
                datetime.now(),
                datetime.now(),
            ),
        )
        line_id = int(db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
        db.execute(
            """
            UPDATE finished_goods_stock
            SET quantity_on_hand = quantity_on_hand - ?, quantity_at_clients = quantity_at_clients + ?, updated_at = ?
            WHERE id = ? AND quantity_on_hand >= ?
            """,
            (qty_sent, qty_sent, datetime.now(), int(fg["id"]), qty_sent),
        )
        if int(
            db.execute("SELECT changes() AS c").fetchone()["c"] or 0
        ) <= 0:
            return {"status": "error", "message": f"Stock changed while creating delivery for item #{fg['id']}."}, 409
        _sync_legacy_finished_goods_delta(fg_row=fg, qty_delta=-qty_sent)
        _insert_stock_movement(
            movement_type="delivered_to_client",
            actor_id=actor_id,
            source_batch_id=fg.get("source_batch_id"),
            finished_goods_stock_id=int(fg["id"]),
            client_id=int(client_id),
            delivery_id=int(delivery_id),
            delivery_line_id=int(line_id),
            qty_bottles=qty_sent,
            qty_ml=float(fg.get("bottle_size_ml") or 0.0) * qty_sent,
            amount_value=float(total),
            notes=f"delivery_type={dtype}",
        )
    _update_delivery_status(delivery_id)
    _log_admin_action(
        actor_id=actor_id,
        action="CLIENT_DELIVERY_CREATE",
        details=f"delivery_id={delivery_id} client_id={client_id} type={dtype}",
    )
    db.commit()
    return {"status": "success", "delivery_id": delivery_id}, 200


def reconcile_delivery(
    *,
    actor_id: int,
    delivery_id: int,
    line_updates: List[Dict[str, Any]],
    allow_closed_override: bool = False,
) -> Tuple[Dict[str, Any], int]:
    db = get_db()
    delivery = db.execute("SELECT id, client_id, status FROM client_deliveries WHERE id = ?", (int(delivery_id),)).fetchone()
    if not delivery:
        return {"status": "error", "message": "Delivery not found."}, 404
    if str(_row_value(delivery, "status", "") or "") == "closed" and not allow_closed_override:
        return {"status": "error", "message": "Closed delivery cannot be reconciled."}, 400
    for upd in line_updates:
        line_id = _to_int(upd.get("line_id"))
        if line_id <= 0:
            continue
        line = db.execute("SELECT * FROM client_delivery_lines WHERE id = ? AND delivery_id = ?", (line_id, int(delivery_id))).fetchone()
        if not line:
            return {"status": "error", "message": f"Delivery line #{line_id} not found."}, 404
        qty_sent = _to_int(line["qty_sent"])
        sold = max(0, _to_int(upd.get("qty_reported_sold"), _to_int(line["qty_reported_sold"])))
        returned = max(0, _to_int(upd.get("qty_returned"), _to_int(line["qty_returned"])))
        damaged = max(0, _to_int(upd.get("qty_damaged"), _to_int(line["qty_damaged"])))
        if sold + returned + damaged > qty_sent:
            return {"status": "error", "message": f"Line #{line_id}: sold+returned+damaged exceeds qty sent."}, 400
        remaining = qty_sent - sold - returned - damaged

        old_sold = _to_int(line["qty_reported_sold"])
        old_returned = _to_int(line["qty_returned"])
        old_damaged = _to_int(line["qty_damaged"])
        if sold < old_sold or returned < old_returned or damaged < old_damaged:
            return {
                "status": "error",
                "message": f"Line #{line_id}: existing sold/returned/damaged values cannot be reduced.",
            }, 400

        delta_sold = sold - old_sold
        delta_returned = returned - old_returned
        delta_damaged = damaged - old_damaged

        db.execute(
            """
            UPDATE client_delivery_lines
            SET qty_reported_sold = ?, qty_returned = ?, qty_damaged = ?, qty_remaining_with_client = ?, updated_at = ?
            WHERE id = ?
            """,
            (sold, returned, damaged, remaining, datetime.now(), int(line_id)),
        )

        fg_id = _to_int(line["finished_goods_stock_id"])
        if fg_id > 0:
            if delta_returned > 0:
                db.execute(
                    "UPDATE finished_goods_stock SET quantity_on_hand = quantity_on_hand + ?, updated_at = ? WHERE id = ?",
                    (delta_returned, datetime.now(), fg_id),
                )
                _sync_legacy_finished_goods_delta(fg_row=dict(line), qty_delta=delta_returned)
                _insert_stock_movement(
                    movement_type="returned_from_client",
                    actor_id=actor_id,
                    finished_goods_stock_id=fg_id,
                    client_id=int(delivery["client_id"]),
                    delivery_id=int(delivery_id),
                    delivery_line_id=int(line_id),
                    qty_bottles=delta_returned,
                    qty_ml=float(line["bottle_size_ml"] or 0.0) * delta_returned,
                )
            if delta_sold > 0:
                _insert_stock_movement(
                    movement_type="marked_sold_by_client",
                    actor_id=actor_id,
                    finished_goods_stock_id=fg_id,
                    client_id=int(delivery["client_id"]),
                    delivery_id=int(delivery_id),
                    delivery_line_id=int(line_id),
                    qty_bottles=delta_sold,
                    qty_ml=float(line["bottle_size_ml"] or 0.0) * delta_sold,
                    amount_value=float(line["unit_price_snapshot"] or 0.0) * delta_sold,
                )
            if delta_damaged > 0:
                _insert_stock_movement(
                    movement_type="damaged_at_client",
                    actor_id=actor_id,
                    finished_goods_stock_id=fg_id,
                    client_id=int(delivery["client_id"]),
                    delivery_id=int(delivery_id),
                    delivery_line_id=int(line_id),
                    qty_bottles=delta_damaged,
                    qty_ml=float(line["bottle_size_ml"] or 0.0) * delta_damaged,
                )
            _refresh_finished_goods_at_clients(fg_id)

    _update_delivery_status(int(delivery_id))
    _log_admin_action(actor_id=actor_id, action="DELIVERY_RECONCILE", details=f"delivery_id={delivery_id}")
    db.commit()
    return {"status": "success"}, 200


def record_payment(
    *,
    actor_id: int,
    client_id: int,
    payment_date: str,
    amount: float,
    payment_method: str,
    reference: str,
    notes: str,
    allocations: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], int]:
    db = get_db()
    amt = _to_float(amount)
    if amt <= 0:
        return {"status": "error", "message": "Payment amount must be greater than zero."}, 400
    c = db.execute("SELECT id FROM clients WHERE id = ?", (int(client_id),)).fetchone()
    if not c:
        return {"status": "error", "message": "Client not found."}, 404
    alloc_total = 0.0
    due_by_delivery: Dict[int, float] = {}
    normalized_allocs: List[Tuple[int, float]] = []
    for a in allocations:
        delivery_id = _to_int(a.get("delivery_id"))
        amount_allocated = _to_float(a.get("amount_allocated"))
        if delivery_id <= 0 or amount_allocated <= 0:
            continue
        if alloc_total + amount_allocated > amt + 1e-9:
            return {"status": "error", "message": "Allocated amount exceeds payment amount."}, 400
        d = db.execute("SELECT id, client_id FROM client_deliveries WHERE id = ?", (int(delivery_id),)).fetchone()
        if not d:
            return {"status": "error", "message": f"Delivery #{delivery_id} not found for allocation."}, 404
        if int(d["client_id"]) != int(client_id):
            return {"status": "error", "message": f"Delivery #{delivery_id} belongs to a different client."}, 400
        if delivery_id not in due_by_delivery:
            due_by_delivery[delivery_id] = float(_delivery_totals(int(delivery_id)).get("due_value") or 0.0)
        if amount_allocated > due_by_delivery[delivery_id] + 1e-9:
            return {"status": "error", "message": f"Allocation for delivery #{delivery_id} exceeds delivery due."}, 400
        alloc_total += amount_allocated
        due_by_delivery[delivery_id] = max(0.0, due_by_delivery[delivery_id] - amount_allocated)
        normalized_allocs.append((int(delivery_id), float(amount_allocated)))

    try:
        db.execute(
            """
            INSERT INTO client_payments (client_id, payment_date, amount, payment_method, reference, notes, created_by_user_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(client_id),
                payment_date if payment_date else datetime.now().strftime("%Y-%m-%d"),
                amt,
                str(payment_method or ""),
                str(reference or ""),
                str(notes or ""),
                int(actor_id or 0),
                datetime.now(),
            ),
        )
        payment_id = int(db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
        for delivery_id, amount_allocated in normalized_allocs:
            db.execute(
                """
                INSERT INTO client_payment_allocations (payment_id, delivery_id, amount_allocated, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (int(payment_id), int(delivery_id), amount_allocated, datetime.now()),
            )
            _log_admin_action(
                actor_id=actor_id,
                action="CLIENT_PAYMENT_ALLOCATE",
                details=f"payment_id={payment_id} delivery_id={delivery_id} amount={amount_allocated:.2f}",
            )
            _update_delivery_status(int(delivery_id))
    except Exception:
        db.rollback()
        return {"status": "error", "message": "Failed to record payment transaction."}, 500
    _insert_stock_movement(
        movement_type="payment_recorded",
        actor_id=actor_id,
        client_id=int(client_id),
        amount_value=amt,
        notes=f"payment_id={payment_id}",
    )
    _log_admin_action(actor_id=actor_id, action="CLIENT_PAYMENT_RECORD", details=f"payment_id={payment_id} amount={amt:.2f}")
    db.commit()
    return {"status": "success", "payment_id": payment_id}, 200


def list_deliveries(*, client_id: int | None = None) -> List[Dict[str, Any]]:
    db = get_db()
    if client_id:
        rows = db.execute(
            """
            SELECT d.*, c.name AS client_name
            FROM client_deliveries d
            JOIN clients c ON c.id = d.client_id
            WHERE d.client_id = ?
            ORDER BY d.delivery_date DESC, d.id DESC
            """,
            (int(client_id),),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT d.*, c.name AS client_name
            FROM client_deliveries d
            JOIN clients c ON c.id = d.client_id
            ORDER BY d.delivery_date DESC, d.id DESC
            LIMIT 300
            """
        ).fetchall()
    out = _rowdicts(rows)
    for d in out:
        d.update(_delivery_totals(int(d["id"])))
    return out


def get_delivery_detail(delivery_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    h = db.execute(
        """
        SELECT d.*, c.name AS client_name, c.client_type, c.payment_terms_days
        FROM client_deliveries d
        JOIN clients c ON c.id = d.client_id
        WHERE d.id = ?
        """,
        (int(delivery_id),),
    ).fetchone()
    if not h:
        return None
    lines = db.execute(
        """
        SELECT *
        FROM client_delivery_lines
        WHERE delivery_id = ?
        ORDER BY id
        """,
        (int(delivery_id),),
    ).fetchall()
    d = dict(h)
    d["lines"] = _rowdicts(lines)
    d["corrections"] = _rowdicts(
        db.execute(
            """
            SELECT c.*, u.username AS created_by_username
            FROM delivery_line_corrections c
            LEFT JOIN users u ON u.id = c.created_by_user_id
            WHERE c.delivery_id = ?
            ORDER BY c.created_at DESC, c.id DESC
            """,
            (int(delivery_id),),
        ).fetchall()
    )
    d.update(_delivery_totals(int(delivery_id)))
    return d


def client_financial_summary(client_id: int) -> Dict[str, Any]:
    deliveries = list_deliveries(client_id=int(client_id))
    owed = sum(float(d.get("due_value") or 0.0) for d in deliveries)
    p = get_db().execute(
        "SELECT COALESCE(SUM(amount), 0.0) AS paid FROM client_payments WHERE client_id = ?",
        (int(client_id),),
    ).fetchone()
    paid = float(_row_value(p, "paid", 0.0) or 0.0)
    at_client = get_db().execute(
        """
        SELECT COALESCE(SUM(qty_remaining_with_client), 0.0) AS q
        FROM client_delivery_lines l
        JOIN client_deliveries d ON d.id = l.delivery_id
        WHERE d.client_id = ?
        """,
        (int(client_id),),
    ).fetchone()
    return {
        "total_owed": round(owed, 2),
        "total_paid": round(paid, 2),
        "stock_currently_at_client": int(_to_float(_row_value(at_client, "q", 0.0))),
    }


def get_client_detail(client_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    c = db.execute("SELECT * FROM clients WHERE id = ?", (int(client_id),)).fetchone()
    if not c:
        return None
    d = dict(c)
    d["deliveries"] = list_deliveries(client_id=int(client_id))
    d["payments"] = _rowdicts(
        db.execute(
            "SELECT * FROM client_payments WHERE client_id = ? ORDER BY payment_date DESC, id DESC",
            (int(client_id),),
        ).fetchall()
    )
    d["summary"] = client_financial_summary(int(client_id))
    return d


def sales_dashboard_summary() -> Dict[str, Any]:
    db = get_db()
    clients = list_clients()
    deliveries = list_deliveries()
    outstanding = sum(float(d.get("due_value") or 0.0) for d in deliveries)
    at_clients = db.execute(
        "SELECT COALESCE(SUM(quantity_at_clients), 0.0) AS q FROM finished_goods_stock WHERE location_status != 'archived'"
    ).fetchone()
    month_paid = db.execute(
        """
        SELECT COALESCE(SUM(amount), 0.0) AS v
        FROM client_payments
        WHERE strftime('%Y-%m', payment_date) = strftime('%Y-%m', 'now')
        """
    ).fetchone()
    top_products = db.execute(
        """
        SELECT product_name_snapshot, bottle_size_ml, COALESCE(SUM(qty_reported_sold),0) AS sold_qty
        FROM client_delivery_lines
        GROUP BY product_name_snapshot, bottle_size_ml
        ORDER BY sold_qty DESC, product_name_snapshot ASC
        LIMIT 8
        """
    ).fetchall()
    overdue = [d for d in deliveries if float(d.get("due_value") or 0.0) > 0.0]
    return {
        "total_outstanding_balances": round(outstanding, 2),
        "total_stock_currently_at_clients": int(_to_float(_row_value(at_clients, "q", 0.0))),
        "payments_received_this_month": round(float(_row_value(month_paid, "v", 0.0) or 0.0), 2),
        "top_selling_products": _rowdicts(top_products),
        "overdue_deliveries": overdue[:20],
        "recent_deliveries": deliveries[:20],
        "recent_payments": _rowdicts(
            db.execute(
                """
                SELECT p.*, c.name AS client_name
                FROM client_payments p
                JOIN clients c ON c.id = p.client_id
                ORDER BY p.payment_date DESC, p.id DESC
                LIMIT 20
                """
            ).fetchall()
        ),
        "client_count": len(clients),
    }


def client_statement_snapshot(client_id: int, date_from: str | None, date_to: str | None) -> Dict[str, Any]:
    db = get_db()
    c = db.execute("SELECT * FROM clients WHERE id = ?", (int(client_id),)).fetchone()
    if not c:
        return {"status": "error", "message": "Client not found."}

    today = datetime.now().strftime("%Y-%m-%d")
    d_to = _normalize_date(date_to, today)
    # Default date_from to first day of month containing date_to.
    d_to_dt = datetime.strptime(d_to, "%Y-%m-%d")
    default_from = d_to_dt.replace(day=1).strftime("%Y-%m-%d")
    d_from = _normalize_date(date_from, default_from)
    if d_from > d_to:
        d_from, d_to = d_to, d_from

    deliveries_rows = db.execute(
        """
        SELECT *
        FROM client_deliveries
        WHERE client_id = ? AND date(delivery_date) <= date(?)
        ORDER BY date(delivery_date) ASC, id ASC
        """,
        (int(client_id), d_to),
    ).fetchall()
    deliveries_all = _rowdicts(deliveries_rows)
    in_period: List[Dict[str, Any]] = []
    opening_due = 0.0
    closing_due = 0.0
    period_sent = 0.0
    period_sold = 0.0
    period_returned_qty = 0
    period_returned_value = 0.0
    period_damaged_qty = 0
    period_damaged_value = 0.0

    for d in deliveries_all:
        did = int(d["id"])
        totals_to_end = _delivery_totals_as_of(delivery_id=did, as_of_date=d_to)
        totals_before = _delivery_totals_as_of(
            delivery_id=did,
            as_of_date=(datetime.strptime(d_from, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d"),
        )
        if datetime.strptime(str(d["delivery_date"])[:10], "%Y-%m-%d").strftime("%Y-%m-%d") < d_from:
            opening_due += float(totals_before["due_value"])
        closing_due += float(totals_to_end["due_value"])

        if d_from <= str(d["delivery_date"])[:10] <= d_to:
            line = db.execute(
                """
                SELECT
                  COALESCE(SUM(qty_returned),0) AS q_ret,
                  COALESCE(SUM(qty_damaged),0) AS q_dmg,
                  COALESCE(SUM(qty_returned * unit_price_snapshot),0.0) AS v_ret,
                  COALESCE(SUM(qty_damaged * unit_price_snapshot),0.0) AS v_dmg
                FROM client_delivery_lines
                WHERE delivery_id = ?
                """,
                (did,),
            ).fetchone()
            paid_period = db.execute(
                """
                SELECT COALESCE(SUM(a.amount_allocated),0.0) AS v
                FROM client_payment_allocations a
                JOIN client_payments p ON p.id = a.payment_id
                WHERE a.delivery_id = ? AND date(p.payment_date) >= date(?) AND date(p.payment_date) <= date(?)
                """,
                (did, d_from, d_to),
            ).fetchone()
            entry = {
                "delivery_id": did,
                "delivery_number": d.get("delivery_number"),
                "delivery_date": d.get("delivery_date"),
                "delivery_type": d.get("delivery_type"),
                "status": d.get("status"),
                "has_corrections": bool(d.get("has_corrections")),
                "sent_value": float(totals_to_end["sent_value"]),
                "sold_value": float(totals_to_end["sold_value"]),
                "payments_allocated_in_period": float(_row_value(paid_period, "v", 0.0) or 0.0),
                "outstanding_after_allocations": float(totals_to_end["due_value"]),
            }
            in_period.append(entry)
            period_sent += entry["sent_value"]
            period_sold += entry["sold_value"]
            q_ret = int(_to_int(_row_value(line, "q_ret", 0)))
            q_dmg = int(_to_int(_row_value(line, "q_dmg", 0)))
            v_ret = float(_to_float(_row_value(line, "v_ret", 0.0)))
            v_dmg = float(_to_float(_row_value(line, "v_dmg", 0.0)))
            period_returned_qty += q_ret
            period_damaged_qty += q_dmg
            period_returned_value += v_ret
            period_damaged_value += v_dmg

    payments_period = db.execute(
        """
        SELECT COALESCE(SUM(amount), 0.0) AS total
        FROM client_payments
        WHERE client_id = ? AND date(payment_date) >= date(?) AND date(payment_date) <= date(?)
        """,
        (int(client_id), d_from, d_to),
    ).fetchone()
    period_payments_received = float(_row_value(payments_period, "total", 0.0) or 0.0)

    closing_stock_rows = db.execute(
        """
        SELECT
          l.product_name_snapshot,
          l.bottle_size_ml,
          COALESCE(SUM(l.qty_remaining_with_client),0) AS qty_still_held,
          GROUP_CONCAT(DISTINCT d.delivery_number) AS delivery_refs
        FROM client_delivery_lines l
        JOIN client_deliveries d ON d.id = l.delivery_id
        WHERE d.client_id = ? AND date(d.delivery_date) <= date(?)
        GROUP BY l.product_name_snapshot, l.bottle_size_ml
        HAVING COALESCE(SUM(l.qty_remaining_with_client),0) > 0
        ORDER BY l.product_name_snapshot COLLATE NOCASE, l.bottle_size_ml
        """,
        (int(client_id), d_to),
    ).fetchall()
    closing_stock = _rowdicts(closing_stock_rows)

    opening_stock_rows = db.execute(
        """
        SELECT
          l.product_name_snapshot,
          l.bottle_size_ml,
          COALESCE(SUM(l.qty_remaining_with_client),0) AS qty_still_held
        FROM client_delivery_lines l
        JOIN client_deliveries d ON d.id = l.delivery_id
        WHERE d.client_id = ? AND date(d.delivery_date) < date(?)
        GROUP BY l.product_name_snapshot, l.bottle_size_ml
        HAVING COALESCE(SUM(l.qty_remaining_with_client),0) > 0
        ORDER BY l.product_name_snapshot COLLATE NOCASE, l.bottle_size_ml
        """,
        (int(client_id), d_from),
    ).fetchall()

    open_deliveries = [x for x in list_deliveries(client_id=int(client_id)) if float(x.get("due_value") or 0.0) > 0.0]

    return {
        "status": "success",
        "client": {
            "id": int(c["id"]),
            "name": c["name"],
            "client_type": c.get("client_type"),
            "payment_terms_days": int(_to_int(c.get("payment_terms_days"), 0)),
        },
        "date_from": d_from,
        "date_to": d_to,
        "opening": {
            "opening_balance_due": round(opening_due, 2),
            "opening_stock_held": _rowdicts(opening_stock_rows),
        },
        "activity": {
            "deliveries": in_period,
            "totals": {
                "total_sent_value": round(period_sent, 2),
                "total_sold_value": round(period_sold, 2),
                "total_returned_qty": int(period_returned_qty),
                "total_returned_value": round(period_returned_value, 2),
                "total_damaged_qty": int(period_damaged_qty),
                "total_damaged_value": round(period_damaged_value, 2),
                "total_payments_received": round(period_payments_received, 2),
                "net_movement_amount_due": round(closing_due - opening_due, 2),
            },
        },
        "closing": {
            "closing_balance_due": round(closing_due, 2),
            "stock_currently_held": closing_stock,
            "open_deliveries": open_deliveries,
            "has_corrected_deliveries": any(bool(d.get("has_corrections")) for d in in_period),
            "overdue_unpaid_count": len([d for d in open_deliveries if float(d.get("due_value") or 0.0) > 0]),
        },
    }


def generate_whatsapp_statement_summary(
    client_id: int,
    date_from: str | None,
    date_to: str | None,
    tone: str = "neutral",
) -> Dict[str, Any]:
    snap = client_statement_snapshot(int(client_id), date_from=date_from, date_to=date_to)
    if snap.get("status") != "success":
        return snap

    d_from = str(snap.get("date_from") or "")
    d_to = str(snap.get("date_to") or "")
    try:
        d1 = datetime.strptime(d_from, "%Y-%m-%d")
        d2 = datetime.strptime(d_to, "%Y-%m-%d")
        if d1.year == d2.year and d1.month == d2.month and d1.day == 1:
            period_label = d2.strftime("%B %Y")
        elif (d2 - d1).days == 30:
            period_label = "Last 30 Days"
        else:
            period_label = f"{d_from} to {d_to}"
    except Exception:
        period_label = f"{d_from} to {d_to}" if d_from and d_to else "Selected Period"

    client = snap.get("client") or {}
    totals = (snap.get("activity") or {}).get("totals") or {}
    closing = snap.get("closing") or {}
    held = closing.get("stock_currently_held") or []
    held_lines: List[str] = []
    for item in held:
        qty = int(_to_int(item.get("qty_still_held"), 0))
        if qty <= 0:
            continue
        held_lines.append(
            f"• {item.get('product_name_snapshot')} {float(_to_float(item.get('bottle_size_ml'), 0)):.0f}ml x {qty}"
        )
    if not held_lines:
        held_lines = ["• None"]

    tone_norm = str(tone or "neutral").strip().lower()
    if tone_norm not in {"neutral", "old_goat"}:
        tone_norm = "neutral"
    corrected_flag = "⚠️ Includes corrected deliveries" if bool(closing.get("has_corrected_deliveries")) else ""
    if tone_norm == "old_goat":
        msg_lines = [
            f"🧾 *Old Goat Reckoning*",
            f"🔥 *Client:* {client.get('name')}",
            f"🔥 *Run:* {period_label}",
            "",
            f"🔥 *Sold through:* R {float(_to_float(totals.get('total_sold_value'), 0.0)):.2f}",
            f"💸 *Cash in:* R {float(_to_float(totals.get('total_payments_received'), 0.0)):.2f}",
            f"⚠️ *Still owing:* R {float(_to_float(closing.get('closing_balance_due'), 0.0)):.2f}",
            "",
            "📦 *Stock still parked at client:*",
            *held_lines,
        ]
    else:
        msg_lines = [
            f"🧾 *Client Statement Summary*",
            f"🔥 *Client:* {client.get('name')}",
            f"🔥 *Period:* {period_label}",
            "",
            f"🔥 *Sold (period):* R {float(_to_float(totals.get('total_sold_value'), 0.0)):.2f}",
            f"💸 *Payments received (period):* R {float(_to_float(totals.get('total_payments_received'), 0.0)):.2f}",
            f"⚠️ *Closing outstanding:* R {float(_to_float(closing.get('closing_balance_due'), 0.0)):.2f}",
            "",
            "📦 *Stock still held by client:*",
            *held_lines,
        ]
    if corrected_flag:
        msg_lines.extend(["", corrected_flag])

    return {
        "status": "success",
        "client_id": int(client_id),
        "date_from": d_from,
        "date_to": d_to,
        "period_label": period_label,
        "tone": tone_norm,
        "message": "\n".join(msg_lines).strip(),
    }


def delivery_consistency_snapshot(delivery_id: int) -> Dict[str, Any]:
    detail = get_delivery_detail(int(delivery_id))
    if not detail:
        return {"status": "error", "message": "Delivery not found."}
    line_checks: List[Dict[str, Any]] = []
    ok = True
    for ln in detail.get("lines") or []:
        sent = int(ln.get("qty_sent") or 0)
        sold = int(ln.get("qty_reported_sold") or 0)
        ret = int(ln.get("qty_returned") or 0)
        dmg = int(ln.get("qty_damaged") or 0)
        rem = int(ln.get("qty_remaining_with_client") or 0)
        expected_rem = sent - sold - ret - dmg
        line_ok = expected_rem == rem and expected_rem >= 0
        if not line_ok:
            ok = False
        line_checks.append(
            {
                "line_id": int(ln.get("id") or 0),
                "qty_sent": sent,
                "qty_reported_sold": sold,
                "qty_returned": ret,
                "qty_damaged": dmg,
                "qty_remaining_with_client": rem,
                "expected_remaining": expected_rem,
                "line_ok": line_ok,
            }
        )
    totals = _delivery_totals(int(delivery_id))
    return {"status": "success", "ok": ok, "delivery_id": int(delivery_id), "totals": totals, "line_checks": line_checks}


def delivery_truth_snapshot(delivery_id: int) -> Dict[str, Any]:
    detail = get_delivery_detail(int(delivery_id))
    if not detail:
        return {"status": "error", "message": "Delivery not found."}
    consistency = delivery_consistency_snapshot(int(delivery_id))
    if consistency.get("status") != "success":
        return consistency
    check_by_line = {int(c["line_id"]): c for c in (consistency.get("line_checks") or [])}
    lines_out: List[Dict[str, Any]] = []
    total_remaining = 0
    total_returned = 0
    total_damaged = 0
    for ln in (detail.get("lines") or []):
        line_id = int(ln.get("id") or 0)
        sent = int(ln.get("qty_sent") or 0)
        sold = int(ln.get("qty_reported_sold") or 0)
        returned = int(ln.get("qty_returned") or 0)
        damaged = int(ln.get("qty_damaged") or 0)
        stored_remaining = int(ln.get("qty_remaining_with_client") or 0)
        unit_price = float(ln.get("unit_price_snapshot") or 0.0)
        sent_value = float(sent) * unit_price
        sold_value = float(sold) * unit_price
        chk = check_by_line.get(line_id) or {}
        expected_remaining = int(chk.get("expected_remaining") or (sent - sold - returned - damaged))
        line_ok = bool(chk.get("line_ok")) if "line_ok" in chk else (expected_remaining == stored_remaining)
        lines_out.append(
            {
                "line_id": line_id,
                "product_name_snapshot": ln.get("product_name_snapshot"),
                "bottle_size_ml": float(ln.get("bottle_size_ml") or 0.0),
                "qty_sent": sent,
                "qty_reported_sold": sold,
                "qty_returned": returned,
                "qty_damaged": damaged,
                "qty_remaining_with_client": stored_remaining,
                "unit_price_snapshot": unit_price,
                "sent_value": sent_value,
                "sold_value": sold_value,
                "expected_remaining": expected_remaining,
                "stored_remaining": stored_remaining,
                "consistency_status": ("ok" if line_ok else "mismatch"),
            }
        )
        total_remaining += stored_remaining
        total_returned += returned
        total_damaged += damaged
    totals = _delivery_totals(int(delivery_id))
    return {
        "status": "success",
        "delivery_id": int(delivery_id),
        "delivery_number": detail.get("delivery_number"),
        "client_name": detail.get("client_name"),
        "delivery_type": detail.get("delivery_type"),
        "delivery_status": detail.get("status"),
        "has_corrections": bool(detail.get("has_corrections")),
        "overall_consistency_ok": bool(consistency.get("ok")),
        "lines": lines_out,
        "totals": {
            "total_sent_value": float(totals.get("sent_value") or 0.0),
            "total_sold_value": float(totals.get("sold_value") or 0.0),
            "total_payments_allocated": float(totals.get("paid_value") or 0.0),
            "total_outstanding": float(totals.get("due_value") or 0.0),
            "total_qty_remaining_with_client": int(total_remaining),
            "total_qty_returned": int(total_returned),
            "total_qty_damaged": int(total_damaged),
        },
    }


def apply_delivery_line_correction(
    *,
    actor_id: int,
    delivery_id: int,
    delivery_line_id: int,
    correction_type: str,
    quantity: int,
    reason: str,
    allow_closed_override: bool = False,
) -> Tuple[Dict[str, Any], int]:
    db = get_db()
    qty = max(0, int(_to_int(quantity, 0)))
    rsn = str(reason or "").strip()
    if qty <= 0:
        return {"status": "error", "message": "Correction quantity must be greater than zero."}, 400
    if not rsn:
        return {"status": "error", "message": "Correction reason is required."}, 400

    delivery = db.execute(
        "SELECT id, client_id, status FROM client_deliveries WHERE id = ?",
        (int(delivery_id),),
    ).fetchone()
    if not delivery:
        return {"status": "error", "message": "Delivery not found."}, 404
    if str(_row_value(delivery, "status", "") or "") == "closed" and not allow_closed_override:
        return {"status": "error", "message": "Closed delivery cannot be corrected."}, 400

    line = db.execute(
        "SELECT * FROM client_delivery_lines WHERE id = ? AND delivery_id = ?",
        (int(delivery_line_id), int(delivery_id)),
    ).fetchone()
    if not line:
        return {"status": "error", "message": "Delivery line not found."}, 404

    try:
        from_state, to_state = _state_pair_for_correction(correction_type)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}, 400
    if from_state == to_state:
        return {"status": "error", "message": "Invalid correction state mapping."}, 400

    cols = _line_state_columns()
    from_col = cols[from_state]
    to_col = cols[to_state]
    from_val = int(_to_int(line[from_col], 0))
    to_val = int(_to_int(line[to_col], 0))
    if qty > from_val:
        return {"status": "error", "message": f"Cannot move {qty}; only {from_val} available in {from_state}."}, 400

    sold = int(_to_int(line["qty_reported_sold"], 0))
    returned = int(_to_int(line["qty_returned"], 0))
    damaged = int(_to_int(line["qty_damaged"], 0))
    remaining = int(_to_int(line["qty_remaining_with_client"], 0))
    states = {"sold": sold, "returned": returned, "damaged": damaged, "remaining": remaining}
    states[from_state] -= qty
    states[to_state] += qty
    if any(v < 0 for v in states.values()):
        return {"status": "error", "message": "Correction would make line quantities negative."}, 400
    if states["sold"] + states["returned"] + states["damaged"] + states["remaining"] != int(_to_int(line["qty_sent"], 0)):
        return {"status": "error", "message": "Correction would break line quantity conservation."}, 400

    # Payment integrity: correction cannot make due < already allocated.
    current_totals = _delivery_totals(int(delivery_id))
    old_sold_value = sold * float(_to_float(line["unit_price_snapshot"], 0.0))
    new_sold_value = states["sold"] * float(_to_float(line["unit_price_snapshot"], 0.0))
    projected_sold_total = float(current_totals["sold_value"]) - old_sold_value + new_sold_value
    paid_value = float(current_totals["paid_value"])
    dtype = str(_row_value(delivery, "delivery_type", "consignment"))
    if dtype == "consignment" and projected_sold_total + 1e-9 < paid_value:
        return {"status": "error", "message": "Correction blocked: sold value would fall below allocated payments."}, 400

    fg_id = int(_to_int(line["finished_goods_stock_id"], 0))
    fg_row = None
    if fg_id > 0:
        fg = db.execute("SELECT * FROM finished_goods_stock WHERE id = ?", (fg_id,)).fetchone()
        fg_row = dict(fg) if fg else None
    # Stock side effect only when return bucket changes.
    return_delta = states["returned"] - returned
    if fg_id > 0 and return_delta < 0:
        # Taking qty out of returned state removes stock from lab on-hand.
        cur = db.execute(
            "UPDATE finished_goods_stock SET quantity_on_hand = quantity_on_hand + ?, updated_at = ? WHERE id = ? AND quantity_on_hand >= ?",
            (return_delta, datetime.now(), fg_id, abs(return_delta)),
        )
        if int(getattr(cur, "rowcount", 0) or 0) <= 0:
            return {"status": "error", "message": "Correction blocked: insufficient lab stock to reverse returned quantity."}, 400
        if fg_row is not None:
            _sync_legacy_finished_goods_delta(fg_row=fg_row, qty_delta=return_delta)
    elif fg_id > 0 and return_delta > 0:
        db.execute(
            "UPDATE finished_goods_stock SET quantity_on_hand = quantity_on_hand + ?, updated_at = ? WHERE id = ?",
            (return_delta, datetime.now(), fg_id),
        )
        if fg_row is not None:
            _sync_legacy_finished_goods_delta(fg_row=fg_row, qty_delta=return_delta)

    db.execute(
        """
        UPDATE client_delivery_lines
        SET qty_reported_sold = ?, qty_returned = ?, qty_damaged = ?, qty_remaining_with_client = ?, updated_at = ?
        WHERE id = ?
        """,
        (states["sold"], states["returned"], states["damaged"], states["remaining"], datetime.now(), int(delivery_line_id)),
    )
    db.execute(
        """
        INSERT INTO delivery_line_corrections (
            delivery_id, delivery_line_id, correction_type, quantity, from_state, to_state, reason, created_by_user_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(delivery_id),
            int(delivery_line_id),
            str(correction_type),
            int(qty),
            from_state,
            to_state,
            rsn,
            int(actor_id or 0),
            datetime.now(),
        ),
    )
    db.execute(
        "UPDATE client_deliveries SET has_corrections = 1, updated_at = ? WHERE id = ?",
        (datetime.now(), int(delivery_id)),
    )
    if fg_id > 0 and return_delta != 0:
        _insert_stock_movement(
            movement_type="reconciliation_correction",
            actor_id=actor_id,
            finished_goods_stock_id=fg_id,
            client_id=int(_to_int(delivery["client_id"], 0)),
            delivery_id=int(delivery_id),
            delivery_line_id=int(delivery_line_id),
            qty_bottles=int(abs(return_delta)),
            qty_ml=float(_to_float(line["bottle_size_ml"], 0.0)) * int(abs(return_delta)),
            notes=f"correction_type={correction_type} from={from_state} to={to_state} reason={rsn}",
        )
    _insert_stock_movement(
        movement_type="reconciliation_correction",
        actor_id=actor_id,
        finished_goods_stock_id=fg_id if fg_id > 0 else None,
        client_id=int(_to_int(delivery["client_id"], 0)),
        delivery_id=int(delivery_id),
        delivery_line_id=int(delivery_line_id),
        qty_bottles=int(qty),
        amount_value=abs(new_sold_value - old_sold_value),
        notes=f"correction_type={correction_type} from={from_state} to={to_state} reason={rsn}",
    )
    _log_admin_action(
        actor_id=actor_id,
        action="DELIVERY_CORRECTION",
        details=(
            f"delivery_id={delivery_id} line_id={delivery_line_id} type={correction_type} "
            f"qty={qty} from={from_state} to={to_state} reason={rsn}"
        ),
    )
    _update_delivery_status(int(delivery_id))
    if fg_id > 0:
        _refresh_finished_goods_at_clients(fg_id)
    db.commit()
    return {"status": "success"}, 200

