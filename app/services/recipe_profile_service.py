"""Explainable, conservative recipe profile suggestions; never alter a formula."""
import hashlib
import html
import json
import re
import unicodedata
import uuid
from collections import defaultdict
from db import get_db
from services.recipe_story_service import PROFILES

RULE_VERSION = '1'
# Whole words/phrases avoid e.g. nut in coconut or ice in licorice.
WORDS = {
 'Dessert': 'cheesecake|ice cream|pudding|meringue|tiramisu|trifle|brulee|dessert|milk tart',
 'Bakery': 'cake|cupcake|biscuit|cookie|shortbread|bread|toast|waffle|pancake|donut|doughnut|pastry|croissant|muffin|pie|tart|cereal|graham|crust',
 'Custard': 'custard',
 'Beverage': 'cola|coffee|cappuccino|espresso|latte|tea|lemonade|soda|cocktail|mojito|milkshake|smoothie|root beer|energy drink',
 'Candy': 'candy|gummy|gummies|jelly bean|bubble gum|bubblegum|taffy|marshmallow|cotton candy|toffee',
 'Tobacco': 'tobacco|cigar|cigarette|ry4|ry 4|latakia|burley|virginia|baccy',
 'Menthol': 'menthol|mint|peppermint|spearmint|koolada|ws 23|ws23|coolant',
 'Cream': 'cream|milk|yogurt|yoghurt|butterscotch|caramel|vanilla',
 'Nut': 'hazelnut|almond|peanut|pecan|walnut|pistachio|macadamia|cashew',
 'Spice': 'cinnamon|clove|ginger|anise|cardamom|nutmeg',
 'Berry': 'strawberry|strawberries|raspberry|raspberries|blueberry|blueberries|blackberry|blackberries|blackcurrant|cranberry|boysenberry|berry|berries',
 'Citrus': 'lemon|lime|orange|grapefruit|mandarin|tangerine|citrus|yuzu',
 'Tropical': 'mango|pineapple|coconut|guava|papaya|passion fruit|passionfruit|lychee|dragon fruit|dragonfruit',
 'Fruit': 'apple|pear|peach|apricot|nectarine|plum|cherry|grape|banana|watermelon|melon|kiwi|fig|fruit',
}
PATTERNS = {p: re.compile(r'\b(?:' + words + r')\b') for p, words in WORDS.items()}
SUPPORT = re.compile(r'\b(?:sweetener|sucralose|super sweet|ethyl maltol|maltol|stevia|sweeten)\b')
FRUITS = {'Fruit', 'Berry', 'Citrus', 'Tropical'}


def normalize(value):
    text = unicodedata.normalize('NFKD', html.unescape(str(value or ''))).lower()
    return re.sub(r'[^a-z0-9]+', ' ', re.sub(r'<[^>]*>', ' ', text)).strip()


def matches(value):
    text = normalize(value)
    return [p for p, pattern in PATTERNS.items() if pattern.search(text)]


def suggest(name, description='', ingredients=(), tags=()):
    scores = defaultdict(float)
    evidence = []
    total = known = 0.0
    for item in ingredients:
        amount = max(0.0, float(item.get('amount_percent') or 0))
        if amount <= 0 or SUPPORT.search(normalize(item['name'])):
            continue
        total += amount
        found = matches(item['name'])
        if found:
            known += amount
            # A compound flavour is counted once; Cheesecake leads over Cream.
            scores[found[0]] += amount
            evidence.append(f"{item['name']} ({amount:g}%) → {found[0]}")
    title = matches(name)
    explicit = [p for p in WORDS if any(normalize(t) == p.lower() for t in tags)]
    secondary = sorted(scores, key=lambda p: (-scores[p], list(WORDS).index(p)))
    if not secondary:
        return dict(profiles=[], confidence='unknown', reasons=['No recognised ingredient evidence. Choose a profile after reviewing the recipe.'])
    # The named finished dish takes precedence over its largest fruit ingredient.
    support = {
        'Dessert': {'Dessert','Bakery','Cream','Custard'},
        'Bakery': {'Bakery'}, 'Custard': {'Custard'},
        'Beverage': {'Beverage'}, 'Candy': {'Candy'},
    }
    main = next((p for p in title if p in scores or (support.get(p, set()) & scores.keys())), None)
    if main is None:
        main = next((p for p in explicit if p in scores or (support.get(p, set()) & scores.keys())), secondary[0])
    coverage = known / total if total else 0
    share = scores[main] / total if total else 0
    named = main in title
    family_share = sum(scores[p] for p in FRUITS) / total if total else 0
    conflicting = [p for p in title if p != main and p not in scores and p not in FRUITS]
    # Descriptions provide review hints, never certainty (may discuss rejected ideas).
    desc = matches(description)
    high = coverage >= .8 and not conflicting and (named or (share >= .8 and not title))
    if any(p not in scores and p != main and p not in FRUITS for p in explicit):
        high = False
    if 'Tobacco' in title and any(p in title for p in ('Dessert','Bakery','Custard','Candy')):
        high = False
    if main in FRUITS and family_share >= .85 and coverage >= .85 and not conflicting and not any(p not in FRUITS for p in title):
        high = True
    if named:
        evidence.insert(0, f'Recipe name and ingredients agree on {main}.')
    elif main in explicit:
        evidence.insert(0, f'Recipe tag and ingredients agree on {main}.')
    else:
        evidence.insert(0, f'Ingredient evidence leads with {main}; check the intended finished flavour.')
    evidence.append(f'Recognised ingredient coverage: {coverage:.0%}.')
    if conflicting:
        evidence.append('Name needs review: ' + ', '.join(conflicting) + '.')
    if desc:
        evidence.append('Description mentions: ' + ', '.join(desc) + ' (context needs review).')
    selected = [main] + [p for p in secondary if p != main and scores[p] / total >= .1]
    return dict(profiles=selected[:4], confidence='high' if high else 'review', reasons=evidence)


def preview():
    db = get_db()
    ingredients = defaultdict(list)
    for row in db.execute('''SELECT ri.version_id,ri.ingredient_id,ri.amount_percent,COALESCE(fr.name,'') AS name
        FROM recipe_items ri JOIN recipes r ON r.current_version_id=ri.version_id AND r.active=1
        LEFT JOIN flavor_reference fr ON fr.id=ri.ingredient_id ORDER BY ri.id'''):
        ingredients[row['version_id']].append(dict(row))
    tags = defaultdict(list)
    for row in db.execute('SELECT recipe_id,tag FROM recipe_tags ORDER BY tag'):
        tags[row['recipe_id']].append(row['tag'])
    result = []
    for row in db.execute('''SELECT r.id,r.name,r.slug,r.visibility,r.current_version_id AS version_id,
        COALESCE(s.profiles_json,'[]') AS profiles_json,COALESCE(s.short_description,'') AS short_description,
        COALESCE(s.description,'') AS description,COALESCE(rv.notes,'') AS notes
        FROM recipes r JOIN recipe_versions rv ON rv.id=r.current_version_id
        LEFT JOIN recipe_stories s ON s.version_id=rv.id WHERE r.active=1 ORDER BY r.id'''):
        item = dict(row)
        # Preserve any non-empty/malformed saved metadata, even unrecognised legacy values.
        if item['profiles_json'].strip() not in ('', '[]', 'null'):
            continue
        item['ingredients'] = ingredients[item['version_id']]
        item['tags'] = tags[item['id']]
        item.update(suggest(item['name'], ' '.join([item['short_description'], item['description'], item['notes']]), item['ingredients'], item['tags']))
        item['fingerprint'] = digest(item)
        result.append(item)
    return result


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def selection_digest(rows):
    return digest([(r['id'], r['fingerprint']) for r in rows])


def apply(expected, actor_id, recipe_id=None, main=None):
    """Re-read under a write lock; stale previews make no changes."""
    if main and (not recipe_id or main not in PROFILES):
        raise ValueError('Choose an available recipe profile.')
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    try:
        rows = preview()
        rows = [r for r in rows if r['id'] == recipe_id] if recipe_id else [r for r in rows if r['confidence'] == 'high']
        if selection_digest(rows) != expected or not rows:
            raise ValueError('Recipes changed since this preview. Refresh and review the latest suggestions; nothing changed.')
        batch = uuid.uuid4().hex
        for row in rows:
            profiles = row['profiles']
            if main:
                profiles = [main] + [p for p in profiles if p != main]
            if not profiles:
                raise ValueError('Choose a profile for this recipe.')
            raw = json.dumps(profiles)
            db.execute('''INSERT INTO recipe_stories(version_id,profiles_json) VALUES(?,?)
                ON CONFLICT(version_id) DO UPDATE SET profiles_json=excluded.profiles_json''', (row['version_id'], raw))
            db.execute('''INSERT INTO recipe_profile_audit(batch_id,recipe_id,version_id,actor_id,previous_json,assigned_json,evidence_json,rule_version)
                VALUES(?,?,?,?,?,?,?,?)''', (batch,row['id'],row['version_id'],actor_id,row['profiles_json'],raw,json.dumps(row['reasons']),RULE_VERSION))
        db.commit()
        return len(rows), batch
    except Exception:
        db.rollback()
        raise


def undo(batch):
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    try:
        changed = 0
        for row in db.execute('SELECT * FROM recipe_profile_audit WHERE batch_id=? AND undone=0', (batch,)).fetchall():
            # Do not undo an owner's later edits or touch an old version after a new save.
            cursor = db.execute('''UPDATE recipe_stories SET profiles_json=? WHERE version_id=? AND profiles_json=?
                AND EXISTS(SELECT 1 FROM recipes WHERE id=? AND current_version_id=?)''',
                (row['previous_json'],row['version_id'],row['assigned_json'],row['recipe_id'],row['version_id']))
            changed += cursor.rowcount
            db.execute('UPDATE recipe_profile_audit SET undone=1 WHERE id=?', (row['id'],))
        db.commit()
        return changed
    except Exception:
        db.rollback()
        raise
