"""Read-only, aggregate-backed experimental recipe suggestions."""
import math
import re
from db import get_db
from config.catalogue_identity import canonical_item
from services.what_can_i_make_service import fetch_stash_reference_qty_ml


def build_context(*, user_id, role, username, main_id=None):
    stock, _ = fetch_stash_reference_qty_ml(user_id=user_id, role=role, username=username)
    db = get_db()
    rows = db.execute('''SELECT fr.id, fr.brand, fr.name, a.avg_pct, a.recipe_count,
        a.likely_recipe_role FROM flavor_reference fr
        LEFT JOIN flavor_recipe_analytics a ON a.reference_id=fr.id''').fetchall()
    choices, seen = [], set()
    for raw in rows:
        row = dict(raw)
        rid = row['id']
        if stock.get(rid, 0) <= 0:
            continue
        key = canonical_item(row['brand'], row['name'])
        if key in seen:
            continue
        seen.add(key)
        avg = row['avg_pct']
        valid = avg is not None and math.isfinite(float(avg)) and 0 < float(avg) < 100
        text = str(row['name']).lower()
        row.update(quantity_ml=round(stock[rid], 3), percent=round(float(avg), 2) if valid else None,
                   kind='cooling' if re.search(r'koolada|ws[ -]?(?:23|3)\b|coolant|polar blast', text)
                   else 'sweetener' if re.search(r'sweetener|super sweet|sucralose|erythritol', text) else 'flavour')
        choices.append(row)
    choices.sort(key=lambda r: (r['kind'] != 'flavour', str(r['name']).lower()))
    main = next((r for r in choices if r['id'] == main_id and r['kind'] == 'flavour'), None)
    pairs = {}
    if main:
        for p in db.execute('''SELECT reference_id_a,reference_id_b,recipe_count FROM flavor_pairings
             WHERE reference_id_a=? OR reference_id_b=?''', (main_id, main_id)):
            pairs[p['reference_id_b'] if p['reference_id_a'] == main_id else p['reference_id_a']] = p['recipe_count']
    for row in choices:
        row['pair_count'] = pairs.get(row['id'], 0)
    if main:
        choices.sort(key=lambda r: (-r['pair_count'], str(r['name']).lower()))
    return dict(choices=choices, main=main)


def draft_rows(context, selected, percentages):
    """Revalidate ownership, finite percentages and 10 ml stock before opening builder."""
    available = {r['id']: r for r in context['choices']}
    if not 1 <= len(selected) <= 4 or len(set(selected)) != len(selected):
        raise ValueError('Choose one to four different flavours, including optional finishing touches.')
    result = []
    for rid, raw in zip(selected, percentages):
        row = available.get(rid)
        pct = float(raw)
        if not row or not math.isfinite(pct) or pct <= 0 or pct >= 100:
            raise ValueError('Each selected flavour needs a positive percentage and must be in your stash.')
        if pct / 10 > row['quantity_ml'] + 0.000001:
            raise ValueError(f"Not enough {row['name']} for a 10 ml test at that percentage.")
        result.append(dict(ingredient_id=rid, percent=pct, name=row['name'], brand=row['brand']))
    if len(result) != len(selected) or sum(r['percent'] for r in result) >= 100:
        raise ValueError('Check the percentages: the recipe needs room for its base liquid.')
    return result
