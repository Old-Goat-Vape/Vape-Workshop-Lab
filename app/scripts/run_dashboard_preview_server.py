"""Temporary preview server with seeded login (member1 / testpass)."""
from __future__ import annotations

import os
import sys
import tempfile

APP_DIR = os.path.join(os.path.dirname(__file__), "..")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from werkzeug.security import generate_password_hash

from app import app as flask_app
from db import get_db
from migrations import run_startup_migrations


def _seed(db_path: str) -> None:
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
    temp_dir = tempfile.mkdtemp(prefix="tvw_dash_preview_")
    db_path = os.path.join(temp_dir, "preview.db")
    flask_app.config["DATABASE"] = db_path
    flask_app.config["WTF_CSRF_ENABLED"] = False
    with flask_app.app_context():
        _seed(db_path)
    print(f"Preview DB: {db_path}")
    print("Login: member1 / testpass")
    flask_app.run(host="127.0.0.1", port=5180, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
