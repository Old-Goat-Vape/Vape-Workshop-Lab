from datetime import datetime,timedelta,timezone
import unittest
from bs4 import BeautifulSoup
from tests import test_tvw_api as base
from db import get_db
from migrations import run_startup_migrations
from services import batch_steeping_service as steep

START=datetime(2020,1,1,12,tzinfo=timezone.utc)


class BatchSteepingTests(unittest.TestCase):
    def setUp(self):
        base.TVWApiTests.setUp(self)
        with base.flask_app.app_context():
            db=get_db()
            db.execute('INSERT INTO recipe_stories(version_id,steep_days) VALUES(1,14)')
            db.commit()
    tearDown=base.TVWApiTests.tearDown

    def create(self,mode='guided'):
        path='/api/mix/guided/session' if mode=='guided' else '/api/mix/quick'
        r=self.client.post(path,json=dict(recipe_id=1,version_id=1,target_ml=10,target_nic_mg_ml=0,
            pg_ratio=30,vg_ratio=70))
        self.assertIn(r.status_code,(200,201))
        if mode=='guided':return r.json['mix_session_id']
        with base.flask_app.app_context():return get_db().execute('SELECT MAX(id) FROM mix_sessions').fetchone()[0]

    def complete(self,sid,at=START):
        with base.flask_app.app_context():
            db=get_db()
            db.execute('UPDATE mix_session_steps SET completed_at=? WHERE mix_session_id=?',(steep.stamp(at),sid))
            db.execute('UPDATE mix_sessions SET completed_at=? WHERE id=?',(steep.stamp(at),sid))
            db.commit()

    def token(self,sid):
        r=self.client.get(f'/my-mixes/{sid}/steeping')
        self.assertEqual(r.status_code,200)
        return BeautifulSoup(r.data,'html.parser').select_one('[name=steeping_token]')['value']

    def test_snapshot_is_frozen_at_creation_and_unknown_is_not_zero(self):
        sid=self.create()
        with base.flask_app.app_context():
            db=get_db();db.execute('UPDATE recipe_stories SET steep_days=30 WHERE version_id=1');db.commit()
            r=steep.fetch(sid,1)
            self.assertEqual((r['source_days'],r['steep_days'],r['state']),(14,14,'not_mixed'))
            steep.save(sid,1,'',False)
            self.assertIsNone(steep.fetch(sid,1)['steep_days'])
            with self.assertRaises(ValueError):steep.save(sid,1,'',True)
            steep.save(sid,1,0,False)
            self.assertEqual(steep.fetch(sid,1)['steep_days'],0)
            self.assertEqual(db.execute('SELECT steep_days FROM recipe_stories WHERE version_id=1').fetchone()[0],30)
        self.complete(sid)
        with base.flask_app.app_context():
            self.assertEqual(steep.fetch(sid,1)['state'],'shake_vape')
            steep.save(sid,1,'',False)
            self.assertEqual(steep.fetch(sid,1)['state'],'not_set')

    def test_guided_start_uses_completion_not_creation_and_utc_boundary(self):
        sid=self.create();self.complete(sid)
        page=self.client.get(f'/guided-mix/session/{sid}')
        self.assertEqual(page.status_code,200)
        self.assertIn(b'STEEP THIS BATCH',page.data)
        with base.flask_app.app_context():
            before=steep.fetch(sid,1,START+timedelta(days=14,seconds=-1))
            after=steep.fetch(sid,1,START+timedelta(days=14))
            self.assertEqual(before['state'],'steeping');self.assertEqual(before['days_remaining'],1)
            self.assertEqual(after['state'],'ready')
            self.assertEqual(after['ready_at'],'2020-01-15T12:00:00+00:00')
            self.assertEqual(after['progress_percent'],100)

    def test_quick_calculation_needs_confirmation_and_repeat_does_not_restart(self):
        sid=self.create('quick')
        with base.flask_app.app_context():
            self.assertEqual(steep.fetch(sid,1)['state'],'not_mixed')
            self.assertEqual(steep.list_for(1),[])
            steep.confirm_quick(sid,1,START)
            steep.confirm_quick(sid,1,START+timedelta(days=1))
            self.assertEqual(steep.fetch(sid,1)['started_at'],steep.stamp(START))
            self.assertEqual(len(steep.list_for(1)),1)

    def test_reminders_opt_in_acknowledge_change_cancel_and_restart(self):
        sid=self.create();self.complete(sid)
        with base.flask_app.app_context():
            self.assertEqual(steep.due_count(1),0)
            steep.save(sid,1,'14',True);self.assertEqual(steep.due_count(1),1)
            self.assertEqual(steep.due_count(2),0)
            steep.acknowledge(sid,1);self.assertEqual(steep.due_count(1),0)
            steep.save(sid,1,'14',True);self.assertEqual(steep.due_count(1),0)
            steep.save(sid,1,'15',True);self.assertEqual(steep.due_count(1),1)
            steep.save(sid,1,'15',False);self.assertEqual(steep.due_count(1),0)
            run_startup_migrations()
            self.assertEqual(steep.fetch(sid,1)['steep_days'],15)
            self.assertEqual(steep.fetch(sid,1)['source_days'],14)

    def test_reopened_or_abandoned_batches_do_not_remind(self):
        sid=self.create();self.complete(sid)
        with base.flask_app.app_context():
            steep.save(sid,1,'1',True);db=get_db()
            db.execute('UPDATE mix_sessions SET completed_at=NULL WHERE id=?',(sid,));db.commit()
            self.assertEqual(steep.due_count(1),0)
            self.assertEqual(steep.fetch(sid,1)['state'],'not_mixed')
            db.execute('UPDATE mix_sessions SET completed_at=?,abandoned_at=? WHERE id=?',(steep.stamp(START),steep.stamp(START),sid));db.commit()
            self.assertEqual(steep.due_count(1),0);self.assertEqual(steep.list_for(1),[])

    def test_new_completion_has_new_timer_and_old_ack_does_not_cover_it(self):
        sid=self.create();self.complete(sid)
        with base.flask_app.app_context():
            steep.save(sid,1,'14',True);steep.acknowledge(sid,1)
        later=datetime.now(timezone.utc)+timedelta(seconds=1)
        self.complete(sid,later)
        with base.flask_app.app_context():
            r=steep.fetch(sid,1,later+timedelta(days=15))
            self.assertEqual(r['ready_at'],steep.stamp(later+timedelta(days=14)))
            self.assertTrue(r['reminder_due'])
            self.assertFalse(r['reminder_acknowledged'])

    def test_due_reminders_sort_before_newer_batches(self):
        old=self.create();self.complete(old)
        new=self.create();self.complete(new)
        with base.flask_app.app_context():
            steep.save(old,1,'14',True)
            db=get_db();db.execute('UPDATE batch_steeping SET acknowledged_at=? WHERE session_id=?',(steep.stamp(START),old));db.commit()
            self.assertEqual(steep.due_count(1),1)
            self.assertEqual(steep.list_for(1)[0]['session_id'],old)

    def test_private_tasting_age_score_zero_validation_and_no_community_votes(self):
        sid=self.create();self.complete(sid)
        with base.flask_app.app_context():
            steep.add_tasting(sid,1,'Needs more time <script>bad()</script>','needs_longer',0,START+timedelta(days=7))
            notes=steep.tastings(sid,1)
            self.assertEqual((notes[0]['age_days'],notes[0]['score']),(7,0))
            self.assertEqual(steep.fetch(sid,1)['steep_days'],14)
            for score in ['nan','inf','-1','11']:
                with self.assertRaises(ValueError):steep.add_tasting(sid,1,'notes','not_sure',score)
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM flavour_reviews').fetchone()[0],0)
        page=self.client.get(f'/my-mixes/{sid}/steeping')
        self.assertIn(b'&lt;script&gt;bad()',page.data);self.assertNotIn(b'<script>bad()',page.data)

    def test_ownership_csrf_routes_reminder_banner_and_my_mixes_links(self):
        sid=self.create();self.complete(sid)
        token=self.token(sid)
        url=f'/my-mixes/{sid}/steeping'
        self.assertEqual(self.client.post(url,data={'steep_days':'5','remind':'1'}).status_code,403)
        self.assertEqual(self.client.post(url,data={'steeping_token':token,'steep_days':'5','remind':'1'}).status_code,302)
        self.assertIn(b'Time for a tasting check',self.client.get('/recipes').data)
        self.assertIn(b'My steeping shelf',self.client.get('/my-mixes').data)
        self.assertIn(b'Batch plan',self.client.get('/my-mixes').data)
        self.assertIn(b'Reminder due',self.client.get('/steeping').data)
        with self.client.session_transaction() as s:
            s.clear();s.update(user_id=2,username='Old Goat',role='Old Goat')
        self.assertEqual(self.client.get(url).status_code,404)
        self.assertEqual(self.client.post(url,data={'steeping_token':token,'steep_days':'0'}).status_code,404)
        self.assertNotIn(b'Time for a tasting check',self.client.get('/recipes').data)

    def test_invalid_plans_and_unmixed_tastings_are_rejected(self):
        sid=self.create()
        with base.flask_app.app_context():
            for days in ['-1','1.5','366','nan']:
                with self.assertRaises(ValueError):steep.save(sid,1,days,False)
            with self.assertRaises(ValueError):steep.add_tasting(sid,1,'premature','not_sure')
            with self.assertRaises(ValueError):steep.confirm_quick(sid,1)
            with self.assertRaises(LookupError):steep.fetch(sid,2)
