"""Nicotine first, original tare, and residue-based correction regressions."""
import unittest
from tests.test_tvw_api import TVWApiTests

class NicotinePourBackTests(unittest.TestCase):
    setUp = TVWApiTests.setUp
    tearDown = TVWApiTests.tearDown
    _create_guided_session_with_nicotine = TVWApiTests._create_guided_session_with_nicotine
    _advance_to_nicotine_add_step = TVWApiTests._advance_to_nicotine_add_step
    _guided_session_steps = TVWApiTests._guided_session_steps
    _base_step_targets = TVWApiTests._base_step_targets
    _nicotine_recovery_log = TVWApiTests._nicotine_recovery_log

    def start_recovery(self):
        sid = self._create_guided_session_with_nicotine(target_ml=10)
        steps = self._guided_session_steps(sid)
        first = next(s for s in steps if s['payload'].get('step_type') == 'add_ingredient')
        self.assertEqual(first['payload']['category'].lower(), 'nicotine')
        no, step = self._advance_to_nicotine_add_step(sid)
        target = round(step['payload']['target_grams'], 2)
        endpoint = f'/api/mix/guided/session/{sid}/steps/{no}/complete'
        self.client.post(endpoint,json={'measured_grams':target+0.01})
        return sid, no, target, endpoint

    def test_residue_unknown_until_read_and_zero_survives_reload(self):
        sid, no, target, endpoint = self.start_recovery()
        body = self.client.post(endpoint,json={'nicotine_action':'poured_back'}).get_json()
        self.assertIsNone(body['reading']['needed_grams'])
        step = next(s for s in body['session']['steps'] if s['step_no']==no)
        self.assertTrue(step['payload']['nicotine_residue_reading_required'])
        self.assertNotIn('measured_grams',step['payload'])
        self.client.post(endpoint,json={'measured_grams':0.08})
        zero = self.client.post(endpoint,json={'measured_grams':0}).get_json()
        self.assertEqual(zero['reading']['needed_grams'],target)
        fresh = self.client.get(f'/api/mix/guided/session/{sid}').get_json()['session']
        step = next(s for s in fresh['steps'] if s['step_no']==no)
        self.assertEqual(step['payload']['recovery_current_scale_grams'],0)
        self.assertIsNone(step['completed_at'])

    def test_only_original_target_finishes_without_changing_carriers(self):
        sid, no, target, endpoint = self.start_recovery()
        before = self._base_step_targets(sid)
        self.client.post(endpoint,json={'nicotine_action':'poured_back'})
        for weight in (target+0.01,0.08,target-0.01):
            body=self.client.post(endpoint,json={'measured_grams':weight,'accept_under':True}).get_json()
            step=next(s for s in body['session']['steps'] if s['step_no']==no)
            self.assertIsNone(step['completed_at'])
            self.assertEqual(body['session']['progress']['current_step_no'],no)
        hit=self.client.post(endpoint,json={'measured_grams':target}).get_json()
        step=next(s for s in hit['session']['steps'] if s['step_no']==no)
        self.assertIsNotNone(step['completed_at'])
        self.assertEqual(step['payload']['measured_grams'],target)
        self.assertEqual(before,self._base_step_targets(sid))
        events=self._nicotine_recovery_log(sid)
        self.assertEqual(sum(e.get('event')=='nicotine_recovery_complete' for e in events),1)

    def test_stale_acceptance_and_reading_cannot_skip_pourback(self):
        sid,no,target,endpoint=self.start_recovery()
        for payload in ({'measured_grams':target},{'nicotine_action':'confirm_accept_over'}):
            body=self.client.post(endpoint,json=payload).get_json()
            step=next(s for s in body['session']['steps'] if s['step_no']==no)
            self.assertIsNone(step['completed_at'])
            self.assertTrue(step['payload']['nicotine_overpour_pending'])
