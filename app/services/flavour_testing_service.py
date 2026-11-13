"""Personal tasting experiments; observations never become automatic recommendations."""
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from db import get_db

KINDS = {'single':'Single flavour','recipe':'In a recipe'}
STRENGTHS = {'unsure':'Not sure yet','weak':'Too weak','balanced':'About right','strong':'Too strong'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS flavour_tasting_tests (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 flavour_id INTEGER NOT NULL REFERENCES flavours(id), label TEXT NOT NULL DEFAULT '',
 kind TEXT NOT NULL, percentage REAL NOT NULL, pg_percent INTEGER,
 device TEXT NOT NULL DEFAULT '', power_watts REAL, shared_note_id INTEGER, shared_digest TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_flavour_tests_owner ON flavour_tasting_tests(user_id,flavour_id);
CREATE TABLE IF NOT EXISTS flavour_tasting_observations (
 id INTEGER PRIMARY KEY, test_id INTEGER NOT NULL REFERENCES flavour_tasting_tests(id) ON DELETE CASCADE,
 age_days INTEGER NOT NULL, strength TEXT NOT NULL, impressions TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_flavour_tastings_test ON flavour_tasting_observations(test_id);
'''


def number(raw, label, minimum, maximum, optional=False, integer=False):
    raw = str('' if raw is None else raw).strip()
    if not raw and optional: return None
    try:
        value = Decimal(raw)
        if not value.is_finite() or not minimum <= value <= maximum or (integer and value != value.to_integral()):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise ValueError(f'{label} must be {"a whole number " if integer else ""}from {minimum} to {maximum}.') from None
    return int(value) if integer else float(value)


def observation_values(form):
    age = number(form.get('age_days'), 'Steep age in days',0,3650,integer=True)
    strength = form.get('strength','unsure')
    impressions = form.get('impressions','').strip()
    if strength not in STRENGTHS: raise ValueError('Choose a strength from the menu.')
    if len(impressions)>5000: raise ValueError('Keep tasting impressions to 5,000 characters.')
    return age,strength,impressions


def create(user_id,flavour_id,form):
    kind = form.get('kind','single')
    if kind not in KINDS: raise ValueError('Choose single flavour or recipe testing.')
    label = form.get('label','').strip()
    device = form.get('device','').strip()
    if len(label)>100 or len(device)>160: raise ValueError('Use at most 100 characters for a test name and 160 for the device.')
    percentage = number(form.get('percentage'),'Flavour percentage',.001,100)
    pg = number(form.get('pg_percent'),'PG percentage',0,100,optional=True,integer=True)
    watts = number(form.get('power_watts'),'Power in watts',.1,1000,optional=True)
    values = observation_values(form)
    db=get_db()
    try:
        test_id=db.execute('INSERT INTO flavour_tasting_tests(user_id,flavour_id,label,kind,percentage,pg_percent,device,power_watts) VALUES(?,?,?,?,?,?,?,?)',(user_id,flavour_id,label,kind,percentage,pg,device,watts)).lastrowid
        db.execute('INSERT INTO flavour_tasting_observations(test_id,age_days,strength,impressions) VALUES(?,?,?,?)',(test_id,*values))
        db.commit()
        return test_id
    except Exception:
        db.rollback();raise


def owned(user_id,flavour_id,test_id):
    return get_db().execute('SELECT * FROM flavour_tasting_tests WHERE id=? AND user_id=? AND flavour_id=?',(test_id,user_id,flavour_id)).fetchone()


def observe(user_id,flavour_id,test_id,form):
    if not owned(user_id,flavour_id,test_id): raise LookupError('Test not found.')
    values=observation_values(form)
    db=get_db()
    observation_id = form.get('observation_id','')
    if observation_id:
        cursor=db.execute('UPDATE flavour_tasting_observations SET age_days=?,strength=?,impressions=? WHERE id=? AND test_id=?',(*values,observation_id,test_id))
        if cursor.rowcount != 1:
            db.rollback();raise LookupError('Tasting not found.')
    else:
        db.execute('INSERT INTO flavour_tasting_observations(test_id,age_days,strength,impressions) VALUES(?,?,?,?)',(test_id,*values))
    db.commit()


def tests_for(user_id,flavour_id):
    db=get_db()
    rows=[dict(r) for r in db.execute('SELECT * FROM flavour_tasting_tests WHERE user_id=? AND flavour_id=? ORDER BY id DESC',(user_id,flavour_id))]
    if not rows:return []
    observations={r['id']:[] for r in rows}
    placeholders=','.join('?' for _ in rows)
    for item in db.execute(f'SELECT * FROM flavour_tasting_observations WHERE test_id IN ({placeholders}) ORDER BY age_days,id',list(observations)):
        observations[item['test_id']].append(dict(item))
    for row in rows:
        row['observations']=observations[row['id']]
        # Public copy intentionally omits private test labels and device identifiers.
        text=f"Tasting record: {KINDS[row['kind']]} at {row['percentage']:g}%."
        if row['pg_percent'] is not None:text+=f" {row['pg_percent']}/{100-row['pg_percent']} PG/VG."
        for item in row['observations']:
            text+=f"\nDay {item['age_days']}: {STRENGTHS[item['strength']]}. {item['impressions']}"
        row['share_preview']=text
        row['share_digest']=sha256(text.encode()).hexdigest()
    return rows


def share(user_id,flavour_id,test_id,expected):
    db=get_db()
    db.execute('BEGIN IMMEDIATE')
    try:
        row=owned(user_id,flavour_id,test_id)
        if not row: raise LookupError('Test not found.')
        test=next(t for t in tests_for(user_id,flavour_id) if t['id']==test_id)
        if test['share_digest'] != expected: raise ValueError('The tasting record changed. Review its public preview before sharing.')
        if row['shared_note_id'] and row['shared_digest'] == expected:
            db.commit();return row['shared_note_id']
        if len(test['share_preview'])>10000:raise ValueError('This record is too long to share as one community note.')
        note_id=db.execute('INSERT INTO flavour_notes(flavour_id,author_user_id,note) VALUES(?,?,?)',(flavour_id,user_id,test['share_preview'])).lastrowid
        db.execute('UPDATE flavour_tasting_tests SET shared_note_id=?,shared_digest=? WHERE id=?',(note_id,expected,test_id))
        db.commit();return note_id
    except Exception:
        db.rollback();raise
