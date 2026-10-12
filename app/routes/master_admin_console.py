from __future__ import annotations

import io
import json
import csv
from datetime import datetime, timedelta
from typing import Any, Dict

from flask import Blueprint, Response, jsonify, redirect, render_template, request, session, url_for
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from utils.auth import login_required, role_required
from utils.auth import ROLE_HIERARCHY
from services import master_admin_console_service as svc
from services import sales_consignment_service as sales_svc
from services import flavor_classification_service as fcls
from services import flavor_recipe_analytics_service as fra


master_admin_bp = Blueprint("master_admin", __name__)


def _safe_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(default)


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


@master_admin_bp.route("/master-admin")
@login_required
@role_required("Old Goat", "Master")
def console():
    tab = (request.args.get("tab") or "scrapers").strip().lower()
    allowed_tabs = {"scrapers", "flavor_intel", "users", "audit", "shopping"}
    if tab not in allowed_tabs:
        tab = "scrapers"
    print(f"[master-admin] tab_requested={tab}")
    role_filter = (request.args.get("role") or "").strip()
    search = (request.args.get("search") or "").strip()

    audit_user = (request.args.get("audit_user") or "").strip()
    audit_action = (request.args.get("audit_action") or "").strip()
    audit_start = (request.args.get("audit_start") or "").strip()
    audit_end = (request.args.get("audit_end") or "").strip()

    thresholds = {
        "Flavor": float(request.args.get("t_flavor") or 10.0),
        "Base": float(request.args.get("t_base") or 100.0),
        "Nicotine": float(request.args.get("t_nic") or 50.0),
        "Hardware": float(request.args.get("t_hw") or 1.0),
    }

    ctx: Dict[str, Any] = {
        "tab": tab,
        "roles": ["Kid", "Hand", "Mixer", "Ram", "Foreman", "Old Goat", "Master"],
        "role_filter": role_filter,
        "search": search,
        "audit_user": audit_user,
        "audit_action": audit_action,
        "audit_start": audit_start,
        "audit_end": audit_end,
        "thresholds": thresholds,
        "summary": svc.get_console_summary(),
        "open_stock_take": svc.get_latest_open_stock_take_run(),
    }

    if tab == "users":
        ctx["users"] = svc.render_user_overview(role_filter=role_filter, search=search)
    elif tab == "audit":
        ctx["audit_logs"] = svc.render_audit_logs(
            user_id=audit_user, action=audit_action, start=audit_start, end=audit_end, limit=300
        )
    elif tab == "shopping":
        items = svc.render_shopping_list(thresholds=thresholds, default_threshold_ml=float(thresholds["Flavor"]))
        first_keys = list(items[0].keys()) if items else []
        print(f"[master-admin] shopping service_return_type={type(items).__name__} first_item_keys={first_keys}")
        ctx["shopping_items"] = items
        ctx["shopping_runs"] = svc.list_shopping_list_runs(limit=25)
        print(f"[master-admin] template_var=shopping_items count={len(items)}")
    elif tab == "flavor_intel":
        ctx["vc_dtl_benchmark_stats"] = svc.list_vc_dtl_benchmark_stats()
    else:
        # scrapers tab
        ctx["scraper_last_runs"] = svc.list_scraper_last_runs()
        ctx["scraper_health"] = svc.scraper_health_report()
        ctx["scheduler_status"] = svc.get_scheduler_status()

    return render_template("master_admin_console.html", **ctx)


@master_admin_bp.route("/master-admin/stock-take/start", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def start_stock_take():
    notes = (request.form.get("notes") or "").strip()
    run_id = svc.start_stock_take_run(actor_id=int(session.get("user_id") or 0), notes=notes)
    return redirect(url_for("master_admin.stock_take_run", run_id=run_id))


@master_admin_bp.route("/master-admin/stock-take/open")
@login_required
@role_required("Old Goat", "Master")
def open_stock_take():
    run = svc.get_latest_open_stock_take_run()
    if not run:
        return redirect(url_for("master_admin.console", tab="users"))
    return redirect(url_for("master_admin.stock_take_run", run_id=int(run["id"])))


@master_admin_bp.route("/master-admin/stock-take/<int:run_id>")
@login_required
@role_required("Old Goat", "Master")
def stock_take_run(run_id: int):
    run = svc.get_stock_take_run(int(run_id))
    if not run:
        return redirect(url_for("master_admin.console", tab="users"))
    search = (request.args.get("q") or "").strip()
    category = (request.args.get("cat") or "").strip()
    uncounted_only = (request.args.get("uncounted") or "").strip() == "1"
    variances_only = (request.args.get("variance_only") or "").strip() == "1"
    items = svc.get_stock_take_items(
        run_id=int(run_id),
        search=search,
        category=category,
        uncounted_only=uncounted_only,
        variances_only=variances_only,
    )
    report = svc.build_stock_take_report(run_id=int(run_id))
    return render_template(
        "master_admin_stock_take.html",
        run=run,
        items=items,
        report=report,
        q=search,
        cat=category,
        uncounted=uncounted_only,
        variance_only=variances_only,
    )


@master_admin_bp.route("/master-admin/stock-take/<int:run_id>/save", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def stock_take_save(run_id: int):
    item_ids = request.form.getlist("item_id")
    updates = []
    for iid in item_ids:
        sid = str(iid).strip()
        if not sid:
            continue
        updates.append(
            {
                "id": int(sid),
                "actual_stock": request.form.get(f"actual_{sid}", ""),
                "action_taken": request.form.get(f"action_{sid}", "skipped"),
                "notes": request.form.get(f"notes_{sid}", ""),
            }
        )
    svc.save_stock_take_progress(run_id=int(run_id), item_updates=updates, actor_id=int(session.get("user_id") or 0))
    return redirect(url_for("master_admin.stock_take_run", run_id=run_id))


@master_admin_bp.route("/master-admin/stock-take/<int:run_id>/finalize", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def stock_take_finalize(run_id: int):
    item_ids = request.form.getlist("item_id")
    updates = []
    for iid in item_ids:
        sid = str(iid).strip()
        if not sid:
            continue
        updates.append(
            {
                "id": int(sid),
                "actual_stock": request.form.get(f"actual_{sid}", ""),
                "action_taken": request.form.get(f"action_{sid}", "skipped"),
                "notes": request.form.get(f"notes_{sid}", ""),
            }
        )
    svc.save_stock_take_progress(run_id=int(run_id), item_updates=updates, actor_id=int(session.get("user_id") or 0))
    svc.finalize_stock_take(run_id=int(run_id), actor_id=int(session.get("user_id") or 0))
    return redirect(url_for("master_admin.stock_take_run", run_id=run_id))


@master_admin_bp.route("/master-admin/production")
@login_required
@role_required("Old Goat", "Master")
def production_home():
    status = (request.args.get("status") or "").strip()
    decant = (request.args.get("decant") or "").strip()
    fg_search = (request.args.get("fg_search") or "").strip()
    batches = svc.list_production_batches(status=status, decant_filter=decant)
    finished_goods = svc.list_finished_goods(search=fg_search)
    return render_template(
        "master_admin_production.html",
        batches=batches,
        finished_goods=finished_goods,
        status=status,
        decant=decant,
        fg_search=fg_search,
    )


@master_admin_bp.route("/master-admin/sales")
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_dashboard():
    return render_template(
        "master_admin_sales_dashboard.html",
        summary=sales_svc.sales_dashboard_summary(),
        clients=sales_svc.list_clients(),
    )


@master_admin_bp.route("/master-admin/sales/clients", methods=["GET", "POST"])
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_clients():
    if request.method == "POST":
        body, code = sales_svc.create_client(
            actor_id=int(session.get("user_id") or 0),
            payload=dict(request.form),
        )
        if code != 200:
            return jsonify(body), code
        return redirect(url_for("master_admin.sales_clients"))
    return render_template("master_admin_sales_clients.html", clients=sales_svc.list_clients())


@master_admin_bp.route("/master-admin/sales/clients/<int:client_id>")
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_client_detail(client_id: int):
    client = sales_svc.get_client_detail(int(client_id))
    if not client:
        return redirect(url_for("master_admin.sales_clients"))
    return render_template("master_admin_sales_client_detail.html", client=client)


@master_admin_bp.route("/master-admin/sales/clients/<int:client_id>/statement")
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_client_statement(client_id: int):
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    preset = (request.args.get("preset") or "").strip().lower()
    if preset in {"this_month", "last_month", "last_30_days"}:
        now = datetime.now()
        if preset == "this_month":
            date_from = now.replace(day=1).strftime("%Y-%m-%d")
            date_to = now.strftime("%Y-%m-%d")
        elif preset == "last_month":
            first_this = now.replace(day=1)
            last_prev = first_this - timedelta(days=1)
            date_from = last_prev.replace(day=1).strftime("%Y-%m-%d")
            date_to = last_prev.strftime("%Y-%m-%d")
        else:
            date_from = (now - timedelta(days=30)).strftime("%Y-%m-%d")
            date_to = now.strftime("%Y-%m-%d")
    snap = sales_svc.client_statement_snapshot(int(client_id), date_from=date_from, date_to=date_to)
    if snap.get("status") != "success":
        return jsonify(snap), 404
    return render_template("master_admin_sales_client_statement.html", statement=snap)


@master_admin_bp.route("/master-admin/sales/finished-goods")
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_finished_goods():
    q = (request.args.get("q") or "").strip()
    return render_template(
        "master_admin_sales_finished_goods.html",
        q=q,
        items=sales_svc.list_finished_goods_stock(search=q),
    )


@master_admin_bp.route("/master-admin/sales/decant", methods=["POST"])
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_create_decant_event():
    body, code = sales_svc.create_decant_event(
        actor_id=int(session.get("user_id") or 0),
        source_batch_id=_safe_int(request.form.get("source_batch_id"), 0),
        recipe_id=_safe_int(request.form.get("recipe_id"), 0) or None,
        recipe_version_id=_safe_int(request.form.get("recipe_version_id"), 0) or None,
        batch_number_snapshot=(request.form.get("batch_number_snapshot") or "").strip(),
        product_name_snapshot=(request.form.get("product_name_snapshot") or "").strip(),
        bottle_size_ml=_safe_float(request.form.get("bottle_size_ml"), 0.0),
        bottles_created=_safe_int(request.form.get("bottles_created"), 0),
        cost_total_snapshot=_safe_float(request.form.get("cost_total_snapshot"), 0.0),
        default_wholesale_price=_safe_float(request.form.get("default_wholesale_price"), 0.0),
        default_retail_reference_price=_safe_float(request.form.get("default_retail_reference_price"), 0.0),
        notes=(request.form.get("notes") or "").strip(),
    )
    if code != 200:
        return jsonify(body), code
    return redirect(url_for("master_admin.sales_finished_goods"))


@master_admin_bp.route("/master-admin/sales/deliveries/new", methods=["GET", "POST"])
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_new_delivery():
    if request.method == "POST":
        lines: list[dict[str, Any]] = []
        row_count = int(request.form.get("row_count") or 0)
        for i in range(1, row_count + 1):
            lines.append(
                {
                    "finished_goods_stock_id": request.form.get(f"fg_id_{i}"),
                    "qty_sent": request.form.get(f"qty_{i}"),
                    "unit_price_snapshot": request.form.get(f"price_{i}"),
                }
            )
        body, code = sales_svc.create_delivery(
            actor_id=int(session.get("user_id") or 0),
            client_id=int(request.form.get("client_id") or 0),
            delivery_type=(request.form.get("delivery_type") or "consignment").strip(),
            delivery_date=(request.form.get("delivery_date") or "").strip(),
            notes=(request.form.get("notes") or "").strip(),
            lines=lines,
        )
        if code != 200:
            return jsonify(body), code
        return redirect(url_for("master_admin.sales_delivery_detail", delivery_id=int(body["delivery_id"])))
    return render_template(
        "master_admin_sales_delivery_new.html",
        clients=sales_svc.list_clients(),
        fg_items=sales_svc.list_finished_goods_stock(),
    )


@master_admin_bp.route("/master-admin/sales/deliveries")
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_deliveries():
    return render_template("master_admin_sales_deliveries.html", deliveries=sales_svc.list_deliveries())


@master_admin_bp.route("/master-admin/sales/deliveries/<int:delivery_id>", methods=["GET", "POST"])
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_delivery_detail(delivery_id: int):
    if request.method == "POST":
        line_updates: list[dict[str, Any]] = []
        ids = request.form.getlist("line_id")
        for sid in ids:
            if not str(sid).strip():
                continue
            line_updates.append(
                {
                    "line_id": int(sid),
                    "qty_reported_sold": request.form.get(f"sold_{sid}"),
                    "qty_returned": request.form.get(f"returned_{sid}"),
                    "qty_damaged": request.form.get(f"damaged_{sid}"),
                }
            )
        body, code = sales_svc.reconcile_delivery(
            actor_id=int(session.get("user_id") or 0),
            delivery_id=int(delivery_id),
            line_updates=line_updates,
            allow_closed_override=ROLE_HIERARCHY.get(session.get("role", "Kid"), 0) >= ROLE_HIERARCHY.get("Master", 7),
        )
        if code != 200:
            return jsonify(body), code
        return redirect(url_for("master_admin.sales_delivery_detail", delivery_id=delivery_id))
    delivery = sales_svc.get_delivery_detail(int(delivery_id))
    if not delivery:
        return redirect(url_for("master_admin.sales_deliveries"))
    truth = sales_svc.delivery_truth_snapshot(int(delivery_id))
    role = session.get("role", "Kid")
    can_correct = ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY.get("Master", 7) or role == "Old Goat"
    return render_template("master_admin_sales_delivery_detail.html", delivery=delivery, can_correct=can_correct, truth=truth)


@master_admin_bp.route("/master-admin/sales/deliveries/<int:delivery_id>/correct", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def sales_delivery_correct(delivery_id: int):
    role = session.get("role", "Kid")
    body, code = sales_svc.apply_delivery_line_correction(
        actor_id=int(session.get("user_id") or 0),
        delivery_id=int(delivery_id),
        delivery_line_id=_safe_int(request.form.get("delivery_line_id"), 0),
        correction_type=(request.form.get("correction_type") or "").strip(),
        quantity=_safe_int(request.form.get("quantity"), 0),
        reason=(request.form.get("reason") or "").strip(),
        allow_closed_override=ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY.get("Master", 7),
    )
    if code != 200:
        return jsonify(body), code
    return redirect(url_for("master_admin.sales_delivery_detail", delivery_id=delivery_id))


@master_admin_bp.route("/master-admin/sales/payments", methods=["GET", "POST"])
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_payments():
    if request.method == "POST":
        allocs: list[dict[str, Any]] = []
        delivery_ids = request.form.getlist("alloc_delivery_id")
        for did in delivery_ids:
            allocs.append({"delivery_id": did, "amount_allocated": request.form.get(f"alloc_amount_{did}")})
        body, code = sales_svc.record_payment(
            actor_id=int(session.get("user_id") or 0),
            client_id=_safe_int(request.form.get("client_id"), 0),
            payment_date=(request.form.get("payment_date") or "").strip(),
            amount=_safe_float(request.form.get("amount"), 0.0),
            payment_method=(request.form.get("payment_method") or "").strip(),
            reference=(request.form.get("reference") or "").strip(),
            notes=(request.form.get("notes") or "").strip(),
            allocations=allocs,
        )
        if code != 200:
            return jsonify(body), code
        return redirect(url_for("master_admin.sales_payments"))
    summary = sales_svc.sales_dashboard_summary()
    return render_template(
        "master_admin_sales_payments.html",
        clients=sales_svc.list_clients(),
        deliveries=sales_svc.list_deliveries(),
        payments=summary.get("recent_payments") or [],
    )


@master_admin_bp.route("/master-admin/sales/reports/outstanding.csv")
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_outstanding_csv():
    deliveries = sales_svc.list_deliveries()
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["Delivery #", "Client", "Type", "Status", "Date", "Sent Value", "Sold Value", "Paid", "Due"])
    for d in deliveries:
        w.writerow(
            [
                d.get("delivery_number"),
                d.get("client_name"),
                d.get("delivery_type"),
                d.get("status"),
                d.get("delivery_date"),
                f"{float(d.get('sent_value') or 0):.2f}",
                f"{float(d.get('sold_value') or 0):.2f}",
                f"{float(d.get('paid_value') or 0):.2f}",
                f"{float(d.get('due_value') or 0):.2f}",
            ]
        )
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=outstanding_balances_report.csv"},
    )


@master_admin_bp.route("/master-admin/sales/reports/stock-at-clients.csv")
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_stock_at_clients_csv():
    rows = sales_svc.list_finished_goods_stock()
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["Product", "Bottle Size (ml)", "At Clients", "On Hand", "Source Batch"])
    for r in rows:
        w.writerow(
            [
                r.get("product_name_snapshot"),
                f"{float(r.get('bottle_size_ml') or 0):.0f}",
                int(r.get("quantity_at_clients") or 0),
                int(r.get("quantity_on_hand") or 0),
                r.get("source_batch_id") or "",
            ]
        )
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=stock_at_client_report.csv"},
    )


@master_admin_bp.route("/master-admin/sales/reports/top-selling.csv")
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_top_selling_csv():
    summary = sales_svc.sales_dashboard_summary()
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["Product", "Bottle Size (ml)", "Sold Qty"])
    for r in (summary.get("top_selling_products") or []):
        w.writerow([r.get("product_name_snapshot"), r.get("bottle_size_ml"), r.get("sold_qty")])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=top_selling_recipe_report.csv"},
    )


@master_admin_bp.route("/master-admin/sales/clients/<int:client_id>/statement.pdf")
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_client_statement_pdf(client_id: int):
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    statement = sales_svc.client_statement_snapshot(int(client_id), date_from=date_from, date_to=date_to)
    if statement.get("status") != "success":
        return redirect(url_for("master_admin.sales_clients"))
    buf = io.BytesIO()
    p = canvas.Canvas(buf, pagesize=letter)
    y = 760
    p.setFont("Helvetica-Bold", 13)
    p.drawString(40, y, "Old Goat Lab - Client Statement")
    y -= 24
    p.setFont("Helvetica", 10)
    p.drawString(40, y, f"Client: {statement.get('client', {}).get('name')}")
    y -= 16
    p.drawString(40, y, f"Range: {statement.get('date_from')} to {statement.get('date_to')}")
    y -= 24
    p.setFont("Helvetica-Bold", 10)
    p.drawString(40, y, "In-period Deliveries")
    y -= 14
    p.setFont("Helvetica", 9)
    for d in (statement.get("activity", {}).get("deliveries") or [])[:25]:
        p.drawString(
            40,
            y,
            f"{d.get('delivery_number')} {d.get('delivery_date')} {d.get('delivery_type')} "
            f"sent R {float(d.get('sent_value') or 0):.2f} sold R {float(d.get('sold_value') or 0):.2f} due R {float(d.get('outstanding_after_allocations') or 0):.2f}",
        )
        y -= 12
        if y < 80:
            p.showPage()
            y = 760
            p.setFont("Helvetica", 9)
    y -= 8
    t = (statement.get("activity", {}).get("totals") or {})
    p.setFont("Helvetica-Bold", 10)
    p.drawString(40, y, "Period Totals")
    y -= 14
    p.setFont("Helvetica", 9)
    p.drawString(40, y, f"Sent: R {float(t.get('total_sent_value') or 0):.2f} | Sold: R {float(t.get('total_sold_value') or 0):.2f} | Payments: R {float(t.get('total_payments_received') or 0):.2f}")
    y -= 12
    p.drawString(40, y, f"Returned qty/value: {int(t.get('total_returned_qty') or 0)} / R {float(t.get('total_returned_value') or 0):.2f}")
    y -= 12
    p.drawString(40, y, f"Damaged qty/value: {int(t.get('total_damaged_qty') or 0)} / R {float(t.get('total_damaged_value') or 0):.2f}")
    y -= 12
    c = (statement.get("closing") or {})
    p.drawString(40, y, f"Closing balance due: R {float(c.get('closing_balance_due') or 0):.2f}")
    p.save()
    pdf = buf.getvalue()
    buf.close()
    return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": f"inline; filename=client_{client_id}_statement.pdf"})


@master_admin_bp.route("/master-admin/sales/clients/<int:client_id>/statement.csv")
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_client_statement_csv(client_id: int):
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    statement = sales_svc.client_statement_snapshot(int(client_id), date_from=date_from, date_to=date_to)
    if statement.get("status") != "success":
        return jsonify(statement), 404
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["Client", statement.get("client", {}).get("name")])
    w.writerow(["Type", statement.get("client", {}).get("client_type")])
    w.writerow(["Range", f"{statement.get('date_from')} to {statement.get('date_to')}"])
    w.writerow([])
    w.writerow(["Delivery #", "Date", "Type", "Status", "Sent Value", "Sold Value", "Payments Allocated (period)", "Outstanding", "Has Corrections"])
    for d in (statement.get("activity", {}).get("deliveries") or []):
        w.writerow(
            [
                d.get("delivery_number"),
                d.get("delivery_date"),
                d.get("delivery_type"),
                d.get("status"),
                f"{float(d.get('sent_value') or 0):.2f}",
                f"{float(d.get('sold_value') or 0):.2f}",
                f"{float(d.get('payments_allocated_in_period') or 0):.2f}",
                f"{float(d.get('outstanding_after_allocations') or 0):.2f}",
                "yes" if d.get("has_corrections") else "no",
            ]
        )
    w.writerow([])
    t = statement.get("activity", {}).get("totals") or {}
    w.writerow(["Total Sent Value", f"{float(t.get('total_sent_value') or 0):.2f}"])
    w.writerow(["Total Sold Value", f"{float(t.get('total_sold_value') or 0):.2f}"])
    w.writerow(["Total Returned Qty", int(t.get("total_returned_qty") or 0)])
    w.writerow(["Total Returned Value", f"{float(t.get('total_returned_value') or 0):.2f}"])
    w.writerow(["Total Damaged Qty", int(t.get("total_damaged_qty") or 0)])
    w.writerow(["Total Damaged Value", f"{float(t.get('total_damaged_value') or 0):.2f}"])
    w.writerow(["Total Payments Received", f"{float(t.get('total_payments_received') or 0):.2f}"])
    w.writerow(["Net Movement Amount Due", f"{float(t.get('net_movement_amount_due') or 0):.2f}"])
    w.writerow([])
    c = statement.get("closing", {}) or {}
    w.writerow(["Closing Balance Due", f"{float(c.get('closing_balance_due') or 0):.2f}"])
    w.writerow(["Has Corrected Deliveries", "yes" if c.get("has_corrected_deliveries") else "no"])
    w.writerow([])
    w.writerow(["Stock Held - Product", "Bottle Size (ml)", "Qty Still Held", "Delivery Refs"])
    for s in (c.get("stock_currently_held") or []):
        w.writerow([s.get("product_name_snapshot"), s.get("bottle_size_ml"), s.get("qty_still_held"), s.get("delivery_refs")])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=client_{client_id}_statement.csv"},
    )


@master_admin_bp.route("/master-admin/sales/clients/<int:client_id>/statement/whatsapp")
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_client_statement_whatsapp(client_id: int):
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    tone = (request.args.get("tone") or "neutral").strip().lower()
    out = sales_svc.generate_whatsapp_statement_summary(int(client_id), date_from=date_from, date_to=date_to, tone=tone)
    if out.get("status") != "success":
        return jsonify(out), 404
    return jsonify(out), 200


@master_admin_bp.route("/master-admin/sales/deliveries/<int:delivery_id>/note.pdf")
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_delivery_note_pdf(delivery_id: int):
    d = sales_svc.get_delivery_detail(int(delivery_id))
    if not d:
        return redirect(url_for("master_admin.sales_deliveries"))
    buf = io.BytesIO()
    p = canvas.Canvas(buf, pagesize=letter)
    y = 760
    p.setFont("Helvetica-Bold", 13)
    p.drawString(40, y, "Old Goat Lab - Delivery Note")
    y -= 24
    p.setFont("Helvetica", 10)
    p.drawString(40, y, f"Delivery: {d.get('delivery_number')} | Client: {d.get('client_name')}")
    y -= 16
    p.drawString(40, y, f"Type: {d.get('delivery_type')} | Date: {d.get('delivery_date')} | Status: {d.get('status')}")
    y -= 20
    p.setFont("Helvetica-Bold", 10)
    p.drawString(40, y, "Lines")
    y -= 14
    p.setFont("Helvetica", 9)
    for ln in (d.get("lines") or [])[:35]:
        p.drawString(
            40,
            y,
            f"{ln.get('product_name_snapshot')} {float(ln.get('bottle_size_ml') or 0):.0f}ml "
            f"x {int(ln.get('qty_sent') or 0)} @ R {float(ln.get('unit_price_snapshot') or 0):.2f}",
        )
        y -= 12
        if y < 80:
            p.showPage()
            y = 760
            p.setFont("Helvetica", 9)
    p.save()
    pdf = buf.getvalue()
    buf.close()
    return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": f"inline; filename=delivery_{delivery_id}.pdf"})


@master_admin_bp.route("/master-admin/sales/debug/delivery/<int:delivery_id>")
@login_required
@role_required("Old Goat", "Master")
def sales_debug_delivery(delivery_id: int):
    snap = sales_svc.delivery_consistency_snapshot(int(delivery_id))
    code = 200 if snap.get("status") == "success" else 404
    return jsonify(snap), code


@master_admin_bp.route("/master-admin/sales/deliveries/<int:delivery_id>/truth.csv")
@login_required
@role_required("Foreman", "Old Goat", "Master")
def sales_delivery_truth_csv(delivery_id: int):
    truth = sales_svc.delivery_truth_snapshot(int(delivery_id))
    if truth.get("status") != "success":
        return jsonify(truth), 404
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(
        [
            "Line ID",
            "Product",
            "Bottle Size (ml)",
            "Qty Sent",
            "Qty Sold",
            "Qty Returned",
            "Qty Damaged",
            "Qty Remaining",
            "Unit Price Snapshot",
            "Sent Value",
            "Sold Value",
            "Expected Remaining",
            "Stored Remaining",
            "Consistency Status",
        ]
    )
    for ln in (truth.get("lines") or []):
        w.writerow(
            [
                ln.get("line_id"),
                ln.get("product_name_snapshot"),
                f"{float(ln.get('bottle_size_ml') or 0):.0f}",
                int(ln.get("qty_sent") or 0),
                int(ln.get("qty_reported_sold") or 0),
                int(ln.get("qty_returned") or 0),
                int(ln.get("qty_damaged") or 0),
                int(ln.get("qty_remaining_with_client") or 0),
                f"{float(ln.get('unit_price_snapshot') or 0):.2f}",
                f"{float(ln.get('sent_value') or 0):.2f}",
                f"{float(ln.get('sold_value') or 0):.2f}",
                int(ln.get("expected_remaining") or 0),
                int(ln.get("stored_remaining") or 0),
                ln.get("consistency_status"),
            ]
        )
    t = truth.get("totals") or {}
    w.writerow([])
    w.writerow(["Delivery #", truth.get("delivery_number")])
    w.writerow(["Delivery Type", truth.get("delivery_type")])
    w.writerow(["Delivery Status", truth.get("delivery_status")])
    w.writerow(["Has Corrections", "yes" if truth.get("has_corrections") else "no"])
    w.writerow(["Overall Consistency", "ok" if truth.get("overall_consistency_ok") else "mismatch"])
    w.writerow(["Total Sent Value", f"{float(t.get('total_sent_value') or 0):.2f}"])
    w.writerow(["Total Sold Value", f"{float(t.get('total_sold_value') or 0):.2f}"])
    w.writerow(["Total Payments Allocated", f"{float(t.get('total_payments_allocated') or 0):.2f}"])
    w.writerow(["Total Outstanding", f"{float(t.get('total_outstanding') or 0):.2f}"])
    w.writerow(["Total Qty Remaining With Client", int(t.get("total_qty_remaining_with_client") or 0)])
    w.writerow(["Total Qty Returned", int(t.get("total_qty_returned") or 0)])
    w.writerow(["Total Qty Damaged", int(t.get("total_qty_damaged") or 0)])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=delivery_{delivery_id}_truth_report.csv"},
    )

@master_admin_bp.route("/master-admin/production/batches/create", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def create_production_batch():
    batch_name = (request.form.get("batch_name") or "").strip()
    total_volume_ml = float(request.form.get("total_volume_ml") or 0.0)
    notes = (request.form.get("notes") or "").strip()
    recipe_raw = (request.form.get("recipe_id") or "").strip()
    recipe_id = int(recipe_raw) if recipe_raw.isdigit() else None
    batch_id = svc.create_production_batch(
        actor_id=int(session.get("user_id") or 0),
        batch_name=batch_name,
        total_volume_ml=total_volume_ml,
        recipe_id=recipe_id,
        notes=notes,
    )
    return redirect(url_for("master_admin.production_decant", batch_id=batch_id))


@master_admin_bp.route("/master-admin/production/batches/<int:batch_id>/decant")
@login_required
@role_required("Old Goat", "Master")
def production_decant(batch_id: int):
    run_id_raw = (request.args.get("run_id") or "").strip()
    if run_id_raw.isdigit():
        run_id = int(run_id_raw)
    else:
        run_id = svc.start_decant_run(batch_id=int(batch_id), actor_id=int(session.get("user_id") or 0))
    run = svc.get_decant_run(int(run_id))
    if not run:
        return redirect(url_for("master_admin.production_home"))
    items = svc.list_decant_items(run_id=int(run_id))
    report = svc.build_production_batch_report(batch_id=int(batch_id))
    return render_template("master_admin_decant.html", run=run, items=items, report=report)


@master_admin_bp.route("/master-admin/production/decant/<int:run_id>/save", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def production_decant_save(run_id: int):
    run = svc.get_decant_run(int(run_id))
    if not run:
        return redirect(url_for("master_admin.production_home"))
    line_count = int(request.form.get("line_count") or 0)
    lines = []
    for idx in range(1, line_count + 1):
        lines.append(
            {
                "bottle_size_ml": request.form.get(f"bottle_size_{idx}", ""),
                "quantity_produced": request.form.get(f"qty_{idx}", ""),
                "label_name": request.form.get(f"label_{idx}", ""),
            }
        )
    wastage_ml = float(request.form.get("wastage_ml") or 0.0)
    notes = (request.form.get("notes") or "").strip()
    svc.save_decant_draft(
        run_id=int(run_id),
        lines=lines,
        wastage_ml=wastage_ml,
        notes=notes,
        actor_id=int(session.get("user_id") or 0),
    )
    return redirect(url_for("master_admin.production_decant", batch_id=int(run["production_batch_id"]), run_id=run_id))


@master_admin_bp.route("/master-admin/production/decant/<int:run_id>/finalize", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def production_decant_finalize(run_id: int):
    run = svc.get_decant_run(int(run_id))
    if not run:
        return redirect(url_for("master_admin.production_home"))
    line_count = int(request.form.get("line_count") or 0)
    lines = []
    for idx in range(1, line_count + 1):
        lines.append(
            {
                "bottle_size_ml": request.form.get(f"bottle_size_{idx}", ""),
                "quantity_produced": request.form.get(f"qty_{idx}", ""),
                "label_name": request.form.get(f"label_{idx}", ""),
            }
        )
    wastage_ml = float(request.form.get("wastage_ml") or 0.0)
    notes = (request.form.get("notes") or "").strip()
    svc.save_decant_draft(
        run_id=int(run_id),
        lines=lines,
        wastage_ml=wastage_ml,
        notes=notes,
        actor_id=int(session.get("user_id") or 0),
    )
    svc.finalize_decant_run(run_id=int(run_id), actor_id=int(session.get("user_id") or 0))
    return redirect(url_for("master_admin.production_decant", batch_id=int(run["production_batch_id"]), run_id=run_id))


@master_admin_bp.route("/master-admin/users/update", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def update_user():
    data = request.get_json(silent=True) or {}
    user_id = int(data.get("user_id") or 0)
    role = data.get("role")
    status = data.get("status")
    if not user_id:
        return jsonify({"status": "error", "message": "Missing user_id"}), 400

    svc.update_user_account(actor_id=int(session.get("user_id") or 0), user_id=user_id, role=role, status=status)
    from db import get_db

    get_db().commit()
    return jsonify({"status": "success"})


@master_admin_bp.route("/master-admin/users/reset-password", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def reset_user_password():
    data = request.get_json(silent=True) or {}
    user_id = int(data.get("user_id") or 0)
    password = (data.get("password") or "").strip()
    confirm = (data.get("confirm_password") or "").strip()
    if not user_id:
        return jsonify({"status": "error", "message": "Missing user_id"}), 400
    if not password:
        return jsonify({"status": "error", "message": "Password is required"}), 400
    if password != confirm:
        return jsonify({"status": "error", "message": "Passwords do not match"}), 400
    try:
        svc.reset_user_password(actor_id=int(session.get("user_id") or 0), user_id=user_id, new_password=password)
        from db import get_db

        get_db().commit()
        return jsonify({"status": "success"})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@master_admin_bp.route("/master-admin/scrapers/trigger/<string:scraper_name>", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def trigger_scraper(scraper_name: str):
    body, status = svc.trigger_scraper(scraper_name, actor_id=int(session.get("user_id") or 0))
    print(
        f"[scraper-response] name={scraper_name} status={body.get('status')} "
        f"fatal_stage={body.get('fatal_stage')} stop_reason={body.get('stop_reason')} "
        f"errors={len(body.get('errors') or [])}"
    )
    return jsonify(body), status


@master_admin_bp.route("/master-admin/scrapers/run-all", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def run_all_scrapers():
    body, status = svc.run_all_scrapers(actor_id=int(session.get("user_id") or 0), run_type="manual")
    return jsonify(body), status


@master_admin_bp.route("/master-admin/scrapers/health", methods=["GET"])
@login_required
@role_required("Old Goat", "Master")
def scraper_health():
    return jsonify(svc.scraper_health_report()), 200


@master_admin_bp.route("/master-admin/scrapers/verify/<string:scraper_name>", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def verify_scraper_run(scraper_name: str):
    body = svc.verify_latest_scraper_run(scraper_name)
    code = 200 if body.get("status") == "success" else 409
    return jsonify(body), code


@master_admin_bp.route("/master-admin/scrapers/scheduler-health", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def scheduler_health():
    body = svc.check_scheduler_health()
    code = 200 if body.get("health") in {"ok", "warning"} else 500
    return jsonify(body), code


@master_admin_bp.route("/master-admin/scrapers/monthly-plan-preview", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def monthly_plan_preview():
    body = svc.preview_monthly_scraper_plan()
    return jsonify(body), 200


@master_admin_bp.route("/master-admin/vc/unresolved-queue", methods=["GET"])
@login_required
@role_required("Old Goat", "Master")
def unresolved_imported_flavours_queue():
    brand = (request.args.get("brand") or "").strip()
    search = (request.args.get("search") or "").strip()
    min_occ = int(request.args.get("min_occurrence") or 1)
    rows = svc.list_unresolved_imported_flavour_queue(brand=brand, min_occurrence=min_occ, search=search, limit=300)
    metrics = svc.get_unresolved_imported_flavour_metrics()
    return jsonify({"status": "success", "rows": rows, "metrics": metrics}), 200


@master_admin_bp.route("/master-admin/vc/unresolved-candidates", methods=["GET"])
@login_required
@role_required("Old Goat", "Master")
def unresolved_imported_flavours_candidates():
    brand = (request.args.get("brand_canonical") or "").strip()
    norm = (request.args.get("flavour_name_normalized") or "").strip()
    out = svc.list_identity_candidates(brand_canonical=brand, flavour_name_normalized=norm, limit=6)
    return jsonify({"status": "success", "candidates": out}), 200


@master_admin_bp.route("/master-admin/vc/create-alias", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def create_alias_from_queue():
    data = request.get_json(silent=True) or {}
    body, status = svc.create_flavor_alias_from_admin_queue(
        actor_id=int(session.get("user_id") or 0),
        alias_raw=str(data.get("alias_raw") or ""),
        brand_canonical=str(data.get("brand_canonical") or ""),
        target_flavor_name=str(data.get("target_flavor_name") or ""),
        notes=str(data.get("notes") or ""),
        confidence_override=float(data.get("confidence_override") or 0.9),
    )
    return jsonify(body), status


@master_admin_bp.route("/master-admin/flavors/classify-all", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def classify_all_flavors():
    data = request.get_json(silent=True) or {}
    apply_mode = bool(data.get("apply"))
    limit = data.get("limit")
    body = fcls.classify_all_flavors(apply=apply_mode, limit=int(limit) if str(limit or "").isdigit() else None)
    return jsonify(body), 200


@master_admin_bp.route("/master-admin/flavors/classify/<int:flavor_id>", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def classify_single_flavor(flavor_id: int):
    data = request.get_json(silent=True) or {}
    apply_mode = bool(data.get("apply"))
    body = fcls.classify_single_flavor(flavor_id=int(flavor_id), apply=apply_mode)
    return jsonify(body), 200 if body.get("status") == "success" else 404


@master_admin_bp.route("/master-admin/flavors/recipe-analytics/recompute", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def recipe_analytics_recompute():
    data = request.get_json(silent=True) or {}
    apply_mode = bool(data.get("apply"))
    mode = (data.get("mode") or "all").strip().lower()
    if mode not in ("all", "usage", "pairings"):
        mode = "all"
    ref_raw = data.get("reference_id")
    ref_id = int(ref_raw) if str(ref_raw or "").strip().isdigit() else None
    body = fra.recompute_recipe_analytics(apply=apply_mode, mode=mode, reference_id=ref_id)
    return jsonify(body), 200


@master_admin_bp.route("/master-admin/flavors/recipe-analytics/recompute/<int:ref_id>", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def recipe_analytics_recompute_single(ref_id: int):
    data = request.get_json(silent=True) or {}
    apply_mode = bool(data.get("apply"))
    mode = (data.get("mode") or "all").strip().lower()
    if mode not in ("all", "usage", "pairings"):
        mode = "all"
    body = fra.recompute_recipe_analytics(apply=apply_mode, mode=mode, reference_id=int(ref_id))
    return jsonify(body), 200


@master_admin_bp.route("/master-admin/shopping/regenerate", methods=["POST"])
@login_required
@role_required("Old Goat", "Master")
def regenerate_shopping_list():
    data = request.get_json(silent=True) or {}
    thresholds = data.get("thresholds") or {}
    if not isinstance(thresholds, dict):
        thresholds = {}
    thresholds = {str(k): float(v) for k, v in thresholds.items()}

    items = svc.render_shopping_list(thresholds=thresholds, default_threshold_ml=float(thresholds.get("Flavor", 10.0)))
    run_id = svc.log_shopping_list_run(actor_id=int(session.get("user_id") or 0), thresholds=thresholds, items=items)
    from db import get_db

    get_db().commit()
    return jsonify({"status": "success", "run_id": run_id, "count": len(items)})


@master_admin_bp.route("/master-admin/audit/<int:log_id>/pdf")
@login_required
@role_required("Old Goat", "Master")
def audit_pdf(log_id: int):
    # Pull one row via service list (cheap and keeps SQL in one place)
    logs = svc.render_audit_logs(limit=1)
    # If you want exact id, query directly:
    from db import get_db

    db = get_db()
    row = db.execute(
        """
        SELECT al.id, al.timestamp, COALESCE(u.username,'SYSTEM') AS username,
               al.action, COALESCE(al.target_table,'') AS target_table,
               COALESCE(al.target_id,0) AS target_id, COALESCE(al.details,'') AS details
        FROM audit_logs al
        LEFT JOIN users u ON u.id = al.actor_id
        WHERE al.id = ?
        """,
        (int(log_id),),
    ).fetchone()
    if not row:
        return redirect(url_for("master_admin.console", tab="audit"))

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    w, h = letter
    y = h - 60

    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, y, "Old Goat Lab — Audit Log Entry")
    y -= 30

    c.setFont("Helvetica", 10)
    fields = [
        ("ID", row["id"]),
        ("Timestamp", str(row["timestamp"])),
        ("User", str(row["username"])),
        ("Action", str(row["action"])),
        ("Target", f"{row['target_table']}:{row['target_id']}"),
        ("Details", str(row["details"])),
    ]
    for k, v in fields:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, y, f"{k}:")
        c.setFont("Helvetica", 10)
        c.drawString(140, y, str(v)[:120])
        y -= 16
        if y < 60:
            c.showPage()
            y = h - 60

    c.showPage()
    c.save()
    pdf = buf.getvalue()
    buf.close()
    return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": f"inline; filename=audit_{log_id}.pdf"})

