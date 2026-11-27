"""Non-destructive eligibility for imported recipe records.

Keep source records and reviews intact; exclude identifiable review collections
from recipe discovery, aggregates and mixing. Do not classify ordinary recipes
from broad words such as 'test' or 'cream'.
"""
import math
import re

_REFERENCE_TITLE = re.compile(
    r'\b(?:single[ -]flavou?r\s+tests?|s[ -]?f\s+tests?|'
    r'reviewed\s+by\s+session\s*drummer|reviews?\s+by\s+session\s*drummer|'
    r'new\s+.*(?:releases|lineup)\s+reviewed)\b', re.I)


def is_reference_collection(name):
    return bool(_REFERENCE_TITLE.search(str(name or '')))


def recipe_quality_sql(alias='r'):
    return (f"NOT (COALESCE({alias}.recipe_code,'') LIKE 'VCREF-%' "
            f"AND tvw_reference_collection({alias}.name)=1)")


def assert_mixable(db, recipe_id, version_id):
    recipe = db.execute('SELECT name,recipe_code FROM recipes WHERE id=?', (recipe_id,)).fetchone()
    if recipe and str(recipe['recipe_code'] or '').startswith('VCREF-') and is_reference_collection(recipe['name']):
        raise ValueError('This imported record is a review or testing collection, not a mixable recipe.')
    rows = db.execute('SELECT amount_percent FROM recipe_items WHERE version_id=?', (version_id,)).fetchall()
    amounts = [float(r['amount_percent'] or 0) for r in rows]
    if not amounts or any(not math.isfinite(x) or x <= 0 for x in amounts) or sum(amounts) > 100:
        raise ValueError('This recipe needs valid positive flavour percentages totalling no more than 100% before mixing.')
