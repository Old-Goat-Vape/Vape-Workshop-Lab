"""Presentation helpers and private notebooks; no changes to flavour identity."""
from db import get_db
from services.recipe_profile_service import matches
from services.recipe_artwork_service import choose_artwork

SCHEMA = '''CREATE TABLE IF NOT EXISTS flavour_private_notebooks (
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 flavour_id INTEGER NOT NULL REFERENCES flavours(id) ON DELETE CASCADE,
 notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(user_id,flavour_id)
);'''


def family(name):
    found = matches(name)
    return ('Tobacco' if 'Tobacco' in found else found[0]) if found else ''


def artwork(name):
    profile = family(name)
    return choose_artwork([profile] if profile else [])


def private_notes(user_id, flavour_id):
    row = get_db().execute('SELECT notes FROM flavour_private_notebooks WHERE user_id=? AND flavour_id=?',(user_id,flavour_id)).fetchone()
    return row['notes'] if row else ''


def save_private_notes(user_id, flavour_id, notes):
    if len(notes) > 10000:
        raise ValueError('Keep your notebook to 10,000 characters or fewer.')
    db = get_db()
    db.execute('''INSERT INTO flavour_private_notebooks(user_id,flavour_id,notes) VALUES(?,?,?)
        ON CONFLICT(user_id,flavour_id) DO UPDATE SET notes=excluded.notes,updated_at=CURRENT_TIMESTAMP''',(user_id,flavour_id,notes))
    db.commit()
