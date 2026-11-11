"""Preview duplicates; apply only selected, fingerprinted groups with a mandatory SQLite backup."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.catalogue_cleanup_service import (
    duplicate_plan,
    consolidate_group,
    category_plan,
    apply_category_corrections,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument(
        "--apply-plan",
        help="Previously reviewed JSON preview; default is read-only preview",
    )
    parser.add_argument(
        "--target-id",
        type=int,
        action="append",
        default=[],
        help="Apply only these canonical target IDs",
    )
    parser.add_argument(
        "--category-id",
        type=int,
        action="append",
        default=[],
        help="Apply only these reviewed category corrections",
    )
    parser.add_argument("--actor-id", type=int)
    parser.add_argument(
        "--backup", help="New, non-existing backup file; mandatory when applying"
    )
    args = parser.parse_args()
    path = Path(args.db).resolve(strict=True)
    db = sqlite3.connect(
        path.as_uri() + "?mode=rw" if args.apply_plan else path.as_uri() + "?mode=ro",
        uri=True,
        timeout=60,
    )
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    if not args.apply_plan:
        print(
            json.dumps(
                {
                    "duplicates": duplicate_plan(db),
                    "category_corrections": category_plan(db),
                },
                indent=2,
                default=str,
            )
        )
        db.close()
        return
    if not (args.target_id or args.category_id) or not args.actor_id or not args.backup:
        parser.error(
            "Applying requires selected --target-id or --category-id, --actor-id and --backup"
        )
    if args.target_id and args.category_id:
        parser.error(
            "Apply category corrections first, generate a fresh preview, then consolidate duplicates"
        )
    manifest = json.loads(Path(args.apply_plan).read_text())
    selected = [p for p in manifest["duplicates"] if p["target_id"] in args.target_id]
    categories = [
        p
        for p in manifest["category_corrections"]
        if p["catalogue_id"] in args.category_id
    ]
    if {p["target_id"] for p in selected} != set(args.target_id):
        parser.error("Every target ID must exist in the preview")
    if {p["catalogue_id"] for p in categories} != set(args.category_id):
        parser.error("Every category ID must exist in the preview")
    actor = db.execute("SELECT role FROM users WHERE id=?", (args.actor_id,)).fetchone()
    if not actor or actor["role"] not in {"Master", "Old Goat"}:
        parser.error("Actor must be an existing Master or Old Goat user")
    backup = Path(args.backup).resolve()
    if backup == path:
        parser.error("Backup must be a separate new file")
    # Exclusive creation prevents accidentally replacing the only recovery copy.
    with backup.open("xb"):
        pass
    with sqlite3.connect(backup) as backup_db:
        db.backup(backup_db)
    try:
        db.execute("BEGIN IMMEDIATE")
        results = [
            consolidate_group(
                db, p, actor_user_id=args.actor_id, actor_role=actor["role"]
            )
            for p in selected
        ]
        corrected = apply_category_corrections(
            db, categories, actor_user_id=args.actor_id, actor_role=actor["role"]
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print(
        json.dumps(
            {
                "backup": str(backup),
                "consolidated": results,
                "category_corrections": corrected,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
