"""Regressions reproduced during live sessions 74 and 75."""
import json
import unittest

from app import app as flask_app
from db import get_db
from tests.test_tvw_api import TVWApiTests


class LiveCompletionAccuracyTests(unittest.TestCase):
    setUp = TVWApiTests.setUp
    tearDown = TVWApiTests.tearDown

    def create(self):
        with flask_app.app_context():
            db = get_db()
            db.execute('UPDATE recipe_items SET amount_percent=2')
            db.execute("INSERT INTO flavor_reference (id,brand,name,category,density,active) VALUES (102,'TFA','Vanilla Swirl','Flavor',1.036,1)")
            db.execute('INSERT INTO recipe_items (version_id,ingredient_id,amount_percent) VALUES (1,102,3)')
            db.commit()
        response = self.client.post('/api/mix/guided/session', json=dict(recipe_id=1,target_ml=15,target_nic_mg_ml=0,pg_ratio=30,vg_ratio=70))
        self.assertEqual(response.status_code,201,response.data)
        return response.get_json()['mix_session_id']

    def bundle(self,sid):
        return self.client.get(f'/api/mix/guided/session/{sid}').get_json()['session']

    def complete(self,sid,all_zero=False):
        for _ in range(35):
            bundle = self.bundle(sid)
            if bundle['progress']['all_done']:
                return bundle
            step = next(s for s in bundle['steps'] if not s['completed_at'])
            p = step['payload']
            data = {}
            if p.get('step_type')=='add_ingredient':
                grams = p['display_target_grams']
                if all_zero or p.get('ingredient_id')==101:
                    grams = 0
                elif p.get('ingredient_id')==102:
                    grams = .49
                data = dict(measured_grams=grams,accept_under=True)
            response = self.client.post(f'/api/mix/guided/session/{sid}/steps/{step["step_no"]}/complete',json=data)
            self.assertEqual(response.status_code,200,response.data)
        self.fail('Mix failed to complete')

    def test_zero_accepted_flavour_is_logged_and_actual_composition_reported(self):
        sid = self.create()
        bundle = self.complete(sid)
        summary = bundle['mix_summary']
        self.assertAlmostEqual(summary['estimated_final_volume_ml'],14.70,places=2)
        self.assertEqual(summary['total_flavor_percent'],5)
        self.assertAlmostEqual(summary['estimated_final_flavor_percent'],3.217,delta=.005)
        self.assertEqual(summary['estimated_final_nicotine_mg_ml'],0)
        under = [d for d in summary['accepted_deviations'] if d['kind']=='flavour_under']
        self.assertEqual(len(under),1)
        self.assertIn('0.00 g',under[0]['label'])
        self.assertFalse(any(d['kind']=='carrier_under' and 'PG' in d['ingredient'] for d in summary['accepted_deviations']))
        self.assertEqual(summary['estimated_final_ratio_label'],'29/71')

    def test_existing_completed_zero_pour_is_visible_without_inventing_acceptance(self):
        sid = self.create()
        self.complete(sid)
        with flask_app.app_context():
            db = get_db()
            meta = json.loads(db.execute('SELECT result_json FROM mix_sessions WHERE id=?',(sid,)).fetchone()[0])
            meta['mix_event_log']=[e for e in meta.get('mix_event_log',[]) if e.get('event')!='accepted_flavour_under']
            db.execute('UPDATE mix_sessions SET result_json=? WHERE id=?',(json.dumps(meta),sid))
            db.commit()
        summary = self.bundle(sid)['mix_summary']
        self.assertTrue(any('recorded 0.00 g' in d['label'] for d in summary['accepted_deviations']))

    def test_all_zero_pours_do_not_turn_into_planned_final_volume(self):
        sid = self.create()
        summary = self.complete(sid,all_zero=True)['mix_summary']
        self.assertEqual(summary['estimated_final_volume_ml'],0)
        self.assertIn('no liquid recorded',summary['estimated_final_ratio_label'])
        self.assertIsNone(summary['estimated_final_flavor_percent'])
        self.assertIsNone(summary['estimated_final_nicotine_mg_ml'])

    def test_completed_dashboard_link_returns_to_community(self):
        sid = self.create()
        self.complete(sid)
        from bs4 import BeautifulSoup
        soup=BeautifulSoup(self.client.get(f'/guided-mix/session/{sid}').data,'html.parser')
        dashboard=next(a for a in soup.select('.tvw-ws-complete-actions a') if a.get_text(strip=True)=='DASHBOARD')
        self.assertIn(dashboard['href'],('/', '/community'))
