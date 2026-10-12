from __future__ import annotations

from typing import Iterable, Tuple

from db import get_db


def _existing_columns(table: str) -> set[str]:
    db = get_db()
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _add_column_if_missing(table: str, column: str, column_sql: str) -> bool:
    """Return True if column was added."""
    db = get_db()
    cols = _existing_columns(table)
    if column in cols:
        return False
    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_sql}")
    return True


def run_startup_migrations() -> dict:
    """Run idempotent startup migrations once per app start.

    This keeps schema evolution out of request handlers.
    """
    db = get_db()

    # Ensure base reference rows exist for logging (FK on mix_history_items.ingredient_id).
    try:
        db.execute(
            "INSERT OR IGNORE INTO flavor_reference (brand, name, category, active, density) VALUES (?, ?, ?, 1, ?)",
            ("BASE", "PG", "Base", 1.036),
        )
        db.execute(
            "INSERT OR IGNORE INTO flavor_reference (brand, name, category, active, density) VALUES (?, ?, ?, 1, ?)",
            ("BASE", "VG", "Base", 1.26),
        )
    except Exception:
        pass

    # Ensure newer log tables exist on older DBs.
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS shopping_list_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER,
            thresholds_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(actor_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS shopping_list_run_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            reference_id INTEGER NOT NULL,
            current_qty_ml REAL DEFAULT 0.0,
            target_qty_ml REAL DEFAULT 0.0,
            supplier TEXT,
            product_url TEXT,
            price_raw REAL DEFAULT 0.0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(run_id) REFERENCES shopping_list_runs(id) ON DELETE CASCADE,
            FOREIGN KEY(reference_id) REFERENCES flavor_reference(id)
        );

        CREATE TABLE IF NOT EXISTS mix_history_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            ingredient_id INTEGER NOT NULL,
            measured_grams REAL NOT NULL,
            target_grams REAL,
            delta_grams REAL,
            cost_at_time REAL DEFAULT 0.0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS scraper_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scraper_name TEXT NOT NULL,
            run_type TEXT NOT NULL,
            run_key TEXT,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_at DATETIME,
            status TEXT DEFAULT 'started',
            summary_json TEXT
        );

        CREATE TABLE IF NOT EXISTS stock_take_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by INTEGER,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_at DATETIME,
            status TEXT DEFAULT 'open',
            notes TEXT,
            FOREIGN KEY(created_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS stock_take_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_take_run_id INTEGER NOT NULL,
            library_item_id INTEGER NOT NULL,
            reference_id INTEGER NOT NULL,
            expected_stock REAL DEFAULT 0.0,
            actual_stock REAL,
            variance REAL DEFAULT 0.0,
            action_taken TEXT DEFAULT 'skipped',
            notes TEXT,
            FOREIGN KEY(stock_take_run_id) REFERENCES stock_take_runs(id) ON DELETE CASCADE,
            FOREIGN KEY(library_item_id) REFERENCES library_items(id),
            FOREIGN KEY(reference_id) REFERENCES flavor_reference(id)
        );

        CREATE TABLE IF NOT EXISTS production_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER,
            batch_name TEXT NOT NULL,
            total_volume_ml REAL NOT NULL DEFAULT 0.0,
            remaining_volume_ml REAL NOT NULL DEFAULT 0.0,
            batch_status TEXT NOT NULL DEFAULT 'open',
            mixed_by INTEGER,
            mixed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            FOREIGN KEY(recipe_id) REFERENCES recipes(id),
            FOREIGN KEY(mixed_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS decant_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            production_batch_id INTEGER NOT NULL,
            created_by INTEGER,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_at DATETIME,
            notes TEXT,
            wastage_ml REAL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'open',
            FOREIGN KEY(production_batch_id) REFERENCES production_batches(id),
            FOREIGN KEY(created_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS decant_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decant_run_id INTEGER NOT NULL,
            bottle_size_ml REAL NOT NULL DEFAULT 0.0,
            quantity_produced INTEGER NOT NULL DEFAULT 0,
            total_volume_used_ml REAL NOT NULL DEFAULT 0.0,
            label_name TEXT,
            FOREIGN KEY(decant_run_id) REFERENCES decant_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS finished_goods_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            production_batch_id INTEGER,
            recipe_id INTEGER,
            product_name TEXT NOT NULL,
            bottle_size_ml REAL NOT NULL DEFAULT 0.0,
            quantity_on_hand INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(production_batch_id) REFERENCES production_batches(id),
            FOREIGN KEY(recipe_id) REFERENCES recipes(id)
        );

        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            contact_person TEXT,
            phone TEXT,
            email TEXT,
            client_type TEXT NOT NULL DEFAULT 'consignment',
            payment_terms_days INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS decant_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_batch_id INTEGER,
            recipe_id INTEGER,
            recipe_version_id INTEGER,
            batch_number_snapshot TEXT,
            product_name_snapshot TEXT NOT NULL,
            bottle_size_ml REAL NOT NULL DEFAULT 0.0,
            bottles_created INTEGER NOT NULL DEFAULT 0,
            total_ml_decanted REAL NOT NULL DEFAULT 0.0,
            cost_total_snapshot REAL NOT NULL DEFAULT 0.0,
            cost_per_bottle_snapshot REAL NOT NULL DEFAULT 0.0,
            created_by_user_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            FOREIGN KEY(source_batch_id) REFERENCES production_batches(id),
            FOREIGN KEY(recipe_id) REFERENCES recipes(id),
            FOREIGN KEY(created_by_user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS finished_goods_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER,
            recipe_version_id INTEGER,
            source_batch_id INTEGER,
            decant_event_id INTEGER,
            product_name_snapshot TEXT NOT NULL,
            bottle_size_ml REAL NOT NULL DEFAULT 0.0,
            quantity_on_hand INTEGER NOT NULL DEFAULT 0,
            quantity_reserved INTEGER NOT NULL DEFAULT 0,
            quantity_at_clients INTEGER NOT NULL DEFAULT 0,
            cost_per_bottle REAL NOT NULL DEFAULT 0.0,
            default_wholesale_price REAL NOT NULL DEFAULT 0.0,
            default_retail_reference_price REAL NOT NULL DEFAULT 0.0,
            location_status TEXT NOT NULL DEFAULT 'lab',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(recipe_id) REFERENCES recipes(id),
            FOREIGN KEY(source_batch_id) REFERENCES production_batches(id),
            FOREIGN KEY(decant_event_id) REFERENCES decant_events(id)
        );

        CREATE TABLE IF NOT EXISTS client_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            delivery_number TEXT NOT NULL UNIQUE,
            delivery_date DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            delivery_type TEXT NOT NULL DEFAULT 'consignment',
            status TEXT NOT NULL DEFAULT 'open',
            notes TEXT,
            created_by_user_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(client_id) REFERENCES clients(id),
            FOREIGN KEY(created_by_user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS client_delivery_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            delivery_id INTEGER NOT NULL,
            finished_goods_stock_id INTEGER,
            source_batch_id INTEGER,
            product_name_snapshot TEXT NOT NULL,
            bottle_size_ml REAL NOT NULL DEFAULT 0.0,
            qty_sent INTEGER NOT NULL DEFAULT 0,
            unit_price_snapshot REAL NOT NULL DEFAULT 0.0,
            total_value_snapshot REAL NOT NULL DEFAULT 0.0,
            qty_reported_sold INTEGER NOT NULL DEFAULT 0,
            qty_returned INTEGER NOT NULL DEFAULT 0,
            qty_damaged INTEGER NOT NULL DEFAULT 0,
            qty_remaining_with_client INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(delivery_id) REFERENCES client_deliveries(id) ON DELETE CASCADE,
            FOREIGN KEY(finished_goods_stock_id) REFERENCES finished_goods_stock(id),
            FOREIGN KEY(source_batch_id) REFERENCES production_batches(id)
        );

        CREATE TABLE IF NOT EXISTS client_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            payment_date DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            amount REAL NOT NULL DEFAULT 0.0,
            payment_method TEXT,
            reference TEXT,
            notes TEXT,
            created_by_user_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(client_id) REFERENCES clients(id),
            FOREIGN KEY(created_by_user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS client_payment_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_id INTEGER NOT NULL,
            delivery_id INTEGER NOT NULL,
            amount_allocated REAL NOT NULL DEFAULT 0.0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(payment_id) REFERENCES client_payments(id) ON DELETE CASCADE,
            FOREIGN KEY(delivery_id) REFERENCES client_deliveries(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS stock_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            movement_type TEXT NOT NULL,
            movement_date DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            recipe_id INTEGER,
            recipe_version_id INTEGER,
            source_batch_id INTEGER,
            decant_event_id INTEGER,
            finished_goods_stock_id INTEGER,
            client_id INTEGER,
            delivery_id INTEGER,
            delivery_line_id INTEGER,
            qty_bottles INTEGER DEFAULT 0,
            qty_ml REAL DEFAULT 0.0,
            amount_value REAL DEFAULT 0.0,
            notes TEXT,
            created_by_user_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(recipe_id) REFERENCES recipes(id),
            FOREIGN KEY(source_batch_id) REFERENCES production_batches(id),
            FOREIGN KEY(decant_event_id) REFERENCES decant_events(id),
            FOREIGN KEY(finished_goods_stock_id) REFERENCES finished_goods_stock(id),
            FOREIGN KEY(client_id) REFERENCES clients(id),
            FOREIGN KEY(delivery_id) REFERENCES client_deliveries(id),
            FOREIGN KEY(delivery_line_id) REFERENCES client_delivery_lines(id),
            FOREIGN KEY(created_by_user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS delivery_line_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            delivery_id INTEGER NOT NULL,
            delivery_line_id INTEGER NOT NULL,
            correction_type TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            from_state TEXT NOT NULL,
            to_state TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_by_user_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(delivery_id) REFERENCES client_deliveries(id) ON DELETE CASCADE,
            FOREIGN KEY(delivery_line_id) REFERENCES client_delivery_lines(id) ON DELETE CASCADE,
            FOREIGN KEY(created_by_user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS flavor_recipe_analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reference_id INTEGER NOT NULL UNIQUE,
            recipe_count INTEGER,
            avg_pct REAL,
            min_pct REAL,
            max_pct REAL,
            median_pct REAL,
            likely_recipe_role TEXT,
            top_pairings_json TEXT,
            pairing_insights_json TEXT,
            analytics_version TEXT,
            updated_at DATETIME,
            FOREIGN KEY(reference_id) REFERENCES flavor_reference(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS flavor_pairings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reference_id_a INTEGER NOT NULL,
            reference_id_b INTEGER NOT NULL,
            recipe_count INTEGER,
            avg_pct_a REAL,
            avg_pct_b REAL,
            pairing_strength REAL,
            relationship_type TEXT,
            pairing_reason TEXT,
            updated_at DATETIME,
            analytics_version TEXT,
            FOREIGN KEY(reference_id_a) REFERENCES flavor_reference(id) ON DELETE CASCADE,
            FOREIGN KEY(reference_id_b) REFERENCES flavor_reference(id) ON DELETE CASCADE,
            UNIQUE(reference_id_a, reference_id_b)
        );

        CREATE TABLE IF NOT EXISTS recipe_references (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_type TEXT NOT NULL,
            device_type TEXT NOT NULL,
            source_url TEXT,
            source_recipe_id TEXT NOT NULL,
            title TEXT,
            author TEXT,
            total_flavour_pct REAL,
            steep_value REAL,
            steep_unit TEXT,
            is_public INTEGER DEFAULT 1,
            is_adapted INTEGER DEFAULT 0,
            is_single_flavour INTEGER DEFAULT 0,
            profile_raw TEXT,
            profile_normalized TEXT,
            scraped_at DATETIME,
            content_hash TEXT,
            benchmark_eligible INTEGER DEFAULT 0,
            UNIQUE(source, source_type, source_recipe_id)
        );

        CREATE TABLE IF NOT EXISTS recipe_reference_flavours (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_reference_id INTEGER NOT NULL,
            brand_raw TEXT,
            brand_canonical TEXT,
            flavour_name_raw TEXT,
            flavour_name_normalized TEXT,
            percentage REAL,
            position INTEGER,
            FOREIGN KEY(recipe_reference_id) REFERENCES recipe_references(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS recipe_reference_benchmark_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            device_type TEXT NOT NULL,
            profile_normalized TEXT NOT NULL,
            recipe_count INTEGER,
            avg_total_flavour_pct REAL,
            median_total_flavour_pct REAL,
            avg_flavour_count REAL,
            avg_pct_per_flavour REAL,
            sweetener_usage_rate REAL,
            cooling_usage_rate REAL,
            analytics_version TEXT,
            updated_at DATETIME,
            UNIQUE(source_type, device_type, profile_normalized)
        );

        -- TVW community V1 domain tables.
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY,
            display_name TEXT,
            bio TEXT,
            avatar_url TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS recipe_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS recipe_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            recipe_version_id INTEGER,
            author_user_id INTEGER,
            feedback_type TEXT NOT NULL DEFAULT 'structured',
            body TEXT,
            score_overall REAL DEFAULT 0.0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE,
            FOREIGN KEY(recipe_version_id) REFERENCES recipe_versions(id),
            FOREIGN KEY(author_user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS recipe_feedback_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feedback_id INTEGER NOT NULL,
            metric_key TEXT NOT NULL,
            metric_value REAL DEFAULT 0.0,
            FOREIGN KEY(feedback_id) REFERENCES recipe_feedback(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS recipe_status_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            recipe_version_id INTEGER,
            from_status TEXT,
            to_status TEXT NOT NULL,
            changed_by INTEGER,
            reason TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE,
            FOREIGN KEY(recipe_version_id) REFERENCES recipe_versions(id),
            FOREIGN KEY(changed_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS flavours (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_name TEXT NOT NULL,
            brand TEXT,
            category TEXT
        );

        CREATE TABLE IF NOT EXISTS flavour_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flavour_id INTEGER NOT NULL,
            alias_raw TEXT NOT NULL,
            alias_normalized TEXT NOT NULL,
            source TEXT DEFAULT 'sync',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(flavour_id) REFERENCES flavours(id) ON DELETE CASCADE,
            UNIQUE(flavour_id, alias_normalized)
        );

        CREATE TABLE IF NOT EXISTS flavour_reference_meta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flavour_id INTEGER NOT NULL UNIQUE,
            source_name TEXT,
            source_table TEXT,
            source_record_id INTEGER,
            source_url TEXT,
            vc_url TEXT,
            reference_payload_json TEXT,
            last_synced_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(flavour_id) REFERENCES flavours(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS flavour_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flavour_id INTEGER NOT NULL,
            author_user_id INTEGER,
            note TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(flavour_id) REFERENCES flavours(id) ON DELETE CASCADE,
            FOREIGN KEY(author_user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS flavour_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flavour_id INTEGER NOT NULL,
            author_user_id INTEGER,
            rating REAL DEFAULT 0.0,
            review TEXT,
            steep_days INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(flavour_id) REFERENCES flavours(id) ON DELETE CASCADE,
            FOREIGN KEY(author_user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS user_stash_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            flavour_id INTEGER NOT NULL,
            quantity_ml REAL DEFAULT 0.0,
            is_favorite INTEGER DEFAULT 0,
            last_used_at DATETIME,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(flavour_id) REFERENCES flavours(id)
        );

        CREATE TABLE IF NOT EXISTS stash_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stash_item_id INTEGER NOT NULL,
            tx_type TEXT NOT NULL,
            delta_ml REAL NOT NULL,
            source TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(stash_item_id) REFERENCES user_stash_items(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS mix_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            recipe_version_id INTEGER,
            mode TEXT NOT NULL DEFAULT 'quick',
            target_ml REAL DEFAULT 0.0,
            target_nic REAL DEFAULT 0.0,
            target_pgvg TEXT,
            result_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_at DATETIME,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(recipe_version_id) REFERENCES recipe_versions(id)
        );

        CREATE TABLE IF NOT EXISTS mix_session_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mix_session_id INTEGER NOT NULL,
            step_no INTEGER NOT NULL,
            step_type TEXT NOT NULL,
            payload_json TEXT,
            completed_at DATETIME,
            FOREIGN KEY(mix_session_id) REFERENCES mix_sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS tvw_admin_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER NOT NULL,
            actor_role TEXT,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id INTEGER,
            reason TEXT,
            details_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(actor_user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS tvw_content_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_user_id INTEGER NOT NULL,
            target_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            detail TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            reviewed_by_user_id INTEGER,
            reviewed_at DATETIME,
            FOREIGN KEY(reporter_user_id) REFERENCES users(id),
            FOREIGN KEY(reviewed_by_user_id) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_tvw_reports_status ON tvw_content_reports(status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_tvw_reports_target ON tvw_content_reports(target_type, target_id);
        CREATE INDEX IF NOT EXISTS idx_tvw_reports_reporter ON tvw_content_reports(reporter_user_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS recipe_favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            recipe_id INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, recipe_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_recipe_favorites_user ON recipe_favorites(user_id, recipe_id);
        CREATE INDEX IF NOT EXISTS idx_recipe_favorites_recipe ON recipe_favorites(recipe_id);

        CREATE TABLE IF NOT EXISTS recipe_cost_ingredient_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            version_id INTEGER NOT NULL,
            ingredient_id INTEGER NOT NULL,
            catalogue_reference_id INTEGER,
            manual_price_per_ml REAL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(recipe_id, version_id, ingredient_id),
            FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE,
            FOREIGN KEY(version_id) REFERENCES recipe_versions(id) ON DELETE CASCADE,
            FOREIGN KEY(ingredient_id) REFERENCES flavor_reference(id),
            FOREIGN KEY(catalogue_reference_id) REFERENCES flavor_reference(id)
        );
        CREATE INDEX IF NOT EXISTS idx_recipe_cost_overrides_lookup
            ON recipe_cost_ingredient_overrides(recipe_id, version_id, ingredient_id);

        CREATE TABLE IF NOT EXISTS recipe_ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            recipe_id INTEGER NOT NULL,
            rating REAL NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, recipe_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_recipe_ratings_recipe ON recipe_ratings(recipe_id);
        """
    )

    # (table, column, column_sql)
    changes: Iterable[Tuple[str, str, str]] = [
        # audit_logs (older DBs may be missing these)
        ("audit_logs", "target_table", "TEXT"),
        ("audit_logs", "details", "TEXT"),
        ("audit_logs", "qty_change", "REAL DEFAULT 0.0"),
        # recipes
        ("recipes", "active", "INTEGER DEFAULT 1"),
        ("recipes", "created_by", "INTEGER"),
        ("recipes", "status", "TEXT DEFAULT 'Development'"),
        ("recipes", "recipe_code", "TEXT UNIQUE"),
        ("recipes", "image_path", "TEXT"),
        ("recipes", "library_type", "TEXT"),
        ("recipes", "owner_user_id", "INTEGER"),
        ("recipes", "slug", "TEXT"),
        ("recipes", "visibility", "TEXT DEFAULT 'private'"),
        ("recipes", "current_version_id", "INTEGER"),
        ("recipes", "created_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("recipes", "updated_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("recipe_feedback", "recipe_version_id", "INTEGER"),
        ("recipe_feedback", "is_hidden", "INTEGER DEFAULT 0"),
        ("recipe_feedback", "moderation_status", "TEXT DEFAULT 'active'"),
        ("recipe_feedback", "moderation_reason", "TEXT"),
        ("recipe_status_updates", "recipe_version_id", "INTEGER"),
        ("mix_sessions", "completed_at", "DATETIME"),
        # community UGC moderation (TVW admin Phase 3)
        ("flavour_notes", "is_hidden", "INTEGER DEFAULT 0"),
        ("flavour_notes", "moderation_status", "TEXT DEFAULT 'active'"),
        ("flavour_notes", "moderation_reason", "TEXT"),
        ("flavour_reviews", "is_hidden", "INTEGER DEFAULT 0"),
        ("flavour_reviews", "moderation_status", "TEXT DEFAULT 'active'"),
        ("flavour_reviews", "moderation_reason", "TEXT"),
        # flavours
        ("flavours", "normalized_name", "TEXT"),
        ("flavours", "canonical_brand", "TEXT"),
        ("flavours", "profile", "TEXT"),
        ("flavours", "source", "TEXT"),
        ("flavours", "source_flavor_ref_id", "INTEGER"),
        ("flavours", "avg_usage", "REAL"),
        ("flavours", "default_percent", "REAL"),
        ("flavours", "created_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("flavours", "updated_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("flavours", "is_hidden", "INTEGER DEFAULT 0"),
        ("flavours", "is_invalid", "INTEGER DEFAULT 0"),
        ("flavours", "moderation_status", "TEXT DEFAULT 'active'"),
        ("flavours", "moderation_reason", "TEXT"),
        ("flavours", "updated_by_user_id", "INTEGER"),
        ("flavours", "merged_into_flavour_id", "INTEGER REFERENCES flavours(id)"),
        ("flavours", "merged_at", "DATETIME"),
        # users
        ("users", "email", "TEXT"),
        ("users", "created_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("users", "moderation_status", "TEXT DEFAULT 'active'"),
        ("users", "is_suspended", "INTEGER DEFAULT 0"),
        ("users", "is_banned", "INTEGER DEFAULT 0"),
        ("users", "is_muted", "INTEGER DEFAULT 0"),
        ("users", "moderation_note", "TEXT"),
        # recipe moderation
        ("recipes", "is_hidden", "INTEGER DEFAULT 0"),
        ("recipes", "moderation_status", "TEXT DEFAULT 'active'"),
        ("recipes", "moderation_reason", "TEXT"),
        # flavor_reference
        ("flavor_reference", "is_nicotine", "INTEGER DEFAULT 0"),
        ("flavor_reference", "category", "TEXT DEFAULT 'Flavor'"),
        ("flavor_reference", "price_10ml", "REAL DEFAULT 0.0"),
        ("flavor_reference", "price_per_ml", "REAL DEFAULT 0.0"),
        ("flavor_reference", "url", "TEXT"),
        ("flavor_reference", "product_url", "TEXT"),
        ("flavor_reference", "price_raw", "REAL DEFAULT 0.0"),
        ("flavor_reference", "unit_size_ml", "REAL"),
        ("flavor_reference", "usage_notes", "TEXT"),
        ("flavor_reference", "description", "TEXT"),
        ("flavor_reference", "rating", "REAL DEFAULT 0.0"),
        ("flavor_reference", "vc_url", "TEXT"),
        ("flavor_reference", "created_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        # flavor classification foundation
        ("flavor_reference", "profile", "TEXT"),
        ("flavor_reference", "role", "TEXT"),
        ("flavor_reference", "potency", "TEXT"),
        ("flavor_reference", "volatility", "TEXT"),
        ("flavor_reference", "body_weight", "TEXT"),
        ("flavor_reference", "pod_suitability", "TEXT"),
        ("flavor_reference", "resolved_profile", "TEXT"),
        ("flavor_reference", "resolved_role", "TEXT"),
        ("flavor_reference", "resolved_potency", "TEXT"),
        ("flavor_reference", "resolved_volatility", "TEXT"),
        ("flavor_reference", "resolved_body_weight", "TEXT"),
        ("flavor_reference", "resolved_pod_suitability", "TEXT"),
        ("flavor_reference", "keyword_primary", "TEXT"),
        ("flavor_reference", "keyword_secondary", "TEXT"),
        ("flavor_reference", "keyword_modifiers", "TEXT"),
        ("flavor_reference", "sweetness_bias", "INTEGER"),
        ("flavor_reference", "brightness_bias", "INTEGER"),
        ("flavor_reference", "harshness_bias", "INTEGER"),
        ("flavor_reference", "coil_risk", "INTEGER"),
        ("flavor_reference", "confidence", "TEXT"),
        ("flavor_reference", "override_reason", "TEXT"),
        ("flavor_reference", "manual_override", "INTEGER DEFAULT 0"),
        ("flavor_reference", "manual_override_payload", "TEXT"),
        ("flavor_reference", "classification_version", "TEXT"),
        ("flavor_reference", "classification_source", "TEXT"),
        ("flavor_reference", "last_classified_at", "DATETIME"),
        ("flavor_reference", "needs_review", "INTEGER DEFAULT 0"),
        # library_items (legacy DBs may miss these)
        ("library_items", "reference_id", "INTEGER"),
        ("library_items", "active", "INTEGER DEFAULT 1"),
        ("library_items", "unit", "TEXT DEFAULT 'ml'"),
        ("library_items", "low_stock_threshold", "REAL DEFAULT 10.0"),
        ("library_items", "cost_per_unit", "REAL DEFAULT 0.0"),
        ("library_items", "status", "TEXT DEFAULT 'In Use'"),
        ("library_items", "updated_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        # mix_history enhancements (optional columns used by mixing engine)
        ("mix_history", "style", "TEXT"),
        ("mix_history", "ready_for_sale", "INTEGER DEFAULT 0"),
        ("mix_history", "safety_json", "TEXT"),
        ("mix_history", "total_cost", "REAL DEFAULT 0.0"),
        ("mix_history", "vessel_size", "REAL"),
        ("mix_history", "mix_mode", "TEXT"),
        ("mix_history", "source_recipe_mode", "TEXT"),
        ("mix_history", "target_mix_mode", "TEXT"),
        ("mix_history", "formula_origin", "TEXT"),
        ("mix_history", "formula_snapshot_json", "TEXT"),
        ("mix_history", "stock_override_used", "INTEGER DEFAULT 0"),
        ("mix_history", "stock_override_reason", "TEXT"),
        ("mix_history", "stock_override_detail_json", "TEXT"),
        # clients + consignment/sales
        ("clients", "updated_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("finished_goods_stock", "quantity_reserved", "INTEGER DEFAULT 0"),
        ("finished_goods_stock", "quantity_at_clients", "INTEGER DEFAULT 0"),
        ("finished_goods_stock", "default_wholesale_price", "REAL DEFAULT 0.0"),
        ("finished_goods_stock", "default_retail_reference_price", "REAL DEFAULT 0.0"),
        ("client_deliveries", "updated_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("client_delivery_lines", "qty_reported_sold", "INTEGER DEFAULT 0"),
        ("client_delivery_lines", "qty_returned", "INTEGER DEFAULT 0"),
        ("client_delivery_lines", "qty_damaged", "INTEGER DEFAULT 0"),
        ("client_delivery_lines", "qty_remaining_with_client", "INTEGER DEFAULT 0"),
        ("client_delivery_lines", "updated_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("client_deliveries", "has_corrections", "INTEGER DEFAULT 0"),
        # mix_history_items (older DBs may miss this after table existed)
        ("mix_history_items", "delta_grams", "REAL"),
        # recipe reference linkage
        ("recipe_references", "recipe_id", "INTEGER"),
        ("recipe_references", "parse_failed", "INTEGER DEFAULT 0"),
        ("recipe_references", "parse_error", "TEXT"),
        ("recipe_references", "needs_review", "INTEGER DEFAULT 0"),
    ]

    added = []
    for table, col, col_sql in changes:
        try:
            if _add_column_if_missing(table, col, col_sql):
                added.append(f"{table}.{col}")
        except Exception:
            # Avoid breaking startup for legacy DB quirks; caller can inspect results.
            pass

    # Performance indexes for large catalog browsing/search.
    try:
        db.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_flavor_reference_name ON flavor_reference(name);
            CREATE INDEX IF NOT EXISTS idx_flavor_reference_brand ON flavor_reference(brand);
            CREATE INDEX IF NOT EXISTS idx_flavor_reference_category ON flavor_reference(category);
            CREATE INDEX IF NOT EXISTS idx_flavor_reference_brand_name ON flavor_reference(brand, name);
            CREATE INDEX IF NOT EXISTS idx_library_items_reference_active ON library_items(reference_id, active);
            CREATE INDEX IF NOT EXISTS idx_flavor_pairings_a ON flavor_pairings(reference_id_a);
            CREATE INDEX IF NOT EXISTS idx_flavor_pairings_b ON flavor_pairings(reference_id_b);
            CREATE INDEX IF NOT EXISTS idx_flavor_recipe_analytics_ref ON flavor_recipe_analytics(reference_id);
            CREATE INDEX IF NOT EXISTS idx_recipe_references_lookup ON recipe_references(source, source_type, source_recipe_id);
            CREATE INDEX IF NOT EXISTS idx_recipe_references_profile ON recipe_references(source_type, device_type, profile_normalized, benchmark_eligible);
            CREATE INDEX IF NOT EXISTS idx_recipe_reference_flavours_ref ON recipe_reference_flavours(recipe_reference_id);
            CREATE INDEX IF NOT EXISTS idx_clients_active ON clients(is_active, client_type);
            CREATE INDEX IF NOT EXISTS idx_decant_events_batch ON decant_events(source_batch_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_finished_goods_stock_lookup ON finished_goods_stock(location_status, product_name_snapshot, bottle_size_ml);
            CREATE INDEX IF NOT EXISTS idx_client_deliveries_client ON client_deliveries(client_id, delivery_date);
            CREATE INDEX IF NOT EXISTS idx_client_delivery_lines_delivery ON client_delivery_lines(delivery_id);
            CREATE INDEX IF NOT EXISTS idx_client_payments_client ON client_payments(client_id, payment_date);
            CREATE INDEX IF NOT EXISTS idx_payment_allocations_delivery ON client_payment_allocations(delivery_id);
            CREATE INDEX IF NOT EXISTS idx_stock_movements_type_date ON stock_movements(movement_type, movement_date);
            CREATE INDEX IF NOT EXISTS idx_delivery_line_corrections_delivery ON delivery_line_corrections(delivery_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_delivery_line_corrections_line ON delivery_line_corrections(delivery_line_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_recipes_slug ON recipes(slug);
            CREATE INDEX IF NOT EXISTS idx_recipes_visibility_status ON recipes(visibility, status);
            CREATE INDEX IF NOT EXISTS idx_recipe_feedback_recipe ON recipe_feedback(recipe_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_recipe_feedback_moderation ON recipe_feedback(moderation_status, is_hidden);
            CREATE INDEX IF NOT EXISTS idx_flavour_reviews_flavour ON flavour_reviews(flavour_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_flavour_reviews_moderation ON flavour_reviews(moderation_status, is_hidden);
            CREATE INDEX IF NOT EXISTS idx_flavour_notes_moderation ON flavour_notes(moderation_status, is_hidden);
            CREATE INDEX IF NOT EXISTS idx_flavours_brand_name ON flavours(brand, canonical_name);
            CREATE INDEX IF NOT EXISTS idx_flavours_norm_name ON flavours(normalized_name);
            CREATE INDEX IF NOT EXISTS idx_flavours_moderation ON flavours(is_hidden, is_invalid, moderation_status);
            CREATE INDEX IF NOT EXISTS idx_flavours_merged_into ON flavours(merged_into_flavour_id);
            CREATE INDEX IF NOT EXISTS idx_flavour_aliases_norm ON flavour_aliases(alias_normalized);
            CREATE INDEX IF NOT EXISTS idx_user_stash_user ON user_stash_items(user_id, is_favorite);
            CREATE INDEX IF NOT EXISTS idx_mix_sessions_user ON mix_sessions(user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_mix_sessions_user_completed ON mix_sessions(user_id, completed_at);
            CREATE INDEX IF NOT EXISTS idx_tvw_admin_audit_created ON tvw_admin_audit_logs(created_at);
            CREATE INDEX IF NOT EXISTS idx_tvw_admin_audit_actor ON tvw_admin_audit_logs(actor_user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_tvw_reports_status ON tvw_content_reports(status, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_tvw_reports_target ON tvw_content_reports(target_type, target_id);
            CREATE INDEX IF NOT EXISTS idx_tvw_reports_reporter ON tvw_content_reports(reporter_user_id, created_at DESC);
            """
        )
    except Exception:
        pass

    # BLCK Shopify variant pricing (source of truth for pack prices / R/ml).
    try:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS blck_product_variants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL DEFAULT 'BLCK',
                source_product_id TEXT NOT NULL,
                source_variant_id TEXT NOT NULL,
                product_title TEXT,
                variant_title TEXT,
                handle TEXT,
                vendor TEXT,
                product_type TEXT,
                option1 TEXT,
                option2 TEXT,
                option3 TEXT,
                sku TEXT,
                available INTEGER DEFAULT 1,
                pack_volume_ml REAL,
                nicotine_strength_mg_ml REAL,
                carrier TEXT,
                nicotine_type TEXT,
                pack_price REAL NOT NULL DEFAULT 0,
                price_per_ml REAL NOT NULL DEFAULT 0,
                product_url TEXT,
                last_scraped TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source, source_variant_id)
            );

            CREATE TABLE IF NOT EXISTS blck_variant_price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL DEFAULT 'BLCK',
                source_variant_id TEXT NOT NULL,
                pack_price REAL NOT NULL,
                price_per_ml REAL NOT NULL,
                available INTEGER DEFAULT 1,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS costing_canonical_variants (
                role TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'BLCK',
                source_product_id TEXT,
                source_variant_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                unavailable_reason TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_blck_variants_product ON blck_product_variants(source_product_id);
            CREATE INDEX IF NOT EXISTS idx_blck_variants_role_fields ON blck_product_variants(pack_volume_ml, carrier, nicotine_type);
            CREATE INDEX IF NOT EXISTS idx_blck_price_hist_variant ON blck_variant_price_history(source_variant_id, recorded_at);
            """
        )
        added.append("blck_variant_tables")
    except Exception:
        pass

    # Workshop create used recipe_code='' which collides on UNIQUE after the first save.
    # NULL is allowed multiple times under SQLite UNIQUE semantics.
    try:
        cleared = db.execute(
            "UPDATE recipes SET recipe_code = NULL WHERE TRIM(COALESCE(recipe_code, '')) = ''"
        ).rowcount
        if cleared:
            added.append(f"recipes.recipe_code_nullified:{cleared}")
    except Exception:
        pass

    # Lab Production Jobs (named manufacturing projects; distinct from personal guided mixes).
    try:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS production_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                job_type TEXT NOT NULL DEFAULT 'internal',
                status TEXT NOT NULL DEFAULT 'planning',
                reference_label TEXT,
                created_by_user_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                started_at DATETIME,
                completed_at DATETIME,
                notes TEXT,
                FOREIGN KEY(created_by_user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS production_job_recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                production_job_id INTEGER NOT NULL,
                recipe_id INTEGER NOT NULL,
                recipe_version_id INTEGER NOT NULL,
                target_bottle_size_ml REAL NOT NULL,
                target_bottle_count INTEGER NOT NULL,
                target_total_ml REAL NOT NULL,
                completed_ml REAL NOT NULL DEFAULT 0,
                completed_bottles REAL NOT NULL DEFAULT 0,
                in_progress_ml REAL NOT NULL DEFAULT 0,
                mix_target_nic_mg_ml REAL NOT NULL DEFAULT 0,
                mix_pg_ratio REAL NOT NULL DEFAULT 30,
                mix_vg_ratio REAL NOT NULL DEFAULT 70,
                mix_nic_base_mg_ml REAL NOT NULL DEFAULT 100,
                mix_nicotine_carrier TEXT NOT NULL DEFAULT 'PG',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(production_job_id) REFERENCES production_jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(recipe_id) REFERENCES recipes(id),
                FOREIGN KEY(recipe_version_id) REFERENCES recipe_versions(id)
            );

            CREATE TABLE IF NOT EXISTS production_job_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                production_job_id INTEGER NOT NULL,
                production_job_recipe_id INTEGER NOT NULL,
                guided_mix_session_id INTEGER,
                recipe_id INTEGER NOT NULL,
                recipe_version_id INTEGER NOT NULL,
                target_ml REAL NOT NULL,
                actual_ml REAL,
                target_bottles REAL NOT NULL DEFAULT 0,
                completed_bottles REAL NOT NULL DEFAULT 0,
                operator_user_id INTEGER,
                status TEXT NOT NULL DEFAULT 'planned',
                inventory_posted INTEGER NOT NULL DEFAULT 0,
                started_at DATETIME,
                completed_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(production_job_id) REFERENCES production_jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(production_job_recipe_id) REFERENCES production_job_recipes(id) ON DELETE CASCADE,
                FOREIGN KEY(guided_mix_session_id) REFERENCES mix_sessions(id),
                FOREIGN KEY(recipe_id) REFERENCES recipes(id),
                FOREIGN KEY(recipe_version_id) REFERENCES recipe_versions(id),
                FOREIGN KEY(operator_user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS production_job_inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                production_job_id INTEGER NOT NULL,
                ingredient_type TEXT NOT NULL,
                ingredient_id INTEGER,
                display_name TEXT NOT NULL,
                required_ml REAL NOT NULL DEFAULT 0,
                purchased_ml REAL NOT NULL DEFAULT 0,
                used_ml REAL NOT NULL DEFAULT 0,
                adjustment_ml REAL NOT NULL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(production_job_id) REFERENCES production_jobs(id) ON DELETE CASCADE,
                UNIQUE(production_job_id, ingredient_type, ingredient_id)
            );

            CREATE TABLE IF NOT EXISTS production_job_inventory_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                production_job_id INTEGER NOT NULL,
                inventory_id INTEGER NOT NULL,
                quantity_ml REAL NOT NULL,
                note TEXT,
                pack_info TEXT,
                recorded_by_user_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(production_job_id) REFERENCES production_jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(inventory_id) REFERENCES production_job_inventory(id) ON DELETE CASCADE,
                FOREIGN KEY(recorded_by_user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS user_capabilities (
                user_id INTEGER NOT NULL,
                capability TEXT NOT NULL,
                granted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, capability),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_prod_jobs_status ON production_jobs(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_prod_job_recipes_job ON production_job_recipes(production_job_id);
            CREATE INDEX IF NOT EXISTS idx_prod_job_batches_job ON production_job_batches(production_job_id, status);
            CREATE INDEX IF NOT EXISTS idx_prod_job_batches_session ON production_job_batches(guided_mix_session_id);
            CREATE INDEX IF NOT EXISTS idx_prod_job_inv_job ON production_job_inventory(production_job_id);
            """
        )
        added.append("production_job_tables")
    except Exception:
        pass

    for table, column, sql in (
        ("mix_sessions", "production_job_id", "INTEGER"),
        ("mix_sessions", "production_job_recipe_id", "INTEGER"),
    ):
        try:
            if _add_column_if_missing(table, column, sql):
                added.append(f"{table}.{column}")
        except Exception:
            pass

    try:
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_prod_job_batches_session_unique "
            "ON production_job_batches(guided_mix_session_id) "
            "WHERE guided_mix_session_id IS NOT NULL"
        )
    except Exception:
        pass

    # Phase 1 hardening columns on production batches.
    for table, column, sql in (
        ("production_job_batches", "cancelled_at", "DATETIME"),
        ("production_job_batches", "cancelled_by_user_id", "INTEGER"),
        ("production_job_batches", "credit_eligible", "INTEGER NOT NULL DEFAULT 1"),
        ("production_jobs", "revenue_mode", "TEXT NOT NULL DEFAULT 'none'"),
        ("production_jobs", "revenue_amount", "REAL NOT NULL DEFAULT 0"),
        ("production_jobs", "revenue_note", "TEXT"),
        ("production_jobs", "bottle_cost_override", "REAL"),
        ("production_jobs", "label_cost_override", "REAL"),
        ("production_jobs", "referral_label", "TEXT"),
        ("production_jobs", "referral_mode", "TEXT NOT NULL DEFAULT 'none'"),
        ("production_jobs", "referral_value", "REAL NOT NULL DEFAULT 0"),
    ):
        try:
            if _add_column_if_missing(table, column, sql):
                added.append(f"{table}.{column}")
        except Exception:
            pass

    for table, column, sql in (
        ("production_job_inventory_purchases", "pack_count", "REAL"),
        ("production_job_inventory_purchases", "pack_size_ml", "REAL"),
        ("production_job_inventory_purchases", "pack_price", "REAL"),
        ("production_job_inventory_purchases", "total_paid", "REAL NOT NULL DEFAULT 0"),
        ("production_job_inventory_purchases", "source", "TEXT"),
        ("production_job_inventory_purchases", "purchased_at", "DATETIME"),
        ("production_job_inventory", "planned_purchase_ml", "REAL NOT NULL DEFAULT 0"),
    ):
        try:
            if _add_column_if_missing(table, column, sql):
                added.append(f"{table}.{column}")
        except Exception:
            pass

    # Phase 2: production pay + job costs.
    try:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS production_pay_rates (
                bottle_size_ml REAL NOT NULL PRIMARY KEY,
                rate_zar REAL NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS production_job_credits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                production_job_id INTEGER NOT NULL,
                production_job_batch_id INTEGER,
                user_id INTEGER NOT NULL,
                credit_type TEXT NOT NULL DEFAULT 'finished_bottle',
                quantity REAL NOT NULL DEFAULT 0,
                unit TEXT NOT NULL DEFAULT 'bottle',
                rate REAL NOT NULL DEFAULT 0,
                amount REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'earned',
                labour_kind TEXT NOT NULL DEFAULT 'helper',
                credit_eligible INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                paid_at DATETIME,
                notes TEXT,
                FOREIGN KEY(production_job_id) REFERENCES production_jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(production_job_batch_id) REFERENCES production_job_batches(id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS production_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                production_job_id INTEGER,
                amount REAL NOT NULL,
                paid_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                recorded_by_user_id INTEGER,
                note TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(production_job_id) REFERENCES production_jobs(id),
                FOREIGN KEY(recorded_by_user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS production_job_expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                production_job_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                amount REAL NOT NULL,
                expense_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                note TEXT,
                recorded_by_user_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(production_job_id) REFERENCES production_jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(recorded_by_user_id) REFERENCES users(id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_prod_credits_batch_type_user
                ON production_job_credits(production_job_batch_id, credit_type, user_id)
                WHERE production_job_batch_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_prod_credits_user ON production_job_credits(user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_prod_payments_user ON production_payments(user_id, paid_at);
            CREATE INDEX IF NOT EXISTS idx_prod_expenses_job ON production_job_expenses(production_job_id, category);
            """
        )
        added.append("production_pay_finance_tables")
    except Exception:
        pass

    # Default finished-bottle rates (source of truth; configurable).
    try:
        db.execute(
            "INSERT OR IGNORE INTO production_pay_rates (bottle_size_ml, rate_zar) VALUES (120, 5.0)"
        )
        db.execute(
            "INSERT OR IGNORE INTO production_pay_rates (bottle_size_ml, rate_zar) VALUES (30, 3.0)"
        )
        added.append("production_pay_rates_defaults")
    except Exception:
        pass

    # One-time: grant Lab helper Riku produce (+view) without Master Admin / manage.
    try:
        riku = db.execute(
            "SELECT id FROM users WHERE lower(username) = 'riku' LIMIT 1"
        ).fetchone()
        if riku:
            for cap in ("lab.production_jobs.view", "lab.production_jobs.produce"):
                db.execute(
                    "INSERT OR IGNORE INTO user_capabilities (user_id, capability) VALUES (?, ?)",
                    (int(riku["id"]), cap),
                )
            added.append("riku_production_jobs_produce")
    except Exception:
        pass

    db.commit()
    return {"added_columns": added, "count": len(added)}

