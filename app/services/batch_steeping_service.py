"""Private batch steep plans: frozen recipe guidance, explicit reminders and tastings."""
from datetime import datetime, timedelta, timezone
import math
from db import get_db

UTC=timezone.utc
SCHEMA="""CREATE TABLE IF NOT EXISTS batch_steeping (
 session_id INTEGER PRIMARY KEY REFERENCES mix_sessions(id) ON DELETE CASCADE,
 source_days INTEGER CHECK(source_days IS NULL OR source_days BETWEEN 0 AND 365),
 steep_days INTEGER CHECK(steep_days IS NULL OR steep_days BETWEEN 0 AND 365),
 mixed_at TEXT,
 reminder_enabled INTEGER NOT NULL DEFAULT 0 CHECK(reminder_enabled IN (0,1)),
 acknowledged_at TEXT,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS batch_tastings (
 id INTEGER PRIMARY KEY,session_id INTEGER NOT NULL REFERENCES batch_steeping(session_id) ON DELETE CASCADE,
 tasted_at TEXT NOT NULL,age_days INTEGER NOT NULL,
 readiness TEXT NOT NULL CHECK(readiness IN ('not_sure','needs_longer','about_right','past_best')),
 score REAL CHECK(score IS NULL OR score BETWEEN 0 AND 10),notes TEXT NOT NULL
);"""
READINESS={'not_sure':'Not sure yet','needs_longer':'Needs longer','about_right':'About right','past_best':'Past its best'}


def stamp(now=None):
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec='seconds')


def parse_time(value):
    if not value:return None
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def snapshot(session_id=None,db=None,user_id=None):
    """Caller can include this insert in session creation's existing transaction."""
    db=db or get_db()
    sql="""INSERT OR IGNORE INTO batch_steeping(session_id,source_days,steep_days)
        SELECT ms.id,rs.steep_days,rs.steep_days FROM mix_sessions ms
        LEFT JOIN recipe_stories rs ON rs.version_id=ms.recipe_version_id"""
    where=[];args=[]
    if session_id is not None:where.append('ms.id=?');args.append(session_id)
    if user_id is not None:where.append('ms.user_id=?');args.append(user_id)
    db.execute(sql+(' WHERE '+' AND '.join(where) if where else ''),args)


def describe(row,now=None):
    row=dict(row);now=now or datetime.now(UTC)
    complete=not row.get('abandoned_at') and (
        row['mode']=='quick' or (row.get('completed_at') and not row['open_steps']))
    started=(row.get('completed_at') if row['mode']=='guided' else row.get('mixed_at')) if complete else None
    start=parse_time(started);days=row['steep_days']
    due=start+timedelta(days=days) if start is not None and days is not None else None
    ack=parse_time(row['acknowledged_at'])
    acknowledged=bool(ack and due and ack>=due)
    state=('not_mixed' if not start else 'not_set' if days is None else 'shake_vape' if days==0 else 'ready' if now>=due else 'steeping')
    row.update(state=state,state_label={'not_mixed':'Not mixed yet','not_set':'Not set','shake_vape':'Shake & vape','ready':'Ready to check','steeping':'Steeping'}[state],
        progress_percent=min(100,max(0,round((now-start).total_seconds()/(days*86400)*100))) if start and days else 100 if start and days==0 else 0,
        started_at=stamp(start) if start else None,ready_at=stamp(due) if due else None,
        days_remaining=max(0,math.ceil((due-now).total_seconds()/86400)) if due else None,
        reminder_due=bool(row['reminder_enabled'] and not acknowledged and due and now>=due),
        reminder_acknowledged=acknowledged,
        age_days=max(0,int((now-start).total_seconds()//86400)) if start else None)
    return row


SELECT="""SELECT b.*,ms.user_id,ms.mode,ms.created_at,ms.completed_at,ms.abandoned_at,
    ms.target_ml,ms.recipe_version_id,rv.version_number,r.name AS recipe_name,
    (SELECT COUNT(*) FROM mix_session_steps s WHERE s.mix_session_id=ms.id AND s.completed_at IS NULL) AS open_steps
    FROM batch_steeping b JOIN mix_sessions ms ON ms.id=b.session_id
    JOIN recipe_versions rv ON rv.id=ms.recipe_version_id JOIN recipes r ON r.id=rv.recipe_id"""


def fetch(session_id,user_id,now=None):
    db=get_db()
    row=db.execute(SELECT+' WHERE b.session_id=? AND ms.user_id=?',(session_id,user_id)).fetchone()
    if not row and db.execute('SELECT id FROM mix_sessions WHERE id=? AND user_id=?',(session_id,user_id)).fetchone():
        snapshot(session_id,db);db.commit()
        row=db.execute(SELECT+' WHERE b.session_id=? AND ms.user_id=?',(session_id,user_id)).fetchone()
    if not row:raise LookupError('Batch not found.')
    return describe(row,now)


SHELF=""" WHERE ms.user_id=? AND ms.abandoned_at IS NULL AND (
    (ms.mode='quick' AND b.mixed_at IS NOT NULL) OR
    (ms.mode='guided' AND ms.completed_at IS NOT NULL AND NOT EXISTS(
        SELECT 1 FROM mix_session_steps s WHERE s.mix_session_id=ms.id AND s.completed_at IS NULL)))"""


def list_for(user_id,now=None,page=1):
    db=get_db();snapshot(db=db,user_id=user_id);db.commit()
    rows=db.execute(SELECT+SHELF+""" ORDER BY CASE WHEN b.reminder_enabled=1
        AND (b.acknowledged_at IS NULL OR datetime(b.acknowledged_at)<
            datetime(CASE ms.mode WHEN 'guided' THEN ms.completed_at ELSE b.mixed_at END,'+'||b.steep_days||' days'))
        AND datetime(CASE ms.mode WHEN 'guided' THEN ms.completed_at ELSE b.mixed_at END,
            '+'||b.steep_days||' days')<=datetime('now') THEN 0 ELSE 1 END,
        ms.created_at DESC,ms.id DESC LIMIT 50 OFFSET ?""",(user_id,(max(1,page)-1)*50))
    return [describe(r,now) for r in rows]


def shelf_count(user_id):
    return get_db().execute('SELECT COUNT(*) FROM ('+SELECT+SHELF+')',(user_id,)).fetchone()[0]


def due_count(user_id):
    # Only opted-in plans are read for every header, rather than all mix history.
    rows=get_db().execute(SELECT+""" WHERE ms.user_id=? AND b.reminder_enabled=1
        AND b.steep_days IS NOT NULL AND ms.abandoned_at IS NULL
        AND (b.acknowledged_at IS NULL OR datetime(b.acknowledged_at) <
            datetime(CASE ms.mode WHEN 'guided' THEN ms.completed_at ELSE b.mixed_at END,'+'||b.steep_days||' days'))""",(user_id,))
    return sum(describe(r)['reminder_due'] for r in rows)


def save(session_id,user_id,raw_days,remind):
    row=fetch(session_id,user_id)
    raw=str(raw_days if raw_days is not None else '').strip()
    # Explicit zero is meaningful, unlike blank.
    if raw and (not raw.isdigit() or not 0<=int(raw)<=365):
        raise ValueError('Use a whole number from 0 to 365 days, or leave blank for Not set.')
    days=int(raw) if raw else None
    if remind and days is None:raise ValueError('Set a steep time before enabling a reminder.')
    changed=days!=row['steep_days'] or bool(remind)!=bool(row['reminder_enabled'])
    db=get_db()
    db.execute("""UPDATE batch_steeping SET steep_days=?,reminder_enabled=?,
        acknowledged_at=CASE WHEN ? THEN NULL ELSE acknowledged_at END,updated_at=CURRENT_TIMESTAMP
        WHERE session_id=?""",(days,int(bool(remind)),int(changed),session_id))
    db.commit()


def confirm_quick(session_id,user_id,now=None):
    row=fetch(session_id,user_id)
    if row['mode']!='quick' or row['abandoned_at']:raise ValueError('Only a quick mix needs bottle confirmation.')
    db=get_db()
    db.execute('UPDATE batch_steeping SET mixed_at=COALESCE(mixed_at,?),updated_at=CURRENT_TIMESTAMP WHERE session_id=?',(stamp(now),session_id))
    db.commit()


def acknowledge(session_id,user_id):
    row=fetch(session_id,user_id)
    if not row['reminder_due']:return
    db=get_db();db.execute('UPDATE batch_steeping SET acknowledged_at=? WHERE session_id=?',(stamp(),session_id));db.commit()


def add_tasting(session_id,user_id,notes,readiness,score='',now=None):
    now=now or datetime.now(UTC);row=fetch(session_id,user_id,now)
    if row['age_days'] is None:raise ValueError('Confirm this batch was mixed before recording a tasting.')
    notes=str(notes or '').strip()
    if not notes or len(notes)>4000:raise ValueError('Add tasting notes, up to 4,000 characters.')
    if readiness not in READINESS:raise ValueError('Choose how the batch tastes today.')
    try:value=float(score) if str(score if score is not None else '').strip() else None
    except (ValueError,TypeError):raise ValueError('Score must be from 0 to 10.')
    if value is not None and (not math.isfinite(value) or not 0<=value<=10):raise ValueError('Score must be from 0 to 10.')
    db=get_db();db.execute("""INSERT INTO batch_tastings(session_id,tasted_at,age_days,readiness,score,notes)
        VALUES(?,?,?,?,?,?)""",(session_id,stamp(now),row['age_days'],readiness,value,notes));db.commit()


def tastings(session_id,user_id):
    fetch(session_id,user_id)
    return [dict(r) for r in get_db().execute('SELECT * FROM batch_tastings WHERE session_id=? ORDER BY id DESC LIMIT 50',(session_id,))]
