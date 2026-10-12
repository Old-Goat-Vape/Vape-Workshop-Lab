"""
Recipe-derived flavor usage analytics (additive layer).

Source set (documented for operators):
- recipes.active = 1
- recipes.status NOT IN ('Archived', 'Development') — same default as the main recipe library
  when development/archived recipes are hidden.
- Latest recipe version per recipe only (ORDER BY created_at DESC, id DESC): current formulation.
- Only rows where flavor_reference.category = 'Flavor' and is_nicotine = 0 (true "flavor" lines).

Does not modify flavor_reference classification columns or identity fields.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime
from itertools import combinations
from typing import Any, DefaultDict, Dict, List, Optional, Sequence, Set, Tuple

from db import get_db

ANALYTICS_VERSION = "ra-v1.0"

# Pair appears in at least this many distinct recipes to be stored (reduces noise).
MIN_PAIR_RECIPE_COUNT = 2

# Rounding for stored percentages (avoid false precision).
PCT_DECIMALS = 2


def _round_pct(x: float) -> float:
    return round(float(x), PCT_DECIMALS)


def _median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return _round_pct(s[mid])
    return _round_pct((s[mid - 1] + s[mid]) / 2.0)


def fetch_included_recipe_version_ids() -> List[Tuple[int, int]]:
    """Return (recipe_id, version_id) rows for the analytics source set."""
    db = get_db()
    rows = db.execute(
        """
        SELECT r.id AS recipe_id,
               (
                   SELECT v.id
                   FROM recipe_versions v
                   WHERE v.recipe_id = r.id
                   ORDER BY v.created_at DESC, v.id DESC
                   LIMIT 1
               ) AS version_id
        FROM recipes r
        WHERE r.active = 1
          AND COALESCE(r.status, '') NOT IN ('Archived', 'Development')
        """
    ).fetchall()
    out: List[Tuple[int, int]] = []
    for row in rows:
        vid = row["version_id"]
        if vid is not None:
            out.append((int(row["recipe_id"]), int(vid)))
    return out


def load_flavor_lines_for_versions(version_ids: Sequence[int]) -> List[Dict[str, Any]]:
    """Flatten recipe_items for given versions with classification fields."""
    if not version_ids:
        return []
    db = get_db()
    placeholders = ",".join(["?"] * len(version_ids))
    q = f"""
        SELECT v.recipe_id AS recipe_id,
               ri.version_id AS version_id,
               ri.ingredient_id AS ref_id,
               ri.amount_percent AS pct,
               COALESCE(fr.resolved_profile, '') AS resolved_profile,
               COALESCE(fr.resolved_role, '') AS resolved_role,
               fr.name AS name,
               fr.brand AS brand
        FROM recipe_items ri
        JOIN recipe_versions v ON v.id = ri.version_id
        JOIN flavor_reference fr ON fr.id = ri.ingredient_id
        WHERE ri.version_id IN ({placeholders})
          AND COALESCE(fr.category, 'Flavor') = 'Flavor'
          AND COALESCE(fr.is_nicotine, 0) = 0
    """
    rows = db.execute(q, list(version_ids)).fetchall()
    return [dict(r) for r in rows]


def compute_likely_recipe_role(
    *,
    avg_pct: float,
    resolved_role: str,
    resolved_profile: str,
) -> str:
    """
    Combine observed average % in mixes with stored classification (resolved_*).
    Short, operator-readable label — not a second classification engine.
    """
    avg = float(avg_pct or 0.0)
    role = (resolved_role or "").strip().lower()
    prof = (resolved_profile or "").strip().lower()

    if role in ("sweetener_support", "sweetener") or prof == "sweetener":
        tier = "sweetener_usage"
    elif role == "cooling" or prof == "cooling":
        tier = "cooling_usage"
    elif avg >= 2.5:
        tier = "primary_in_mix"
    elif avg >= 0.85:
        tier = "supporting"
    else:
        tier = "accent_trace"

    class_hint = resolved_role or resolved_profile or "unclassified"
    return f"{tier}; class:{class_hint}"


def classify_relationship_type(profile_a: str, profile_b: str) -> str:
    """Simple ordered pair label from resolved profiles (fallback unknown)."""
    pa = (profile_a or "unknown").strip().lower() or "unknown"
    pb = (profile_b or "unknown").strip().lower() or "unknown"
    x, y = sorted([pa, pb])
    pairs = frozenset({pa, pb})
    if pairs == {"fruit", "cream"}:
        return "fruit + cream"
    if pairs == {"fruit", "cooling"} or (pa == "fruit" and pb == "cooling") or (pa == "cooling" and pb == "fruit"):
        return "fruit + cooling"
    if pairs == {"fruit", "candy"}:
        return "fruit + candy"
    if pairs == {"candy", "sour"}:
        return "candy + sour"
    if "cream" in pairs and "bakery" in pairs:
        return "cream + bakery"
    if "fruit" in pairs and "bakery" in pairs:
        return "fruit + bakery"
    if "beverage" in pairs:
        return f"{x} + {y}"
    return f"{x} + {y}"


def detect_pairing_reason(
    *,
    relationship_type: str,
    profile_a: str,
    profile_b: str,
    role_a: str,
    role_b: str,
    avg_pct_a: float,
    avg_pct_b: float,
) -> str:
    """
    Rule-based, practical explanations only (no faux chemistry).
    """
    parts: List[str] = []
    rt = relationship_type.lower()
    if "fruit + cream" in rt or ({"fruit", "cream"} <= {profile_a.lower(), profile_b.lower()}):
        parts.append("Often used so a creamy note backs a brighter fruit without washing it out.")
    elif "fruit + cooling" in rt:
        parts.append("Commonly paired for cooling lift and freshness on fruit profiles.")
    elif ("fruit + candy" in rt) or ("candy" in rt and "fruit" in rt):
        parts.append("Frequently combined for sweetened fruit amplification in the mix.")
    elif "candy + sour" in rt or ("candy" in profile_a.lower() and "sour" in profile_b.lower()) or (
        "candy" in profile_b.lower() and "sour" in profile_a.lower()
    ):
        parts.append("Typical candy–sour stack: sweetness with a tart edge.")
    elif "cream + bakery" in rt or ("cream" in profile_a.lower() and "bakery" in profile_b.lower()):
        parts.append("Adds mouthfeel and density common in dessert-style recipes.")
    elif "bakery" in profile_a.lower() or "bakery" in profile_b.lower():
        parts.append("Bakery-style body often pairs with richer supporting notes.")

    # Usage balance (plain language).
    if avg_pct_a >= 2 * max(avg_pct_b, 0.05):
        parts.append("In your data, one side is usually dosed higher than the other.")
    elif avg_pct_b >= 2 * max(avg_pct_a, 0.05):
        parts.append("In your data, one side is usually dosed higher than the other.")

    if not parts:
        parts.append(
            "Co-occurs in recipes; check profiles/roles for complementary top-note vs body support."
        )
    return " ".join(parts)[:800]


def pairing_strength_score(recipe_count: int, max_count: int) -> float:
    """0..1 heuristic from co-occurrence frequency (explainable, not statistical p-values)."""
    if recipe_count <= 0 or max_count <= 0:
        return 0.0
    return round(min(1.0, math.log1p(recipe_count) / math.log1p(max_count)), 4)


def build_pairing_insights(top_reasons: Sequence[str], limit: int = 4) -> List[str]:
    """Dedupe while preserving order."""
    seen: Set[str] = set()
    out: List[str] = []
    for r in top_reasons:
        t = (r or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= limit:
            break
    return out


def _meta_by_ref(lines: Sequence[Dict[str, Any]]) -> Dict[int, Dict[str, str]]:
    m: Dict[int, Dict[str, str]] = {}
    for ln in lines:
        rid = int(ln["ref_id"])
        if rid not in m:
            m[rid] = {
                "resolved_profile": str(ln.get("resolved_profile") or ""),
                "resolved_role": str(ln.get("resolved_role") or ""),
                "name": str(ln.get("name") or ""),
                "brand": str(ln.get("brand") or ""),
            }
    return m


def aggregate_usage_and_pairs(
    lines: Sequence[Dict[str, Any]],
) -> Tuple[Dict[int, List[float]], Dict[Tuple[int, int], Dict[str, Any]], int]:
    """
    Build per-reference usage percents (one entry per recipe appearance) and pair aggregates.
    Returns (usage_pcts, pair_data, recipe_count_total).
    """
    by_recipe: DefaultDict[int, List[Dict[str, Any]]] = defaultdict(list)
    for ln in lines:
        by_recipe[int(ln["recipe_id"])].append(ln)

    usage_pcts: DefaultDict[int, List[float]] = defaultdict(list)
    pair_pcts_a: DefaultDict[Tuple[int, int], List[float]] = defaultdict(list)
    pair_pcts_b: DefaultDict[Tuple[int, int], List[float]] = defaultdict(list)
    pair_recipes: DefaultDict[Tuple[int, int], Set[int]] = defaultdict(set)

    recipe_total = len(by_recipe)
    meta = _meta_by_ref(lines)

    for recipe_id, items in by_recipe.items():
        # One percent per ref per recipe (max if duplicates).
        by_ref: Dict[int, float] = {}
        for it in items:
            rid = int(it["ref_id"])
            pct = float(it["pct"] or 0.0)
            by_ref[rid] = max(by_ref.get(rid, 0.0), pct)
        for rid, pct in by_ref.items():
            usage_pcts[rid].append(pct)

        refs_sorted = sorted(by_ref.keys())
        for a, b in combinations(refs_sorted, 2):
            if a > b:
                a, b = b, a
            key = (a, b)
            pair_recipes[key].add(recipe_id)
            pair_pcts_a[key].append(by_ref[a])
            pair_pcts_b[key].append(by_ref[b])

    pair_out: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for key, rids in pair_recipes.items():
        n = len(rids)
        if n < MIN_PAIR_RECIPE_COUNT:
            continue
        a, b = key
        avga = sum(pair_pcts_a[key]) / n
        avgb = sum(pair_pcts_b[key]) / n
        pa = meta[a]["resolved_profile"] or "unknown"
        pb = meta[b]["resolved_profile"] or "unknown"
        ra = meta[a]["resolved_role"] or ""
        rb = meta[b]["resolved_role"] or ""
        rel = classify_relationship_type(pa, pb)
        reason = detect_pairing_reason(
            relationship_type=rel,
            profile_a=pa,
            profile_b=pb,
            role_a=ra,
            role_b=rb,
            avg_pct_a=_round_pct(avga),
            avg_pct_b=_round_pct(avgb),
        )
        pair_out[key] = {
            "recipe_count": n,
            "avg_pct_a": _round_pct(avga),
            "avg_pct_b": _round_pct(avgb),
            "relationship_type": rel,
            "pairing_reason": reason,
            "profile_a": pa,
            "profile_b": pb,
            "meta_a": meta[a],
            "meta_b": meta[b],
        }

    return dict(usage_pcts), pair_out, recipe_total


def _top_pairings_for_ref(
    ref_id: int,
    pair_out: Dict[Tuple[int, int], Dict[str, Any]],
    meta: Dict[int, Dict[str, str]],
    limit: int = 8,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Partner list for JSON + reason strings for insights (stable ordering by count then partner id)."""
    rows: List[Tuple[int, int, float, Dict[str, Any]]] = []
    for (a, b), pdata in pair_out.items():
        if a == ref_id:
            partner = b
            other_avg = pdata["avg_pct_b"]
        elif b == ref_id:
            partner = a
            other_avg = pdata["avg_pct_a"]
        else:
            continue
        rows.append((pdata["recipe_count"], partner, other_avg, pdata))
    rows.sort(key=lambda x: (-x[0], x[1]))

    top: List[Dict[str, Any]] = []
    reasons: List[str] = []
    for n_rc, partner, other_avg, pdata in rows[:limit]:
        pm = meta.get(partner, {})
        top.append(
            {
                "partner_id": partner,
                "partner_brand": pm.get("brand", ""),
                "partner_name": pm.get("name", ""),
                "recipe_count": n_rc,
                "avg_pct_partner": other_avg,
                "relationship_type": pdata["relationship_type"],
                "reason_snippet": pdata["pairing_reason"][:240],
            }
        )
        reasons.append(pdata["pairing_reason"])
    return top, reasons


def build_analytics_snapshot() -> Dict[str, Any]:
    """In-memory snapshot from DB (no writes)."""
    rv = fetch_included_recipe_version_ids()
    version_ids = [v for _, v in rv]
    lines = load_flavor_lines_for_versions(version_ids)
    usage_pcts, pair_out, recipe_total = aggregate_usage_and_pairs(lines)
    meta = _meta_by_ref(lines)
    max_pair = max((p["recipe_count"] for p in pair_out.values()), default=0)
    return {
        "lines": lines,
        "usage_pcts": usage_pcts,
        "pair_out": pair_out,
        "recipe_total": recipe_total,
        "meta": meta,
        "max_pair_recipes": max_pair,
        "flavors_touched": len(usage_pcts),
        "pair_count": len(pair_out),
    }


def _rows_for_per_flavor_analytics(
    usage_pcts: Dict[int, List[float]],
    pair_out: Dict[Tuple[int, int], Dict[str, Any]],
    meta: Dict[int, Dict[str, str]],
) -> List[Tuple[Any, ...]]:
    """Tuples for INSERT into flavor_recipe_analytics."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows_out: List[Tuple[Any, ...]] = []
    for ref_id, pcts in usage_pcts.items():
        n = len(pcts)
        avg = _round_pct(sum(pcts) / n) if n else 0.0
        mn = _round_pct(min(pcts)) if pcts else None
        mx = _round_pct(max(pcts)) if pcts else None
        med = _median(pcts)
        m = meta.get(ref_id, {})
        role_guess = compute_likely_recipe_role(
            avg_pct=avg,
            resolved_role=m.get("resolved_role", "") or "",
            resolved_profile=m.get("resolved_profile", "") or "",
        )
        top, reasons = _top_pairings_for_ref(ref_id, pair_out, meta, limit=8)
        insights = build_pairing_insights(reasons, limit=4)
        rows_out.append(
            (
                ref_id,
                n,
                avg,
                mn,
                mx,
                med,
                role_guess,
                json.dumps(top),
                json.dumps(insights),
                ANALYTICS_VERSION,
                now,
            )
        )
    return rows_out


def _rows_for_pairings(
    pair_out: Dict[Tuple[int, int], Dict[str, Any]],
    max_pair: int,
) -> List[Tuple[Any, ...]]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows: List[Tuple[Any, ...]] = []
    for (a, b), pdata in pair_out.items():
        assert a < b
        strength = pairing_strength_score(pdata["recipe_count"], max_pair)
        rows.append(
            (
                a,
                b,
                pdata["recipe_count"],
                pdata["avg_pct_a"],
                pdata["avg_pct_b"],
                strength,
                pdata["relationship_type"],
                pdata["pairing_reason"],
                now,
                ANALYTICS_VERSION,
            )
        )
    return rows


def recompute_recipe_analytics(
    *,
    apply: bool,
    mode: str = "all",
    reference_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    mode: 'all' | 'usage' | 'pairings'
    reference_id: if set with apply, refresh that flavor's analytics row and pair rows touching it only
                   (still does a full scan for correctness).
    """
    snap = build_analytics_snapshot()
    usage_pcts = snap["usage_pcts"]
    pair_out = snap["pair_out"]
    meta = snap["meta"]
    max_pair = snap["max_pair_recipes"]

    summary: Dict[str, Any] = {
        "status": "success",
        "apply": bool(apply),
        "mode": mode,
        "analytics_version": ANALYTICS_VERSION,
        "source": {
            "description": "active recipes excluding Archived/Development; latest version; Flavor + non-nicotine only",
            "recipes_in_set": snap["recipe_total"],
            "flavor_lines_loaded": len(snap["lines"]),
            "flavors_with_usage": snap["flavors_touched"],
            "pair_rows_computed": snap["pair_count"],
            "min_pair_recipe_count": MIN_PAIR_RECIPE_COUNT,
        },
    }

    if not apply:
        if reference_id is not None:
            rid = int(reference_id)
            pcts = usage_pcts.get(rid, [])
            m = meta.get(rid, {})
            top, _ = _top_pairings_for_ref(rid, pair_out, meta, limit=6)
            summary["flavor_preview"] = {
                "reference_id": rid,
                "recipe_count": len(pcts),
                "avg_pct": _round_pct(sum(pcts) / len(pcts)) if pcts else None,
                "min_pct": _round_pct(min(pcts)) if pcts else None,
                "max_pct": _round_pct(max(pcts)) if pcts else None,
                "median_pct": _median(pcts),
                "likely_recipe_role": compute_likely_recipe_role(
                    avg_pct=float(sum(pcts) / len(pcts)) if pcts else 0.0,
                    resolved_role=m.get("resolved_role", "") or "",
                    resolved_profile=m.get("resolved_profile", "") or "",
                )
                if pcts
                else None,
                "top_pairings_preview": top,
            }
        return summary

    db = get_db()
    if reference_id is not None:
        rid = int(reference_id)
        if rid not in usage_pcts:
            summary["warning"] = "Flavor not present in analytics source set; cleared stale rows if any."
            db.execute("DELETE FROM flavor_recipe_analytics WHERE reference_id = ?", (rid,))
            db.execute(
                "DELETE FROM flavor_pairings WHERE reference_id_a = ? OR reference_id_b = ?",
                (rid, rid),
            )
            db.commit()
            return summary

        if mode in ("all", "usage"):
            rows = _rows_for_per_flavor_analytics(usage_pcts, pair_out, meta)
            row = next((r for r in rows if r[0] == rid), None)
            db.execute("DELETE FROM flavor_recipe_analytics WHERE reference_id = ?", (rid,))
            if row:
                db.execute(
                    """
                    INSERT INTO flavor_recipe_analytics (
                        reference_id, recipe_count, avg_pct, min_pct, max_pct, median_pct,
                        likely_recipe_role, top_pairings_json, pairing_insights_json,
                        analytics_version, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
        if mode in ("all", "pairings"):
            db.execute(
                "DELETE FROM flavor_pairings WHERE reference_id_a = ? OR reference_id_b = ?",
                (rid, rid),
            )
            pair_rows = _rows_for_pairings(pair_out, max_pair)
            for pr in pair_rows:
                if pr[0] == rid or pr[1] == rid:
                    db.execute(
                        """
                        INSERT INTO flavor_pairings (
                            reference_id_a, reference_id_b, recipe_count,
                            avg_pct_a, avg_pct_b, pairing_strength, relationship_type,
                            pairing_reason, updated_at, analytics_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        pr,
                    )
        db.commit()
        return summary

    # Full recompute
    if mode == "all":
        db.execute("DELETE FROM flavor_recipe_analytics")
        db.execute("DELETE FROM flavor_pairings")
    elif mode == "usage":
        db.execute("DELETE FROM flavor_recipe_analytics")
    elif mode == "pairings":
        db.execute("DELETE FROM flavor_pairings")

    if mode in ("all", "usage"):
        for row in _rows_for_per_flavor_analytics(usage_pcts, pair_out, meta):
            db.execute(
                """
                INSERT INTO flavor_recipe_analytics (
                    reference_id, recipe_count, avg_pct, min_pct, max_pct, median_pct,
                    likely_recipe_role, top_pairings_json, pairing_insights_json,
                    analytics_version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
    if mode in ("all", "pairings"):
        for pr in _rows_for_pairings(pair_out, max_pair):
            db.execute(
                """
                INSERT INTO flavor_pairings (
                    reference_id_a, reference_id_b, recipe_count,
                    avg_pct_a, avg_pct_b, pairing_strength, relationship_type,
                    pairing_reason, updated_at, analytics_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                pr,
            )

    db.commit()
    return summary


def get_detail_attachment(reference_id: int) -> Optional[Dict[str, Any]]:
    """For API / flavor detail: summary + structured pair list from DB."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM flavor_recipe_analytics WHERE reference_id = ?",
        (int(reference_id),),
    ).fetchone()
    if not row:
        return None
    d = dict(row)

    def _parse_j(raw: Any, default: Any) -> Any:
        if not raw:
            return default
        if isinstance(raw, (list, dict)):
            return raw
        try:
            return json.loads(str(raw))
        except json.JSONDecodeError:
            return default

    d["top_pairings"] = _parse_j(d.get("top_pairings_json"), [])
    d["pairing_insights"] = _parse_j(d.get("pairing_insights_json"), [])

    rid = int(reference_id)
    pairs = db.execute(
        """
        SELECT fp.recipe_count, fp.pairing_strength, fp.relationship_type, fp.pairing_reason,
               CASE WHEN fp.reference_id_a = ? THEN fp.reference_id_b ELSE fp.reference_id_a END AS partner_id,
               CASE WHEN fp.reference_id_a = ? THEN fp.avg_pct_b ELSE fp.avg_pct_a END AS partner_avg_pct,
               fr.brand AS partner_brand, fr.name AS partner_name
        FROM flavor_pairings fp
        JOIN flavor_reference fr ON fr.id = (
            CASE WHEN fp.reference_id_a = ? THEN fp.reference_id_b ELSE fp.reference_id_a END
        )
        WHERE fp.reference_id_a = ? OR fp.reference_id_b = ?
        ORDER BY fp.recipe_count DESC
        LIMIT 12
        """,
        (rid, rid, rid, rid, rid),
    ).fetchall()
    d["pairings_detail"] = [dict(p) for p in pairs]
    return d
