"""Reversible abandonment of personal, incomplete guided mixes."""
from db import get_db


def set_abandoned(*, session_id: int, user_id: int, abandoned: bool) -> None:
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    try:
        row = db.execute(
            'SELECT * FROM mix_sessions WHERE id=? AND user_id=?',
            (session_id, user_id),
        ).fetchone()
        if not row:
            raise LookupError('Mix session not found.')
        production = db.execute(
            'SELECT 1 FROM production_job_batches WHERE guided_mix_session_id=? LIMIT 1',
            (session_id,),
        ).fetchone()
        if row['production_job_id'] or production:
            raise ValueError('Use the production job controls to cancel a production batch.')
        if str(row['mode']).lower() != 'guided' or row['completed_at']:
            raise ValueError('Only incomplete guided mixes can be abandoned.')
        if not db.execute(
            'SELECT 1 FROM mix_session_steps WHERE mix_session_id=? AND completed_at IS NULL LIMIT 1',
            (session_id,),
        ).fetchone():
            raise ValueError('This mix has no incomplete steps.')
        if abandoned:
            db.execute(
                "UPDATE mix_sessions SET abandoned_at=COALESCE(abandoned_at,datetime('now')) WHERE id=? AND user_id=?",
                (session_id,user_id),
            )
        else:
            db.execute('UPDATE mix_sessions SET abandoned_at=NULL WHERE id=? AND user_id=?',(session_id,user_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
