"""Exercise durable pagination, retries, leases, pause and recovery without a network."""
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from flask import Flask
from services import vc_import_job_service as jobs


class ImportQueueTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.app = Flask(__name__)
        self.context = self.app.app_context()
        self.context.push()
        jobs.ensure_schema(self.db)
        self.job = jobs.start_job(self.db, actor_id=6)
        self.complete = set()
        self.patches = [patch.object(jobs.vc, '_ensure_reference_tables'),
                        patch.object(jobs, '_complete_recipe', side_effect=lambda db, rid: rid in self.complete)]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in self.patches: p.stop()
        self.db.close()
        self.context.pop()

    def page(self, ids):
        return [f'https://vceliquidrecipes.com/recipe/{rid}' for rid in ids]

    def import_recipe(self, db, **kwargs):
        self.complete.update(kwargs['target_ids'])
        self.assertFalse(kwargs['maintenance'])
        return {'status': 'success', 'stored_recipes': 1}

    def step(self):
        self.assertTrue(jobs.process_step(self.db, 'worker-A'))

    def test_walks_every_page_and_finishes_without_clicking_resume(self):
        with patch.object(jobs.vc, '_fetch_links_via_public_controller', side_effect=[self.page([1,2]), self.page([2,3]), []]) as discover, patch.object(jobs.vc, 'run_vc_dtl_scraper', side_effect=self.import_recipe) as run:
            for _ in range(7): self.step()
        job = jobs.snapshot(self.db)
        self.assertEqual((job['status'], job['imported'], job['discovered'], job['pending']), ('completed',3,3,0))
        self.assertEqual([c.kwargs['start_page'] for c in discover.call_args_list], [1,2,3])
        self.assertEqual(run.call_count, 3)
        self.assertFalse(jobs.process_step(self.db, 'worker-A'))

    def test_start_is_idempotent_while_active(self):
        second = jobs.start_job(self.db, actor_id=6, source_user_id='2078')
        self.assertEqual(second['id'], self.job['id'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM vc_import_jobs').fetchone()[0],1)

    def test_pause_finishes_current_item_and_resume_preserves_queue(self):
        with patch.object(jobs.vc, '_fetch_links_via_public_controller', return_value=self.page([1,2])): self.step()
        def finish_and_pause(db, **kwargs):
            jobs.control_job(db, self.job['id'], 'pause')
            return self.import_recipe(db, **kwargs)
        with patch.object(jobs.vc, 'run_vc_dtl_scraper', side_effect=finish_and_pause): self.step()
        self.assertEqual(jobs.snapshot(self.db)['status'], 'paused')
        self.assertFalse(jobs.process_step(self.db, 'worker-B'))
        jobs.control_job(self.db, self.job['id'], 'resume')
        with patch.object(jobs.vc, 'run_vc_dtl_scraper', side_effect=self.import_recipe): self.step()
        self.assertEqual(jobs.snapshot(self.db)['imported'],2)

    def test_failed_recipe_is_reported_not_marked_done_and_can_retry(self):
        with patch.object(jobs.vc, '_fetch_links_via_public_controller', side_effect=[self.page([1]), []]):
            self.step()
            with patch.object(jobs.vc, 'run_vc_dtl_scraper', return_value={'errors':['Unresolved flavour']}):
                for _ in range(3):
                    self.db.execute('UPDATE vc_import_job_items SET retry_after=0');self.db.commit();self.step()
            self.step();self.step()
        job=jobs.snapshot(self.db)
        self.assertEqual((job['status'],job['failed'],job['imported']),('completed_with_errors',1,0))
        self.assertIn('Unresolved flavour',job['failures'][0]['error'])
        jobs.control_job(self.db,job['id'],'retry_failed')
        with patch.object(jobs.vc,'run_vc_dtl_scraper',side_effect=self.import_recipe):self.step()
        self.step()
        self.assertEqual(jobs.snapshot(self.db)['status'],'completed')

    def test_review_post_is_skipped_without_retry(self):
        with patch.object(jobs.vc,'_fetch_links_via_public_controller',side_effect=[self.page([42]), []]):
            self.step()
            with patch.object(jobs.vc,'run_vc_dtl_scraper',return_value={'empty_formula_ids':['42']}):self.step()
            self.step();self.step()
        job=jobs.snapshot(self.db)
        self.assertEqual((job['status'],job['skipped'],job['failed'],job['imported']),('completed',1,0,0))
        self.assertIn('no ingredient formula',job['failures'][0]['error'])

    def test_rate_limit_pauses_without_consuming_recipe_or_cursor(self):
        with patch.object(jobs.vc, '_fetch_links_via_public_controller', return_value=self.page([1])): self.step()
        with patch.object(jobs.vc, 'run_vc_dtl_scraper', return_value={'stop_reason':'source_http_429; pause before resuming'}): self.step()
        job=jobs.snapshot(self.db)
        self.assertEqual((job['status'],job['pending'],job['next_page']),('paused',1,2))
        self.assertIn('429',job['last_error'])
        self.assertEqual(self.db.execute('SELECT attempts FROM vc_import_job_items').fetchone()[0],0)

    def test_repeated_page_pauses_instead_of_claiming_completion(self):
        with patch.object(jobs.vc, '_fetch_links_via_public_controller', return_value=self.page([1])):
            self.step();self.complete.add('1');self.step();self.step()
        job=jobs.snapshot(self.db)
        self.assertEqual(job['status'],'paused')
        self.assertIn('same page',job['last_error'])
        self.assertEqual(job['next_page'],2)

    def test_discovery_failure_does_not_advance_and_retries(self):
        with patch.object(jobs.vc, '_fetch_links_via_public_controller', side_effect=TimeoutError('source slow')): self.step()
        job=jobs.snapshot(self.db)
        self.assertEqual((job['status'],job['next_page'],job['discovered']),('queued',1,0))
        self.assertIn('source slow',job['last_error'])

    def test_new_process_cannot_steal_live_lease_but_recovers_expired_lease(self):
        self.assertIsNotNone(jobs._claim(self.db,'dead-process'))
        self.assertIsNone(jobs._claim(self.db,'new-process'))
        self.db.execute('UPDATE vc_import_jobs SET lease_until=0');self.db.commit()
        self.assertIsNotNone(jobs._claim(self.db,'new-process'))

    def test_profile_uses_persisted_pages_and_actor(self):
        jobs.control_job(self.db,self.job['id'],'pause')
        profile=jobs.start_job(self.db,actor_id=6,source_user_id='2078')
        with patch.object(jobs.vc,'_fetch_links_for_user_profile',return_value=self.page([42])) as fetch:
            self.step()
        self.assertEqual(fetch.call_args.kwargs['user_id'],'2078')
        self.assertEqual(jobs.snapshot(self.db,profile['id'])['next_page'],2)

    def test_listing_totals_stop_at_last_page_without_requesting_out_of_range(self):
        def page(client, headers, max_recipes, start_page, progress):
            progress.update(total_pages=1,total_items=1)
            return self.page([99])
        with patch.object(jobs.vc,'_fetch_links_via_public_controller',side_effect=page) as fetch, patch.object(jobs.vc,'run_vc_dtl_scraper',side_effect=self.import_recipe):
            self.step();self.step();self.step()
        job=jobs.snapshot(self.db)
        self.assertEqual((job['status'],job['total_items'],job['total_pages']),('completed',1,1))
        self.assertEqual(fetch.call_count,1)

    def test_empty_first_page_is_not_reported_as_finished(self):
        with patch.object(jobs.vc,'_fetch_links_via_public_controller',return_value=[]):self.step()
        self.assertEqual(jobs.snapshot(self.db)['status'],'paused')

    def test_start_reuses_paused_scan_of_same_source(self):
        jobs.control_job(self.db,self.job['id'],'pause')
        again=jobs.start_job(self.db,actor_id=6)
        self.assertEqual(again['id'],self.job['id'])
        self.assertEqual(again['status'],'queued')

    def test_second_job_waits_for_paused_jobs_inflight_request(self):
        jobs._claim(self.db,'first-process')
        jobs.control_job(self.db,self.job['id'],'pause')
        jobs.start_job(self.db,actor_id=6,source_user_id='2078')
        self.assertIsNone(jobs._claim(self.db,'second-process'))
        self.db.execute('UPDATE vc_import_jobs SET lease_until=0');self.db.commit()
        self.assertIsNotNone(jobs._claim(self.db,'second-process'))

    def test_two_connections_start_one_job_and_only_one_claims_it(self):
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            filename=str(Path(folder)/'queue.db')
            initial=sqlite3.connect(filename);jobs.ensure_schema(initial);initial.close()
            def start_and_claim(owner):
                db=sqlite3.connect(filename);db.row_factory=sqlite3.Row
                job=jobs.start_job(db,actor_id=6)
                claim=jobs._claim(db,owner)
                db.close()
                return job['id'],bool(claim)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(start_and_claim,['process-A','process-B']))
            self.assertEqual(len({item[0] for item in results}),1)
            self.assertEqual(sum(item[1] for item in results),1)


class JobRouteTests(unittest.TestCase):
    def test_admin_only_nonblocking_start_poll_pause_and_report(self):
        from routes.master_admin_console import master_admin_bp
        app=Flask(__name__);app.secret_key='test';app.testing=True
        app.add_url_rule('/',endpoint='tvw_community.community_home',view_func=lambda:'Home')
        app.register_blueprint(master_admin_bp)
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        client=app.test_client()
        with client.session_transaction() as state:state.update(user_id=6,role='member')
        with patch('models.users_repo.fetch_user_by_id',return_value={'id':6,'password_hash':'x'}), patch('db.get_db',return_value=db), patch.object(jobs,'start_worker') as worker:
            self.assertEqual(client.post('/master-admin/vc-import',json={'action':'start'}).status_code,302)
            self.assertEqual(client.get('/master-admin/vc-import').status_code,302)
            self.assertEqual(client.get('/master-admin/vc-import/1/report').status_code,302)
            worker.assert_not_called()
            with client.session_transaction() as state:state['role']='Old Goat'
            response=client.post('/master-admin/vc-import',json={'action':'start'})
            self.assertEqual(response.status_code,202)
            job=response.json['job'];self.assertEqual(job['actor_id'],6)
            self.assertNotIn('lease_owner',job)
            self.assertEqual(client.get('/master-admin/vc-import').json['job']['id'],job['id'])
            self.assertEqual(client.post('/master-admin/vc-import',json={'action':'pause','job_id':job['id']}).json['job']['status'],'paused')
            report=client.get(f"/master-admin/vc-import/{job['id']}/report")
            self.assertEqual(report.status_code,200)
            self.assertIn('attachment',report.headers['Content-Disposition'])
            self.assertEqual(client.post('/master-admin/vc-import',json={'source_user_id':'nonsense'}).status_code,400)
        db.close()


if __name__=='__main__':unittest.main()
