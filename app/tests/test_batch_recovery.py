"""End-to-end recipe rescue using three flavours, nicotine and persisted bottle tasks."""
import math
import unittest
from app import app as flask_app
from db import get_db
from tests.test_tvw_api import TVWApiTests

class BatchRecoveryTests(unittest.TestCase):
    setUp=TVWApiTests.setUp
    tearDown=TVWApiTests.tearDown
    _guided_session_steps=TVWApiTests._guided_session_steps

    def start_overpour(self, factor=1.5, nicotine=3, carrier="PG",duplicate=False,slight_first=False):
        with flask_app.app_context():
            db=get_db()
            db.execute('UPDATE recipe_items SET amount_percent=2 WHERE ingredient_id=101')
            for ident,name,pct in ((102,'Apple',3),(103,'Cookie',5)):
                db.execute("INSERT INTO flavor_reference (id,brand,name,category,density,active) VALUES (?,'QA',?,'Flavor',1.036,1)",(ident,name))
                db.execute('INSERT INTO recipe_items (version_id,ingredient_id,amount_percent) VALUES (1,?,?)',(ident,pct))
            if duplicate:
                db.execute('INSERT INTO recipe_items (version_id,ingredient_id,amount_percent) VALUES (1,101,1)')
            db.commit()
        response=self.client.post('/api/mix/guided/session',json=dict(recipe_id=1,target_ml=30,target_nic_mg_ml=nicotine,nic_base_mg_ml=100,pg_ratio=30,vg_ratio=70,nicotine_carrier=carrier))
        self.assertIn(response.status_code,(200,201),response.data)
        sid=response.get_json()['mix_session_id']
        flavors=0
        for _ in range(60):
            step=next(s for s in self._guided_session_steps(sid) if not s['completed_at'])
            p=step['payload'];data={}
            if p.get('step_type')=='add_ingredient':
                weight=round(p['target_grams'],2)
                if p.get('category') not in ('nicotine','pg','vg'):
                    flavors+=1
                    if flavors==1 and slight_first: weight=round(weight+.02,2)
                    if flavors==3: weight=round(weight*factor,2)
                data={'measured_grams':weight}
            done=self.client.post(f'/api/mix/guided/session/{sid}/steps/{step["step_no"]}/complete',json=data)
            self.assertEqual(done.status_code,200,done.data)
            if flavors==3:
                return sid,step['step_no']
        self.fail('No third flavour')

    def post(self,sid,operation,data):
        return self.client.post(f'/api/mix/guided/session/{sid}/batch-recovery/{operation}',json=data)

    def apply_plan(self,sid,mode='two_bottles'):
        response=self.post(sid,'preview',{'mode':mode})
        self.assertEqual(response.status_code,200,response.data)
        plan=response.get_json()['plan']
        response=self.post(sid,'apply',dict(mode=mode,fingerprint=plan['fingerprint'],new_ml=plan['new_ml'],capacity_ml=plan['bottle_ml'],confirmed=True))
        self.assertEqual(response.status_code,200,response.data)
        return plan,response.get_json()['session']['batch_recovery']

    def advance(self,sid,state,weight=None):
        response=self.post(sid,'advance',dict(revision=state['revision'],cursor=state['cursor'],weight=weight))
        self.assertEqual(response.status_code,200,response.data)
        return response.get_json()['session']

    def finish(self,sid,state):
        for _ in range(100):
            task=state['tasks'][state['cursor']]
            weight=None
            if task['type']=='pour':weight=task['target_grams']
            if task['type']=='transfer':weight=round(sum(state['actuals'].values())/2,2)
            bundle=self.advance(sid,state,weight)
            state=bundle['batch_recovery']
            if state['status']=='complete':return bundle
        self.fail('Recovery did not complete')

    def test_two_bottles_revisits_all_flavours_and_nicotine_then_splits(self):
        sid,over_no=self.start_overpour()
        plan,state=self.apply_plan(sid)
        self.assertEqual(plan['new_ml'],60)
        fixed=[i for i in plan['ingredients'] if i['payload']['category'] not in ('pg','vg')]
        self.assertEqual(len(fixed),4)
        for i in fixed:
            self.assertAlmostEqual(i['actual_grams']+i['add_grams'],i['original_grams']*2)
        self.assertEqual(next(t for t in state['tasks'] if t['type']=='pour')['key'],fixed[0]['key'])
        # Original controls cannot skip the rescue, even using a new weight.
        blocked=self.client.post(f'/api/mix/guided/session/{sid}/steps/{over_no}/complete',json={'measured_grams':1})
        self.assertNotEqual(blocked.status_code,200)
        # Resume from a fresh GET rather than client-local progress.
        state=self.client.get(f'/api/mix/guided/session/{sid}').get_json()['session']['batch_recovery']
        bundle=self.finish(sid,state)
        self.assertTrue(bundle['progress']['all_done'])
        self.assertEqual(bundle['target_ml'],60)
        self.assertEqual(bundle['mix_summary']['batch_recovery']['plan']['bottle_count'],2)
        self.assertAlmostEqual(bundle['mix_summary']['estimated_final_volume_ml'],60,delta=.1)
        self.assertEqual(bundle['mix_summary']['estimated_final_ratio_label'],'30/70')
        self.assertTrue(bundle['batch_recovery']['split_done'])
        self.assertNotIn('additions',bundle['batch_recovery'])
        self.assertEqual(len(bundle['mix_summary']['batch_recovery']['bottles']),2)
        for bottle in bundle['mix_summary']['batch_recovery']['bottles']:
            self.assertAlmostEqual(bottle['estimated_nicotine_mg_ml'],3,delta=.02)
            self.assertAlmostEqual(bottle['estimated_volume_ml'],30,delta=.1)
        self.assertAlmostEqual(sum(bundle['batch_recovery']['bottle_volumes_ml']),60)

    def test_larger_vessel_uses_minimum_and_subtracts_completed_pours(self):
        sid,_=self.start_overpour()
        plan,state=self.apply_plan(sid,'larger_vessel')
        self.assertGreaterEqual(plan['new_ml'],44)
        self.assertLess(plan['new_ml'],46)
        self.assertFalse(any(t['type']=='transfer' for t in state['tasks']))
        bundle=self.finish(sid,state)
        self.assertAlmostEqual(bundle['mix_summary']['estimated_final_volume_ml'],plan['new_ml'],delta=.1)

    def test_two_bottles_rejects_excess_that_needs_more_than_double(self):
        sid,_=self.start_overpour(2.5)
        response=self.post(sid,'preview',{'mode':'two_bottles'})
        self.assertEqual(response.status_code,409)
        self.assertIn('not enough',response.get_json()['message'])

    def test_duplicate_request_and_stale_plan_cannot_double_topups(self):
        sid,_=self.start_overpour()
        plan,state=self.apply_plan(sid)
        data=dict(revision=state['revision'],cursor=state['cursor'])
        self.assertEqual(self.post(sid,'advance',data).status_code,200)
        self.assertEqual(self.post(sid,'advance',data).status_code,409)
        stale=self.post(sid,'apply',dict(mode='two_bottles',fingerprint=plan['fingerprint'],capacity_ml=30,confirmed=True))
        self.assertEqual(stale.status_code,409)

    def test_under_reading_survives_reload_without_accepting_or_taring_again(self):
        sid,_=self.start_overpour()
        _,state=self.apply_plan(sid)
        while state['tasks'][state['cursor']]['type']!='pour':
            state=self.advance(sid,state)['batch_recovery']
        state=self.advance(sid,state,0)['batch_recovery']
        no=state['cursor']
        state=self.client.get(f'/api/mix/guided/session/{sid}').get_json()['session']['batch_recovery']
        self.assertEqual(state['cursor'],no)
        self.assertEqual(state['tasks'][no]['reading'],0)
        task=state['tasks'][no]
        state=self.advance(sid,state,task['target_grams'])['batch_recovery']
        self.assertEqual(state['cursor'],no+1)

    def test_topup_overage_pauses_and_is_included_in_fresh_plan(self):
        sid,_=self.start_overpour()
        _,state=self.apply_plan(sid)
        while state['tasks'][state['cursor']]['type']!='pour':
            state=self.advance(sid,state)['batch_recovery']
        task=state['tasks'][state['cursor']]
        state=self.advance(sid,state,task['target_grams']+.01)['batch_recovery']
        self.assertEqual(state['status'],'blocked')
        self.assertIn('Never return mixed liquid',state['message'])
        preview=self.post(sid,'preview',{'mode':'larger_vessel'})
        self.assertEqual(preview.status_code,200,preview.data)
        i=next(i for i in preview.get_json()['plan']['ingredients'] if i['key']==task['key'])
        self.assertEqual(i['actual_grams'],state['actuals'][task['key']])

    def test_foreign_session_and_unreviewed_application_are_rejected(self):
        sid,_=self.start_overpour()
        response=self.post(sid,'apply',dict(mode='two_bottles',confirmed=False))
        self.assertEqual(response.status_code,409)
        with flask_app.app_context():
            get_db().execute("INSERT INTO users (id,username,password_hash,role,status) VALUES (3,'other','x','member','Active')")
            get_db().commit()
        with self.client.session_transaction() as s:
            s.clear();s.update(user_id=3,username='other',role='member')
        self.assertEqual(self.post(sid,'preview',{'mode':'two_bottles'}).status_code,404)

    def test_two_bottle_capacity_is_checked_before_any_mutation(self):
        from services.batch_recovery_service import _build_plan, RecoveryError
        items=[dict(key='f',payload={'category':'concentrate'},original_grams=6,density=1,actual_grams=9),
               dict(key='vg',payload={'category':'vg'},original_grams=4,density=1,actual_grams=0)]
        with self.assertRaisesRegex(RecoveryError,'before splitting'):
            _build_plan(items,10,'two_bottles')

    def test_partial_transfer_cannot_be_replanned_as_one_bottle(self):
        sid,_=self.start_overpour()
        _,state=self.apply_plan(sid)
        while state['tasks'][state['cursor']]['type']!='transfer':
            task=state['tasks'][state['cursor']]
            state=self.advance(sid,state,task.get('target_grams'))['batch_recovery']
        state=self.advance(sid,state,.1)['batch_recovery']
        self.assertTrue(state['transfer_started'])
        self.assertFalse(state['split_done'])
        self.assertEqual(self.post(sid,'preview',{'mode':'larger_vessel'}).status_code,409)

    def test_bad_scale_values_and_wrong_revisions_do_not_advance(self):
        sid,_=self.start_overpour()
        _,state=self.apply_plan(sid)
        while state['tasks'][state['cursor']]['type']!='pour':
            state=self.advance(sid,state)['batch_recovery']
        for value in (-1,'NaN','Infinity',True,'',None):
            response=self.post(sid,'advance',dict(revision=state['revision'],cursor=state['cursor'],weight=value))
            self.assertEqual(response.status_code,409,(value,response.data))
        self.assertEqual(self.post(sid,'advance',dict(revision=-1,cursor=state['cursor'],weight=0)).status_code,409)

    def test_pending_partial_pour_is_not_lost_when_replanning(self):
        sid,_=self.start_overpour()
        _,state=self.apply_plan(sid)
        while state['tasks'][state['cursor']]['type']!='pour':
            state=self.advance(sid,state)['batch_recovery']
        task=state['tasks'][state['cursor']]
        original=state['actuals'][task['key']]
        partial=round(task['target_grams']/2,2)
        state=self.advance(sid,state,partial)['batch_recovery']
        response=self.post(sid,'preview',{'mode':'larger_vessel'})
        self.assertEqual(response.status_code,200,response.data)
        line=next(i for i in response.get_json()['plan']['ingredients'] if i['key']==task['key'])
        self.assertAlmostEqual(line['actual_grams'],original+partial)

    def test_zero_nicotine_rescue_never_adds_a_nicotine_step(self):
        sid,_=self.start_overpour(nicotine=0)
        plan,state=self.apply_plan(sid)
        self.assertFalse(any(i['payload']['category']=='nicotine' for i in plan['ingredients']))
        bundle=self.finish(sid,state)
        self.assertEqual(bundle['batch_recovery']['estimated_nicotine_mg_ml'],0)

    def test_vg_nicotine_preserves_strength_and_carrier_ratio(self):
        sid,_=self.start_overpour(carrier='VG')
        plan,state=self.apply_plan(sid)
        bundle=self.finish(sid,state)
        self.assertEqual(bundle['batch_recovery']['estimated_ratio'],'30/70')
        self.assertAlmostEqual(bundle['batch_recovery']['estimated_nicotine_mg_ml'],3,delta=.02)

    def test_partial_pour_replan_retains_history_and_does_not_double_count(self):
        sid,_=self.start_overpour()
        _,state=self.apply_plan(sid)
        while state['tasks'][state['cursor']]['type']!='pour':
            state=self.advance(sid,state)['batch_recovery']
        state=self.advance(sid,state,round(state['tasks'][state['cursor']]['target_grams']/2,2))['batch_recovery']
        plan,state=self.apply_plan(sid,'larger_vessel')
        bundle=self.finish(sid,state)
        self.assertAlmostEqual(bundle['mix_summary']['estimated_final_volume_ml'],plan['new_ml'],delta=.1)

    def test_typing_correction_requires_confirmation_and_does_not_double_count(self):
        sid,_=self.start_overpour()
        _,state=self.apply_plan(sid)
        while state['tasks'][state['cursor']]['type']!='pour':
            state=self.advance(sid,state)['batch_recovery']
        task=state['tasks'][state['cursor']]
        original=state['actuals'][task['key']]
        state=self.advance(sid,state,task['target_grams']+.02)['batch_recovery']
        denied=self.post(sid,'advance',dict(revision=state['revision'],cursor=state['cursor'],weight=task['target_grams']))
        self.assertEqual(denied.status_code,409)
        fixed=self.post(sid,'advance',dict(revision=state['revision'],cursor=state['cursor'],weight=task['target_grams'],recording_error=True))
        self.assertEqual(fixed.status_code,200,fixed.data)
        state=fixed.get_json()['session']['batch_recovery']
        self.assertEqual(state['status'],'active')
        self.assertAlmostEqual(state['actuals'][task['key']],original+task['target_grams'])

    def test_duplicate_flavour_entries_are_combined_by_stable_id(self):
        sid,_=self.start_overpour(duplicate=True)
        plan,state=self.apply_plan(sid)
        repeated=[i for i in plan['ingredients'] if i['key']=='ingredient:101']
        self.assertEqual(len(repeated),1)
        self.assertAlmostEqual(repeated[0]['original_grams'],.9324,places=4)
        bundle=self.finish(sid,state)
        self.assertAlmostEqual(bundle['mix_summary']['estimated_final_volume_ml'],60,delta=.1)

    def test_catalogue_edits_cannot_change_the_captured_mix_formula(self):
        sid,_=self.start_overpour()
        before=self.post(sid,'preview',{'mode':'two_bottles'}).get_json()['plan']
        with flask_app.app_context():
            get_db().execute("UPDATE flavor_reference SET name='Renamed',brand='Changed',density=2,category='Hardware' WHERE id=101")
            get_db().commit()
        after=self.post(sid,'preview',{'mode':'two_bottles'}).get_json()['plan']
        self.assertEqual(before['ingredients'],after['ingredients'])
        self.assertEqual(before['fingerprint'],after['fingerprint'])

    def test_previous_pg_trim_does_not_become_the_new_recipe_target(self):
        sid,_=self.start_overpour(slight_first=True)
        plan,state=self.apply_plan(sid)
        pg=next(i for i in plan['ingredients'] if i['payload']['category']=='pg')
        self.assertAlmostEqual(pg['total_target_grams'],10.5672,places=4)
        bundle=self.finish(sid,state)
        self.assertEqual(bundle['batch_recovery']['estimated_ratio'],'30/70')

    def test_physical_overpour_after_split_pauses_with_bottle_identity(self):
        sid,_=self.start_overpour()
        _,state=self.apply_plan(sid)
        while not (state['split_done'] and state['tasks'][state['cursor']]['type']=='pour'):
            task=state['tasks'][state['cursor']]
            weight=task.get('target_grams')
            if task['type']=='transfer':weight=round(sum(state['actuals'].values())/2,2)
            state=self.advance(sid,state,weight)['batch_recovery']
        task=state['tasks'][state['cursor']]
        state=self.advance(sid,state,task['target_grams']+.1)['batch_recovery']
        self.assertEqual(state['status'],'blocked')
        self.assertIn('Bottle 1',state['message'])
        self.assertEqual(self.post(sid,'preview',{'mode':'larger_vessel'}).status_code,409)

    def test_unmeasurable_positive_dose_rejects_start_without_saving_session(self):
        with flask_app.app_context():
            db=get_db()
            db.execute('UPDATE recipe_items SET amount_percent=0.001')
            db.commit()
        response=self.client.post('/api/mix/guided/session',json=dict(recipe_id=1,target_ml=10,target_nic_mg_ml=0,pg_ratio=30,vg_ratio=70))
        self.assertEqual(response.status_code,400,response.data)
        self.assertIn('0.01 g scale resolution',response.get_json()['message'])
        with flask_app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM mix_sessions').fetchone()[0],0)

    def test_small_measurable_dose_is_present_and_zero_requires_decision(self):
        with flask_app.app_context():
            db=get_db()
            db.execute('UPDATE recipe_items SET amount_percent=0.1')
            db.commit()
        response=self.client.post('/api/mix/guided/session',json=dict(recipe_id=1,target_ml=10,target_nic_mg_ml=0,pg_ratio=30,vg_ratio=70))
        self.assertEqual(response.status_code,201,response.data)
        sid=response.get_json()['mix_session_id']
        for _ in range(20):
            step=next(s for s in self._guided_session_steps(sid) if not s['completed_at'])
            if step['payload'].get('step_type')=='add_ingredient':
                self.assertAlmostEqual(round(step['payload']['target_grams'],2),0.01)
                done=self.client.post(f'/api/mix/guided/session/{sid}/steps/{step["step_no"]}/complete',json={'measured_grams':0})
                self.assertEqual(done.status_code,200,done.data)
                self.assertFalse(next(s for s in self._guided_session_steps(sid) if s['step_no']==step['step_no'])['completed_at'])
                return
            self.client.post(f'/api/mix/guided/session/{sid}/steps/{step["step_no"]}/complete',json={})
        self.fail('Tiny dose was skipped')

    def test_incomplete_legacy_formula_cannot_silently_rescue_missing_flavour(self):
        import json
        sid,_=self.start_overpour()
        with flask_app.app_context():
            db=get_db()
            meta=json.loads(db.execute('SELECT result_json FROM mix_sessions WHERE id=?',(sid,)).fetchone()[0])
            meta.pop('recovery_formula')
            meta['planned_composition']['flavor_pg_ml']+=0.01
            db.execute('UPDATE mix_sessions SET result_json=? WHERE id=?',(json.dumps(meta),sid));db.commit()
        response=self.post(sid,'preview',{'mode':'two_bottles'})
        self.assertEqual(response.status_code,409,response.data)
        self.assertIn('complete flavour formula',response.get_json()['message'])

    def test_resume_counts_use_recovery_tasks_and_report_contains_each_bottle(self):
        sid,_=self.start_overpour()
        _,state=self.apply_plan(sid)
        self.advance(sid,state)
        listing=self.client.get('/api/mix/guided/sessions').get_json()['items']
        row=next(item for item in listing if item['id']==sid)
        self.assertTrue(row['recovery_active'])
        self.assertEqual(row['total_steps'],len(state['tasks']))
        self.assertEqual(row['open_steps'],len(state['tasks'])-1)
        state=self.client.get(f'/api/mix/guided/session/{sid}').get_json()['session']['batch_recovery']
        result=self.finish(sid,state)['mix_summary']
        self.assertTrue(result['adjustments_superseded_by_recovery'])
        self.assertEqual([b['number'] for b in result['batch_recovery']['bottles']],[1,2])
        for bottle in result['batch_recovery']['bottles']:
            self.assertAlmostEqual(bottle['estimated_volume_ml'],30,delta=.05)
