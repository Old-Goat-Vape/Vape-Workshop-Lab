"""Reviewable exact-identity consolidation. No fuzzy matches or historical mix rewrites."""

import hashlib
import json
from collections import defaultdict

from config.catalogue_identity import canonical_item
from config.canonical_library_brands import normalize_flavor_identity_name
from models.tvw_admin_repo import (
    _merge_user_stash_flavours,
    _repoint_recipe_items_merge_dup_versions,
)
from config.reference_category_infer import merge_category_bucket
from config.catalogue_product_policy import product_kind


def category_plan(db):
    """Suggestions require explicit review, including corrections of old false hardware labels."""
    result = []
    for row in db.execute(
        "SELECT * FROM flavours WHERE COALESCE(merged_into_flavour_id,0)=0 "
        "AND COALESCE(is_hidden,0)=0 AND COALESCE(is_invalid,0)=0 ORDER BY id"
    ):
        item = dict(row)
        ref = dict(
            db.execute(
                "SELECT * FROM flavor_reference WHERE id=?",
                (item["source_flavor_ref_id"],),
            ).fetchone()
            or {}
        )
        proposed = merge_category_bucket(
            item["brand"],
            item["canonical_name"],
            item["category"],
            ref.get("is_nicotine", 0),
        )
        ref_proposed = (
            merge_category_bucket(
                ref.get("brand", ""),
                ref.get("name", ""),
                ref.get("category"),
                ref.get("is_nicotine", 0),
            )
            if ref
            else proposed
        )
        if proposed == item["category"] and (
            not ref or ref.get("category") == proposed
        ):
            continue
        issues = []
        kind = product_kind(item["brand"], item["canonical_name"])
        if kind in {
            "Pending Verification",
            "Powder Additive",
            "Other Chemical",
            "Fragrance",
            "Finished Liquid",
        }:
            issues.append(
                f"Excluded from recipe choices: {kind}; do not infer a mixing ingredient category"
            )
        if (
            ref
            and canonical_item(item["brand"], item["canonical_name"], "Flavor")[2]
            != canonical_item(ref.get("brand"), ref.get("name"), "Flavor")[2]
        ):
            issues.append(
                "Catalogue and linked ingredient names or manufacturers disagree; review their identity first"
            )
        if ref_proposed != proposed:
            issues.append(
                "Catalogue and linked ingredient disagree; review their identity first"
            )
        if (
            ref
            and db.execute(
                "SELECT 1 FROM mix_sessions ms JOIN recipe_items ri ON ri.version_id=ms.recipe_version_id "
                "WHERE ms.completed_at IS NULL AND ms.mode='guided' AND ri.ingredient_id=? LIMIT 1",
                (ref["id"],),
            ).fetchone()
        ):
            issues.append("Linked ingredient is used by an unfinished guided mix")
        payload = {
            "catalogue_id": item["id"],
            "brand": item["brand"],
            "name": item["canonical_name"],
            "proposed_category": proposed,
            "product_kind": kind,
            "before": item,
            "reference": ref,
            "issues": issues,
        }
        payload["fingerprint"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()
        result.append(payload)
    return result


def apply_category_correction(db, plan, *, actor_user_id, actor_role):
    return apply_category_corrections(
        db, [plan], actor_user_id=actor_user_id, actor_role=actor_role
    )[0]


def apply_category_corrections(db, plans, *, actor_user_id, actor_role):
    """Validate every selection once before writing; caller holds the write transaction.

    Shared ingredient links with the same correction are supported. Conflicting corrections,
    stale previews and blocked selections reject the whole batch before its first mutation.
    """
    if not plans:
        return []
    current_by_id = {p["catalogue_id"]: p for p in category_plan(db)}
    validated = []
    selected_ids = set()
    reference_targets = {}
    for plan in plans:
        current = current_by_id.get(plan["catalogue_id"])
        if not current or current["fingerprint"] != plan["fingerprint"]:
            raise ValueError(
                "Catalogue category changed since preview; generate a fresh plan."
            )
        if current["issues"]:
            raise ValueError("; ".join(current["issues"]))
        if current["catalogue_id"] in selected_ids:
            raise ValueError("A catalogue correction was selected more than once.")
        selected_ids.add(current["catalogue_id"])
        if current["reference"]:
            ref_id = current["reference"]["id"]
            previous = reference_targets.setdefault(
                ref_id, current["proposed_category"]
            )
            if previous != current["proposed_category"]:
                raise ValueError(
                    "Selected corrections disagree about a shared ingredient category."
                )
        validated.append(current)
    return [
        _apply_validated_category_correction(
            db, current, actor_user_id=actor_user_id, actor_role=actor_role
        )
        for current in validated
    ]


def _apply_validated_category_correction(db, current, *, actor_user_id, actor_role):
    db.execute(
        "UPDATE flavours SET category=? WHERE id=?",
        (current["proposed_category"], current["catalogue_id"]),
    )
    if current["reference"]:
        db.execute(
            "UPDATE flavor_reference SET category=?,is_nicotine=? WHERE id=?",
            (
                current["proposed_category"],
                int(current["proposed_category"] == "Nicotine"),
                current["reference"]["id"],
            ),
        )
    db.execute(
        "INSERT INTO tvw_admin_audit_logs(actor_user_id,actor_role,action,target_type,target_id,reason,details_json) "
        "VALUES(?,?,'catalogue_category_corrected','flavour',?,'Reviewed category correction',?)",
        (
            actor_user_id,
            actor_role,
            current["catalogue_id"],
            json.dumps(current, default=str),
        ),
    )
    return {
        "catalogue_id": current["catalogue_id"],
        "category": current["proposed_category"],
    }


def duplicate_plan(db):
    groups = defaultdict(list)
    for row in db.execute(
        "SELECT * FROM flavours WHERE COALESCE(merged_into_flavour_id,0)=0 "
        "AND COALESCE(is_invalid,0)=0 AND COALESCE(is_hidden,0)=0 "
        "AND LOWER(COALESCE(moderation_status,'active'))='active' ORDER BY id"
    ):
        item = dict(row)
        brand, name, key = canonical_item(
            item["brand"], item["canonical_name"], item["category"]
        )
        if key is not None and json.loads(key)[0] == "Flavor":
            item["reference"] = dict(
                db.execute(
                    "SELECT * FROM flavor_reference WHERE id=?",
                    (item["source_flavor_ref_id"],),
                ).fetchone()
                or {}
            )
            groups[key].append(item)
    result = []
    for key, rows in sorted(groups.items()):
        if len(rows) < 2:
            continue
        rows.sort(
            key=lambda r: (
                -int(float(r["reference"].get("price_per_ml") or 0) > 0),
                r["id"],
            )
        )
        brand, name, _ = canonical_item(
            rows[0]["brand"], rows[0]["canonical_name"], rows[0]["category"]
        )
        issues = []
        refs = {r["source_flavor_ref_id"] for r in rows}
        for row in rows:
            ref = row["reference"]
            if (
                not ref
                or canonical_item(
                    ref.get("brand"), ref.get("name"), ref.get("category")
                )[2]
                != key
            ):
                issues.append(
                    f"Catalogue {row['id']} has a missing or conflicting ingredient link"
                )
        for ref_id in refs:
            others = db.execute(
                "SELECT id,brand,canonical_name,category FROM flavours "
                "WHERE source_flavor_ref_id=? AND COALESCE(merged_into_flavour_id,0)=0",
                (ref_id,),
            ).fetchall()
            if any(r["id"] not in {x["id"] for x in rows} for r in others):
                issues.append(
                    f"Ingredient {ref_id} is shared with a catalogue entry outside this group"
                )
            active_mix = db.execute(
                "SELECT 1 FROM mix_sessions ms JOIN recipe_items ri ON ri.version_id=ms.recipe_version_id "
                "WHERE ms.completed_at IS NULL AND ms.mode='guided' AND ri.ingredient_id=? LIMIT 1",
                (ref_id,),
            ).fetchone()
            if active_mix:
                issues.append(
                    f"Ingredient {ref_id} is used by an unfinished guided mix"
                )
        payload = {
            "identity": key,
            "brand": brand,
            "name": name,
            "target_id": rows[0]["id"],
            "source_ids": [r["id"] for r in rows[1:]],
            "rows": rows,
            "issues": sorted(set(issues)),
        }
        payload["fingerprint"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()
        result.append(payload)
    return result


def consolidate_group(db, plan, *, actor_user_id, actor_role):
    """Caller holds a write transaction and commits only after every selected group succeeds."""
    current = next(
        (p for p in duplicate_plan(db) if p["identity"] == plan["identity"]), None
    )
    if current is None or current["fingerprint"] != plan["fingerprint"]:
        raise ValueError("Catalogue changed since preview; generate a fresh plan.")
    if current["issues"]:
        raise ValueError("; ".join(current["issues"]))
    target_id = current["target_id"]
    target = next(r for r in current["rows"] if r["id"] == target_id)
    target_ref = target["source_flavor_ref_id"]
    source_refs = set()
    db.execute(
        "INSERT OR IGNORE INTO flavour_aliases(flavour_id,alias_raw,alias_normalized,source) "
        "VALUES(?,?,?,?)",
        (
            target_id,
            target["canonical_name"],
            normalize_flavor_identity_name(target["canonical_name"]),
            "catalogue_cleanup",
        ),
    )
    for source in current["rows"]:
        sid = source["id"]
        if sid == target_id:
            continue
        source_ref = source["source_flavor_ref_id"]
        if source_ref != target_ref:
            _repoint_recipe_items_merge_dup_versions(
                db, source_ref_id=source_ref, target_ref_id=target_ref
            )
            # Keep individual stock lots, costs and quantities. Only their ingredient link changes.
            db.execute(
                "UPDATE library_items SET reference_id=? WHERE reference_id=?",
                (target_ref, source_ref),
            )
            for table, column in [
                ("recipe_reference_flavours", "matched_flavor_id"),
                ("flavor_aliases", "target_flavor_id"),
            ]:
                columns = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
                if column in columns:
                    db.execute(
                        f"UPDATE {table} SET {column}=? WHERE {column}=?",
                        (target_ref, source_ref),
                    )
            source_refs.add(source_ref)
        _merge_user_stash_flavours(
            db, source_flavour_id=sid, target_flavour_id=target_id
        )
        for table in ("flavour_notes", "flavour_reviews"):
            db.execute(
                f"UPDATE {table} SET flavour_id=? WHERE flavour_id=?", (target_id, sid)
            )
        # Supplier metadata and all original price fields remain on archived source rows.
        db.execute(
            "INSERT OR IGNORE INTO flavour_aliases(flavour_id,alias_raw,alias_normalized,source) "
            "VALUES(?,?,?,?)",
            (
                target_id,
                source["canonical_name"],
                normalize_flavor_identity_name(source["canonical_name"]),
                "catalogue_cleanup",
            ),
        )
        for alias in db.execute(
            "SELECT alias_raw,alias_normalized,source FROM flavour_aliases WHERE flavour_id=?",
            (sid,),
        ).fetchall():
            db.execute(
                "INSERT OR IGNORE INTO flavour_aliases(flavour_id,alias_raw,alias_normalized,source) "
                "VALUES(?,?,?,?)",
                (
                    target_id,
                    alias["alias_raw"],
                    alias["alias_normalized"],
                    alias["source"],
                ),
            )
        for column in ("avg_usage", "default_percent", "profile"):
            db.execute(
                f"UPDATE flavours SET {column}=? WHERE id=? AND ({column} IS NULL OR {column}='' OR {column}=0)",
                (source.get(column), target_id),
            )
        db.execute(
            "UPDATE flavours SET merged_into_flavour_id=?,merged_at=CURRENT_TIMESTAMP,is_hidden=1,"
            "moderation_status='merged',moderation_reason='Exact identity catalogue cleanup',"
            "updated_by_user_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (target_id, actor_user_id, sid),
        )
    db.execute(
        "UPDATE flavours SET brand=?,canonical_brand=?,canonical_name=?,normalized_name=? WHERE id=?",
        (
            current["brand"],
            current["brand"],
            current["name"],
            normalize_flavor_identity_name(current["name"]),
            target_id,
        ),
    )
    for ref_id in source_refs:
        # Retain source identities for historical mixes, source prices and provenance.
        db.execute("UPDATE flavor_reference SET active=0 WHERE id=?", (ref_id,))
    db.execute(
        "INSERT INTO tvw_admin_audit_logs(actor_user_id,actor_role,action,target_type,target_id,reason,details_json) "
        "VALUES(?,?,'catalogue_identity_consolidated','flavour',?,'Exact identity cleanup',?)",
        (actor_user_id, actor_role, target_id, json.dumps(current, default=str)),
    )
    return {"target_id": target_id, "source_ids": current["source_ids"]}
