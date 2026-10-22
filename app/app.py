import sys

# Windows consoles often default to cp1252; emoji/log prints then crash before the server binds.
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from deploy_env import load_local_env

load_local_env()

print("THE VAPE WORKSHOP CALCULATOR STARTING")

from flask import Flask, render_template, request, redirect, url_for, session, flash
from database_path import (
    data_dir_for_database,
    register_dev_instance_lock,
    resolve_database_path,
)
from db import get_db, close_db, init_app, log_sqlite_startup_diagnostics
from werkzeug.security import generate_password_hash
import os
import sqlite3
import time
import argparse

# Startup migrations (no request-time schema changes)
from migrations import run_startup_migrations

# -----------------------------
# BLUEPRINT IMPORTS
# -----------------------------
from routes.auth import auth_bp
from utils.auth import (
    ROLE_HIERARCHY,
    can_access_lab_bridge,
    can_access_production_jobs,
    can_access_tvw_admin,
    login_required,
)
from routes.library_main import library_bp
from routes.recipes import recipes_bp
from routes.mix import mix_bp
from routes.users import users_bp
from routes.api_lab import api_lab_bp
from routes.inventory import inventory_bp 
from routes.tvw_community import tvw_community_bp
from routes.tvw_api import tvw_api_bp
from routes.tvw_recipes import tvw_recipes_bp
from routes.tvw_admin import tvw_admin_bp

_BOOT_T0 = time.perf_counter()


def _startup_phase(label: str) -> None:
    elapsed = time.perf_counter() - _BOOT_T0
    print(f"[startup +{elapsed:0.2f}s] {label}")


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "off", "no"}


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "tvw_calculator_secret_key_9921")
app.config["RECIPE_UPLOAD_SUBDIR"] = os.path.join("uploads", "recipes")
app.config["RECIPE_UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", app.config["RECIPE_UPLOAD_SUBDIR"])
_startup_phase("config loaded")

# Startup behavior flags (heavy sync defaults OFF)
app.config["RUN_STARTUP_BLCK_SYNC"] = _bool_env("RUN_STARTUP_BLCK_SYNC", False)
app.config["RUN_STARTUP_CLYROLINX_SYNC"] = _bool_env("RUN_STARTUP_CLYROLINX_SYNC", False)
app.config["RUN_STARTUP_VC_SYNC"] = _bool_env("RUN_STARTUP_VC_SYNC", False)
app.config["RUN_STARTUP_IDENTITY_BACKFILL"] = _bool_env("RUN_STARTUP_IDENTITY_BACKFILL", False)
app.config["RUN_STARTUP_MONTHLY_CATCHUP"] = _bool_env("RUN_STARTUP_MONTHLY_CATCHUP", False)
app.config["TVW_ENABLE_LAB_MODULES"] = _bool_env("TVW_ENABLE_LAB_MODULES", False)
master_admin_bp = None
master_admin_svc = None
if bool(app.config["TVW_ENABLE_LAB_MODULES"]):
    from routes.master_admin_console import master_admin_bp
    from services import master_admin_console_service as master_admin_svc

# Ensure upload folder exists (safe on repeated starts).
try:
    os.makedirs(app.config["RECIPE_UPLOAD_FOLDER"], exist_ok=True)
except Exception as e:
    print(f"⚠️ Upload folder init note: {e}")

# -----------------------------
# DATABASE CONFIGURATION
# -----------------------------
# Runtime DB lives in local app data (not OneDrive). Override with DATABASE_PATH.
_resolved_db_path, _db_path_info = resolve_database_path(app.root_path)
app.config["DATABASE"] = _resolved_db_path
app.config["DATABASE_INFO"] = _db_path_info
_instance_lock = register_dev_instance_lock(data_dir_for_database(_resolved_db_path))

# Initialize Database Hooks (teardown, etc.)
init_app(app)
with app.app_context():
    log_sqlite_startup_diagnostics(app, instance_lock=_instance_lock)
_startup_phase("db hooks initialized")

# -----------------------------
# DATABASE INITIALIZATION
# -----------------------------
def init_on_start():
    """Ensures database tables exist and a default admin user is present."""
    with app.app_context():
        db = get_db()
        try:
            # Check for 'users' table specifically to see if we need to run schema.sql
            table_check = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
            ).fetchone()
            
            if not table_check:
                print("📦 Initializing Database Schema...")
                schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
                if os.path.exists(schema_path):
                    with open(schema_path, 'r') as f:
                        db.executescript(f.read())
                    print("✅ Schema loaded successfully.")
                else:
                    print(f"❌ Critical Error: schema.sql not found at {schema_path}")

            # Run idempotent migrations once at startup.
            mig = run_startup_migrations()
            if mig.get("count"):
                print(f"🧱 Startup migrations applied: {mig['count']} columns added.")

            # Ensure a bootstrap admin exists.
            admin_exists = db.execute(
                "SELECT * FROM users WHERE role = ? OR role = ?", ("admin", "Old Goat")
            ).fetchone()

            if not admin_exists:
                admin_password = os.environ.get("TVW_ADMIN_PASSWORD", "").strip()
                if admin_password:
                    hashed_pw = generate_password_hash(admin_password)
                    db.execute("""
                        INSERT INTO users (username, password_hash, role, status)
                        VALUES (?, ?, ?, 'Active')
                    """, ("tvw_admin", hashed_pw, "admin"))
                    db.commit()
                    print("✅ Administrator user created from TVW_ADMIN_PASSWORD.")
                else:
                    print("⚠️ No administrator exists and TVW_ADMIN_PASSWORD is not set; bootstrap admin creation skipped.")

            # Ensure legacy owner login exists for continuity in TVW environments.
            old_goat_exists = db.execute(
                "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                ("Old Goat",),
            ).fetchone()
            if not old_goat_exists:
                old_goat_password = os.environ.get("TVW_OLD_GOAT_PASSWORD", "").strip()
                if old_goat_password:
                    old_goat_hash = generate_password_hash(old_goat_password)
                    db.execute(
                        """
                        INSERT INTO users (username, password_hash, role, status)
                        VALUES (?, ?, ?, 'Active')
                        """,
                        ("Old Goat", old_goat_hash, "Old Goat"),
                    )
                    db.commit()
                    print("✅ Legacy owner user 'Old Goat' created from TVW_OLD_GOAT_PASSWORD.")
                else:
                    print("ℹ️ TVW_OLD_GOAT_PASSWORD is not set; legacy owner bootstrap creation skipped.")

            # Optional startup catch-up is disabled by default to keep boot fast.
            if bool(app.config.get("TVW_ENABLE_LAB_MODULES")) and bool(app.config.get("RUN_STARTUP_MONTHLY_CATCHUP")):
                try:
                    monthly = master_admin_svc.run_monthly_scrapers_if_due()
                    print(f"🗓️ Monthly startup catch-up: {monthly.get('status', 'done')}")
                except Exception as monthly_exc:
                    print(f"⚠️ Monthly startup catch-up failed: {monthly_exc}")
            elif bool(app.config.get("TVW_ENABLE_LAB_MODULES")):
                print("🗓️ Monthly startup catch-up: skipped (RUN_STARTUP_MONTHLY_CATCHUP=false)")
            else:
                print("🗓️ Monthly startup catch-up: disabled (TVW_ENABLE_LAB_MODULES=false)")

            if bool(app.config.get("TVW_ENABLE_LAB_MODULES")) and master_admin_svc is not None:
                try:
                    stale = master_admin_svc.abandon_stale_scraper_runs()
                    if stale.get("abandoned_count"):
                        print(f"🧹 Stale scraper runs marked: {stale.get('abandoned_count')}")
                except Exception as stale_exc:
                    print(f"⚠️ Stale scraper cleanup failed: {stale_exc}")

            # Start recurring scheduler loop (registration only, no immediate heavy dispatch).
            if bool(app.config.get("TVW_ENABLE_LAB_MODULES")):
                try:
                    is_reloader_parent = app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true"
                    if not is_reloader_parent:
                        sched = master_admin_svc.start_scheduler(app, tick_seconds=900, run_on_start=False)
                        print(
                            "🧭 Scheduler state: "
                            f"enabled={sched.get('monthly_job_enabled')} "
                            f"registered={sched.get('monthly_job_registered')} "
                            f"active={sched.get('scheduler_process_active')}"
                        )
                except Exception as sched_exc:
                    print(f"⚠️ Scheduler startup failed: {sched_exc}")
                
        except Exception as e:
            print(f"⚠️ Database Init Note: {e}")
    _startup_phase("db initialized + scheduler registered")


def run_maintenance_tasks(args) -> int:
    """Explicit maintenance mode; never runs automatically during normal startup."""
    requested = []
    if args.sync_blck:
        requested.append("blck")
    if args.sync_clyrolinx:
        requested.append("clyrolinx")
    if args.sync_vc:
        requested.append("vc_dtl_public_resume")
    if args.sync_identity:
        requested.append("vc_flavour_identity_backfill")
    if args.run_maintenance:
        requested = ["blck", "clyrolinx", "vc_flavours", "vc_dtl_public_resume", "vc_flavour_identity_backfill"]
    if not requested:
        return 0
    with app.app_context():
        print(f"🛠️ Maintenance mode starting: {requested}")
        if not bool(app.config.get("TVW_ENABLE_LAB_MODULES")) or master_admin_svc is None:
            print("🛠️ Maintenance mode unavailable when TVW_ENABLE_LAB_MODULES=false")
            return 1
        for name in requested:
            body, status = master_admin_svc.trigger_scraper(name, actor_id=0)
            print(f"   - {name}: status_code={status} status={body.get('status')} message={body.get('message', '')}")
    print("🛠️ Maintenance mode complete")
    return 0

# -----------------------------
# GLOBAL CONTEXT PROCESSOR
# -----------------------------
@app.context_processor
def inject_global_data():
    """
    Injects user session and Role Hierarchy into ALL templates.
    """
    data = {
        "user": None,
        "ROLE_HIERARCHY": ROLE_HIERARCHY,
        "lab_bridge_enabled": bool(app.config.get("TVW_ENABLE_LAB_MODULES")),
        "can_access_lab_bridge": False,
        "can_access_production_jobs": False,
        "can_access_tvw_admin": False,
    }
    if "user_id" in session:
        role = session.get("role")
        data["user"] = {
            "username": session.get("username"),
            "role": role,
            "user_id": session.get("user_id")
        }
        data["can_access_lab_bridge"] = bool(app.config.get("TVW_ENABLE_LAB_MODULES")) and can_access_lab_bridge(role)
        data["can_access_production_jobs"] = bool(app.config.get("TVW_ENABLE_LAB_MODULES")) and can_access_production_jobs(
            role, session.get("user_id")
        )
        data["can_access_tvw_admin"] = can_access_tvw_admin(role)
    return data

# -----------------------------
# DASHBOARD (ROOT ROUTE)
# -----------------------------
@app.route("/lab")
@login_required
def dashboard_index():
    """Main Laboratory Dashboard."""
    if not bool(app.config.get("TVW_ENABLE_LAB_MODULES")):
        return redirect(url_for("tvw_community.community_feed"))
    if not can_access_lab_bridge(session.get("role")):
        flash("Lab access is restricted to admin-level users.", "warning")
        return redirect(url_for("tvw_community.community_feed"))
    db = get_db()
    try:
        # Aggregated Stats for the Lab Overview
        total_items = db.execute("SELECT COUNT(*) FROM flavor_reference WHERE active = 1").fetchone()[0]

        # Source-of-truth low stock count from shopping/low-stock service logic.
        default_thresholds = {"Flavor": 10.0, "Base": 100.0, "Nicotine": 50.0, "Hardware": 1.0}
        low_stock_items = []
        if bool(app.config.get("TVW_ENABLE_LAB_MODULES")) and master_admin_svc is not None:
            low_stock_items = master_admin_svc.render_shopping_list(
                thresholds=default_thresholds,
                default_threshold_ml=float(default_thresholds["Flavor"]),
            )
        low_stock = len(low_stock_items)
        print(f"[dashboard] low_stock_count={low_stock}")
        
        daily_vol = db.execute("""
            SELECT SUM(total_volume_actual) 
            FROM mix_history 
            WHERE date(created_at) = date('now')
        """).fetchone()[0] or 0
        
    except Exception as e:
        print(f"❌ Dashboard Stats Error: {e}")
        total_items = low_stock = daily_vol = 0

    stats = {
        'total_items': total_items,
        'low_stock_count': low_stock,
        'recent_mixes': daily_vol
    }
    return render_template("dashboard.html", stats=stats)

# -----------------------------
# BLUEPRINT REGISTRATION
# -----------------------------
app.register_blueprint(auth_bp, url_prefix='') 
app.register_blueprint(tvw_community_bp)
app.register_blueprint(tvw_recipes_bp)
app.register_blueprint(tvw_api_bp)
app.register_blueprint(tvw_admin_bp)
if bool(app.config.get("TVW_ENABLE_LAB_MODULES")):
    app.register_blueprint(library_bp)
    app.register_blueprint(recipes_bp)
    app.register_blueprint(mix_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(api_lab_bp)
    from routes.production_jobs import production_jobs_bp

    app.register_blueprint(production_jobs_bp)
    if master_admin_bp is not None:
        app.register_blueprint(master_admin_bp)
_startup_phase("routes registered")


@app.errorhandler(sqlite3.OperationalError)
def _api_sqlite_operational_error(exc: sqlite3.OperationalError):
    """Never return HTML 500 for SQLite locks on API endpoints used by guided mix JS."""
    from flask import jsonify, request

    path = str(getattr(request, "path", "") or "")
    if path.startswith("/api/"):
        app.logger.warning("API sqlite error on %s: %s", path, exc)
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Database is busy. Try again in a moment.",
                    "code": "database_locked",
                }
            ),
            503,
        )
    return exc


# Run database setup check before the app starts serving
init_on_start()
print("THE VAPE WORKSHOP CALCULATOR READY")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--sync-blck", action="store_true", help="Run BLCK sync and exit")
    parser.add_argument("--sync-clyrolinx", action="store_true", help="Run Clyrolinx sync and exit")
    parser.add_argument("--sync-vc", action="store_true", help="Run VC public resume sync and exit")
    parser.add_argument("--sync-identity", action="store_true", help="Run imported flavour identity backfill and exit")
    parser.add_argument("--run-maintenance", action="store_true", help="Run all maintenance sync tasks and exit")
    parser.add_argument("--serve-after-maintenance", action="store_true", help="Run server after maintenance tasks")
    parser.add_argument(
        "--host",
        default=os.environ.get("FLASK_RUN_HOST", "127.0.0.1"),
        help="Bind address (use 0.0.0.0 for same-Wi-Fi phone/tablet testing)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("FLASK_RUN_PORT", "5000")),
        help="TCP port (5055 recommended for LAN device testing)",
    )
    args, _unknown = parser.parse_known_args(sys.argv[1:])

    ran = any([args.sync_blck, args.sync_clyrolinx, args.sync_vc, args.sync_identity, args.run_maintenance])
    if ran:
        code = run_maintenance_tasks(args)
        if not args.serve_after_maintenance:
            raise SystemExit(code)

    from dev_server_urls import print_dev_access_urls

    bind_host = str(args.host or "127.0.0.1")
    bind_port = int(args.port)
    if bind_host in {"0.0.0.0", "::"}:
        print_dev_access_urls(host=bind_host, port=bind_port)

    use_reloader = _bool_env("FLASK_USE_RELOADER", False)
    print(
        f"[startup] single-instance mode: use_reloader={use_reloader} "
        f"(set FLASK_USE_RELOADER=1 to enable Werkzeug reloader)"
    )
    app.run(debug=True, host=bind_host, port=bind_port, use_reloader=use_reloader)