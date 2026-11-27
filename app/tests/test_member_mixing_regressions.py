import unittest
from bs4 import BeautifulSoup
from tests import test_tvw_api as base
from db import get_db
from config.recipe_quality import is_reference_collection


class MemberMixingRegressions(unittest.TestCase):
    setUp = base.TVWApiTests.setUp
    tearDown = base.TVWApiTests.tearDown
    _guided_first_add_step = base.TVWApiTests._guided_first_add_step
    _trigger_serious_flavour_overpour = base.TVWApiTests._trigger_serious_flavour_overpour
    _first_add_ingredient_step = base.TVWApiTests._first_add_ingredient_step

    def test_recovery_reweigh_returns_to_original_pour_and_can_recover_again(self):
        sid = self.client.post('/api/mix/guided/session', json={'recipe_id':1,'target_ml':30,'target_nic_mg_ml':0}).json['mix_session_id']
        while True:
            session=self.client.get(f'/api/mix/guided/session/{sid}').json['session']
            current=next(s for s in session['steps'] if s['step_no']==session['progress']['current_step_no'])
            if current['payload']['step_type']=='add_ingredient':break
            self.client.post(f"/api/mix/guided/session/{sid}/steps/{current['step_no']}/complete",json={})
        _, session = self._trigger_serious_flavour_overpour(sid)
        recovery_no = session['progress']['current_step_no']
        response = self.client.post(f'/api/mix/guided/session/{sid}/steps/{recovery_no}/complete',json={'undo':True})
        self.assertEqual(response.status_code,200,response.json)
        session = response.json['session']
        current = next(s for s in session['steps'] if s['step_no']==session['progress']['current_step_no'])
        self.assertEqual(current['payload']['step_type'],'add_ingredient')
        self.assertNotIn('measured_grams',current['payload'])
        self.assertNotIn('overpour_recovery_pending',current['payload'])
        # Another genuinely large reading must create a fresh recovery, not silently accept it.
        result=self.client.post(f"/api/mix/guided/session/{sid}/steps/{current['step_no']}/complete",json={'measured_grams':50})
        self.assertTrue(result.json['awaiting_overpour_recovery'])
        stale=self.client.post(f'/api/mix/guided/session/{sid}/steps/{recovery_no}/complete',json={'undo':True})
        self.assertEqual(stale.json['status'],'error')

    def test_reference_collections_are_not_mixable_or_analytics(self):
        with base.flask_app.app_context():
            db=get_db();db.execute("UPDATE recipes SET name='Capella Single Flavor Tests by SessionDrummer',recipe_code='VCREF-650',current_version_id=1 WHERE id=1");db.commit()
            from models.recipes_repo import tvw_recipe_analytics_predicate_sql
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recipes r WHERE '+tvw_recipe_analytics_predicate_sql()).fetchone()[0],0)
        for endpoint in ('/api/mix/quick','/api/mix/guided/session'):
            response=self.client.post(endpoint,json={'recipe_id':1,'target_ml':10,'target_nic_mg_ml':0})
            self.assertEqual(response.status_code,400,response.json)
        self.assertNotIn(b'Capella Single Flavor Tests',self.client.get('/what-can-i-make').data)
        self.assertFalse(is_reference_collection('Test Kitchen Custard'))
        self.assertFalse(is_reference_collection('The Golden Swirl'))

    def test_zero_percentage_cannot_be_mixed(self):
        with base.flask_app.app_context():
            db=get_db();db.execute('UPDATE recipe_items SET amount_percent=0 WHERE version_id=1');db.commit()
        self.assertEqual(self.client.post('/api/mix/quick',json={'recipe_id':1,'target_ml':10,'target_nic_mg_ml':0}).status_code,400)

    def test_exact_alias_links_and_stock_without_crossing_versions(self):
        with base.flask_app.app_context():
            db=get_db()
            db.executemany("INSERT INTO flavor_reference(id,brand,name,category) VALUES(?,?,?,'Flavor')",[(102,'TFA','Vanilla Swirl Concentrate ()'),(103,'TFA','Vanilla Swirl'),(104,'TFA','Vanilla Swirl V2')])
            db.execute("INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(2,'TFA','Vanilla Swirl','Flavor',103)")
            db.commit()
            from services.what_can_i_make_service import map_reference_ids_to_flavour_pages,_expand_equivalent_stock
            self.assertEqual(map_reference_ids_to_flavour_pages({102,104}),{102:2,104:None})
            self.assertEqual(_expand_equivalent_stock({103:12})[102],12)
            self.assertNotIn(104,_expand_equivalent_stock({103:12}))
            db.execute('DELETE FROM recipe_items WHERE version_id=1')
            db.executemany('INSERT INTO recipe_items(version_id,ingredient_id,amount_percent) VALUES(1,?,2)',[(102,),(103,)])
            from services.what_can_i_make_service import fetch_version_ingredients_map
            items=fetch_version_ingredients_map([1])[1]
            self.assertEqual(len(items),1)
            self.assertEqual(items[0]['amount_percent'],4)
            from config.catalogue_identity import canonical_item
            self.assertEqual(canonical_item('FA','Meringue Concentrate ()')[2],canonical_item('Flavour Art','Meringue')[2])
            self.assertNotEqual(canonical_item('FA','Meringue')[2],canonical_item('TFA','Meringue')[2])

    def test_guide_real_recipe_presets_and_no_write(self):
        with base.flask_app.app_context():
            db=get_db()
            db.execute("UPDATE recipes SET status='Ready',visibility='public',slug='real-custard',current_version_id=1 WHERE id=1")
            db.execute("INSERT INTO flavor_reference(id,brand,name,category) VALUES(102,'Test','Cream','Flavor')")
            db.execute("INSERT INTO recipe_items(version_id,ingredient_id,amount_percent) VALUES(1,102,1)")
            db.executemany("INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(?,'Test',?,'Flavor',?)",[(2,'Vanilla',101),(3,'Cream',102)])
            db.commit()
        response=self.client.get('/first-mix')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'real-custard',response.data)
        self.assertIn(b'target_nic_mg_ml=0',response.data)
        page=BeautifulSoup(response.data,'html.parser')
        link=next(a['href'] for a in page.select('a[href]') if '/recipes/real-custard?' in a['href'])
        recipe=BeautifulSoup(self.client.get(link).data,'html.parser')
        self.assertEqual(recipe.select_one('#target_ml')['value'],'10')
        self.assertEqual(recipe.select_one('#target_nic')['value'],'0')
        with base.flask_app.app_context():self.assertEqual(get_db().execute('SELECT COUNT(*) FROM mix_sessions').fetchone()[0],0)


class CoachingWordingRegressions(unittest.TestCase):
    def test_unclassified_overpour_is_not_called_slight(self):
        from core.calculator import build_live_coach_summary
        for count in (1, 2):
            summary=build_live_coach_summary(tone_profile='tvw',performance_summary={'overshoot_count':count,'recovery_count':1})
            self.assertNotIn('slight',str(summary).lower())
