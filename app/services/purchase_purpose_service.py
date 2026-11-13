"""Private shopping intentions survive purchases, stash removal and flavour merges."""
import math
from urllib.parse import urlsplit
from db import get_db
from models import recipes_repo

SCHEMA = '''CREATE TABLE IF NOT EXISTS flavour_purchase_purposes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 flavour_id INTEGER NOT NULL REFERENCES flavours(id),
 recipe_id INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
 notes TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
 planned_ml REAL, status TEXT NOT NULL DEFAULT 'shopping'
 CHECK(status IN ('shopping','bought','archived')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_purchase_purpose_owner ON flavour_purchase_purposes(user_id,flavour_id,status);'''


def visible_recipe(recipe_id, user_id, old_goat=False):
    recipe = recipes_repo.fetch_recipe(recipe_id) if recipe_id else None
    return recipe if recipes_repo.can_view_recipe_in_tvw_community(
        recipe, viewer_user_id=user_id, viewer_is_old_goat=old_goat) else None


def entries(user_id, flavour_id=None, status=None, old_goat=False):
    sql = '''SELECT p.*,f.canonical_name AS flavour_name,f.brand AS brand FROM flavour_purchase_purposes p
        JOIN flavours f ON f.id=p.flavour_id WHERE p.user_id=?'''
    args = [user_id]
    if flavour_id:
        sql += ' AND p.flavour_id=?'; args.append(flavour_id)
    if status in ('shopping','bought','archived'):
        sql += ' AND p.status=?'; args.append(status)
    rows = [dict(r) for r in get_db().execute(sql+' ORDER BY p.updated_at DESC,p.id DESC',args).fetchall()]
    cache = {}
    for row in rows:
        rid = row['recipe_id']
        if rid not in cache:
            cache[rid] = visible_recipe(rid,user_id,old_goat)
        row['recipe'] = cache[rid]
    return rows


def save(user_id, flavour_id, form, old_goat=False, entry_id=None):
    db = get_db()
    if entry_id and not db.execute('SELECT id FROM flavour_purchase_purposes WHERE id=? AND user_id=? AND flavour_id=?',
                                  (entry_id,user_id,flavour_id)).fetchone():
        raise LookupError('Purchase reason not found.')
    notes = str(form.get('notes') or '').strip()
    source = str(form.get('source_url') or '').strip()
    if len(notes)>4000 or len(source)>2000:
        raise ValueError('Keep notes under 4,000 characters and source links under 2,000.')
    if source:
        try:
            parsed = urlsplit(source)
            valid = parsed.scheme in ('http','https') and parsed.hostname and not parsed.username and not parsed.password
            parsed.port
        except ValueError:
            valid = False
        if not valid or any(c.isspace() or ord(c)<32 for c in source):
            raise ValueError('Use a complete http:// or https:// source link.')
    try:
        recipe_id = int(form.get('recipe_id')) if form.get('recipe_id') else None
        amount = float(form.get('planned_ml')) if form.get('planned_ml') else None
    except (ValueError,TypeError):
        raise ValueError('Choose a recipe and enter a valid planned quantity.')
    if amount is not None and (not math.isfinite(amount) or amount<=0 or amount>100000):
        raise ValueError('Planned quantity must be greater than zero and at most 100,000 ml.')
    if recipe_id and not visible_recipe(recipe_id,user_id,old_goat):
        raise ValueError('That recipe is unavailable. Choose another recipe.')
    if not notes and not source and not recipe_id:
        raise ValueError('Add a reason, intended recipe or source link.')
    values = (recipe_id,notes,source,amount)
    if entry_id:
        db.execute('''UPDATE flavour_purchase_purposes SET recipe_id=?,notes=?,source_url=?,planned_ml=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?''',values+(entry_id,user_id))
    else:
        status = form.get('status','shopping')
        if status not in ('shopping','bought'):
            raise ValueError('Choose To buy or Already bought.')
        db.execute('''INSERT INTO flavour_purchase_purposes(recipe_id,notes,source_url,planned_ml,user_id,flavour_id,status)
            VALUES(?,?,?,?,?,?,?)''',values+(user_id,flavour_id,status))
    db.commit()


def change_status(user_id, entry_id, status):
    if status not in ('shopping','bought','archived'):
        raise ValueError('Unknown shopping status.')
    db = get_db()
    row = db.execute('SELECT flavour_id FROM flavour_purchase_purposes WHERE id=? AND user_id=?',(entry_id,user_id)).fetchone()
    if not row:
        raise LookupError('Purchase reason not found.')
    db.execute('UPDATE flavour_purchase_purposes SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?',
               (status,entry_id,user_id))
    db.commit()
    return row['flavour_id']


def stash_summaries(user_id):
    rows = get_db().execute("""SELECT flavour_id,notes FROM flavour_purchase_purposes
        WHERE user_id=? AND status!='archived' ORDER BY updated_at DESC,id DESC""",(user_id,)).fetchall()
    summaries = {}
    for row in rows:
        summary = summaries.setdefault(row['flavour_id'],dict(count=0,notes=''))
        summary['count'] += 1
        if not summary['notes'] and row['notes']: summary['notes'] = row['notes'][:120]
    return summaries
