"""Catalogue boundaries and conservative identities, independent of guided mixing."""

import unittest
import os
import sqlite3
import json
from pathlib import Path
import subprocess
import sys

from app import app as flask_app
from config.catalogue_identity import canonical_item
from db import get_db
from models import inventory_repo, recipes_repo
from services.stash_service import fetch_stash_quick_add_options
from tests import test_flavour_search as _fixtures


class IdentityTests(unittest.TestCase):
    def test_known_silverline_aliases_share_identity(self):
        keys = {
            canonical_item(b, n)[2]
            for b, n in [
                ("CAP", "27 Fish"),
                ("CAP (SL)", "27 Fish (Sl)"),
                ("CAP", "27 Fish Concentrate SL"),
                ("CSL", "27 Fish"),
            ]
        }
        self.assertEqual(len(keys), 1)
        self.assertEqual(
            canonical_item("CAP", "27 Fish Concentrate SL")[:2], ("CAP (SL)", "27 Fish")
        )

    def test_versions_manufacturers_categories_and_lines_stay_separate(self):
        for first, second in [
            (("CAP", "Custard V1", "Flavor"), ("CAP", "Custard V2", "Flavor")),
            (("CAP", "Vanilla", "Flavor"), ("TFA", "Vanilla", "Flavor")),
            (("CAP", "Vanilla", "Flavor"), ("CAP (SL)", "Vanilla", "Flavor")),
            (("CAP", "27 Fish", "Flavor"), ("CAP (ES)", "27 Fish", "Flavor")),
            (("Test", "Liquid", "Base"), ("Test", "Liquid", "Flavor")),
        ]:
            self.assertNotEqual(canonical_item(*first)[2], canonical_item(*second)[2])

    def test_legacy_helpers_and_supplier_normalizer_preserve_versions_and_lines(self):
        from config.canonical_library_brands import (
            canonical_library_brand,
            fold_capella_brand_if_name_unmarked,
        )
        from scrapers.clyrolinx_scraper import normalize_name

        self.assertEqual(canonical_library_brand("CAP (ES)", "Vanilla"), "CAP (ES)")
        self.assertEqual(
            fold_capella_brand_if_name_unmarked("CAP (SL)", "Vanilla"), "CAP (SL)"
        )
        self.assertNotEqual(
            normalize_name("Vanilla (V1)"), normalize_name("Vanilla (V2)")
        )


class CatalogueBoundaryTests(unittest.TestCase):
    setUp = _fixtures.FlavourSearchTests.setUp
    tearDown = _fixtures.FlavourSearchTests.tearDown

    def test_batch_corrections_share_one_validation_snapshot_and_support_shared_references(
        self,
    ):
        from unittest.mock import patch
        from services import catalogue_cleanup_service as service

        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO flavor_reference(id,brand,name,category,active) VALUES(301,'CAP','Cotton Candy','Hardware',1)"
            )
            for fid in range(301, 341):
                db.execute(
                    "INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(?,'CAP','Cotton Candy','Hardware',301)",
                    (fid,),
                )
            plans = service.category_plan(db)
            self.assertEqual(len(plans), 40)
            with patch.object(
                service, "category_plan", wraps=service.category_plan
            ) as scan:
                results = service.apply_category_corrections(
                    db, plans, actor_user_id=1, actor_role="Master"
                )
                self.assertEqual(scan.call_count, 1)
            self.assertEqual(len(results), 40)
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM flavours WHERE category='Flavor'"
                ).fetchone()[0],
                40,
            )
            self.assertEqual(
                db.execute(
                    "SELECT category FROM flavor_reference WHERE id=301"
                ).fetchone()[0],
                "Flavor",
            )

    def test_stale_last_category_rejects_entire_batch_before_first_update(self):
        from services import catalogue_cleanup_service as service

        with flask_app.app_context():
            db = get_db()
            for fid in (301, 302):
                db.execute(
                    "INSERT INTO flavor_reference(id,brand,name,category,active) VALUES(?,'CAP',?,'Hardware',1)",
                    (fid, f"Candy {fid}"),
                )
                db.execute(
                    "INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(?,'CAP',?,'Hardware',?)",
                    (fid, f"Candy {fid}", fid),
                )
            plans = service.category_plan(db)
            db.execute(
                "UPDATE flavours SET profile='Changed after preview' WHERE id=302"
            )
            with self.assertRaisesRegex(ValueError, "changed since preview"):
                service.apply_category_corrections(
                    db, plans, actor_user_id=1, actor_role="Master"
                )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM flavours WHERE category='Hardware'"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM tvw_admin_audit_logs").fetchone()[0], 0
            )

    def test_category_correction_blocks_a_different_linked_product_even_when_categories_agree(
        self,
    ):
        from services.catalogue_cleanup_service import (
            category_plan,
            apply_category_correction,
        )

        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO flavor_reference(id,brand,name,category,is_nicotine,active) VALUES(301,'Clyrolinx','ClyroNic PG 100mg/ml','Nicotine',1,1)"
            )
            db.execute(
                "INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(301,'CAP','Cotton Candy','Hardware',301)"
            )
            plan = category_plan(db)[0]
            self.assertTrue(
                any(
                    "names or manufacturers disagree" in issue
                    for issue in plan["issues"]
                )
            )
            with self.assertRaisesRegex(ValueError, "names or manufacturers disagree"):
                apply_category_correction(
                    db, plan, actor_user_id=1, actor_role="Master"
                )
            self.assertEqual(
                db.execute("SELECT category FROM flavours WHERE id=301").fetchone()[0],
                "Hardware",
            )
            self.assertEqual(
                tuple(
                    db.execute(
                        "SELECT category,is_nicotine FROM flavor_reference WHERE id=301"
                    ).fetchone()
                ),
                ("Nicotine", 1),
            )

    def test_edit_cannot_create_another_existing_flavour_identity(self):
        from services.inventory_service import update_item_from_form

        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO flavor_reference(id,brand,name,category,active) VALUES(301,'CAP (SL)','27 Fish (Sl)','Flavor',1)"
            )
            item_id = inventory_repo.insert_library_item(
                reference_id=101, current_stock=10
            )
            db.commit()
            result, status = update_item_from_form(
                actor_id=1,
                actor_username="member1",
                item_id=item_id,
                name="27 Fish Concentrate SL",
                brand="CAP",
                category="Flavor",
                stock=99,
                unit="ml",
                cost_per_g=None,
            )
            self.assertEqual(status, 409)
            self.assertIn("already exists", result["message"])
            self.assertEqual(
                db.execute(
                    "SELECT current_stock FROM library_items WHERE id=?", (item_id,)
                ).fetchone()[0],
                10,
            )
            self.assertEqual(
                db.execute("SELECT name FROM flavor_reference WHERE id=101").fetchone()[
                    0
                ],
                "Biscuit",
            )

    def test_all_flavour_pickers_exclude_non_flavours_and_hidden_entries(self):
        with flask_app.app_context():
            db = get_db()
            for fid, brand, name, category in [
                (201, "Demon Killer", "Ceramic Tweezers", "Hardware"),
                (202, "Base supplier", "Vegetable Glycerin", "Base"),
                (203, "Nic supplier", "Freebase", "Nicotine"),
                (204, "CAP", "Cotton Candy", "Flavor"),
            ]:
                db.execute(
                    "INSERT INTO flavor_reference(id,brand,name,category,active) VALUES(?,?,?,?,1)",
                    (fid, brand, name, category),
                )
                db.execute(
                    "INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(?,?,?,?,?)",
                    (fid, brand, name, category, fid),
                )
            # A stale catalogue category must not override its hardware reference.
            db.execute("UPDATE flavours SET category='Flavor' WHERE id=201")
            db.execute(
                "INSERT INTO flavours(id,brand,canonical_name,category,is_hidden) VALUES(205,'Hidden','Secret','Flavor',1)"
            )
            db.execute(
                "INSERT INTO flavours(id,brand,canonical_name,category,is_invalid) VALUES(206,'Invalid','Bad','Flavor',1)"
            )
            db.execute(
                "INSERT INTO flavours(id,brand,canonical_name,category,moderation_status) VALUES(207,'Pending','Wait','Flavor','pending')"
            )
            rows, total = recipes_repo.fetch_tvw_flavours(page_size=1)
            self.assertEqual(total, 1)
            self.assertEqual(rows[0]["id"], 204)
            self.assertEqual(
                recipes_repo.fetch_tvw_flavours(page=2, page_size=1)[0], []
            )
            self.assertEqual(recipes_repo.fetch_tvw_flavour_manufacturers(), ["CAP"])
            self.assertEqual([r["id"] for r in fetch_stash_quick_add_options()], [204])
            options = recipes_repo.fetch_flavour_reference_options()
            ids = {r["id"] for r in options}
            self.assertIn(204, ids)
            self.assertTrue(ids.isdisjoint({201, 202, 203}))
            self.assertEqual(
                recipes_repo.search_flavour_reference_for_builder(query="Tweezers"), []
            )

    def test_manual_add_reuses_priced_alias_without_changing_stock_or_prices(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO flavor_reference(id,brand,name,category,active,price_per_ml) VALUES(201,'CAP','27 Fish','Flavor',1,0)"
            )
            db.execute(
                "INSERT INTO flavor_reference(id,brand,name,category,active,price_per_ml) VALUES(202,'CAP (SL)','27 Fish (Sl)','Flavor',1,2.5)"
            )
            self.assertEqual(
                inventory_repo.insert_reference(
                    brand="CAP", name="27 Fish Concentrate SL", category="Flavor"
                ),
                202,
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM flavor_reference WHERE id>=201"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                db.execute(
                    "SELECT price_per_ml FROM flavor_reference WHERE id=202"
                ).fetchone()[0],
                2.5,
            )

    def test_repeated_sync_reuses_aliases_in_both_catalogues(self):
        from scripts.sync_tvw_flavour_reference import sync_flavours

        source = os.path.join(self.temp_dir.name, "source.db")
        with sqlite3.connect(self.db_path) as target, sqlite3.connect(source) as src:
            target.backup(src)
            for fid, brand, name in [
                (301, "CAP", "27 Fish"),
                (302, "CAP (SL)", "27 Fish (Sl)"),
                (303, "CAP", "27 Fish Concentrate SL"),
            ]:
                src.execute(
                    "INSERT INTO flavor_reference(id,brand,name,category,active) VALUES(?,?,?,'Flavor',1)",
                    (fid, brand, name),
                )
        sync_flavours(source, self.db_path)
        again = sync_flavours(source, self.db_path)
        self.assertEqual(again["flavor_reference_created"], 0)
        self.assertEqual(again["flavours_created"], 0)
        with flask_app.app_context():
            db = get_db()
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM flavor_reference WHERE name LIKE '27 Fish%'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM flavours WHERE canonical_name LIKE '27 Fish%'"
                ).fetchone()[0],
                1,
            )

    def test_cleanup_detects_name_variants_but_ignores_merged_aliases(self):
        from models.tvw_admin_repo import list_flavour_identity_cleanup_rows

        with flask_app.app_context():
            db = get_db()
            for fid, brand, name in [
                (301, "CAP", "27 Fish"),
                (302, "CAP (SL)", "27 Fish (Sl)"),
                (303, "CAP", "27 Fish Concentrate SL"),
            ]:
                db.execute(
                    "INSERT INTO flavours(id,brand,canonical_name,category) VALUES(?,?,?,'Flavor')",
                    (fid, brand, name),
                )
            rows, total = list_flavour_identity_cleanup_rows(view="duplicates")
            self.assertEqual(total, 3)
            self.assertEqual({r["dup_group_size"] for r in rows}, {3})
            db.execute(
                "UPDATE flavours SET merged_into_flavour_id=301 WHERE id IN (302,303)"
            )
            self.assertEqual(
                list_flavour_identity_cleanup_rows(view="duplicates")[1], 0
            )

    def test_merge_aliases_linked_to_same_reference_keeps_recipe_ingredients(self):
        from models.tvw_admin_repo import _repoint_recipe_items_merge_dup_versions

        with flask_app.app_context():
            db = get_db()
            # Regression: the old merge helper found its own row, doubled it, then deleted it.
            db.execute("INSERT INTO recipes(id,name) VALUES(1,'Test')")
            db.execute(
                "INSERT INTO recipe_versions(id,recipe_id,version_number) VALUES(1,1,'1')"
            )
            db.execute(
                "INSERT INTO recipe_items(version_id,ingredient_id,amount_percent) VALUES(1,101,2.5)"
            )
            self.assertEqual(
                _repoint_recipe_items_merge_dup_versions(
                    db, source_ref_id=101, target_ref_id=101
                ),
                0,
            )
            self.assertEqual(
                db.execute(
                    "SELECT amount_percent FROM recipe_items WHERE ingredient_id=101"
                ).fetchone()[0],
                2.5,
            )

    def test_supplier_and_recipe_imports_reuse_priced_silverline_record(self):
        from scrapers.vc_flavours_scraper import (
            build_identity_index,
            upsert_vc_reference_item,
        )
        from scrapers.vc_recipes_dtl_scraper import _ensure_reference_for_flavor
        from scrapers.blck_scraper import _upsert_flavour_reference_from_primary_variant

        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO flavor_reference(id,brand,name,category,active,price_per_ml) VALUES(301,'CAP (SL)','27 Fish (Sl)','Flavor',1,2.5)"
            )
            outcome = upsert_vc_reference_item(
                db,
                vc_item={
                    "brand": "CAP",
                    "name": "27 Fish Concentrate SL",
                    "avg_usage": 2.5,
                },
                now_ts="2026-11-11",
                identity_index=build_identity_index(db),
            )
            self.assertEqual(outcome, "enriched")
            self.assertEqual(
                _ensure_reference_for_flavor(
                    db, reference_lookup={}, brand_raw="CAP", flavor_raw="27 Fish"
                ),
                301,
            )
            added, updated = [], []
            _upsert_flavour_reference_from_primary_variant(
                db,
                product={
                    "title": "27 Fish Concentrate SL",
                    "vendor": "Capella",
                    "handle": "27-fish",
                    "variants": [
                        {"id": 123, "title": "10ml", "option1": "10ml", "price": "25"}
                    ],
                },
                category="Flavor",
                now="2026-11-11",
                added=added,
                updated=updated,
            )
            self.assertEqual(added, [])
            self.assertEqual(len(updated), 1)
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM flavor_reference WHERE id>=301"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute(
                    "SELECT price_per_ml FROM flavor_reference WHERE id=301"
                ).fetchone()[0],
                2.5,
            )

    def test_reviewed_categories_remove_hardware_and_restore_mislabelled_candy(self):
        from services.catalogue_cleanup_service import (
            category_plan,
            apply_category_correction,
        )

        with flask_app.app_context():
            db = get_db()
            for fid, brand, name, category in [
                (301, "Demon Killer", "Ceramic Tweezers", "Flavor"),
                (302, "CAP", "Cotton Candy", "Hardware"),
            ]:
                db.execute(
                    "INSERT INTO flavor_reference(id,brand,name,category,active) VALUES(?,?,?,?,1)",
                    (fid, brand, name, category),
                )
                db.execute(
                    "INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(?,?,?,?,?)",
                    (fid, brand, name, category, fid),
                )
            plans = category_plan(db)
            self.assertEqual(
                [(p["catalogue_id"], p["proposed_category"]) for p in plans],
                [(301, "Hardware"), (302, "Flavor")],
            )
            for p in plans:
                self.assertEqual(p["issues"], [])
                apply_category_correction(db, p, actor_user_id=1, actor_role="Master")
            self.assertEqual(
                [r["id"] for r in recipes_repo.fetch_tvw_flavours()[0]], [302]
            )


class CatalogueConsolidationTests(unittest.TestCase):
    setUp = _fixtures.FlavourSearchTests.setUp
    tearDown = _fixtures.FlavourSearchTests.tearDown

    def seed_duplicates(self, db):
        for fid, brand, name, price in [
            (301, "CAP", "27 Fish", 0),
            (302, "CAP (SL)", "27 Fish (Sl)", 2.5),
            (303, "CAP", "27 Fish Concentrate SL", 1.5),
        ]:
            db.execute(
                "INSERT INTO flavor_reference(id,brand,name,category,price_per_ml,active) VALUES(?,?,?,'Flavor',?,1)",
                (fid, brand, name, price),
            )
            db.execute(
                "INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id,avg_usage) VALUES(?,?,?,'Flavor',?,2.5)",
                (fid, brand, name, fid),
            )
            db.execute(
                "INSERT INTO library_items(reference_id,current_stock,cost_per_unit,notes) VALUES(?,?,?,?)",
                (fid, 10, price, f"Original lot {fid}"),
            )
            db.execute(
                "INSERT INTO user_stash_items(user_id,flavour_id,quantity_ml,is_favorite) VALUES(1,?,?,?)",
                (fid, 20, int(fid == 301)),
            )
        db.execute("INSERT INTO recipes(id,name) VALUES(1,'Fish recipe')")
        db.execute(
            "INSERT INTO recipe_versions(id,recipe_id,version_number) VALUES(1,1,'1')"
        )
        for fid, pct in [(301, 2.5), (302, 3), (303, 1)]:
            db.execute(
                "INSERT INTO recipe_items(version_id,ingredient_id,amount_percent) VALUES(1,?,?)",
                (fid, pct),
            )
        db.execute(
            "INSERT INTO flavour_notes(flavour_id,author_user_id,note) VALUES(301,1,'Keep this note')"
        )
        db.execute(
            "INSERT INTO flavour_reviews(flavour_id,author_user_id,rating,review) VALUES(303,1,4,'Keep this review')"
        )
        db.execute(
            "INSERT INTO mix_sessions(id,user_id,recipe_version_id,mode,result_json,completed_at) "
            "VALUES(1,1,1,'guided','{\"ingredient_id\":301,\"grams\":0.62}','2026-11-01')"
        )

    def test_consolidation_preserves_quantities_prices_notes_recipes_and_history(self):
        from services.catalogue_cleanup_service import duplicate_plan, consolidate_group

        with flask_app.app_context():
            db = get_db()
            self.seed_duplicates(db)
            plan = duplicate_plan(db)[0]
            self.assertEqual(plan["target_id"], 302)
            self.assertEqual(plan["issues"], [])
            consolidate_group(db, plan, actor_user_id=1, actor_role="Master")
            self.assertEqual(recipes_repo.resolve_tvw_flavour_id(301), 302)
            self.assertEqual(recipes_repo.resolve_tvw_flavour_id(303), 302)
            self.assertEqual(recipes_repo.fetch_tvw_flavours()[1], 1)
            self.assertEqual(
                db.execute(
                    "SELECT amount_percent FROM recipe_items WHERE ingredient_id=302"
                ).fetchone()[0],
                6.5,
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM recipe_items").fetchone()[0], 1
            )
            self.assertEqual(
                db.execute(
                    "SELECT SUM(current_stock) FROM library_items WHERE reference_id=302"
                ).fetchone()[0],
                30,
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(DISTINCT notes) FROM library_items"
                ).fetchone()[0],
                3,
            )
            stash = db.execute(
                "SELECT quantity_ml,is_favorite FROM user_stash_items"
            ).fetchone()
            self.assertEqual(tuple(stash), (60, 1))
            self.assertEqual(
                db.execute("SELECT flavour_id FROM flavour_notes").fetchone()[0], 302
            )
            self.assertEqual(
                db.execute("SELECT flavour_id FROM flavour_reviews").fetchone()[0], 302
            )
            self.assertEqual(
                [
                    tuple(r)
                    for r in db.execute(
                        "SELECT id,price_per_ml,active FROM flavor_reference WHERE id>=301 ORDER BY id"
                    )
                ],
                [(301, 0.0, 0), (302, 2.5, 1), (303, 1.5, 0)],
            )
            self.assertEqual(
                db.execute("SELECT result_json FROM mix_sessions").fetchone()[0],
                '{"ingredient_id":301,"grams":0.62}',
            )
            self.assertEqual(duplicate_plan(db), [])
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM tvw_admin_audit_logs WHERE action='catalogue_identity_consolidated'"
                ).fetchone()[0],
                1,
            )

    def test_stale_plan_and_unfinished_mix_block_cleanup(self):
        from services.catalogue_cleanup_service import duplicate_plan, consolidate_group

        with flask_app.app_context():
            db = get_db()
            self.seed_duplicates(db)
            plan = duplicate_plan(db)[0]
            db.execute(
                "UPDATE flavours SET profile='Changed after preview' WHERE id=301"
            )
            with self.assertRaisesRegex(ValueError, "changed since preview"):
                consolidate_group(db, plan, actor_user_id=1, actor_role="Master")
            db.execute("UPDATE mix_sessions SET completed_at=NULL WHERE id=1")
            fresh = duplicate_plan(db)[0]
            self.assertTrue(
                any("unfinished guided mix" in issue for issue in fresh["issues"])
            )
            with self.assertRaisesRegex(ValueError, "unfinished guided mix"):
                consolidate_group(db, fresh, actor_user_id=1, actor_role="Master")
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM recipe_items").fetchone()[0], 3
            )

    def test_cli_backup_apply_and_transaction_rollback(self):
        with flask_app.app_context():
            db = get_db()
            self.seed_duplicates(db)
            db.execute("UPDATE users SET role='Master' WHERE id=1")
            db.execute(
                "CREATE TRIGGER reject_last_stash BEFORE UPDATE OF flavour_id ON user_stash_items "
                "WHEN OLD.flavour_id=303 BEGIN SELECT RAISE(ABORT,'simulated failure'); END"
            )
            # The stash helper deletes a duplicate instead of updating it when the target exists.
            db.execute(
                "CREATE TRIGGER reject_last_stash_delete BEFORE DELETE ON user_stash_items "
                "WHEN OLD.flavour_id=303 BEGIN SELECT RAISE(ABORT,'simulated failure'); END"
            )
            db.commit()
        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "cleanup_flavour_catalogue.py"
        )
        cmd = [sys.executable, str(script), "--db", self.db_path]
        preview = subprocess.run(cmd, capture_output=True, text=True, check=True)
        manifest = Path(self.temp_dir.name) / "plan.json"
        manifest.write_text(preview.stdout)
        self.assertEqual(json.loads(preview.stdout)["duplicates"][0]["target_id"], 302)
        apply = cmd + [
            "--apply-plan",
            str(manifest),
            "--target-id",
            "302",
            "--actor-id",
            "1",
            "--backup",
            str(Path(self.temp_dir.name) / "backup.db"),
        ]
        failed = subprocess.run(apply, capture_output=True, text=True)
        self.assertNotEqual(failed.returncode, 0)
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM recipe_items").fetchone()[0], 3
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM flavours WHERE merged_into_flavour_id IS NOT NULL"
                ).fetchone()[0],
                0,
            )
            db.execute("DROP TRIGGER reject_last_stash")
            db.execute("DROP TRIGGER reject_last_stash_delete")
        # Existing backup is never overwritten, even after an unsuccessful apply.
        self.assertNotEqual(
            subprocess.run(apply, capture_output=True, text=True).returncode, 0
        )
        apply[-1] = str(Path(self.temp_dir.name) / "second-backup.db")
        subprocess.run(apply, capture_output=True, text=True, check=True)
        with sqlite3.connect(apply[-1]) as backup:
            self.assertEqual(
                backup.execute("SELECT COUNT(*) FROM recipe_items").fetchone()[0], 3
            )
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM recipe_items").fetchone()[0], 1
            )


if __name__ == "__main__":
    unittest.main()
