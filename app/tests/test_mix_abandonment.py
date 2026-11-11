"""Personal incomplete mixes can be abandoned and restored without losing pours."""
import unittest
from tests import test_tvw_api as fixtures
from db import get_db
from migrations import run_startup_migrations
from services import guided_mix_session_service as guided
from services.mix_session_lifecycle_service import set_abandoned


class MixAbandonmentTests(unittest.TestCase):
    setUp = fixtures.TVWApiTests.setUp
    tearDown = fixtures.TVWApiTests.tearDown

    def create_session(self, *, mode='guided', completed=False, production=False):
        with fixtures.flask_app.app_context():
            db=get_db()
            cur=db.execute("INSERT INTO mix_sessions(user_id,recipe_version_id,mode,target_ml,result_json,completed_at,production_job_id) VALUES(1,1,?,10,?, ?, ?)", (mode, '{"user_notes":"QA only","batch_recovery":{"status":"active","cursor":0,"tasks":[]}}', '2026-11-11' if completed else None, 99 if production else None))
            sid=cur.lastrowid
            for no,done in ((1,'2026-11-11'),(2,'2026-11-11' if completed else None)):
                db.execute("INSERT INTO mix_session_steps(mix_session_id,step_no,step_type,payload_json,completed_at) VALUES(?,?,'add_ingredient',?,?)",(sid,no,'{"actual_grams":0.52,"name":"Vanilla"}',done))
            db.commit()
            return sid

    def snapshot(self, sid):
        with fixtures.flask_app.app_context():
            db=get_db()
            row=dict(db.execute('SELECT * FROM mix_sessions WHERE id=?',(sid,)).fetchone())
            row.pop('abandoned_at')
            steps=[dict(r) for r in db.execute('SELECT * FROM mix_session_steps WHERE mix_session_id=? ORDER BY id',(sid,))]
            other={r[0]:[tuple(x) for x in db.execute('SELECT * FROM "'+r[0]+'"')] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('mix_sessions','mix_session_steps')")}
            return row,steps,other

    def test_abandon_hides_history_resume_and_stale_session_then_restore_preserves_data(self):
        sid=self.create_session()
        before=self.snapshot(sid)
        response=self.client.post(f'/my-mixes/{sid}/abandon',data={'filter':'in_progress'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.snapshot(sid),before)
        for filt in ('all','in_progress','guided','completed','quick'):
            self.assertEqual(self.client.get('/api/mix/history?filter='+filt).get_json()['total'],0)
        archived=self.client.get('/api/mix/history?filter=abandoned').get_json()
        self.assertEqual(archived['total'],1)
        self.assertTrue(archived['items'][0]['abandoned'])
        self.assertFalse(archived['items'][0]['in_progress'])
        self.assertFalse(archived['items'][0]['completed'])
        with fixtures.flask_app.app_context():
            self.assertEqual(guided.list_user_guided_sessions(user_id=1),[])
            self.assertIsNone(guided.get_guided_session_bundle(session_id=sid,user_id=1))
            outcome=guided.complete_guided_step_with_policy(session_id=sid,user_id=1,step_no=2)
            self.assertFalse(outcome['ok'])
        self.assertEqual(self.snapshot(sid),before)
        self.assertEqual(self.client.post(f'/my-mixes/{sid}/restore').status_code,302)
        self.assertEqual(self.snapshot(sid),before)
        self.assertEqual(self.client.get('/api/mix/history?filter=in_progress').get_json()['total'],1)
        self.assertEqual(self.client.get('/api/mix/history?filter=abandoned').get_json()['total'],0)

    def test_other_user_and_get_cannot_abandon_or_restore(self):
        sid=self.create_session()
        before=self.snapshot(sid)
        self.assertEqual(self.client.get(f'/my-mixes/{sid}/abandon').status_code,405)
        with self.client.session_transaction() as sess:
            sess['user_id']=2
            sess['username']='Old Goat'
            sess['role']='Old Goat'
        for action in ('abandon','restore'):
            self.assertEqual(self.client.post(f'/my-mixes/{sid}/{action}').status_code,404)
        self.assertEqual(before,self.snapshot(sid))

    def test_completed_quick_and_production_are_protected(self):
        for kwargs in ({'completed':True},{'mode':'quick'},{'production':True}):
            sid=self.create_session(**kwargs)
            before=self.snapshot(sid)
            with fixtures.flask_app.app_context():
                with self.assertRaises(ValueError):set_abandoned(session_id=sid,user_id=1,abandoned=True)
            self.assertEqual(before,self.snapshot(sid))

    def test_desktop_and_mobile_controls_switch_to_restore(self):
        sid=self.create_session()
        html=self.client.get('/my-mixes').get_data(as_text=True)
        self.assertEqual(html.count('>Abandon mix</button>'),2)
        self.client.post(f'/my-mixes/{sid}/abandon')
        html=self.client.get('/my-mixes?filter=abandoned').get_data(as_text=True)
        self.assertEqual(html.count('>Restore mix</button>'),2)
        self.assertNotIn('>Resume</a>',html)
        self.assertNotIn('Guided completed',html)

    def test_migration_is_idempotent_and_abandon_requests_are_idempotent(self):
        sid=self.create_session()
        with fixtures.flask_app.app_context():
            run_startup_migrations()
            set_abandoned(session_id=sid,user_id=1,abandoned=True)
            first=get_db().execute('SELECT abandoned_at FROM mix_sessions WHERE id=?',(sid,)).fetchone()[0]
            set_abandoned(session_id=sid,user_id=1,abandoned=True)
            self.assertEqual(first,get_db().execute('SELECT abandoned_at FROM mix_sessions WHERE id=?',(sid,)).fetchone()[0])
