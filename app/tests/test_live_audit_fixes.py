"""Regressions for the November live member audit."""
import unittest
from tests.test_tvw_api import TVWApiTests
from tests.test_recipe_builder_create import RecipeBuilderCreateTests, _valid_create_form
from app import app as flask_app
from db import get_db
from core.calculator import ingredient_over_requires_recovery, tvw_mix_policy

class LiveAuditFixTests(unittest.TestCase):
    setUp=TVWApiTests.setUp
    tearDown=TVWApiTests.tearDown
    def test_impossible_mix_never_creates_a_session(self):
        for endpoint in ('/api/mix/quick','/api/mix/guided/session'):
            for nicotine in (150,100):
                with self.subTest(endpoint=endpoint,nicotine=nicotine):
                    response=self.client.post(endpoint,json=dict(recipe_id=1,version_id=1,target_ml=10,target_nic_mg_ml=nicotine,nic_base_mg_ml=100))
                    self.assertEqual(response.status_code,400)
        with flask_app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM mix_sessions').fetchone()[0],0)
    def test_live_calc_preserves_zeroes(self):
        response=self.client.post('/api/recipes/live-calc',json=dict(target_ml=10,target_nic_mg_ml=0,nic_base_mg_ml=100,pg_ratio=0,vg_ratio=100,rows=[]))
        self.assertEqual(response.status_code,200)
        totals=response.get_json()['result']['totals']
        self.assertEqual(totals['nicotine_ml'],0)
        self.assertEqual(totals['pg_ml'],0)
        self.assertEqual(totals['vg_ml'],10)
    def test_unreachable_ratio_keeps_volume_and_nicotine_target(self):
        response=self.client.post('/api/mix/quick',json=dict(recipe_id=1,version_id=1,target_ml=100,target_nic_mg_ml=3,nic_base_mg_ml=100,pg_ratio=0,vg_ratio=100))
        self.assertEqual(response.status_code,200)
        totals=response.get_json()['result']['totals']
        self.assertFalse(totals['ratio_achievable'])
        self.assertAlmostEqual(totals['flavor_ml']+totals['nicotine_ml']+totals['pg_ml']+totals['vg_ml'],100)
        self.assertEqual(totals['nicotine_ml'],3)

    def test_invalid_numbers_and_zero_batch_are_rejected(self):
        for value in (0,-1,'nan','inf'):
            with self.subTest(value=value):
                response=self.client.post('/api/mix/guided/session',json=dict(recipe_id=1,target_ml=value,target_nic_mg_ml=0))
                self.assertEqual(response.status_code,400)
    def test_stash_search_beyond_seed_excludes_hidden_aliases(self):
        with flask_app.app_context():
            db=get_db()
            db.executemany("INSERT INTO flavours (canonical_name,brand,category) VALUES (?,'AAA','Flavor')",[(f'Other {i}',) for i in range(1100)])
            db.execute("INSERT INTO flavours (id,canonical_name,brand,category,source_flavor_ref_id) VALUES (2000,'Vanilla Swirl','TFA','Flavor',101)")
            db.execute("INSERT INTO flavours (id,canonical_name,brand,category,is_hidden) VALUES (2001,'Vanilla Swirl hidden','TFA','Flavor',1)")
            db.execute("INSERT INTO flavours (id,canonical_name,brand,category,merged_into_flavour_id) VALUES (2002,'Vanilla Swirl alias','TFA','Flavor',2000)")
            db.commit()
        response=self.client.get('/api/stash/flavour-search?q=Vanilla%20Swirl')
        self.assertEqual(response.status_code,200)
        self.assertEqual([r['id'] for r in response.get_json()['items']],[2000])
        self.assertIn(self.client.post('/api/stash',json={'flavour_id':2000,'quantity_ml':15}).status_code,(200,201))
        self.assertIn(b'Vanilla Swirl',self.client.get('/stash').data)
    def test_wcm_includes_own_private_not_another_users(self):
        with flask_app.app_context():
            db=get_db()
            db.execute("UPDATE recipes SET current_version_id=1,slug='owner-private',visibility='private' WHERE id=1")
            db.execute("INSERT INTO recipes (id,name,status,visibility,active,created_by,current_version_id,slug) VALUES (2,'Other private','WIP','private',1,2,2,'other-private')")
            db.execute("INSERT INTO recipe_versions (id,recipe_id,version_number,total_flavor_percent) VALUES (2,2,'1',5)")
            db.execute("INSERT INTO recipe_items (version_id,ingredient_id,amount_percent) VALUES (2,101,5)")
            db.commit()
        response=self.client.get('/what-can-i-make')
        self.assertIn(b'Recipe1',response.data)
        self.assertNotIn(b'Other private',response.data)
    def test_profile_renders_html_and_keeps_private_recipes_private(self):
        with flask_app.app_context():
            db=get_db()
            db.execute("UPDATE recipes SET current_version_id=1,slug='secret-owner',visibility='private' WHERE id=1")
            db.commit()
        own=self.client.get('/profile/member1')
        self.assertEqual(own.status_code,200)
        self.assertIn(b'Recipe1',own.data)
        self.assertIn(b'<h1>member1</h1>',own.data)
        with flask_app.app_context():
            db=get_db()
            db.execute("INSERT INTO users (id,username,password_hash,role,status) VALUES (3,'member3','x','member','Active')")
            db.commit()
        with self.client.session_transaction() as sess:
            sess['user_id']=3
            sess['username']='member3'
        other=self.client.get('/profile/member1')
        self.assertEqual(other.status_code,200)
        self.assertNotIn(b'Recipe1',other.data)
        self.assertEqual(self.client.get('/profile/does-not-exist').status_code,404)

    def test_small_batch_slack_precedes_percentage_threshold(self):
        args=dict(ingredient_category='flavor',display_target=.21,batch_size_ml=10,policy=tvw_mix_policy())
        self.assertFalse(ingredient_over_requires_recovery(measured=.23,**args))
        self.assertTrue(ingredient_over_requires_recovery(measured=.50,**args))

    def test_stash_batch_matches_recipe_default_and_selected_override(self):
        from bs4 import BeautifulSoup
        with flask_app.app_context():
            db=get_db()
            db.execute("UPDATE recipes SET current_version_id=1,slug='recipe-one' WHERE id=1")
            db.execute('UPDATE recipe_versions SET default_batch_ml=15 WHERE id=1')
            db.commit()
        for query,expected in (('',15),('?batch_ml=60',60)):
            response=self.client.get('/recipes/recipe-one'+query)
            self.assertEqual(response.status_code,200,response.data)
            soup=BeautifulSoup(response.data,'html.parser')
            selected=soup.select_one('#batch_ml_sel option[selected]')
            self.assertEqual(float(selected['value']),expected)
            self.assertIn(f'Stash check uses your saved flavours for {expected} ml.',soup.get_text(' ',strip=True))

class LiveAuditBuilderTests(unittest.TestCase):
    setUp=RecipeBuilderCreateTests.setUp
    tearDown=RecipeBuilderCreateTests.tearDown
    _login=RecipeBuilderCreateTests._login
    def test_impossible_recipe_rejected_without_writing(self):
        self._login(1,'member1','member')
        response=self.client.post('/recipes/save',data=_valid_create_form(calc_target_nic='150'))
        self.assertIn(b'Target nicotine cannot exceed',response.data)
        with flask_app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM recipes').fetchone()[0],0)
