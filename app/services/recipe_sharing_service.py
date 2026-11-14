"""Owner-controlled recipe access. Grants never change recipe visibility."""
from db import get_db
from models import users_repo

SCHEMA = """CREATE TABLE IF NOT EXISTS recipe_access (
 recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 can_edit INTEGER NOT NULL DEFAULT 0 CHECK(can_edit IN (0,1)),
 granted_by INTEGER NOT NULL REFERENCES users(id),
 granted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 revoked_at TEXT,
 PRIMARY KEY(recipe_id,user_id)
);
CREATE INDEX IF NOT EXISTS idx_recipe_access_recipient ON recipe_access(user_id,revoked_at,recipe_id);
CREATE TABLE IF NOT EXISTS recipe_access_audit (
 id INTEGER PRIMARY KEY,recipe_id INTEGER NOT NULL,user_id INTEGER NOT NULL,
 actor_id INTEGER NOT NULL,action TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);"""


def grant_sql(alias='r'):
    # User ID remains a bound parameter supplied by the caller.
    return f"""EXISTS(SELECT 1 FROM recipe_access ra WHERE ra.recipe_id={alias}.id
        AND ra.user_id=? AND ra.revoked_at IS NULL)"""


def access(recipe_id,user_id):
    if not user_id:return None
    row=get_db().execute("""SELECT can_edit FROM recipe_access
        WHERE recipe_id=? AND user_id=? AND revoked_at IS NULL""",(recipe_id,user_id)).fetchone()
    return dict(row) if row else None


def owner_recipe(recipe_id,actor_id):
    row=get_db().execute('SELECT * FROM recipes WHERE id=? AND created_by=?',(recipe_id,actor_id)).fetchone()
    return dict(row) if row else None


def history_user(recipe,user_id,old_goat=False):
    """A private recipe grant does not share someone else's personal batch logs."""
    if (str(recipe.get('visibility') or 'private').lower()!='public' and not old_goat
            and int(recipe.get('created_by') or 0)!=int(user_id or 0)):
        return user_id
    return None


def recipients(recipe_id,actor_id):
    if not owner_recipe(recipe_id,actor_id):raise PermissionError('Only the recipe owner manages access.')
    return [dict(r) for r in get_db().execute("""SELECT ra.user_id,u.username,ra.can_edit,ra.granted_at
        FROM recipe_access ra JOIN users u ON u.id=ra.user_id
        WHERE ra.recipe_id=? AND ra.revoked_at IS NULL ORDER BY u.username COLLATE NOCASE""",(recipe_id,))]


def search_users(query,actor_id):
    query=str(query or '').strip()[:80]
    if len(query)<2:return []
    escaped=query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
    rows=get_db().execute("""SELECT id,username FROM users WHERE id!=? AND username LIKE ? ESCAPE '\\'
        AND LOWER(COALESCE(status,''))!='disabled' AND COALESCE(is_banned,0)=0 AND COALESCE(is_suspended,0)=0
        AND LOWER(COALESCE(moderation_status,'active')) NOT IN ('banned','suspended')
        ORDER BY username COLLATE NOCASE LIMIT 10""",(actor_id,'%'+escaped+'%')).fetchall()
    return [dict(r) for r in rows]


def change(recipe_id,actor_id,recipient_id,permission):
    db=get_db()
    # Re-check ownership inside the write transaction too; a view permission is
    # never sufficient, even if the caller posts directly rather than using UI.
    db.execute('BEGIN IMMEDIATE')
    try:
        if not owner_recipe(recipe_id,actor_id):raise PermissionError('Only the recipe owner manages access.')
        if users_repo.user_is_muted(actor_id):raise PermissionError('Your account is muted.')
        if actor_id==recipient_id:raise ValueError('You already own this recipe.')
        if permission not in ('view','edit','revoke'):raise ValueError('Choose view and mix, edit, or revoke.')
        if permission=='revoke':
            changed=db.execute("""UPDATE recipe_access SET revoked_at=CURRENT_TIMESTAMP
                WHERE recipe_id=? AND user_id=? AND revoked_at IS NULL""",(recipe_id,recipient_id)).rowcount
        else:
            user=db.execute('SELECT id FROM users WHERE id=?',(recipient_id,)).fetchone()
            if not user or users_repo.tvw_account_access_violation(recipient_id):
                raise ValueError('Choose an available user from the search results.')
            db.execute("""INSERT INTO recipe_access(recipe_id,user_id,can_edit,granted_by)
                VALUES(?,?,?,?) ON CONFLICT(recipe_id,user_id) DO UPDATE SET
                can_edit=excluded.can_edit,granted_by=excluded.granted_by,
                granted_at=CURRENT_TIMESTAMP,revoked_at=NULL""",(recipe_id,recipient_id,int(permission=='edit'),actor_id))
            changed=1
        if changed:
            db.execute('INSERT INTO recipe_access_audit(recipe_id,user_id,actor_id,action) VALUES(?,?,?,?)',
                (recipe_id,recipient_id,actor_id,permission))
        db.commit()
    except Exception:
        db.rollback();raise
