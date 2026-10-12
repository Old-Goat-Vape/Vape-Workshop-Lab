"""Lab Production Jobs routes — manufacturing projects with job-linked guided mixes."""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from models import recipes_repo
from services import production_job_finance_service as finance
from services import production_job_service as pjs
from services import production_pay_service as pay
from services.recipe_cost_calculator_service import recipe_picker_rows
from utils.auth import (
    can_manage_production_jobs,
    can_produce_on_production_jobs,
    is_old_goat_role,
    login_required,
    production_jobs_required,
)

production_jobs_bp = Blueprint("production_jobs", __name__)


def _uid() -> int:
    return int(session["user_id"])


def _role() -> str | None:
    return session.get("role")


@production_jobs_bp.route("/lab/production-jobs")
@login_required
@production_jobs_required(min_level="view")
def index():
    jobs = pjs.list_jobs(include_cancelled=True)
    return render_template(
        "production_jobs/list.html",
        jobs=jobs,
        can_manage=can_manage_production_jobs(_role(), _uid()),
        can_produce=can_produce_on_production_jobs(_role(), _uid()),
    )


@production_jobs_bp.route("/lab/production-jobs/new", methods=["GET", "POST"])
@login_required
@production_jobs_required(min_level="manage")
def create():
    if request.method == "POST":
        try:
            job_id = pjs.create_job(
                name=request.form.get("name") or "",
                job_type=request.form.get("job_type") or "internal",
                description=request.form.get("description") or "",
                notes=request.form.get("notes") or "",
                reference_label=request.form.get("reference_label") or "",
                created_by_user_id=_uid(),
                status="planning",
            )
            flash("Production job created.", "success")
            return redirect(url_for("production_jobs.detail", job_id=job_id))
        except ValueError as exc:
            flash(str(exc), "danger")
    return render_template(
        "production_jobs/create.html",
        job_types=pjs.JOB_TYPES,
    )


@production_jobs_bp.route("/lab/production-jobs/earnings")
@login_required
@production_jobs_required(min_level="view")
def earnings():
    """Helper sees own earnings; managers may view any user via ?user_id=."""
    target_id = _uid()
    if can_manage_production_jobs(_role(), _uid()):
        raw = request.args.get("user_id")
        if raw and str(raw).isdigit():
            target_id = int(raw)
    summary = pay.earnings_summary_for_user(user_id=target_id)
    from db import get_db

    user_row = get_db().execute(
        "SELECT id, username FROM users WHERE id = ?",
        (target_id,),
    ).fetchone()
    helpers = []
    if can_manage_production_jobs(_role(), _uid()):
        helpers = get_db().execute(
            """
            SELECT DISTINCT u.id, u.username
            FROM users u
            JOIN production_job_credits c ON c.user_id = u.id
            WHERE c.labour_kind = 'helper'
            ORDER BY u.username COLLATE NOCASE
            """
        ).fetchall()
    return render_template(
        "production_jobs/earnings.html",
        summary=summary,
        target_user=dict(user_row) if user_row else {"id": target_id, "username": "User"},
        helpers=[dict(h) for h in helpers],
        can_manage=can_manage_production_jobs(_role(), _uid()),
    )


@production_jobs_bp.route("/lab/production-jobs/earnings/pay", methods=["POST"])
@login_required
@production_jobs_required(min_level="manage")
def record_helper_payment():
    try:
        pay.record_payment(
            user_id=int(request.form.get("user_id") or 0),
            amount=float(request.form.get("amount") or 0),
            production_job_id=int(request.form.get("production_job_id") or 0) or None,
            recorded_by_user_id=_uid(),
            note=request.form.get("note") or "",
        )
        flash("Payment recorded.", "success")
    except (TypeError, ValueError) as exc:
        flash(str(exc) or "Could not record payment.", "danger")
    return redirect(
        url_for("production_jobs.earnings", user_id=request.form.get("user_id") or _uid())
    )


@production_jobs_bp.route("/lab/production-jobs/<int:job_id>")
@login_required
@production_jobs_required(min_level="view")
def detail(job_id: int):
    tab = (request.args.get("tab") or "overview").strip().lower()
    if tab not in ("overview", "recipes", "production", "ingredients", "costs", "pay"):
        tab = "overview"
    try:
        overview = pjs.job_overview(job_id)
    except ValueError:
        abort(404)
    batches = pjs.list_job_batches(job_id) if tab in ("production", "overview", "pay") else []
    inventory = pjs.list_job_inventory(job_id) if tab in ("ingredients", "costs") else []
    purchases = finance.list_purchase_transactions(job_id) if tab == "ingredients" else []
    financials = finance.job_financial_summary(job_id) if tab in ("costs", "overview", "pay") else None
    pay_sum = pay.job_production_pay_summary(job_id) if tab in ("pay", "overview") else None
    picker = []
    if tab == "recipes" and can_manage_production_jobs(_role(), _uid()):
        picker = recipe_picker_rows(session_user_id=_uid())
    return render_template(
        "production_jobs/detail.html",
        tab=tab,
        overview=overview,
        job=overview["job"],
        recipes=overview["recipes"],
        batches=batches,
        inventory=inventory,
        purchases=purchases,
        financials=financials,
        pay_summary=pay_sum,
        recipe_picker=picker,
        can_manage=can_manage_production_jobs(_role(), _uid()),
        can_produce=can_produce_on_production_jobs(_role(), _uid()),
        job_statuses=pjs.JOB_STATUSES,
    )


@production_jobs_bp.route("/lab/production-jobs/<int:job_id>/status", methods=["POST"])
@login_required
@production_jobs_required(min_level="manage")
def set_status(job_id: int):
    try:
        pjs.update_job_status(job_id=job_id, status=request.form.get("status") or "")
        flash("Job status updated.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("production_jobs.detail", job_id=job_id, tab="overview"))


@production_jobs_bp.route("/lab/production-jobs/<int:job_id>/recipes/add", methods=["POST"])
@login_required
@production_jobs_required(min_level="manage")
def add_recipe(job_id: int):
    try:
        recipe_id = int(request.form.get("recipe_id") or 0)
        version_id = int(request.form.get("recipe_version_id") or 0)
        size = float(request.form.get("target_bottle_size_ml") or 0)
        count = int(request.form.get("target_bottle_count") or 0)
        recipe = recipes_repo.fetch_recipe(recipe_id)
        if not recipe or not recipes_repo.can_view_recipe_in_tvw_community(
            recipe,
            viewer_user_id=_uid(),
            viewer_is_old_goat=is_old_goat_role(_role()),
        ):
            raise ValueError("Recipe is not available for this Production Job.")
        pjs.add_recipe_to_job(
            job_id=job_id,
            recipe_id=recipe_id,
            recipe_version_id=version_id,
            target_bottle_size_ml=size,
            target_bottle_count=count,
            mix_target_nic_mg_ml=float(request.form.get("mix_target_nic_mg_ml") or 0),
            mix_pg_ratio=float(request.form.get("mix_pg_ratio") or 30),
            mix_vg_ratio=float(request.form.get("mix_vg_ratio") or 70),
            mix_nic_base_mg_ml=float(request.form.get("mix_nic_base_mg_ml") or 100),
            mix_nicotine_carrier=request.form.get("mix_nicotine_carrier") or "PG",
        )
        flash("Recipe locked onto this job.", "success")
    except (TypeError, ValueError) as exc:
        flash(str(exc) or "Could not add recipe.", "danger")
    return redirect(url_for("production_jobs.detail", job_id=job_id, tab="recipes"))


@production_jobs_bp.route(
    "/lab/production-jobs/<int:job_id>/recipes/<int:job_recipe_id>/mix",
    methods=["GET", "POST"],
)
@login_required
@production_jobs_required(min_level="produce")
def mix_for_job(job_id: int, job_recipe_id: int):
    jr = pjs.get_job_recipe(job_recipe_id)
    if not jr or int(jr["production_job_id"]) != int(job_id):
        abort(404)
    recipe = recipes_repo.fetch_recipe(int(jr["recipe_id"]))
    if not recipe or not recipes_repo.can_view_recipe_in_tvw_community(
        recipe,
        viewer_user_id=_uid(),
        viewer_is_old_goat=is_old_goat_role(_role()),
    ):
        flash("This recipe is unavailable.", "warning")
        return redirect(url_for("production_jobs.detail", job_id=job_id, tab="recipes"))

    remaining_bottles = max(
        float(jr.get("target_bottle_count") or 0) - float(jr.get("completed_bottles") or 0),
        0.0,
    )
    size = float(jr["target_bottle_size_ml"])

    if request.method == "POST":
        try:
            mode = (request.form.get("bottle_mode") or "custom").strip().lower()
            if mode == "1":
                bottles = 1.0
            elif mode == "5":
                bottles = 5.0
            elif mode == "10":
                bottles = 10.0
            elif mode == "remaining":
                bottles = remaining_bottles if remaining_bottles > 0 else 1.0
            else:
                bottles = float(request.form.get("custom_bottles") or 0)
            result = pjs.start_job_linked_mix(
                job_recipe_id=job_recipe_id,
                operator_user_id=_uid(),
                bottle_count=bottles,
            )
            return redirect(
                url_for("tvw_recipes.tvw_guided_mix_session", session_id=int(result["mix_session_id"]))
            )
        except ValueError as exc:
            flash(str(exc), "danger")

    return render_template(
        "production_jobs/mix_start.html",
        job_id=job_id,
        job_recipe=jr,
        remaining_bottles=remaining_bottles,
        bottle_size_ml=size,
        producing_as=session.get("username") or "Operator",
    )


@production_jobs_bp.route(
    "/lab/production-jobs/<int:job_id>/batches/<int:batch_id>/cancel",
    methods=["POST"],
)
@login_required
@production_jobs_required(min_level="produce")
def cancel_batch(job_id: int, batch_id: int):
    try:
        batches = pjs.list_job_batches(job_id)
        match = next((b for b in batches if int(b["id"]) == int(batch_id)), None)
        if not match:
            raise ValueError("Batch not found on this job.")
        if int(match.get("operator_user_id") or 0) != _uid() and not can_manage_production_jobs(
            _role(), _uid()
        ):
            raise ValueError("You can only cancel your own in-progress batches.")
        pjs.cancel_batch(batch_id=batch_id, cancelled_by_user_id=_uid())
        flash("Batch cancelled — no production credit or ingredient usage applied.", "warning")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("production_jobs.detail", job_id=job_id, tab="production"))


@production_jobs_bp.route("/lab/production-jobs/sessions/<int:session_id>/cancel", methods=["POST"])
@login_required
@production_jobs_required(min_level="produce")
def cancel_session_batch(session_id: int):
    """Cancel production batch from Guided Mix bench."""
    ctx = pjs.production_context_for_session(session_id)
    if not ctx:
        flash("This mix is not linked to a Production Job.", "warning")
        return redirect(url_for("tvw_recipes.tvw_guided_mix_session", session_id=session_id))
    if int(ctx.get("operator_user_id") or 0) != _uid() and not can_manage_production_jobs(
        _role(), _uid()
    ):
        flash("You can only cancel your own production batch.", "danger")
        return redirect(url_for("tvw_recipes.tvw_guided_mix_session", session_id=session_id))
    try:
        result = pjs.cancel_batch_for_guided_session(
            mix_session_id=session_id,
            cancelled_by_user_id=_uid(),
        )
        flash("Production batch cancelled.", "warning")
        job_id = int((result or ctx)["production_job_id"])
        return redirect(url_for("production_jobs.detail", job_id=job_id, tab="production"))
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("tvw_recipes.tvw_guided_mix_session", session_id=session_id))


@production_jobs_bp.route("/lab/production-jobs/<int:job_id>/ingredients/purchase", methods=["POST"])
@login_required
@production_jobs_required(min_level="manage")
def record_purchase(job_id: int):
    try:
        pack_count = request.form.get("pack_count")
        pack_size = request.form.get("pack_size_ml")
        pack_price = request.form.get("pack_price")
        total_paid = request.form.get("total_paid")
        finance.record_job_purchase(
            job_id=job_id,
            inventory_id=int(request.form.get("inventory_id") or 0),
            quantity_ml=float(request.form.get("quantity_ml") or 0),
            pack_count=float(pack_count) if pack_count not in (None, "") else None,
            pack_size_ml=float(pack_size) if pack_size not in (None, "") else None,
            pack_price=float(pack_price) if pack_price not in (None, "") else None,
            total_paid=float(total_paid) if total_paid not in (None, "") else None,
            source=request.form.get("source") or "",
            note=request.form.get("note") or "",
            pack_info=request.form.get("pack_info") or "",
            recorded_by_user_id=_uid(),
        )
        flash("Purchase recorded for this job inventory.", "success")
    except (TypeError, ValueError) as exc:
        flash(str(exc) or "Could not record purchase.", "danger")
    return redirect(url_for("production_jobs.detail", job_id=job_id, tab="ingredients"))


@production_jobs_bp.route("/lab/production-jobs/<int:job_id>/costs/revenue", methods=["POST"])
@login_required
@production_jobs_required(min_level="manage")
def set_revenue(job_id: int):
    try:
        finance.update_job_revenue(
            job_id=job_id,
            revenue_mode=request.form.get("revenue_mode") or "none",
            revenue_amount=float(request.form.get("revenue_amount") or 0),
            revenue_note=request.form.get("revenue_note") or "",
        )
        flash("Revenue updated.", "success")
    except (TypeError, ValueError) as exc:
        flash(str(exc), "danger")
    return redirect(url_for("production_jobs.detail", job_id=job_id, tab="costs"))


@production_jobs_bp.route("/lab/production-jobs/<int:job_id>/costs/packaging", methods=["POST"])
@login_required
@production_jobs_required(min_level="manage")
def set_packaging(job_id: int):
    try:
        bottle = request.form.get("bottle_cost_override")
        label = request.form.get("label_cost_override")
        finance.update_job_packaging_overrides(
            job_id=job_id,
            bottle_cost_override=float(bottle) if bottle not in (None, "") else None,
            label_cost_override=float(label) if label not in (None, "") else None,
        )
        flash("Packaging overrides saved.", "success")
    except (TypeError, ValueError) as exc:
        flash(str(exc), "danger")
    return redirect(url_for("production_jobs.detail", job_id=job_id, tab="costs"))


@production_jobs_bp.route("/lab/production-jobs/<int:job_id>/costs/referral", methods=["POST"])
@login_required
@production_jobs_required(min_level="manage")
def set_referral(job_id: int):
    try:
        finance.update_job_referral(
            job_id=job_id,
            referral_label=request.form.get("referral_label") or "",
            referral_mode=request.form.get("referral_mode") or "none",
            referral_value=float(request.form.get("referral_value") or 0),
        )
        flash("Referral commission saved.", "success")
    except (TypeError, ValueError) as exc:
        flash(str(exc), "danger")
    return redirect(url_for("production_jobs.detail", job_id=job_id, tab="costs"))


@production_jobs_bp.route("/lab/production-jobs/<int:job_id>/costs/expense", methods=["POST"])
@login_required
@production_jobs_required(min_level="manage")
def add_expense(job_id: int):
    try:
        finance.add_job_expense(
            job_id=job_id,
            category=request.form.get("category") or "other",
            description=request.form.get("description") or "",
            amount=float(request.form.get("amount") or 0),
            note=request.form.get("note") or "",
            recorded_by_user_id=_uid(),
        )
        flash("Expense recorded.", "success")
    except (TypeError, ValueError) as exc:
        flash(str(exc), "danger")
    return redirect(url_for("production_jobs.detail", job_id=job_id, tab="costs"))
