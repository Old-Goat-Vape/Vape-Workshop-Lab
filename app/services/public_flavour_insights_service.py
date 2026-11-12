"""Aggregate flavour insights from current TVW recipes at every visibility level.

Private recipe identities and formulations are never returned; only aggregates.
The separate Lab cache is not used because its current-version rules differ.
"""
from collections import Counter
from math import ceil
from statistics import mean, median
from db import get_db
from models import recipes_repo
from config.catalogue_identity import visible_flavour_sql


def _usage_cohort(ref_ids):
    """One observation per current recipe, at every visibility level."""
    ids = sorted(set(int(x) for x in ref_ids))
    placeholders = ','.join('?' for _ in ids)
    eligible = recipes_repo.tvw_recipe_analytics_predicate_sql('r')
    cohort = f"""
        SELECT r.id AS recipe_id, rv.id AS version_id, SUM(ri.amount_percent) AS pct
        FROM recipes r JOIN recipe_versions rv ON rv.id=r.current_version_id AND rv.recipe_id=r.id
        JOIN recipe_items ri ON ri.version_id=rv.id
        WHERE {eligible} AND ri.ingredient_id IN ({placeholders})
          AND ri.amount_percent>0 AND ri.amount_percent<=100
        GROUP BY r.id,rv.id HAVING SUM(ri.amount_percent)<=100
    """
    return ids, cohort


def builder_flavour_usage(reference_id):
    """Lightweight live aggregate for an exact, recipe-eligible ingredient ID.

    No name/fuzzy matching here: selecting CAP Vanilla Custard V1 cannot use V2
    or another manufacturer's statistics. Never return private recipe records.
    """
    from config.catalogue_identity import recipe_flavour_sql
    db = get_db()
    eligible = db.execute(f"""SELECT fr.id FROM flavor_reference fr
        WHERE fr.id=? AND COALESCE(fr.active,1)=1 AND COALESCE(fr.is_nicotine,0)=0
          AND {recipe_flavour_sql('fr')}
          AND (NOT EXISTS(SELECT 1 FROM flavours af WHERE af.source_flavor_ref_id=fr.id
                         AND COALESCE(af.merged_into_flavour_id,0)=0)
               OR EXISTS(SELECT 1 FROM flavours f WHERE f.source_flavor_ref_id=fr.id
                         AND {visible_flavour_sql('f')}))""", (reference_id,)).fetchone()
    if not eligible:
        return None
    ids, cohort = _usage_cohort([reference_id])
    row = db.execute(f"SELECT COUNT(*) AS count, AVG(pct) AS average FROM ({cohort})", ids).fetchone()
    return dict(ingredient_id=int(reference_id), count=int(row['count']), average=row['average'])


def flavour_insights(ref_ids):
    empty = dict(count=0, average=None, median=None, minimum=None, maximum=None, distribution=[], pairings=[])
    if not ref_ids:
        return empty
    ids, cohort = _usage_cohort(ref_ids)
    placeholders = ','.join('?' for _ in ids)
    db = get_db()
    values = [float(r['pct']) for r in db.execute(cohort,ids)]
    if not values: return empty
    # Equal-width bins, with an explicit sample count rather than implied confidence.
    step = max(1,ceil(max(values)/5))
    bins = Counter(min(4,int(v/step)) for v in values)
    distribution = [dict(low=i*step,high=(i+1)*step,count=bins[i],last=i==4) for i in range(5)]
    pairs = db.execute(f"""
        WITH cohort AS ({cohort})
        SELECT MIN(f.id) AS flavour_id, f.brand, f.canonical_name AS name,
               COUNT(DISTINCT c.recipe_id) AS recipe_count
        FROM cohort c JOIN recipe_items ri ON ri.version_id=c.version_id
        JOIN flavours f ON f.source_flavor_ref_id=ri.ingredient_id
        WHERE ri.ingredient_id NOT IN ({placeholders})
          AND ri.amount_percent>0 AND ri.amount_percent<=100
          AND {visible_flavour_sql('f')}
        GROUP BY COALESCE(tvw_catalogue_key(f.brand,f.canonical_name,f.category),CAST(f.id AS TEXT))
        ORDER BY recipe_count DESC, f.brand COLLATE NOCASE, name COLLATE NOCASE
        LIMIT 8
    """,(*ids,*ids)).fetchall()
    return dict(count=len(values),average=mean(values),median=median(values),minimum=min(values),maximum=max(values),
                distribution=distribution,pairings=[dict(r) for r in pairs])
