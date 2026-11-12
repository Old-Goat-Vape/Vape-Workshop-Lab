"""Versioned recipe presentation and private per-user notebooks."""
import io
import json
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError
from db import get_db

PROFILES = ('Fruit', 'Berry', 'Citrus', 'Tropical', 'Dessert', 'Bakery', 'Custard', 'Cream', 'Beverage', 'Candy', 'Tobacco', 'Menthol', 'Nut', 'Spice')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS recipe_stories (
 version_id INTEGER PRIMARY KEY REFERENCES recipe_versions(id) ON DELETE CASCADE,
 short_description TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
 profiles_json TEXT NOT NULL DEFAULT '[]', steep_days INTEGER CHECK(steep_days IS NULL OR steep_days BETWEEN 0 AND 365),
 image_data BLOB
);
CREATE TABLE IF NOT EXISTS recipe_profile_backfills (
 name TEXT PRIMARY KEY, assigned_count INTEGER NOT NULL,
 batch_id TEXT, completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS recipe_profile_audit (
 id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL, recipe_id INTEGER NOT NULL,
 version_id INTEGER NOT NULL, actor_id INTEGER NOT NULL,
 previous_json TEXT NOT NULL, assigned_json TEXT NOT NULL, evidence_json TEXT NOT NULL,
 rule_version TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 undone INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_recipe_profile_audit_batch ON recipe_profile_audit(batch_id);
CREATE TABLE IF NOT EXISTS recipe_private_notebooks (
 recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 notes TEXT NOT NULL DEFAULT '', PRIMARY KEY(recipe_id,user_id)
);
'''

def read(version_id):
    row = get_db().execute('SELECT short_description,description,profiles_json,steep_days,image_data IS NOT NULL AS has_image FROM recipe_stories WHERE version_id=?', (version_id,)).fetchone()
    if not row:
        return dict(short_description='', description='', profiles=[], main_profile='', steep_days=None, has_image=False)
    result = dict(row)
    result['profiles'] = json.loads(result.pop('profiles_json'))
    result['main_profile'] = result['profiles'][0] if result['profiles'] else ''
    return result


def private_notes(recipe_id, user_id):
    row = get_db().execute('SELECT notes FROM recipe_private_notebooks WHERE recipe_id=? AND user_id=?', (recipe_id,user_id)).fetchone()
    return row['notes'] if row else ''


def parse(form):
    values = {k: (form.get(k) or '').strip() for k in ('short_description','description','private_notes','steep_days','main_profile')}
    values['profiles'] = form.getlist('profiles')
    errors = {}
    for key, limit in [('short_description',180),('description',10000),('private_notes',10000)]:
        if len(values[key]) > limit:
            errors[key] = f'{key.replace("_", " ").capitalize()} must be {limit} characters or fewer.'
    if len(values['profiles']) > len(PROFILES) or any(p not in PROFILES for p in values['profiles']):
        errors['profiles'] = 'Choose flavour profiles from the available options.'
    main = values['main_profile']
    if main and main not in values['profiles']:
        errors['main_profile'] = 'Choose a main profile from the profiles selected for this recipe.'
    elif main:
        values['profiles'] = [main] + [p for p in values['profiles'] if p != main]
    raw = values['steep_days']
    if raw and (len(raw) > 3 or not raw.isascii() or not raw.isdigit() or not 0 <= int(raw) <= 365):
        errors['steep_days'] = 'Steep time must be a whole number from 0 to 365 days, or blank for Not set.'
    return values, errors


def prepare_image(upload):
    if not upload or not upload.filename:
        return None
    data = upload.read(32 * 1024 * 1024 + 1)
    if len(data) > 32 * 1024 * 1024:
        raise ValueError('The original photo exceeds 32 MB. Try a browser that supports automatic photo resizing.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                if im.format not in {'JPEG','PNG','WEBP'} or im.width * im.height > 80000000:
                    raise ValueError('Use a JPG, PNG or WebP photo. This original exceeds the server image limit; use browser resizing for very large photos.')
                im.draft('RGB', (2400,2400))
                im = ImageOps.exif_transpose(im).convert('RGB')
                im.thumbnail((1600,1600))
                im = ImageOps.fit(im, (1200,675))
                output = io.BytesIO()
                im.save(output, format='WEBP', quality=82, method=4)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('This image could not be read. Choose a JPG, PNG or WebP photo.') from None


def save(*, version_id, recipe_id, user_id, values, image=None, previous_version_id=None, remove_image=False):
    db = get_db()
    if image is None and previous_version_id and not remove_image:
        row = db.execute('SELECT image_data FROM recipe_stories WHERE version_id=?', (previous_version_id,)).fetchone()
        image = row['image_data'] if row else None
    db.execute('INSERT INTO recipe_stories(version_id,short_description,description,profiles_json,steep_days,image_data) VALUES(?,?,?,?,?,?)',
        (version_id,values['short_description'],values['description'],json.dumps(list(dict.fromkeys(values['profiles']))),int(values['steep_days']) if values['steep_days'] != '' else None,image))
    db.execute('INSERT INTO recipe_private_notebooks(recipe_id,user_id,notes) VALUES(?,?,?) ON CONFLICT(recipe_id,user_id) DO UPDATE SET notes=excluded.notes', (recipe_id,user_id,values['private_notes']))
