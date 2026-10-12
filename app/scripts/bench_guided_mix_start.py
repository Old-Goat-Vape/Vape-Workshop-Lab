"""Benchmark recipe-detail Start guided mix → bench GET (before/after perf pass)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager

# Run from app/ with PYTHONPATH=.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _seed_12_flavour_recipe(db) -> None:
    db.execute(
        "INSERT INTO users (id, username, password_hash, role, status) VALUES (1, 'member1', 'x', 'member', 'Active')"
    )
    flavours = [
        ("FLV", "Apricot"),
        ("TFA", "Brown Sugar"),
        ("FLV", "Brulee"),
        ("CAP", "Vanilla Custard"),
        ("TFA", "Dragonfruit"),
        ("FA", "Cookie"),
        ("INW", "Biscuit"),
        ("CAP", "Super Sweet"),
        ("TFA", "Acetyl Pyrazine"),
        ("FLV", "Cream"),
        ("CAP", "Sugar Cookie"),
        ("TFA", "Marshmallow"),
    ]
    for i, (brand, name) in enumerate(flavours, start=101):
        db.execute(
            "INSERT INTO flavor_reference (id, brand, name, category, density, active) VALUES (?, ?, ?, 'Flavor', 1.036, 1)",
            (i, brand, name),
        )
        db.execute(
            "INSERT INTO flavours (id, canonical_name, brand, category, source_flavor_ref_id) VALUES (?, ?, ?, 'Flavor', ?)",
            (i - 100, name, brand, i),
        )
    db.execute(
        "INSERT INTO recipes (id, name, status, visibility, moderation_status, active, created_by, slug, current_version_id) "
        "VALUES (1, 'Malva Bench', 'WIP', 'public', 'active', 1, 1, 'malva-bench', 1)"
    )
    db.execute(
        "INSERT INTO recipe_versions (id, recipe_id, version_number, total_flavor_percent, target_vg_pg_ratio) "
        "VALUES (1, 1, '1.0', 9.35, '70/30')"
    )
    pcts = [0.30, 1.00, 0.75, 1.50, 0.50, 0.80, 0.60, 0.40, 0.20, 1.00, 1.50, 0.80]
    for i, pct in enumerate(pcts):
        db.execute(
            "INSERT INTO recipe_items (version_id, ingredient_id, amount_percent) VALUES (1, ?, ?)",
            (101 + i, pct),
        )
    db.commit()


@contextmanager
def _sql_counter(db):
    counts = {"n": 0, "statements": []}

    def tracer(statement: str) -> None:
        counts["n"] += 1
        counts["statements"].append(str(statement).strip().split("\n")[0][:120])

    db.set_trace_callback(tracer)
    try:
        yield counts
    finally:
        db.set_trace_callback(None)


def main() -> None:
    os.environ["TVW_GUIDED_MIX_PERF"] = "1"
    from app import app as flask_app
    from db import get_db
    from migrations import run_startup_migrations
    from services import guided_mix_session_service as gms

    temp = tempfile.TemporaryDirectory()
    db_path = os.path.join(temp.name, "bench.db")
    flask_app.config["TESTING"] = True
    flask_app.config["DATABASE"] = db_path
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        db = get_db()
        schema_path = os.path.join(os.path.dirname(__file__), "..", "schema.sql")
        with open(schema_path, "r", encoding="utf-8") as f:
            db.executescript(f.read())
        run_startup_migrations()
        _seed_12_flavour_recipe(db)

    client = flask_app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "member1"
        sess["role"] = "member"

    form = {
        "version_id": "1",
        "target_ml": "30",
        "target_nic_mg_ml": "3",
        "pg_ratio": "30",
        "vg_ratio": "70",
        "nic_base_mg_ml": "100",
        "nicotine_carrier": "PG",
    }

    # Warm-up import path
    client.get("/recipes/malva-bench")

    # --- create only (service) ---
    with flask_app.app_context():
        db = get_db()
        with _sql_counter(db) as create_sql:
            t0 = time.perf_counter()
            sid, vid, steps = gms.create_guided_mix_session(
                user_id=1,
                recipe_id=1,
                requested_version_id=1,
                target_ml=30,
                target_nic_mg_ml=3,
                nic_base_mg_ml=100,
                pg_ratio=30,
                vg_ratio=70,
                nicotine_carrier="PG",
            )
            create_ms = (time.perf_counter() - t0) * 1000
        row = db.execute("SELECT result_json FROM mix_sessions WHERE id = ?", (sid,)).fetchone()
        result_bytes = len((row["result_json"] or "").encode("utf-8")) if row else 0
        step_n = db.execute(
            "SELECT COUNT(1) AS c FROM mix_session_steps WHERE mix_session_id = ?", (sid,)
        ).fetchone()["c"]

    print("=== SERVICE create_guided_mix_session ===")
    print(f"create_ms={create_ms:.1f}")
    print(f"steps={len(steps)} step_rows={step_n}")
    print(f"result_json_bytes={result_bytes}")
    print(f"sql_count={create_sql['n']}")
    for s in create_sql["statements"]:
        print(f"  SQL: {s}")

    # --- bundle GET ---
    with flask_app.app_context():
        db = get_db()
        with _sql_counter(db) as bundle_sql:
            t0 = time.perf_counter()
            bundle = gms.get_guided_session_bundle(session_id=sid, user_id=1, for_client=True)
            bundle_ms = (time.perf_counter() - t0) * 1000
        has_summary = bool(bundle and bundle.get("mix_summary"))
        has_raw = False
        if bundle and isinstance(bundle.get("mix_summary"), dict):
            has_raw = "raw_event_log" in bundle["mix_summary"]
        payload_bytes = len(json.dumps(bundle, default=str).encode("utf-8")) if bundle else 0

    print("=== SERVICE get_guided_session_bundle (new session) ===")
    print(f"bundle_ms={bundle_ms:.1f}")
    print(f"payload_bytes={payload_bytes}")
    print(f"mix_summary={has_summary} raw_event_log={has_raw}")
    print(f"sql_count={bundle_sql['n']}")

    # --- full HTTP POST → redirect → GET ---
    req_id = uuid.uuid4().hex[:8]
    t0 = time.perf_counter()
    post = client.post("/recipes/malva-bench/guided-mix/start", data=form, follow_redirects=False)
    post_ms = (time.perf_counter() - t0) * 1000
    loc = post.headers.get("Location", "")
    t1 = time.perf_counter()
    get = client.get(loc)
    get_ms = (time.perf_counter() - t1) * 1000
    total_ms = (time.perf_counter() - t0) * 1000

    print("=== HTTP POST→redirect→GET ===")
    print(f"request={req_id}")
    print(f"post_status={post.status_code} post_ms={post_ms:.1f}")
    print(f"location={loc}")
    print(f"get_status={get.status_code} get_ms={get_ms:.1f} body_bytes={len(get.data)}")
    print(f"total_transition_ms={total_ms:.1f}")
    print(f"setup_page_leaked={b'Start guided session' in get.data}")

    temp.cleanup()


if __name__ == "__main__":
    main()
