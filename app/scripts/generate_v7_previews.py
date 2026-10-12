"""Generate guided V7 preview HTML snapshots."""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app as flask_app
from db import get_db
from migrations import run_startup_migrations
from services.guided_mix_session_service import create_guided_mix_session


def main() -> None:
    preview_dir = os.path.join(os.path.dirname(__file__), "..", "static", "previews")
    os.makedirs(preview_dir, exist_ok=True)

    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "preview.db")
    flask_app.config["TESTING"] = True
    flask_app.config["DATABASE"] = db_path
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        db = get_db()
        schema = os.path.join(os.path.dirname(__file__), "..", "schema.sql")
        with open(schema, "r", encoding="utf-8") as f:
            db.executescript(f.read())
        run_startup_migrations()
        db.execute(
            "INSERT INTO users (id, username, password_hash, role, status) "
            "VALUES (1, 'member1', 'x', 'member', 'Active')"
        )
        db.execute(
            "INSERT INTO flavor_reference (id, brand, name, category, density, active) "
            "VALUES (101, 'Test', 'Vanilla', 'Flavor', 1.036, 1)"
        )
        db.execute(
            "INSERT INTO recipes (id, name, slug, status, visibility, moderation_status, "
            "active, created_by, current_version_id) "
            "VALUES (1, 'Bakery Test', 'bakery-test', 'WIP', 'public', 'active', 1, 1, 1)"
        )
        db.execute(
            "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent) "
            "VALUES (1, 1, '1', 5.0)"
        )
        db.execute(
            "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (1, 101, 5.0)"
        )
        db.execute(
            "INSERT INTO flavours (id, canonical_name, brand, category, source_flavor_ref_id) "
            "VALUES (1, 'Vanilla', 'Test', 'Flavor', 101)"
        )
        db.commit()
        sid, _, _ = create_guided_mix_session(
            user_id=1,
            recipe_id=1,
            requested_version_id=1,
            target_ml=100,
            target_nic_mg_ml=3,
            nic_base_mg_ml=100,
            pg_ratio=30,
            vg_ratio=70,
            nicotine_carrier="PG",
        )

    client = flask_app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "member1"
        sess["role"] = "member"

    html = client.get(f"/guided-mix/session/{sid}").data.decode("utf-8")
    with open(os.path.join(preview_dir, "guided_v7_prep.html"), "w", encoding="utf-8") as f:
        f.write(html)

    client.post(
        f"/api/mix/guided/session/{sid}/steps/1/complete",
        json={"undo": False, "measured_grams": ""},
    )
    html = client.get(f"/guided-mix/session/{sid}").data.decode("utf-8")
    with open(os.path.join(preview_dir, "guided_v7_nicotine_tare.html"), "w", encoding="utf-8") as f:
        f.write(html)

    session = client.get(f"/api/mix/guided/session/{sid}").get_json()["session"]
    tare = next(s for s in session["steps"] if not s.get("completed_at"))
    client.post(
        f"/api/mix/guided/session/{sid}/steps/{tare['step_no']}/complete",
        json={"undo": False, "measured_grams": ""},
    )
    html = client.get(f"/guided-mix/session/{sid}").data.decode("utf-8")
    with open(os.path.join(preview_dir, "guided_v7_nicotine_pour.html"), "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Wrote previews to {os.path.abspath(preview_dir)}")


if __name__ == "__main__":
    main()
