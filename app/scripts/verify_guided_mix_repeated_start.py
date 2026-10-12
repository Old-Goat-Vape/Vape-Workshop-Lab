"""Verify 10 consecutive guided-mix starts against the live local database."""

from __future__ import annotations

import os
import sys
import time

APP_ROOT = os.path.dirname(os.path.dirname(__file__))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

os.environ.setdefault("FLASK_USE_RELOADER", "0")
os.environ.setdefault("TESTING", "0")

from app import app as flask_app  # noqa: E402


def _find_malva_recipe() -> tuple[str, int] | None:
    from db import get_db

    db = get_db()
    row = db.execute(
        """
        SELECT id, slug FROM recipes
        WHERE LOWER(name) LIKE '%malva%'
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    route_key = str(row["slug"] or row["id"])
    return route_key, int(row["id"])


def _login_user(client, user_id: int = 1) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["username"] = "Old Goat"
        sess["role"] = "Old Goat"


def main() -> int:
    client = flask_app.test_client()
    with flask_app.app_context():
        found = _find_malva_recipe()
        if not found:
            print("ERROR: No Malva recipe found in database.")
            return 1
        route_key, recipe_id = found
        from db import get_db

        user = get_db().execute(
            "SELECT id, username, role FROM users WHERE role IN ('Old Goat', 'admin') ORDER BY CASE role WHEN 'Old Goat' THEN 0 ELSE 1 END, id LIMIT 1"
        ).fetchone()
        if not user:
            print("ERROR: No suitable user found.")
            return 1
        _login_user(client, int(user["id"]))
        detail = client.get(f"/recipes/{route_key}")
        if detail.status_code != 200:
            print(f"ERROR: recipe detail {detail.status_code} for /recipes/{route_key}")
            return 1
        version_row = get_db().execute(
            "SELECT id FROM recipe_versions WHERE recipe_id = ? ORDER BY id DESC LIMIT 1",
            (recipe_id,),
        ).fetchone()
        version_id = str(version_row["id"]) if version_row else "1"
        form = {
            "version_id": version_id,
            "target_ml": "60",
            "target_nic_mg_ml": "3",
            "pg_ratio": "30",
            "vg_ratio": "70",
            "nic_base_mg_ml": "100",
            "nicotine_carrier": "PG",
        }
        print(f"Database: {flask_app.config.get('DATABASE')}")
        print(f"Recipe: Malva Pudding /recipes/{route_key} (user {user['username']})")
        failures = 0
        timings: list[float] = []
        for i in range(10):
            t0 = time.perf_counter()
            post = client.post(f"/recipes/{route_key}/guided-mix/start", data=form, follow_redirects=False)
            post_ms = (time.perf_counter() - t0) * 1000
            if post.status_code != 302:
                print(f"  {i + 1}: FAIL post={post.status_code} ({post_ms:.0f}ms)")
                failures += 1
                continue
            loc = post.headers.get("Location", "")
            t1 = time.perf_counter()
            get = client.get(loc)
            get_ms = (time.perf_counter() - t1) * 1000
            total_ms = (time.perf_counter() - t0) * 1000
            busy = b"The database was busy while starting your mix" in (get.data or b"")
            if get.status_code != 200 or busy:
                print(f"  {i + 1}: FAIL get={get.status_code} busy={busy} ({total_ms:.0f}ms)")
                failures += 1
            else:
                print(f"  {i + 1}: OK post={post_ms:.0f}ms get={get_ms:.0f}ms total={total_ms:.0f}ms")
                timings.append(total_ms)
        if timings:
            print(f"Average total: {sum(timings) / len(timings):.0f}ms")
        print(f"Result: {10 - failures}/10 succeeded")
        return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
