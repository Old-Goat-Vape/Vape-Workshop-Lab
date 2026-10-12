"""Capture Recipe Library Pass 1B screenshots and computed sizes."""
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
        "INSERT INTO flavor_reference (id, brand, name, category, density, active) VALUES (101, 'Test', 'Vanilla', 'Flavor', 1.036, 1)"
    )
    recipes = [
        (1, "Malva Pudding", "malva-pudding", "ready", "private", 1, "Dessert"),
        (2, "Crispy Treats", "crispy-treats", "ready", "public", 2, "Bakery"),
        (3, "Sunrise Custard", "sunrise-custard", "wip", "public", 3, "Dessert"),
    ]
    for rid, name, slug, status, vis, vid, tag in recipes:
        db.execute(
            "INSERT INTO recipes (id, name, slug, status, visibility, moderation_status, active, created_by, current_version_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', 1, 1, ?, datetime('now', ?), datetime('now', ?))",
            (rid, name, slug, status, vis, vid, f"-{rid * 7} day", f"-{rid} day"),
        )
        db.execute(
            "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent) VALUES (?, ?, '1', ?)",
            (vid, rid, 5.0 + rid),
        )
        db.execute("INSERT INTO recipe_tags (recipe_id, tag) VALUES (?, ?)", (rid, tag))
    db.commit()


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(f"Playwright required: {exc}") from exc

    from app import app as flask_app

    temp_dir = tempfile.mkdtemp(prefix="tvw_lib_measure_")
    db_path = os.path.join(temp_dir, "measure.db")
    flask_app.config["DATABASE"] = db_path
    flask_app.config["WTF_CSRF_ENABLED"] = False
    with flask_app.app_context():
        _seed(db_path)

    port = _free_port()

    def _serve() -> None:
        flask_app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)

    threading.Thread(target=_serve, daemon=True).start()
    time.sleep(4)

    viewports = [
        ("1920x1080", 1920, 1080),
        ("1366x768", 1366, 768),
        ("390x844", 390, 844),
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
          padding: cs.padding,
        };
      };
      const cards = document.querySelectorAll('.tvw-lib-card');
      const gridCols = cards.length >= 2
        ? getComputedStyle(document.querySelector('.tvw-lib-card-grid')).gridTemplateColumns
        : null;
      return {
        title: pick('.tvw-lib-title'),
        counts: pick('.tvw-lib-counts'),
        searchInput: pick('.tvw-lib-search-input'),
        searchBtn: pick('.tvw-lib-search-btn'),
        scopeTab: pick('.tvw-lib-scope-tab'),
        filtersBtn: pick('.tvw-lib-filters-toggle'),
        card: pick('.tvw-lib-card'),
        cardTitle: pick('.tvw-lib-card-title'),
        cardMeta: pick('.tvw-lib-card-meta'),
        cardSubmeta: pick('.tvw-lib-card-submeta'),
        cardBtn: pick('.tvw-lib-card-actions .btn'),
        favoriteBtn: pick('.tvw-lib-favorite-btn'),
        moreBtn: pick('.tvw-lib-more-menu summary'),
        gridColumns: gridCols,
        cardCount: cards.length,
        overflowX: document.documentElement.scrollWidth > window.innerWidth,
        viewport: { w: window.innerWidth, h: window.innerHeight },
      };
    }
    """

    out_dir = os.path.join(APP_DIR, "static", "previews", "recipe-library-pass1b")
    os.makedirs(out_dir, exist_ok=True)
    report: dict = {"viewports": {}}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for label, w, h in viewports:
            context = browser.new_context(viewport={"width": w, "height": h})
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{port}/login", wait_until="networkidle")
            page.fill('input[name="username"]', "member1")
            page.fill('input[name="password"]', "testpass")
            page.click('button[type="submit"]')
            page.wait_for_url("**/*", timeout=15000)
            page.goto(f"http://127.0.0.1:{port}/recipes", wait_until="networkidle")
            page.wait_for_selector(".tvw-lib-card", timeout=15000)
            shot_path = os.path.join(out_dir, f"recipe-library-{label}.png")
            page.screenshot(path=shot_path, full_page=True)
            metrics = page.evaluate(js)
            report["viewports"][label] = {"screenshot": shot_path, "metrics": metrics}
            context.close()
        browser.close()

    report_path = os.path.join(out_dir, "pass1b-metrics.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))
    print(f"Saved to {out_dir}")


if __name__ == "__main__":
    main()
