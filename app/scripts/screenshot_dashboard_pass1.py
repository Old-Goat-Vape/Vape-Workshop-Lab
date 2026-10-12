"""Capture dashboard Pass 1 screenshots at required viewports."""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time

from playwright.sync_api import sync_playwright
from werkzeug.security import generate_password_hash

APP_DIR = os.path.join(os.path.dirname(__file__), "..")
OUT_DIR = os.path.join(APP_DIR, "static", "previews", "dashboard-pass1")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
VIEWPORTS = [
    ("1920x1080", 1920, 1080),
    ("1366x768", 1366, 768),
    ("390x844", 390, 844),
]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _seed_test_db(db_path: str) -> None:
    from db import get_db
    from migrations import run_startup_migrations

    schema_path = os.path.join(APP_DIR, "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        get_db().executescript(f.read())
    run_startup_migrations()
    db = get_db()
    db.execute(
        "INSERT INTO users (id, username, password_hash, role, status) VALUES (1, 'member1', ?, 'member', 'Active')",
        (generate_password_hash("testpass"),),
    )
    db.execute(
        "INSERT INTO flavor_reference (id, brand, name, category, density, active) VALUES (101, 'Test', 'Vanilla', 'Flavor', 1.036, 1)"
    )
    db.execute(
        "INSERT INTO recipes (id, name, slug, status, visibility, moderation_status, active, created_by, current_version_id) "
        "VALUES (1, 'Sunrise Custard', 'sunrise-custard', 'ready', 'public', 'active', 1, 1, 1)"
    )
    db.execute(
        "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent) VALUES (1, 1, '1.0', 8.0)"
    )
    db.execute(
        "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (1, 101, 8.0)"
    )
    db.execute(
        "INSERT INTO flavours (id, canonical_name, brand, category, source_flavor_ref_id) VALUES (1, 'Vanilla', 'Test', 'Flavor', 101)"
    )
    db.commit()


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    temp_dir = tempfile.TemporaryDirectory()
    db_path = os.path.join(temp_dir.name, "dashboard_preview.db")

    from app import app as flask_app

    flask_app.config["TESTING"] = False
    flask_app.config["DATABASE"] = db_path
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _seed_test_db(db_path)

    port = _free_port()

    def _serve() -> None:
        flask_app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    time.sleep(2.5)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{port}/login", wait_until="networkidle")
        page.fill('input[name="username"]', "member1")
        page.fill('input[name="password"]', "testpass")
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state("networkidle")
        page.goto(f"http://127.0.0.1:{port}/community", wait_until="networkidle")
        page.wait_for_timeout(500)

        for label, width, height in VIEWPORTS:
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(300)
            out = os.path.join(OUT_DIR, f"community-home-{label}.png")
            page.screenshot(path=out, full_page=True)
            print(f"Wrote {out}")

        browser.close()

    temp_dir.cleanup()


if __name__ == "__main__":
    main()
