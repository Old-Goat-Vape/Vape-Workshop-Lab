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

RULE_VERSION = '2'
# Whole words/phrases avoid e.g. nut in coconut or ice in licorice.
WORDS = {
 'Dessert': 'cheesecake|ice cream|pudding|meringue|tiramisu|trifle|brulee|dessert|milk tart|soft serve|sundae|gelato|parfait|mousse|sorbet|sherbet|kulfi|flan|panna cotta|rice pudding',
 'Bakery': 'cake|cupcake|biscuit|cookie|shortbread|bread|toast|waffle|pancake|donut|doughnut|pastry|croissant|muffin|pie|tart|cereal|grahams?|crust|crumble|crumbleberry|cobbler|cronut|funfetti|zeppola|wafer|danish|gingerbread|macaroon|macaron|biscotti|biskit|biscotto|churro|funnel cake|dough|flapjack|streusel|tres leches|tert|panettone|panetone|baklava|scone|cinnamon roll|baked oats|rice crunchies|crispy treats|honey puffs|popcorn|kettle corn|sprinkles|oatmeal|s mores|smores',
 'Custard': 'custard',
 'Beverage': 'cola|coffee|cappuccino|espresso|latte|tea|lemonade|soda|cocktail|mojito|milkshake|smoothie|root beer|energy drink|limeade|macchiato|mocha|frappe|frappuccino|bourbon|vodka|gin|rum|whisky|whiskey|brandy|absinthe|eggnog|beer|wine|sangria|tequila|amaretto|liqueur|leche merengada|hops|tonic|dew|juice',
 'Candy': 'candy|gummy|gummies|jelly bean|bubble gum|bubblegum|taffy|marshmallow|cotton candy|toffee|chocolate|cocoa|cacao|nougat|honeycomb|fudge|bonbon|bon bon|licorice|liquorice|jelly|jellies|praline|molasses|golden syrup|maple syrup|honey',
 'Tobacco': 'tobacco|bacco|cigar|cigarette|ry4|ry 4|latakia|burley|virginia|baccy|kentucky blend|soho|cured|oriental tobacco|red burley|black fire',
 'Menthol': 'menthol|mint|peppermint|spearmint|koolada|ws 23|ws23|coolant|ice|iced|icy|freeze|frozen|polar blast|creme de menthe',
 'Cream': 'cream|milk|yogurt|yoghurt|butterscotch|caramel|vanilla|buttercream|frosting|dulce de leche|butter|buttermilk|budino|creme|malty|malted milk',
 'Nut': 'hazelnut|almond|peanut|pecan|walnut|pistachio|macadamia|cashew|peanuts|almonds|hazelnuts|walnuts|pistachios|beer nuts|nutty|marzipan',
 'Spice': 'cinnamon|clove|ginger|anise|cardamom|nutmeg|allspice|pumpkin spice|aniseed|basil',
 'Berry': 'strawberry|strawberries|raspberry|raspberries|blueberry|blueberries|blackberry|blackberries|blackcurrant|cranberry|boysenberry|berry|berries|bilberry|huckleberry|black currant|red currant|elderberry|blue raz|blue razz',
 'Citrus': 'lemon|lime|orange|grapefruit|mandarin|tangerine|citrus|yuzu|citron|lemons|limes|oranges|buddha s hand',
 'Tropical': 'mango|pineapple|coconut|guava|papaya|passion fruit|passionfruit|lychee|dragon fruit|dragonfruit|jackfruit|litchi|rambutan|mangosteen|mangoes',
 'Fruit': 'apple|pear|peach|apricot|nectarine|plum|cherry|grape|banana|watermelon|melon|kiwi|fig|fruit|cantaloupe|honeydew|cherries|black cherry|pomegranate|raisin|grapes|peaches|pears|apples|date|dates',
}
PATTERNS = {p: re.compile(r'\b(?:' + words + r')\b') for p, words in WORDS.items()}
SUPPORT = re.compile(r'\b(?:sweetener|sucralose|super sweet|ethyl maltol|maltol|stevia|sweeten|sweetness|brown sugar|powdered sugar|acetyl pyrazine|saline|citric acid|malic acid|tartaric acid|sour|pop it enhancer)\b')
FRUITS = {'Fruit', 'Berry', 'Citrus', 'Tropical'}


def normalize(value):
    text = unicodedata.normalize('NFKD', html.unescape(str(value or ''))).lower()
    return re.sub(r'[^a-z0-9]+', ' ', re.sub(r'<[^>]*>', ' ', text)).strip()


def matches(value):
    text = normalize(value)
    found = [p for p, pattern in PATTERNS.items() if pattern.search(text)]
    if re.search(r'\b(?:ice cream|iced cake|iced cookie|frosted cake)\b', text):
        found = [p for p in found if p != 'Menthol']
    if 'Nut' in found and re.search(r'\b(?:butter|praline|beer nuts)\b', text):
        found = [p for p in found if p not in {'Cream','Beverage'}]
    return found


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
            primary = 'Tobacco' if 'Tobacco' in found else found[0]
            scores[primary] += amount
            evidence.append(f"{item['name']} ({amount:g}%) → {primary}")
    title_text = normalize(re.split(r'\s+(?:by|reviewed by|adapted by)\s+|--', str(name or ''), maxsplit=1, flags=re.I)[0])
    title = matches(title_text)
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
    share = scores.get(main, 0) / total if total else 0
    named = main in title
    family_share = sum(scores.get(p, 0) for p in FRUITS) / total if total else 0
    conflicting = [p for p in title if p != main and p not in scores and p not in FRUITS]
    # A broader second pass can infer coherent families without pretending to taste them.
    desc = matches(description)
    tag_conflict = any(p not in scores and p != main and p not in FRUITS for p in explicit)
    tobacco_mix = 'Tobacco' in title and any(p in title for p in ('Dessert','Bakery','Custard','Candy'))
    dessert_family = {'Dessert','Bakery','Custard','Cream'}
    dessert_share = sum(scores.get(p,0) for p in dessert_family) / total if total else 0
    ranked = sorted(scores.values(), reverse=True)
    lead = (ranked[0] - (ranked[1] if len(ranked)>1 else 0)) / total if total else 0
    high = coverage >= .8 and not conflicting and (named or (share >= .8 and not title))
    if main in FRUITS and family_share >= .85 and coverage >= .85 and not any(p not in FRUITS for p in title):
        high = True
    # Unnamed bakery/cream/custard formulas can still clearly belong to that family.
    confection_share = dessert_share + (scores.get('Candy',0)+scores.get('Nut',0)) / total if total else 0
    if not title and dessert_share >= .25 and confection_share >= .9 and coverage >= .9 and not tag_conflict:
        main = 'Dessert'
        evidence.insert(0, 'Second pass: mixed confection, cream and bakery ingredients support a broad Dessert profile.')
    coherent = (main in dessert_family and dessert_share >= .85 and coverage >= .85) or (main == 'Dessert' and not title and confection_share >= .9 and dessert_share >= .25)
    dominant = coverage >= .85 and share >= .55 and lead >= .15
    if not conflicting and not tag_conflict and not tobacco_mix and (coherent or dominant):
        high = True
        evidence.insert(0, 'Second pass: coherent ingredient family or a clear lead category.')
    # Name agreement can support a modestly incomplete ingredient vocabulary.
    if named and coverage >= .65 and not conflicting and not tag_conflict and not tobacco_mix and share >= .25:
        high = True
        evidence.insert(0, 'Second pass: recipe identity is supported by recognised ingredients.')
    if tag_conflict or tobacco_mix:
        high = False
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
        batch = _write_rows(db, rows, actor_id, main)
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


def _write_rows(db, rows, actor_id, main=None):
    """Caller owns the transaction; shared by reviewed application and one-time backfill."""
    batch = uuid.uuid4().hex if rows else None
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
    return batch


SECOND_PASS_KEY = 'recipe-profile-families-2026-11-12'


def backfill_once():
    """Owner-requested data migration. Undo remains final across subsequent restarts."""
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    try:
        done = db.execute('SELECT assigned_count,batch_id FROM recipe_profile_backfills WHERE name=?', (SECOND_PASS_KEY,)).fetchone()
        if done:
            db.commit()
            return dict(already_applied=True, count=done['assigned_count'], batch_id=done['batch_id'])
        rows = [r for r in preview() if r['confidence'] == 'high']
        # actor 0 identifies this system data migration; human Lab actions retain their user ID.
        batch = _write_rows(db, rows, 0)
        db.execute('INSERT INTO recipe_profile_backfills(name,assigned_count,batch_id) VALUES(?,?,?)', (SECOND_PASS_KEY,len(rows),batch))
        db.commit()
        return dict(already_applied=False, count=len(rows), batch_id=batch)
    except Exception:
        db.rollback()
        raise
