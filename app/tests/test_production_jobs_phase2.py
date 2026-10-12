"""Lab Production Jobs Phase 1 hardening + Phase 2 pay/costs."""

from __future__ import annotations

import os
import tempfile
import unittest

from app import app as flask_app
from db import get_db
from migrations import run_startup_migrations
from services import guided_mix_session_service, production_job_finance_service as finance
from services import production_job_service as pjs
from services import production_pay_service as pay
from utils.auth import (
    CAP_PRODUCTION_JOBS_MANAGE,
    CAP_PRODUCTION_JOBS_PRODUCE,
    CAP_PRODUCTION_JOBS_VIEW,
    can_access_lab_bridge,
    can_manage_production_jobs,
    can_produce_on_production_jobs,
    grant_user_capability,
    production_jobs_access_level,
)


class ProductionJobsPhase2Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "prod_jobs_p2.db")
        flask_app.config["TESTING"] = True
        flask_app.config["DATABASE"] = self.db_path
        flask_app.config["WTF_CSRF_ENABLED"] = False
        flask_app.config["TVW_ENABLE_LAB_MODULES"] = True

        from routes.production_jobs import production_jobs_bp

        if "production_jobs" not in flask_app.blueprints:
            flask_app.register_blueprint(production_jobs_bp)

        with flask_app.app_context():
            db = get_db()
            schema_path = os.path.join(os.path.dirname(__file__), "..", "schema.sql")
            with open(schema_path, "r", encoding="utf-8") as f:
                db.executescript(f.read())
            run_startup_migrations()
            db.execute(
                "INSERT INTO users (id, username, password_hash, role, status) "
                "VALUES (1, 'Wynand', 'x', 'Old Goat', 'Active')"
            )
            db.execute(
                "INSERT INTO users (id, username, password_hash, role, status) "
                "VALUES (2, 'Riku', 'x', 'member', 'Active')"
            )
            db.execute(
                "INSERT INTO flavor_reference (id, brand, name, category, density, price_per_ml, active) "
                "VALUES (101, 'INW', 'Biscuit', 'Flavor', 1.036, 1.0, 1)"
            )
            db.execute(
                "INSERT INTO recipes (id, name, recipe_code, status, created_by, current_version_id, visibility) "
                "VALUES (10, 'Yogi the Pink', 'YOGI', 'Production', 1, 100, 'private')"
            )
            db.execute(
                "INSERT INTO recipe_versions (id, recipe_id, version_number, target_vg_pg_ratio, total_flavor_percent) "
                "VALUES (100, 10, '1.3', '70/30', 10.0)"
            )
            db.execute(
                "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (100, 101, 8.0)"
            )
            db.commit()
            grant_user_capability(2, CAP_PRODUCTION_JOBS_VIEW)
            grant_user_capability(2, CAP_PRODUCTION_JOBS_PRODUCE)

        self.client = flask_app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _trinidad(self, *, size=120.0, count=30) -> tuple[int, int]:
        job_id = pjs.create_job(name="Trinidad", job_type="customer_order", created_by_user_id=1)
        jr_id = pjs.add_recipe_to_job(
            job_id=job_id,
            recipe_id=10,
            recipe_version_id=100,
            target_bottle_size_ml=size,
            target_bottle_count=count,
            mix_target_nic_mg_ml=3.0,
            mix_pg_ratio=30,
            mix_vg_ratio=70,
        )
        return job_id, jr_id

    def test_identity_operator_is_logged_in_user(self):
        with flask_app.app_context():
            _job_id, jr_id = self._trinidad()
            a = pjs.start_job_linked_mix(job_recipe_id=jr_id, operator_user_id=1, bottle_count=1)
            b = pjs.start_job_linked_mix(job_recipe_id=jr_id, operator_user_id=2, bottle_count=1)
            self.assertEqual(
                int(get_db().execute(
                    "SELECT operator_user_id FROM production_job_batches WHERE id=?",
                    (a["batch_id"],),
                ).fetchone()["operator_user_id"]),
                1,
            )
            self.assertEqual(
                int(get_db().execute(
                    "SELECT operator_user_id FROM production_job_batches WHERE id=?",
                    (b["batch_id"],),
                ).fetchone()["operator_user_id"]),
                2,
            )

    def test_riku_permissions_no_manage_no_lab_bridge(self):
        with flask_app.app_context():
            self.assertEqual(production_jobs_access_level("member", 2), "produce")
            self.assertTrue(can_produce_on_production_jobs("member", 2))
            self.assertFalse(can_manage_production_jobs("member", 2))
            self.assertFalse(can_access_lab_bridge("member"))
            # Manage capability not granted
            caps = {
                r["capability"]
                for r in get_db().execute(
                    "SELECT capability FROM user_capabilities WHERE user_id=2"
                ).fetchall()
            }
            self.assertIn(CAP_PRODUCTION_JOBS_PRODUCE, caps)
            self.assertNotIn(CAP_PRODUCTION_JOBS_MANAGE, caps)

    def test_production_credit_120_and_30(self):
        with flask_app.app_context():
            job120, jr120 = self._trinidad(size=120, count=30)
            started = pjs.start_job_linked_mix(job_recipe_id=jr120, operator_user_id=2, bottle_count=10)
            pjs.complete_batch_for_guided_session(mix_session_id=int(started["mix_session_id"]))
            summary = pay.earnings_summary_for_user(user_id=2)
            self.assertAlmostEqual(summary["total_earned"], 50.0)

            job30, jr30 = self._trinidad(size=30, count=20)
            get_db().execute("UPDATE production_jobs SET name='Poison Rally' WHERE id=?", (job30,))
            get_db().commit()
            s2 = pjs.start_job_linked_mix(job_recipe_id=jr30, operator_user_id=2, bottle_count=10)
            pjs.complete_batch_for_guided_session(mix_session_id=int(s2["mix_session_id"]))
            summary2 = pay.earnings_summary_for_user(user_id=2)
            self.assertAlmostEqual(summary2["total_earned"], 80.0)  # 50 + 30

    def test_cancelled_batch_no_credit(self):
        with flask_app.app_context():
            _job, jr = self._trinidad()
            started = pjs.start_job_linked_mix(job_recipe_id=jr, operator_user_id=2, bottle_count=10)
            pjs.cancel_batch(batch_id=int(started["batch_id"]), cancelled_by_user_id=2)
            row = get_db().execute(
                "SELECT status, cancelled_at FROM production_job_batches WHERE id=?",
                (started["batch_id"],),
            ).fetchone()
            self.assertEqual(row["status"], "cancelled")
            self.assertIsNotNone(row["cancelled_at"])
            self.assertAlmostEqual(pay.earnings_summary_for_user(user_id=2)["total_earned"], 0.0)

    def test_duplicate_completion_no_duplicate_credit(self):
        with flask_app.app_context():
            _job, jr = self._trinidad()
            started = pjs.start_job_linked_mix(job_recipe_id=jr, operator_user_id=2, bottle_count=10)
            sid = int(started["mix_session_id"])
            pjs.complete_batch_for_guided_session(mix_session_id=sid)
            pjs.complete_batch_for_guided_session(mix_session_id=sid)
            n = get_db().execute(
                "SELECT COUNT(1) AS c FROM production_job_credits WHERE user_id=2"
            ).fetchone()["c"]
            self.assertEqual(int(n), 1)
            self.assertAlmostEqual(pay.earnings_summary_for_user(user_id=2)["total_earned"], 50.0)

    def test_payment_ledger_partial(self):
        with flask_app.app_context():
            job_id, jr = self._trinidad()
            started = pjs.start_job_linked_mix(job_recipe_id=jr, operator_user_id=2, bottle_count=10)
            pjs.complete_batch_for_guided_session(mix_session_id=int(started["mix_session_id"]))
            pay.record_payment(user_id=2, amount=30, recorded_by_user_id=1, production_job_id=job_id)
            summary = pay.earnings_summary_for_user(user_id=2)
            self.assertAlmostEqual(summary["total_earned"], 50.0)
            self.assertAlmostEqual(summary["total_paid"], 30.0)
            self.assertAlmostEqual(summary["total_owed"], 20.0)
            pay.record_payment(user_id=2, amount=10, recorded_by_user_id=1)
            summary2 = pay.earnings_summary_for_user(user_id=2)
            self.assertAlmostEqual(summary2["total_paid"], 40.0)
            self.assertAlmostEqual(summary2["total_owed"], 10.0)
            self.assertEqual(len(summary2["payments"]), 2)

    def test_purchases_and_financials(self):
        with flask_app.app_context():
            job_id, jr = self._trinidad()
            inv = pjs.list_job_inventory(job_id)
            vg = next(x for x in inv if x["ingredient_type"] == "vg")
            finance.record_job_purchase(
                job_id=job_id,
                inventory_id=int(vg["id"]),
                quantity_ml=10000,
                pack_count=2,
                pack_size_ml=5000,
                pack_price=369,
                total_paid=738,
                source="BLCK",
                recorded_by_user_id=1,
            )
            started = pjs.start_job_linked_mix(job_recipe_id=jr, operator_user_id=2, bottle_count=10)
            pjs.complete_batch_for_guided_session(mix_session_id=int(started["mix_session_id"]))
            finance.update_job_revenue(
                job_id=job_id, revenue_mode="customer_order", revenue_amount=19800
            )
            finance.update_job_referral(
                job_id=job_id, referral_label="Jaco", referral_mode="percent", referral_value=5
            )
            finance.add_job_expense(
                job_id=job_id,
                category="shipping",
                description="Courier",
                amount=1200,
                recorded_by_user_id=1,
            )
            fin = finance.job_financial_summary(job_id)
            self.assertAlmostEqual(fin["revenue"], 19800.0)
            self.assertAlmostEqual(fin["referral"]["amount"], 990.0)
            self.assertAlmostEqual(fin["shipping_packing"], 1200.0)
            self.assertAlmostEqual(fin["production_pay"], 50.0)
            self.assertGreater(fin["cash_spent_on_inventory"], 0)
            self.assertAlmostEqual(fin["old_goat_profit"], fin["revenue"] - fin["total_fulfilment_cost"])

    def test_personal_mix_isolation_financials(self):
        with flask_app.app_context():
            job_id, jr = self._trinidad()
            started = pjs.start_job_linked_mix(job_recipe_id=jr, operator_user_id=2, bottle_count=10)
            pjs.complete_batch_for_guided_session(mix_session_id=int(started["mix_session_id"]))
            before = pay.earnings_summary_for_user(user_id=2)
            before_fin = finance.job_financial_summary(job_id)

            sid, _, _ = guided_mix_session_service.create_guided_mix_session(
                user_id=1,
                recipe_id=10,
                requested_version_id=100,
                target_ml=120,
                target_nic_mg_ml=3,
                nic_base_mg_ml=100,
                pg_ratio=30,
                vg_ratio=70,
                nicotine_carrier="PG",
            )
            row = get_db().execute(
                "SELECT production_job_id FROM mix_sessions WHERE id=?", (sid,)
            ).fetchone()
            self.assertIsNone(row["production_job_id"])
            after = pay.earnings_summary_for_user(user_id=2)
            after_fin = finance.job_financial_summary(job_id)
            self.assertAlmostEqual(before["total_earned"], after["total_earned"])
            self.assertAlmostEqual(before_fin["production_pay"], after_fin["production_pay"])
            overview = pjs.job_overview(job_id)
            self.assertAlmostEqual(overview["completed_bottles"], 10.0)

    def test_owner_labour_tracked_not_owed(self):
        with flask_app.app_context():
            _job, jr = self._trinidad()
            started = pjs.start_job_linked_mix(job_recipe_id=jr, operator_user_id=1, bottle_count=10)
            pjs.complete_batch_for_guided_session(mix_session_id=int(started["mix_session_id"]))
            # Wynand/Old Goat credits are owner labour — not in helper earnings
            self.assertAlmostEqual(pay.earnings_summary_for_user(user_id=1)["total_earned"], 0.0)
            credit = get_db().execute(
                "SELECT labour_kind, amount FROM production_job_credits WHERE user_id=1"
            ).fetchone()
            self.assertEqual(credit["labour_kind"], "owner")
            self.assertAlmostEqual(float(credit["amount"]), 50.0)

    def test_cancel_from_session_helper(self):
        with flask_app.app_context():
            job_id, jr = self._trinidad()
            started = pjs.start_job_linked_mix(job_recipe_id=jr, operator_user_id=2, bottle_count=5)
            result = pjs.cancel_batch_for_guided_session(
                mix_session_id=int(started["mix_session_id"]),
                cancelled_by_user_id=2,
            )
            self.assertEqual(result["status"], "cancelled")
            self.assertEqual(int(result["production_job_id"]), job_id)
            self.assertAlmostEqual(pjs.job_overview(job_id)["completed_bottles"], 0.0)


if __name__ == "__main__":
    unittest.main()
