"""Measure dashboard computed layout at key viewports (Pass 2B verification)."""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time

APP_DIR = os.path.join(os.path.dirname(__file__), "..")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from werkzeug.security import generate_password_hash


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _seed(db_path: str) -> None:
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
        "INSERT INTO recipes (id, name, slug, status, visibility, moderation_status, active, created_by, current_version_id, created_at, updated_at) "
        "VALUES (1, 'Sunrise Custard', 'sunrise-custard', 'ready', 'public', 'active', 1, 1, 1, datetime('now'), datetime('now'))"
    )
    db.execute(
        "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent, notes) "
        "VALUES (1, 1, '1', 12.5, 'A smooth vanilla custard with a bright citrus top note.')"
    )
    db.execute(
        "INSERT INTO recipes (id, name, slug, status, visibility, moderation_status, active, created_by, current_version_id, created_at, updated_at) "
        "VALUES (2, 'Midnight Berry', 'midnight-berry', 'ready', 'public', 'active', 1, 1, 2, datetime('now', '-3 day'), datetime('now', '-1 day'))"
    )
    db.execute(
        "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent) VALUES (2, 2, '2', 9.0)"
    )
    db.execute(
        "INSERT INTO recipes (id, name, slug, status, visibility, moderation_status, active, created_by, current_version_id, created_at, updated_at) "
        "VALUES (3, 'Desert Mirage', 'desert-mirage', 'WIP', 'public', 'active', 1, 1, 3, datetime('now', '-10 day'), datetime('now', '-2 day'))"
    )
    db.execute(
        "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent) VALUES (3, 3, '1', 7.5)"
    )
    db.execute(
        "INSERT INTO recipe_tags (recipe_id, tag) VALUES (1, 'Dessert'), (2, 'Fruit'), (3, 'Tobacco')"
    )
    db.execute(
        "INSERT INTO recipe_ratings (recipe_id, user_id, rating) VALUES (2, 1, 5), (3, 1, 4)"
    )
    db.execute(
        "INSERT INTO recipe_favorites (recipe_id, user_id) VALUES (2, 1), (3, 1)"
    )
    db.commit()


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(f"Playwright required: {exc}") from exc

    from app import app as flask_app

    temp_dir = tempfile.mkdtemp(prefix="tvw_measure_")
    db_path = os.path.join(temp_dir, "measure.db")
    flask_app.config["DATABASE"] = db_path
    flask_app.config["WTF_CSRF_ENABLED"] = False
    with flask_app.app_context():
        _seed(db_path)

    port = _free_port()

    def _serve() -> None:
        flask_app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)

    threading.Thread(target=_serve, daemon=True).start()
    time.sleep(3)

    viewports = [
        ("1920x1080", 1920, 1080, 1),
        ("1366x768", 1366, 768, 1),
        ("1024x768", 1024, 768, 1),
        ("390x844", 390, 844, 1),
        ("1366x768@125", 1366, 768, 1.25),
        ("1366x768@150", 1366, 768, 1.5),
    ]

    js = """
    () => {
      const pick = (sel) => {
        const el = document.querySelector(sel);
        if (!el) return null;
        const cs = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return {
          fontSize: cs.fontSize,
          width: Math.round(r.width),
          height: Math.round(r.height),
        };
      };
      return {
        shell: pick('.tvw-shell'),
        dashboard: pick('.tvw-dashboard-shell'),
        actionGrid: pick('.tvw-action-grid'),
        brand: pick('.tvw-brand'),
        nav: pick('.tvw-nav-link'),
        title: pick('.tvw-dashboard-title'),
        welcome: pick('.tvw-dashboard-welcome'),
        primaryCard: pick('.tvw-action-card--mix'),
        secondaryCard: pick('.tvw-action-card--slot-top'),
        bottomCard: pick('.tvw-action-card--slot-bottom'),
        cardTitle: pick('.tvw-action-card--mix .tvw-action-card-title'),
        cardBody: pick('.tvw-card-tagline'),
        mixIllus: pick('.tvw-illus--mix'),
        createIllus: pick('.tvw-illus--create'),
        primaryBtn: pick('.tvw-action-card-btn--primary'),
        secondaryBtn: pick('.tvw-action-card--slot-top .tvw-action-card-btn'),
        discoveryTitle: pick('.tvw-discover-title'),
        discoverFeatured: pick('.tvw-discover-featured'),
        discoverTab: pick('.tvw-discover-tab'),
        discoverCard: pick('.tvw-discover-card'),
        recipeTitle: pick('.tvw-discover-card-title'),
        actionAreaBottom: (() => {
          const grid = document.querySelector('.tvw-action-grid');
          return grid ? Math.round(grid.getBoundingClientRect().bottom) : null;
        })(),
        viewport: { w: window.innerWidth, h: window.innerHeight, zoom: Math.round((window.devicePixelRatio || 1) * 100) / 100 },
      };
    }
    """

    out_dir = os.path.join(APP_DIR, "static", "previews", "dashboard-pass3")
    os.makedirs(out_dir, exist_ok=True)
    report: dict = {"viewports": {}}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{port}/login", wait_until="networkidle")
        page.fill('input[name="username"]', "member1")
        page.fill('input[name="password"]', "testpass")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        for label, w, h, zoom in viewports:
            page.set_viewport_size({"width": w, "height": h})
            if zoom != 1:
                page.evaluate(f"document.body.style.zoom = '{zoom}'")
            else:
                page.evaluate("document.body.style.zoom = '1'")
            page.goto(f"http://127.0.0.1:{port}/community", wait_until="networkidle")
            page.wait_for_timeout(400)
            metrics = page.evaluate(js)
            report["viewports"][label] = metrics
            shot = os.path.join(out_dir, f"community-{label.replace('@', '-')}.png")
            page.screenshot(path=shot, full_page=True)
            print(f"Wrote {shot}")
            print(json.dumps({label: metrics}, indent=2))

        browser.close()

    report_path = os.path.join(out_dir, "layout-metrics.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"Metrics: {report_path}")


if __name__ == "__main__":
    main()
