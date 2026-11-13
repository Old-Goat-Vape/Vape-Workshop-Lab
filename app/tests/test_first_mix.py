import unittest
from bs4 import BeautifulSoup
from tests import test_recipe_builder_create as base
from db import get_db

class FirstMixTests(unittest.TestCase):
    setUp=base.RecipeBuilderCreateTests.setUp
    tearDown=base.RecipeBuilderCreateTests.tearDown
    _login=base.RecipeBuilderCreateTests._login

    def test_optional_guide_and_links_do_not_create_records(self):
        self._login(1,'member1','member')
        with base.flask_app.app_context():before=get_db().execute('SELECT COUNT(*) FROM recipes').fetchone()[0]
        response=self.client.get('/first-mix')
        self.assertEqual(response.status_code,200)
        page=BeautifulSoup(response.data,'html.parser')
        self.assertEqual(len(page.select('.guide-panel')),5)
        self.assertFalse(any(p.has_attr('hidden') for p in page.select('.guide-panel')))
        self.assertIn(b'nicotine is always first',response.data)
        self.assertIn(b'2 ml plain PG',response.data)
        self.assertTrue(page.select_one('a[href="/recipes/new?practice=1"]'))
        self.assertIn(b'Your first mix guide',response.data)
        with base.flask_app.app_context():self.assertEqual(get_db().execute('SELECT COUNT(*) FROM recipes').fetchone()[0],before)

    def test_practice_presets_and_separate_draft_slot(self):
        self._login(1,'member1','member')
        page=BeautifulSoup(self.client.get('/recipes/new?practice=1').data,'html.parser')
        self.assertEqual(page.select_one('#calcTargetMl')['value'],'10')
        self.assertEqual(page.select_one('#calcTargetNic')['value'],'0')
        self.assertEqual(page.select_one('#calcTargetNicHidden')['value'],'0')
        self.assertEqual(page.select_one('[name="practice_mode"]')['value'],'1')
        self.assertIn('new-practice',str(page))
        ordinary=self.client.get('/recipes/new')
        self.assertNotIn(b'new-practice',ordinary.data)
        self.assertNotIn(b'Practice draft:',ordinary.data)

    def test_practice_recipe_stays_private_and_zero_when_saved(self):
        self._login(1,'member1','member')
        response=self.client.post('/recipes/save',data=base._valid_create_form(name='My practice',practice_mode='1',calc_target_ml='10',calc_target_nic='0'))
        self.assertEqual(response.status_code,302)
        with base.flask_app.app_context():
            db=get_db();recipe=db.execute('SELECT * FROM recipes ORDER BY id DESC LIMIT 1').fetchone()
            version=db.execute('SELECT * FROM recipe_versions WHERE id=?',(recipe['current_version_id'],)).fetchone()
            self.assertEqual(recipe['visibility'],'private')
            self.assertEqual(version['default_batch_ml'],10)
            self.assertEqual(version['default_nic_mg_ml'],0)
        with self.client.session_transaction() as state:self.assertTrue(state['recipe_builder_saved']['draft_key'].endswith(':new-practice'))

    def test_signed_out_guide_requires_login(self):
        self.assertEqual(self.client.get('/first-mix').status_code,302)
