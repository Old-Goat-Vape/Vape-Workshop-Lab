"""A recovery forecast includes remaining VG rather than only liquid poured so far."""
import unittest
from tests.test_tvw_api import TVWApiTests

class RecoveryRatioPreviewTests(unittest.TestCase):
    setUp=TVWApiTests.setUp
    tearDown=TVWApiTests.tearDown
    _create_guided_session_with_nicotine=TVWApiTests._create_guided_session_with_nicotine
    _advance_to_add_ingredient_category=TVWApiTests._advance_to_add_ingredient_category
    _guided_session_steps=TVWApiTests._guided_session_steps

    def test_pg_trim_projects_remaining_vg_after_nicotine_and_flavour(self):
        sid=self._create_guided_session_with_nicotine(target_ml=10)
        no,step=self._advance_to_add_ingredient_category(sid,'concentrate')
        target=round(step['payload']['target_grams'],2)
        body=self.client.post(f'/api/mix/guided/session/{sid}/steps/{no}/complete',json={'measured_grams':target+.02}).get_json()
        self.assertTrue(body['reading']['base_adjusted'])
        pg=next(s['payload'] for s in body['session']['steps'] if s['payload'].get('category')=='pg' and s['payload'].get('step_type')=='add_ingredient')
        self.assertIn('Estimated final ratio: 30/70',pg['pg_adjustment_note'])
        self.assertLess(pg['target_grams'],pg['original_pg_target_grams'])
