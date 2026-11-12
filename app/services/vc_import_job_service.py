"""Persistent, single-consumer VC import queue. Network work never holds a DB lock."""
from __future__ import annotations

import hashlib
import threading
import time
import uuid

import requests
from db import get_db
from scrapers import vc_recipes_dtl_scraper as vc

_worker_lock = threading.Lock()
_workers = {}
LEASE_SECONDS = 600
MAX_ATTEMPTS = 3


def ensure_schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS vc_import_jobs (
            id INTEGER PRIMARY KEY, mode TEXT NOT NULL, source_user_id TEXT NOT NULL DEFAULT '',
            actor_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
            next_page INTEGER NOT NULL DEFAULT 1, pages_read INTEGER NOT NULL DEFAULT 0,
            discovery_done INTEGER NOT NULL DEFAULT 0, total_pages INTEGER, total_items INTEGER, last_page_hash TEXT NOT NULL DEFAULT '',
            lease_owner TEXT, lease_until REAL NOT NULL DEFAULT 0,
            retry_after REAL NOT NULL DEFAULT 0, discovery_attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vc_import_job_items (
            job_id INTEGER NOT NULL REFERENCES vc_import_jobs(id), recipe_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            retry_after REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(job_id, recipe_id)
        );
        CREATE TABLE IF NOT EXISTS vc_import_job_pages (
            job_id INTEGER NOT NULL REFERENCES vc_import_jobs(id), page_hash TEXT NOT NULL,
            PRIMARY KEY(job_id,page_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_vc_import_queue ON vc_import_job_items(job_id,status,retry_after);
    ''')
    db.commit()


def snapshot(db, job_id=None):
    row = db.execute('SELECT * FROM vc_import_jobs ' + ('WHERE id=?' if job_id else 'ORDER BY id DESC LIMIT 1'),
                     (job_id,) if job_id else ()).fetchone()
    if not row:
        return None
    job = dict(row)
    for key in ('lease_owner', 'lease_until'):
        job.pop(key, None)
    counts = {r['status']: r['n'] for r in db.execute(
        'SELECT status,COUNT(*) n FROM vc_import_job_items WHERE job_id=? GROUP BY status', (job['id'],))}
    job.update(discovered=sum(counts.values()), imported=counts.get('done', 0),
               pending=counts.get('pending', 0), failed=counts.get('failed', 0), skipped=counts.get('skipped',0))
    job['failures'] = [dict(r) for r in db.execute(
        "SELECT recipe_id,error,attempts,status FROM vc_import_job_items WHERE job_id=? AND error!='' ORDER BY recipe_id LIMIT 20", (job['id'],))]
    return job


def start_job(db, *, actor_id, source_user_id=''):
    if source_user_id and not str(source_user_id).isdigit():
        raise ValueError('VC user ID must be a number.')
    ensure_schema(db)
    db.execute('BEGIN IMMEDIATE')
    try:
        active = db.execute("SELECT id FROM vc_import_jobs WHERE status IN ('queued','running') ORDER BY id LIMIT 1").fetchone()
        if active:
            job_id = active['id']
        else:
            paused = db.execute("SELECT id FROM vc_import_jobs WHERE status='paused' AND source_user_id=? ORDER BY id DESC LIMIT 1", (str(source_user_id),)).fetchone()
            if paused:
                job_id = paused['id']
                db.execute("UPDATE vc_import_jobs SET status='queued',retry_after=0,discovery_attempts=0,last_error='' WHERE id=?", (job_id,))
                db.commit()
                return snapshot(db, job_id)
            now = time.time()
            job_id = db.execute('''INSERT INTO vc_import_jobs(mode,source_user_id,actor_id,created_at,updated_at)
                VALUES(?,?,?,?,?)''', ('vc_user_profile' if source_user_id else 'vc_public_all', str(source_user_id), actor_id, now, now)).lastrowid
        db.commit()
    except Exception:
        db.rollback()
        raise
    return snapshot(db, job_id)


def control_job(db, job_id, action):
    if action not in ('pause', 'resume', 'retry_failed'):
        raise ValueError('Unknown import action.')
    db.execute('BEGIN IMMEDIATE')
    try:
        row = db.execute('SELECT * FROM vc_import_jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise ValueError('Import not found.')
        if action == 'pause':
            if row['status'] in ('queued', 'running'):
                db.execute("UPDATE vc_import_jobs SET status='paused',updated_at=? WHERE id=?", (time.time(), job_id))
        else:
            other = db.execute("SELECT id FROM vc_import_jobs WHERE status IN ('queued','running') AND id!=?", (job_id,)).fetchone()
            if other:
                raise ValueError('Pause the active import before resuming another one.')
            if action == 'retry_failed':
                db.execute("UPDATE vc_import_job_items SET status='pending',attempts=0,retry_after=0,error='' WHERE job_id=? AND status='failed'", (job_id,))
            db.execute("UPDATE vc_import_jobs SET status='queued',retry_after=0,discovery_attempts=0,last_error='',updated_at=? WHERE id=?", (time.time(), job_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return snapshot(db, job_id)


def _complete_recipe(db, recipe_id):
    # Parsing a reference alone is insufficient: verify a complete usable library version.
    return db.execute('''SELECT 1 FROM recipe_references rr JOIN recipes r ON r.id=rr.recipe_id
        JOIN recipe_versions v ON v.id=r.current_version_id AND v.recipe_id=r.id
        WHERE rr.source='vc' AND rr.source_type='vc_dtl' AND rr.source_recipe_id=?
        AND COALESCE(rr.parse_failed,0)=0 AND r.visibility='private' AND r.library_type='reference'
        AND EXISTS(SELECT 1 FROM recipe_items ri WHERE ri.version_id=v.id)
        AND (SELECT COUNT(*) FROM recipe_items ri WHERE ri.version_id=v.id)=
            (SELECT COUNT(*) FROM recipe_reference_flavours rf WHERE rf.recipe_reference_id=rr.id)''', (recipe_id,)).fetchone() is not None


def _claim(db, owner):
    now = time.time()
    if not db.execute("SELECT 1 FROM vc_import_jobs WHERE status IN ('queued','running') AND retry_after<=?", (now,)).fetchone():
        return None
    db.execute('BEGIN IMMEDIATE')
    if db.execute('SELECT 1 FROM vc_import_jobs WHERE lease_until>? AND lease_owner!=?', (now, owner)).fetchone():
        db.commit()
        return None
    row = db.execute('''SELECT * FROM vc_import_jobs WHERE status IN ('queued','running')
        AND retry_after<=? AND (lease_until<? OR lease_owner=?) ORDER BY id LIMIT 1''', (now, now, owner)).fetchone()
    if row:
        db.execute("UPDATE vc_import_jobs SET status='running',lease_owner=?,lease_until=?,updated_at=? WHERE id=?",
                   (owner, now + LEASE_SECONDS, now, row['id']))
    db.commit()
    return dict(row) if row else None


def _discover(db, job):
    progress = {}
    headers = {'User-Agent': 'TVW-Recipe-Importer/1.0', 'Accept': 'application/json', 'Referer': 'https://vceliquidrecipes.com/recipes'}
    with requests.Session() as client:
        if job['mode'] == 'vc_user_profile':
            links = vc._fetch_links_for_user_profile(client, headers, user_id=job['source_user_id'],
                max_recipes=50, start_page=job['next_page'], progress=progress)
        else:
            links = vc._fetch_links_via_public_controller(client, headers, max_recipes=300,
                start_page=job['next_page'], progress=progress)
    ids = sorted({link.rsplit('/', 1)[-1] for link in links if link.rsplit('/', 1)[-1].isdigit()})
    digest = hashlib.sha256(','.join(ids).encode()).hexdigest()
    if not ids and job['pages_read'] == 0:
        raise ValueError('No recipes found on the first page. Check the source or user ID before resuming.')
    if ids and db.execute('SELECT 1 FROM vc_import_job_pages WHERE job_id=? AND page_hash=?', (job['id'], digest)).fetchone():
        raise ValueError('VC returned the same page again. Paused to avoid silently missing recipes.')
    if ids:
        db.execute('INSERT INTO vc_import_job_pages(job_id,page_hash) VALUES(?,?)', (job['id'], digest))
    for rid in ids:
        db.execute('INSERT OR IGNORE INTO vc_import_job_items(job_id,recipe_id) VALUES(?,?)', (job['id'], rid))
    total_pages = int(progress['total_pages']) if progress.get('total_pages') is not None else None
    total_items = int(progress['total_items']) if progress.get('total_items') is not None else None
    end = not ids or (total_pages is not None and job['next_page'] >= total_pages)
    db.execute('''UPDATE vc_import_jobs SET next_page=next_page+1,pages_read=pages_read+1,
        discovery_done=?,total_pages=?,total_items=?,last_page_hash=?,discovery_attempts=0,last_error='',retry_after=0 WHERE id=?''',
        (int(end), total_pages, total_items, digest, job['id']))
    db.commit()
    from flask import current_app
    current_app.logger.warning('VC import job %s: page %s, %s recipe IDs, end=%s', job['id'], job['next_page'], len(ids), end)


def process_step(db, owner):
    """One page or one recipe per lease. Safe to resume after process death."""
    job = _claim(db, owner)
    if not job:
        return False
    job_id = job['id']
    try:
        vc._ensure_reference_tables(db)
        db.commit()
        item = db.execute("SELECT * FROM vc_import_job_items WHERE job_id=? AND status='pending' AND retry_after<=? ORDER BY attempts,recipe_id LIMIT 1",
                          (job_id, time.time())).fetchone()
        if item:
            rid = item['recipe_id']
            if _complete_recipe(db, rid):
                result = {}
                done = True
            else:
                result = vc.run_vc_dtl_scraper(db, mode='vc_targeted_ids', target_ids=[rid],
                    actor_id=job['actor_id'], maintenance=False)
                done = _complete_recipe(db, rid)
            reason = str(result.get('stop_reason') or '')
            if reason.startswith('source_http_'):
                db.execute("UPDATE vc_import_jobs SET status='paused',last_error=? WHERE id=?", (reason, job_id))
            elif rid in [str(value) for value in result.get('empty_formula_ids', [])]:
                db.execute("UPDATE vc_import_job_items SET status='skipped',error='Source page has no ingredient formula (testing/review post).' WHERE job_id=? AND recipe_id=?", (job_id, rid))
            elif done:
                db.execute("UPDATE vc_import_job_items SET status='done',error='' WHERE job_id=? AND recipe_id=?", (job_id, rid))
            else:
                attempts = item['attempts'] + 1
                error = '; '.join(str(e) for e in result.get('errors', [])) or reason or 'Recipe could not be imported completely; review required.'
                db.execute('''UPDATE vc_import_job_items SET attempts=?,status=?,retry_after=?,error=? WHERE job_id=? AND recipe_id=?''',
                    (attempts, 'failed' if attempts >= MAX_ATTEMPTS else 'pending', time.time() + 30 * attempts, error[:2000], job_id, rid))
            db.commit()
        elif not job['discovery_done']:
            _discover(db, job)
        elif not db.execute("SELECT 1 FROM vc_import_job_items WHERE job_id=? AND status='pending'", (job_id,)).fetchone():
            failures = db.execute("SELECT COUNT(*) FROM vc_import_job_items WHERE job_id=? AND status='failed'", (job_id,)).fetchone()[0]
            db.execute('UPDATE vc_import_jobs SET status=? WHERE id=?', ('completed_with_errors' if failures else 'completed', job_id))
            db.commit()
            from flask import current_app
            current_app.logger.warning('VC import job %s finished; failed=%s', job_id, failures)
    except Exception as exc:
        db.rollback()
        attempts = job['discovery_attempts'] + 1
        http = getattr(getattr(exc, 'response', None), 'status_code', None)
        pause = http in (403, 429, 503) or attempts >= MAX_ATTEMPTS or isinstance(exc, ValueError)
        db.execute('''UPDATE vc_import_jobs SET status=?,discovery_attempts=?,retry_after=?,last_error=? WHERE id=?''',
                   ('paused' if pause else 'queued', attempts, time.time() + 30 * attempts, f'{type(exc).__name__}: {exc}'[:2000], job_id))
        db.commit()
        from flask import current_app
        current_app.logger.exception('VC import job %s paused/retrying', job_id)
    finally:
        db.execute('UPDATE vc_import_jobs SET lease_owner=NULL,lease_until=0,updated_at=? WHERE id=? AND lease_owner=?', (time.time(), job_id, owner))
        db.commit()
    return True


def start_worker(app):
    """A process-local thread with a database lease for multi-process exclusion."""
    if app.testing or not app.config.get('VC_IMPORT_WORKER_ENABLED', True):
        return
    key = id(app)
    with _worker_lock:
        if key in _workers and _workers[key].is_alive():
            return
        def run():
            owner = uuid.uuid4().hex
            while True:
                try:
                    # Fresh connection/context per step also survives worker restarts cleanly.
                    with app.app_context():
                        db = get_db()
                        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='vc_import_jobs'").fetchone():
                            ensure_schema(db)
                        worked = process_step(db, owner)
                    time.sleep(1 if worked else 5)
                except Exception:
                    app.logger.exception('VC import worker will retry after an unexpected error')
                    time.sleep(10)
        thread = threading.Thread(target=run, name='tvw-vc-import', daemon=True)
        _workers[key] = thread
        thread.start()
