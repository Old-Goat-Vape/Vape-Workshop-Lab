"""
Repair `flavor_reference`: canonical brands, cleaned names, safe duplicate merge (by identity key),
category correction, FK repoints.

Idempotent and rerunnable. Use dry_run=True for preview counts only.

Identity: `flavor_identity_match() key` from config.canonical_library_brands`.
Category: `config.reference_category_infer` (conservative).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, DefaultDict, Dict, List, Set, Tuple

from config.reference_category_infer import merge_category_bucket, suggest_category_correction
from config.canonical_library_brands import (
    canonical_library_brand,
    fold_capella_brand_if_name_unmarked,
    flavor_identity_match_key,
    preferred_flavor_storage_name,
)

_PROTECTED_BRAND_LOWER = {"system"}

_REPOINT_SPECS: List[Tuple[str, str]] = [
    ("recipe_items", "ingredient_id"),
    ("library_items", "reference_id"),
    ("recipe_reference_flavours", "matched_flavor_id"),
    ("flavor_aliases", "target_flavor_id"),
    ("shopping_list_run_items", "reference_id"),
    ("stock_take_items", "reference_id"),
    ("batch_ingredients", "reference_id"),
    ("mix_history_items", "ingredient_id"),
]


def _is_protected_row(brand: str, category: str | None) -> bool:
    return (brand or "").strip().lower() in _PROTECTED_BRAND_LOWER


def _usage_score(db, fid: int) -> Tuple[int, int, int]:
    ri = int(
        (db.execute("SELECT COUNT(*) AS c FROM recipe_items WHERE ingredient_id = ?", (fid,)).fetchone() or {"c": 0})[
            "c"
        ]
    )
    li = int(
        (db.execute("SELECT COUNT(*) AS c FROM library_items WHERE reference_id = ?", (fid,)).fetchone() or {"c": 0})[
            "c"
        ]
    )
    mh = int(
        (
            db.execute(
                "SELECT COUNT(*) AS c FROM mix_history_items WHERE ingredient_id = ? AND ingredient_id > 0",
                (fid,),
            ).fetchone()
            or {"c": 0}
        )["c"]
    )
    return ri + li + mh, ri, li


def _pick_winner(rows: List[Dict[str, Any]], db) -> int:
    best_id: int | None = None
    best_key: Tuple[int, int, int, int, int] | None = None
    for r in rows:
        fid = int(r["id"])
        b = str(r["brand"] or "")
        n = str(r["name"] or "")
        cb = canonical_library_brand(b, n)
        pref = 1 if cb == b.strip() else 0
        score, _, _ = _usage_score(db, fid)
        pn = preferred_flavor_storage_name(n)
        key = (pref, score, -len(pn), -fid)
        if best_key is None or key > best_key:
            best_key = key
            best_id = fid
    return int(best_id or rows[0]["id"])


def _best_storage_name(candidates: List[str]) -> str:
    cleaned: List[str] = []
    for c in candidates:
        c = (c or "").strip()
        if not c:
            continue
        p = preferred_flavor_storage_name(c)
        if p:
            cleaned.append(p)
    if not cleaned:
        for c in candidates:
            if (c or "").strip():
                return (c or "").strip()
        return ""
    cleaned.sort(key=lambda x: (len(x), x.lower()))
    return cleaned[0]


def _repoint(db, *, old_id: int, new_id: int) -> int:
    if old_id == new_id:
        return 0
    changed = 0
    for table, col in _REPOINT_SPECS:
        try:
            cur = db.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE {col} = ?", (old_id,)).fetchone()
            n = int((cur or {"c": 0})["c"])
            if n:
                db.execute(f"UPDATE {table} SET {col} = ? WHERE {col} = ?", (new_id, old_id))
            changed += n
        except Exception:
            continue
    return changed


def _purge_analytics_for_ref(db, rid: int) -> None:
    try:
        db.execute("DELETE FROM flavor_recipe_analytics WHERE reference_id = ?", (rid,))
    except Exception:
        pass
    try:
        db.execute(
            "DELETE FROM flavor_pairings WHERE reference_id_a = ? OR reference_id_b = ?",
            (rid, rid),
        )
    except Exception:
        pass


def _bump_norm_target(stats: Dict[str, Any], target_brand: str) -> None:
    nb = stats["normalization_by_target"]
    if target_brand not in nb:
        nb[target_brand] = 0
    nb[target_brand] += 1


def _other_active_row_same_brand_name(
    db, *, brand: str, name: str, exclude_id: int, merged_away: Set[int]
) -> Any:
    """Row id reserved for phase-1 losers is excluded so preview/merge logic stays consistent."""
    if merged_away:
        ids = tuple(sorted(merged_away))
        ph = ",".join(["?"] * len(ids))
        return db.execute(
            f"""
            SELECT id, brand, name, category, COALESCE(is_nicotine,0) AS is_nicotine
            FROM flavor_reference
            WHERE brand = ? AND name = ? AND COALESCE(active,1) = 1 AND id != ?
              AND id NOT IN ({ph})
            LIMIT 1
            """,
            (brand, name, exclude_id) + ids,
        ).fetchone()
    return db.execute(
        """
        SELECT id, brand, name, category, COALESCE(is_nicotine,0) AS is_nicotine
        FROM flavor_reference
        WHERE brand = ? AND name = ? AND COALESCE(active,1) = 1 AND id != ?
        LIMIT 1
        """,
        (brand, name, exclude_id),
    ).fetchone()


def _duplicate_group_buckets_ok(grp: List[Dict[str, Any]]) -> bool:
    buckets: Set[str] = set()
    for x in grp:
        buckets.add(
            merge_category_bucket(
                str(x.get("brand") or ""),
                str(x.get("name") or ""),
                str(x.get("category") or ""),
                int(x.get("is_nicotine") or 0),
            )
        )
    return len(buckets) <= 1


def run_canonical_library_repair(db, *, dry_run: bool = False) -> Dict[str, Any]:
    if dry_run:
        db.execute("BEGIN")
    try:
        return _run_canonical_library_repair_txn(db, dry_run=dry_run)
    finally:
        if dry_run:
            db.rollback()


def _run_canonical_library_repair_txn(db, *, dry_run: bool) -> Dict[str, Any]:
    rows = list(
        db.execute(
            """
            SELECT id, brand, name, category, active, COALESCE(is_nicotine,0) AS is_nicotine
            FROM flavor_reference WHERE COALESCE(active,1) = 1
            """
        ).fetchall()
    )
    stats: Dict[str, Any] = {
        "dry_run": dry_run,
        "repair_kind": "canonical_library",
        "rows_scanned": len(rows),
        "brands_updated": 0,
        "names_updated": 0,
        "categories_updated": 0,
        "categories_label_normalized": 0,
        "category_updates_by_reason": {},
        "is_nicotine_synced": 0,
        "duplicates_merged": 0,
        "total_fk_updates": 0,
        "duplicate_groups_found": 0,
        "skipped_protected": 0,
        "ambiguous_skipped": 0,
        "normalization_by_target": {"FLV": 0, "CAP": 0, "CAP (SL)": 0, "CAP (ES)": 0, "TFA": 0},
        "samples_brand_update": [],
        "samples_name_update": [],
        "samples_category_update": [],
        "samples_merge": [],
        "note": "After apply: run Fix Recipe Flavor Links, then Analyze All Recipe Usage (and pairings).",
    }

    merged_away: set[int] = set()

    # --- Phase 1: merge rows that share the same identity key (compatible category buckets only) ---
    groups: DefaultDict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        fid = int(r["id"])
        if fid in merged_away:
            continue
        brand = str(r["brand"] or "")
        name = str(r["name"] or "")
        cat = str(r["category"] or "")
        if _is_protected_row(brand, cat):
            continue
        k = flavor_identity_match_key(brand, name)
        groups[k].append(dict(r))

    for _key, grp in groups.items():
        if len(grp) < 2:
            continue
        if any(_is_protected_row(str(x.get("brand") or ""), str(x.get("category") or "")) for x in grp):
            stats["ambiguous_skipped"] += 1
            continue
        if not _duplicate_group_buckets_ok(grp):
            stats["ambiguous_skipped"] += 1
            continue
        winner = _pick_winner(grp, db)
        best_name = _best_storage_name([str(x.get("name") or "") for x in grp])
        wrow = next(x for x in grp if int(x["id"]) == winner)
        canon_b = canonical_library_brand(str(wrow.get("brand") or ""), str(wrow.get("name") or ""))
        canon_b = fold_capella_brand_if_name_unmarked(canon_b, best_name)
        old_brand = str(wrow.get("brand") or "")
        old_name = str(wrow.get("name") or "")

        try:
            db.execute(
                "UPDATE flavor_reference SET brand = ?, name = ? WHERE id = ?",
                (canon_b, best_name, winner),
            )
        except Exception:
            stats["ambiguous_skipped"] += 1
            continue
        stats["duplicate_groups_found"] += 1

        if canon_b != old_brand:
            _bump_norm_target(stats, canon_b)
        if canon_b != old_brand:
            stats["brands_updated"] += 1
            if len(stats["samples_brand_update"]) < 15:
                stats["samples_brand_update"].append(
                    {"id": winner, "from": old_brand, "to": canon_b, "name": best_name}
                )
        if best_name != old_name:
            stats["names_updated"] += 1
            if len(stats["samples_name_update"]) < 15:
                stats["samples_name_update"].append(
                    {"id": winner, "from": old_name, "to": best_name, "brand": canon_b}
                )

        for r in grp:
            fid = int(r["id"])
            if fid == winner:
                continue
            stats["duplicates_merged"] += 1
            nfk = _repoint(db, old_id=fid, new_id=winner)
            stats["total_fk_updates"] += nfk
            _purge_analytics_for_ref(db, fid)
            merged_away.add(fid)
            db.execute("UPDATE flavor_reference SET active = 0 WHERE id = ?", (fid,))
            if len(stats["samples_merge"]) < 20:
                stats["samples_merge"].append(
                    {
                        "from_id": fid,
                        "into_id": winner,
                        "old_brand": str(r.get("brand") or ""),
                        "name_before": str(r.get("name") or ""),
                        "target_brand": canon_b,
                        "reason": "duplicate_identity_key",
                    }
                )

    # --- Phase 2: normalize brand + storage name on remaining active rows ---
    rows2 = db.execute(
        """
        SELECT id, brand, name, category, COALESCE(is_nicotine,0) AS is_nicotine
        FROM flavor_reference WHERE COALESCE(active,1) = 1
        """
    ).fetchall()
    for r in rows2:
        fid = int(r["id"])
        if fid in merged_away:
            continue
        brand = str(r["brand"] or "")
        name = str(r["name"] or "")
        cat = str(r["category"] or "")
        if _is_protected_row(brand, cat):
            stats["skipped_protected"] += 1
            continue
        tb = canonical_library_brand(brand, name)
        tn = preferred_flavor_storage_name(name)
        tb = fold_capella_brand_if_name_unmarked(tb, tn)
        if tb == brand and tn == name:
            continue

        o = _other_active_row_same_brand_name(db, brand=tb, name=tn, exclude_id=fid, merged_away=merged_away)
        if o:
            oid = int(o["id"])
            if merge_category_bucket(brand, name, cat, int(r["is_nicotine"] or 0)) != merge_category_bucket(
                str(o["brand"] or ""),
                str(o["name"] or ""),
                str(o["category"] or ""),
                int(o["is_nicotine"] or 0),
            ):
                stats["ambiguous_skipped"] += 1
                continue
            sf, _, _ = _usage_score(db, fid)
            so, _, _ = _usage_score(db, oid)
            if sf > so or (sf == so and fid < oid):
                winner_id, loser_id = fid, oid
            else:
                winner_id, loser_id = oid, fid
            if loser_id in merged_away or winner_id in merged_away:
                stats["ambiguous_skipped"] += 1
                continue
            stats["duplicates_merged"] += 1
            stats["duplicate_groups_found"] += 1
            nfk = _repoint(db, old_id=loser_id, new_id=winner_id)
            stats["total_fk_updates"] += nfk
            _purge_analytics_for_ref(db, loser_id)
            merged_away.add(loser_id)
            db.execute("UPDATE flavor_reference SET active = 0 WHERE id = ?", (loser_id,))
            w_before = db.execute(
                "SELECT brand, name FROM flavor_reference WHERE id = ?", (winner_id,)
            ).fetchone()
            old_wb = str(w_before["brand"] if w_before else "")
            old_wn = str(w_before["name"] if w_before else "")
            try:
                db.execute(
                    "UPDATE flavor_reference SET brand = ?, name = ? WHERE id = ?",
                    (tb, tn, winner_id),
                )
            except Exception:
                stats["ambiguous_skipped"] += 1
                continue
            if old_wb != tb:
                stats["brands_updated"] += 1
                if len(stats["samples_brand_update"]) < 15:
                    stats["samples_brand_update"].append(
                        {"id": winner_id, "from": old_wb, "to": tb, "name": tn}
                    )
            if old_wn != tn:
                stats["names_updated"] += 1
                if len(stats["samples_name_update"]) < 15:
                    stats["samples_name_update"].append(
                        {"id": winner_id, "from": old_wn, "to": tn, "brand": tb}
                    )
            if len(stats["samples_merge"]) < 25:
                stats["samples_merge"].append(
                    {
                        "from_id": loser_id,
                        "into_id": winner_id,
                        "logical": f"{tb} / {tn}",
                        "reason": "unique_conflict_absorb",
                    }
                )
            continue

        if tb != brand:
            _bump_norm_target(stats, tb)

        try:
            db.execute("UPDATE flavor_reference SET brand = ?, name = ? WHERE id = ?", (tb, tn, fid))
        except Exception:
            stats["ambiguous_skipped"] += 1
            continue
        if tb != brand:
            stats["brands_updated"] += 1
            if len(stats["samples_brand_update"]) < 15:
                stats["samples_brand_update"].append({"id": fid, "from": brand, "to": tb, "name": tn})
        if tn != name:
            stats["names_updated"] += 1
            if len(stats["samples_name_update"]) < 15:
                stats["samples_name_update"].append({"id": fid, "from": name, "to": tn, "brand": tb})

    # --- Phase 3: category + is_nicotine (conservative) ---
    rows3 = db.execute(
        """
        SELECT id, brand, name, category, COALESCE(is_nicotine,0) AS is_nicotine,
               COALESCE(source,'') AS source
        FROM flavor_reference WHERE COALESCE(active,1) = 1
        """
    ).fetchall()
    for r in rows3:
        fid = int(r["id"])
        if fid in merged_away:
            continue
        brand = str(r["brand"] or "")
        name = str(r["name"] or "")
        cat = str(r["category"] or "")
        if _is_protected_row(brand, cat):
            continue
        nic = int(r["is_nicotine"] or 0)
        src = str(r["source"] or "")
        new_cat, nic_new, reason = suggest_category_correction(brand, name, cat, nic, src)
        raw_cat = cat.strip()
        set_parts: List[str] = []
        vals: List[Any] = []
        if new_cat is not None and new_cat != raw_cat:
            set_parts.append("category = ?")
            vals.append(new_cat)
            cbr: Dict[str, int] = stats["category_updates_by_reason"]
            cbr[reason] = cbr.get(reason, 0) + 1
            if reason == "normalized_label_only":
                stats["categories_label_normalized"] += 1
            else:
                stats["categories_updated"] += 1
            if reason != "normalized_label_only" and len(stats["samples_category_update"]) < 15:
                stats["samples_category_update"].append(
                    {"id": fid, "from": raw_cat or cat, "to": new_cat, "reason": reason, "brand": brand, "name": name}
                )
        if nic_new is not None and nic_new != nic:
            set_parts.append("is_nicotine = ?")
            vals.append(int(nic_new))
            stats["is_nicotine_synced"] += 1
        if set_parts:
            vals.append(fid)
            db.execute(f"UPDATE flavor_reference SET {', '.join(set_parts)} WHERE id = ?", vals)

    stats["analytics_recompute_recommended"] = True
    stats["analytics_recompute_tables"] = ["flavor_recipe_analytics", "flavor_pairings", "recipe_items"]
    stats["status"] = "preview" if dry_run else "applied"
    stats["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return stats


def run_canonical_brand_repair(db, *, dry_run: bool = False) -> Dict[str, Any]:
    """Backward-compatible alias for the full library repair pipeline."""
    return run_canonical_library_repair(db, dry_run=dry_run)
