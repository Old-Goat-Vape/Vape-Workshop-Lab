"""Match community recipes to the current user's stash using canonical `flavor_reference` ids.

Stash resolution:
  - Old Goat: `holdings_bridge_service.list_old_goat_shared_holdings()` (Lab `library_items`).
  - Everyone else: `user_stash_items` → resolve merged `flavours.id` → `source_flavor_ref_id`.

Recipes use `recipe_items.ingredient_id` = `flavor_reference.id` (same keys as stash).

WCM v1.1 — quantity feasibility (practical, not lab-precise):
  For each flavour line, required concentrate ml ≈ target_batch_ml × (amount_percent / 100).
  Stash ml per reference is compared to that requirement. Assumes stash quantities are the same
  “concentrate ml” basis as recipe percentages (explicit in UI).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from db import get_db
from models import recipes_repo
from services import holdings_bridge_service

MIN_STASH_ML = 0.05
DEFAULT_MAX_RECIPES = 2500
VERSION_ID_CHUNK = 450

# Default batch size for “can I mix this recipe?” (user can override via query param).
DEFAULT_TARGET_BATCH_ML = 30.0
MIN_TARGET_BATCH_ML = 5.0
MAX_TARGET_BATCH_ML = 500.0

# Float slack when comparing required vs on-hand ml.
QTY_EPSILON_ML = 0.02

# If feasible but any line has less than this headroom ratio (have/required), mark “low stock”.
LOW_STOCK_HEADROOM_RATIO = 1.1

# If feasible but any line would leave less than this much in the bottle after the batch, mark “low stock”.
LOW_STOCK_REMAINING_ML = 0.5


def _resolve_flavour_id_in_memory(
    flavour_id: int,
    *,
    merged_to: dict[int, int | None],
) -> int:
    cur = int(flavour_id)
    seen: set[int] = set()
    while cur in merged_to and merged_to[cur]:
        nxt = int(merged_to[cur])
        if nxt in seen:
            break
        seen.add(cur)
        cur = nxt
    return cur


def fetch_stash_reference_qty_ml(
    *,
    user_id: int | None,
    role: str | None,
    username: str | None,
) -> tuple[dict[int, float], str]:
    """Returns (reference_id -> total ml, source_label)."""
    if holdings_bridge_service.is_old_goat_user(user_id=user_id, role=role, username=username):
        items = holdings_bridge_service.list_old_goat_shared_holdings()
        qty: dict[int, float] = {}
        for it in items:
            rid = int(it["reference_id"])
            ml = float(it.get("quantity_ml") or 0.0)
            if ml <= MIN_STASH_ML:
                continue
            qty[rid] = qty.get(rid, 0.0) + ml
        return qty, "lab_shared_holdings"

    if not user_id:
        return {}, "user_stash_items"

    db = get_db()
    rows = db.execute(
        """
        SELECT flavour_id, COALESCE(quantity_ml, 0) AS quantity_ml
        FROM user_stash_items
        WHERE user_id = ?
        """,
        (int(user_id),),
    ).fetchall()
    flavour_ids = [int(r["flavour_id"]) for r in rows if r["flavour_id"] is not None]
    if not flavour_ids:
        return {}, "user_stash_items"

    ph = ",".join("?" * len(flavour_ids))
    meta = db.execute(
        f"""
        SELECT id, merged_into_flavour_id, source_flavor_ref_id
        FROM flavours
        WHERE id IN ({ph})
        """,
        flavour_ids,
    ).fetchall()
    merged_to: dict[int, int | None] = {}
    ref_for_flavour: dict[int, int | None] = {}
    for r in meta:
        fid = int(r["id"])
        m = r["merged_into_flavour_id"]
        merged_to[fid] = int(m) if m else None
        sr = r["source_flavor_ref_id"]
        ref_for_flavour[fid] = int(sr) if sr else None

    qty = defaultdict(float)
    for r in rows:
        fid_raw = r["flavour_id"]
        if fid_raw is None:
            continue
        fid = _resolve_flavour_id_in_memory(int(fid_raw), merged_to=merged_to)
        ref_id = ref_for_flavour.get(fid)
        if not ref_id:
            continue
        ml = float(r["quantity_ml"] or 0.0)
        if ml <= MIN_STASH_ML:
            continue
        qty[int(ref_id)] += ml

    return dict(qty), "user_stash_items"


def _fetch_public_recipes_for_scan(*, limit: int) -> list[dict[str, Any]]:
    db = get_db()
    pub = recipes_repo.tvw_recipe_public_list_predicate_sql("r")
    lim = max(1, min(int(limit or DEFAULT_MAX_RECIPES), 20_000))
    rows = db.execute(
        f"""
        SELECT r.id,
               r.name,
               r.slug,
               r.current_version_id,
               rv.version_number AS current_version_number,
               rv.total_flavor_percent AS current_total_flavour_pct
        FROM recipes r
        INNER JOIN recipe_versions rv ON rv.id = r.current_version_id
        WHERE r.active = 1
          AND r.current_version_id IS NOT NULL
          AND {pub}
        ORDER BY r.updated_at DESC, r.id DESC
        LIMIT ?
        """,
        (lim,),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_version_ingredients_map(version_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not version_ids:
        return {}
    db = get_db()
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for i in range(0, len(version_ids), VERSION_ID_CHUNK):
        chunk = version_ids[i : i + VERSION_ID_CHUNK]
        ph = ",".join("?" * len(chunk))
        rows = db.execute(
            f"""
            SELECT ri.version_id,
                   ri.ingredient_id,
                   ri.amount_percent,
                   fr.brand,
                   fr.name
            FROM recipe_items ri
            JOIN flavor_reference fr ON fr.id = ri.ingredient_id
            WHERE ri.version_id IN ({ph})
            ORDER BY fr.brand COLLATE NOCASE, fr.name COLLATE NOCASE
            """,
            chunk,
        ).fetchall()
        for r in rows:
            vid = int(r["version_id"])
            out[vid].append(
                {
                    "ingredient_id": int(r["ingredient_id"]),
                    "amount_percent": float(r["amount_percent"] or 0.0),
                    "brand": r["brand"],
                    "name": r["name"],
                }
            )
    return dict(out)


def map_reference_ids_to_flavour_pages(reference_ids: set[int]) -> dict[int, int | None]:
    """Public wrapper: canonical flavour_reference.id → TVW `flavours.id` for linking."""
    return _reference_to_display_flavour_ids(reference_ids)


def _reference_to_display_flavour_ids(reference_ids: set[int]) -> dict[int, int | None]:
    if not reference_ids:
        return {}
    db = get_db()
    ids = list(reference_ids)
    result: dict[int, int | None] = {int(rid): None for rid in reference_ids}
    for i in range(0, len(ids), VERSION_ID_CHUNK):
        chunk = ids[i : i + VERSION_ID_CHUNK]
        ph = ",".join("?" * len(chunk))
        rows = db.execute(
            f"""
            SELECT source_flavor_ref_id AS ref_id, MIN(id) AS flavour_id
            FROM flavours
            WHERE source_flavor_ref_id IN ({ph})
              AND COALESCE(merged_into_flavour_id, 0) = 0
            GROUP BY source_flavor_ref_id
            """,
            chunk,
        ).fetchall()
        for r in rows:
            rid = int(r["ref_id"])
            result[rid] = int(r["flavour_id"]) if r["flavour_id"] is not None else None
    return result


def _classify_recipe_qty(
    *,
    ingredients: list[dict[str, Any]],
    stash_qty: dict[int, float],
    target_batch_ml: float,
    ref_to_flavour: dict[int, int | None],
) -> dict[str, Any]:
    """All ingredients already in stash_qty keys (presence). Classify ok / tight / short."""
    lines: list[dict[str, Any]] = []
    short = False
    tight = False
    for row in ingredients:
        rid = int(row["ingredient_id"])
        pct = float(row.get("amount_percent") or 0.0)
        need = float(target_batch_ml) * (pct / 100.0)
        have = float(stash_qty.get(rid, 0.0))
        rem = have - need
        ratio = (have / need) if need > QTY_EPSILON_ML else None

        ln = {
            "ingredient_id": rid,
            "brand": row.get("brand"),
            "name": row.get("name"),
            "amount_percent": pct,
            "required_ml": round(need, 3),
            "have_ml": round(have, 3),
            "remaining_after_ml": round(rem, 3),
            "flavour_id": ref_to_flavour.get(rid),
        }

        if need <= QTY_EPSILON_ML:
            ln["line_status"] = "negligible"
            ln["line_tight"] = False
            lines.append(ln)
            continue

        line_tight = False
        if have + QTY_EPSILON_ML < need:
            short = True
            ln["line_status"] = "short"
        else:
            ln["line_status"] = "ok"
            if ratio is not None and ratio < LOW_STOCK_HEADROOM_RATIO:
                tight = True
                line_tight = True
            if rem < LOW_STOCK_REMAINING_ML:
                tight = True
                line_tight = True
        ln["line_tight"] = line_tight
        lines.append(ln)

    if short:
        status = "short"
    elif tight:
        status = "tight"
    else:
        status = "ok"

    reason = ""
    if status == "short":
        reason = (
            f"At least one flavour does not have enough ml for a {target_batch_ml:g} ml batch "
            "(see per-line required vs have)."
        )
    elif status == "tight":
        reason = (
            "You can make this batch, but one or more bottles are tight on headroom "
            f"(ratio < {LOW_STOCK_HEADROOM_RATIO:g}× required or < {LOW_STOCK_REMAINING_ML:g} ml left after)."
        )

    return {"status": status, "lines": lines, "reason": reason}


def summarize_recipe_stash_fit(
    *,
    ingredients: list[dict[str, Any]],
    stash_qty: dict[int, float],
    target_batch_ml: float,
    ref_to_flavour: dict[int, int | None],
) -> dict[str, Any]:
    """Library / discovery: one recipe vs stash (same canonical rules as WCM v1.1)."""
    if not ingredients:
        return {
            "stash_signal": "no_items",
            "sort_rank": 9,
            "label": "No ingredients",
            "missing_count": 0,
            "wcm_qty": None,
        }

    stash_set = set(stash_qty.keys())
    missing = [row for row in ingredients if int(row["ingredient_id"]) not in stash_set]
    miss_n = len(missing)

    if miss_n >= 3:
        return {
            "stash_signal": "missing_more",
            "sort_rank": 5,
            "label": f"Missing {miss_n} flavours",
            "missing_count": miss_n,
            "wcm_qty": None,
        }
    if miss_n == 2:
        return {
            "stash_signal": "missing_2",
            "sort_rank": 4,
            "label": "Missing 2 flavours",
            "missing_count": 2,
            "wcm_qty": None,
        }
    if miss_n == 1:
        return {
            "stash_signal": "missing_1",
            "sort_rank": 3,
            "label": "Missing 1 flavour",
            "missing_count": 1,
            "wcm_qty": None,
        }

    wcm_qty = _classify_recipe_qty(
        ingredients=ingredients,
        stash_qty=stash_qty,
        target_batch_ml=target_batch_ml,
        ref_to_flavour=ref_to_flavour,
    )
    st = str((wcm_qty or {}).get("status") or "ok")
    if st == "ok":
        return {
            "stash_signal": "ready",
            "sort_rank": 0,
            "label": "Can make now",
            "missing_count": 0,
            "wcm_qty": wcm_qty,
        }
    if st == "tight":
        return {
            "stash_signal": "low_stock",
            "sort_rank": 1,
            "label": "Low stock",
            "missing_count": 0,
            "wcm_qty": wcm_qty,
        }
    return {
        "stash_signal": "short_batch",
        "sort_rank": 2,
        "label": "Not enough for batch",
        "missing_count": 0,
        "wcm_qty": wcm_qty,
    }


def build_what_can_i_make_bundle(
    *,
    user_id: int | None,
    role: str | None,
    username: str | None,
    max_recipes: int = DEFAULT_MAX_RECIPES,
    target_batch_ml: float | None = None,
) -> dict[str, Any]:
    raw_target = float(target_batch_ml) if target_batch_ml is not None else DEFAULT_TARGET_BATCH_ML
    target_ml = max(MIN_TARGET_BATCH_ML, min(MAX_TARGET_BATCH_ML, raw_target))

    stash_qty, stash_source = fetch_stash_reference_qty_ml(user_id=user_id, role=role, username=username)
    stash_set = set(stash_qty.keys())

    recipes = _fetch_public_recipes_for_scan(limit=max_recipes)
    vids = [int(r["current_version_id"]) for r in recipes if r.get("current_version_id")]
    ving = fetch_version_ingredients_map(vids)

    all_missing_refs: set[int] = set()
    all_ingredient_refs: set[int] = set()
    built: list[dict[str, Any]] = []
    for rec in recipes:
        vid = rec.get("current_version_id")
        if not vid:
            continue
        ing = ving.get(int(vid), [])
        if not ing:
            continue
        for row in ing:
            all_ingredient_refs.add(int(row["ingredient_id"]))
        missing_rows = [row for row in ing if int(row["ingredient_id"]) not in stash_set]
        miss_n = len(missing_rows)
        for row in missing_rows:
            all_missing_refs.add(int(row["ingredient_id"]))
        built.append(
            {
                "id": int(rec["id"]),
                "name": rec["name"],
                "slug": rec.get("slug"),
                "current_version_number": rec.get("current_version_number"),
                "current_total_flavour_pct": rec.get("current_total_flavour_pct"),
                "missing_count": miss_n,
                "missing": missing_rows,
                "ingredients": ing,
            }
        )

    ref_to_flavour = _reference_to_display_flavour_ids(all_missing_refs | all_ingredient_refs)
    for entry in built:
        for m in entry["missing"]:
            rid = int(m["ingredient_id"])
            m["flavour_id"] = ref_to_flavour.get(rid)
        wcm_qty = None
        if int(entry["missing_count"]) == 0:
            wcm_qty = _classify_recipe_qty(
                ingredients=entry["ingredients"],
                stash_qty=stash_qty,
                target_batch_ml=target_ml,
                ref_to_flavour=ref_to_flavour,
            )
        entry["wcm_qty"] = wcm_qty
        entry.pop("ingredients", None)

    ready: list[dict[str, Any]] = []
    low_stock: list[dict[str, Any]] = []
    miss1: list[dict[str, Any]] = []
    miss2: list[dict[str, Any]] = []
    miss_more: list[dict[str, Any]] = []

    def sort_key(e: dict[str, Any]) -> tuple:
        return (str(e.get("name") or "").lower(), int(e.get("id") or 0))

    for e in built:
        n = int(e["missing_count"])
        if n == 0:
            qst = (e.get("wcm_qty") or {}).get("status") or "ok"
            if qst == "ok":
                ready.append(e)
            else:
                low_stock.append(e)
        elif n == 1:
            miss1.append(e)
        elif n == 2:
            miss2.append(e)
        else:
            miss_more.append(e)

    ready.sort(key=sort_key)
    low_stock.sort(key=sort_key)
    miss1.sort(key=sort_key)
    miss2.sort(key=sort_key)
    miss_more.sort(key=lambda x: (int(x["missing_count"]), sort_key(x)))

    return {
        "stash_source": stash_source,
        "stash_reference_count": len(stash_set),
        "recipe_scan_count": len(built),
        "target_batch_ml": target_ml,
        "assumptions": [
            (
                f"Target batch: {target_ml:g} ml total liquid. Each flavour line uses recipe % of that total: "
                "required ml ≈ batch ml × (% ÷ 100)."
            ),
            "Stash ml is treated as pourable concentrate for that calculation (same basis as recipe ingredient percents).",
            "Ignores PG/VG/nic, syringe error, and partial bottles — use as a planning hint, not a guarantee.",
            (
                f"Presence still uses ≥ {MIN_STASH_ML:g} ml in stash; quantity checks use your logged ml per "
                "canonical reference."
            ),
        ],
        "qty_constants": {
            "headroom_ratio": LOW_STOCK_HEADROOM_RATIO,
            "remaining_after_ml": LOW_STOCK_REMAINING_ML,
            "epsilon_ml": QTY_EPSILON_ML,
        },
        "ready": ready,
        "low_stock": low_stock,
        "missing_1": miss1,
        "missing_2": miss2,
        "missing_more": miss_more,
    }
