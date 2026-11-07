"""Master live-audit regressions using isolated production data."""
import unittest
from app import app as flask_app
from db import get_db
from services import guided_mix_session_service as guided, production_job_service as production
from tests.test_production_jobs import ProductionJobsPhase1Tests
from routes.master_admin_console import master_admin_bp
if "master_admin" not in flask_app.blueprints:
    flask_app.register_blueprint(master_admin_bp)

class MasterAuditTests(unittest.TestCase):
    setUp = ProductionJobsPhase1Tests.setUp
    tearDown = ProductionJobsPhase1Tests.tearDown
    _create_trinidad = ProductionJobsPhase1Tests._create_trinidad

    def test_cancelled_production_cannot_progress_or_undo(self):
        for cancel_job in (False, True):
            with self.subTest(cancel_job=cancel_job), flask_app.app_context():
                job_id, recipe_id = self._create_trinidad()
                started = production.start_job_linked_mix(job_recipe_id=recipe_id, operator_user_id=1, bottle_count=1)
                sid = int(started['mix_session_id'])
                if cancel_job:
                    get_db().execute("UPDATE production_jobs SET status='cancelled' WHERE id=?", (job_id,))
                    get_db().commit()
                else:
                    production.cancel_batch(batch_id=int(started['batch_id']))
                before = [tuple(r) for r in get_db().execute('SELECT * FROM mix_session_steps WHERE mix_session_id=?', (sid,))]
                for undo in (False, True):
                    result = guided.complete_guided_step_with_policy(session_id=sid, user_id=1, step_no=1, undo=undo)
                    self.assertFalse(result['ok'])
                    self.assertEqual(result['policy']['decision'], 'production_cancelled')
                after = [tuple(r) for r in get_db().execute('SELECT * FROM mix_session_steps WHERE mix_session_id=?', (sid,))]
                self.assertEqual(before, after)
                self.assertNotIn(sid, [row['id'] for row in guided.list_user_guided_sessions(user_id=1)])

    def sign_in_master(self):
        with flask_app.app_context():
            get_db().execute("UPDATE users SET role='Master' WHERE id=1")
            get_db().commit()
        with self.client.session_transaction() as session:
            session.update(user_id=1, username='Wynand', role='Master')

    def test_role_editor_preserves_current_roles(self):
        from bs4 import BeautifulSoup
        self.sign_in_master()
        with flask_app.app_context():
            get_db().execute("INSERT INTO users (id,username,password_hash,role,status) VALUES (3,'Admin QA','x','admin','Active')")
            get_db().commit()
        response = self.client.get('/master-admin?tab=users')
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.data, 'html.parser')
        selected = [option.get('value') for option in soup.select('select option[selected]')]
        self.assertIn('member', selected)
        self.assertIn('admin', selected)
        self.assertIn('Master', selected)

    def test_master_manual_inventory_add_and_member_denial(self):
        self.sign_in_master()
        response = self.client.post('/library/manual-add', data=dict(brand='QA',name='QA Biscuit',amount=0,unit='ml'))
        self.assertEqual(response.status_code,302)
        with flask_app.app_context():
            self.assertIsNotNone(get_db().execute("SELECT id FROM flavor_reference WHERE name='QA Biscuit'").fetchone())
        with self.client.session_transaction() as session:
            session.clear()
            session.update(user_id=2, username='Riku', role='member')
        self.client.post('/library/manual-add', data=dict(brand='QA',name='Forbidden QA',amount=0,unit='ml'))
        with flask_app.app_context():
            self.assertIsNone(get_db().execute("SELECT id FROM flavor_reference WHERE name='Forbidden QA'").fetchone())

    def test_classification_preview_json_does_not_write(self):
        self.sign_in_master()
        with flask_app.app_context():
            before = tuple(get_db().execute('SELECT * FROM flavor_reference WHERE id=101').fetchone())
        response = self.client.post('/master-admin/flavors/classify/101',json={'apply':False})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json()['status'],'success')
        with flask_app.app_context():
            self.assertEqual(before,tuple(get_db().execute('SELECT * FROM flavor_reference WHERE id=101').fetchone()))
