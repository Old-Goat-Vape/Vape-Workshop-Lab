"""Conservative inference, permissions, stale previews and safe undo."""
import os
os.environ.setdefault('TVW_ENABLE_LAB_MODULES','true')
import json
import re
import unittest
from tests import test_recipe_builder_create as base
from db import get_db
from services import recipe_profile_service as profiles


def ingredient(name, amount=4):
    return dict(name=name, amount_percent=amount)


class SuggestionTests(unittest.TestCase):
    def test_finished_dessert_beats_largest_fruit(self):
        result=profiles.suggest('Strawberry Cheesecake',ingredients=[ingredient('Strawberry',6),ingredient('New York Cheesecake',2)])
        self.assertEqual(result['profiles'],['Dessert','Berry'])
        self.assertEqual(result['confidence'],'high')

    def test_component_cheesecake_still_leads_with_dessert(self):
        result=profiles.suggest('Strawberry Cheesecake',ingredients=[ingredient('Strawberry',6),ingredient('Cream',2),ingredient('Graham Crust',1)])
        self.assertEqual(result['profiles'][0],'Dessert')
        self.assertEqual(result['confidence'],'high')

    def test_unknown_ingredients_prevent_bulk_guess(self):
        result=profiles.suggest('Strawberry Cheesecake',ingredients=[ingredient('Strawberry',1),ingredient('Mystery Base',7)])
        self.assertNotEqual(result['confidence'],'high')
        result=profiles.suggest('Old Goat Number Nine',ingredients=[ingredient('Mystery Base')])
        self.assertEqual(result['confidence'],'unknown')
        self.assertEqual(result['profiles'],[])

    def test_boundaries_supports_and_mixed_recipes(self):
        self.assertNotIn('Nut',profiles.matches('Coconut'))
        self.assertNotIn('Menthol',profiles.matches('Licorice'))
        result=profiles.suggest('Raspberry Lime',ingredients=[ingredient('Raspberry',4),ingredient('Lime',2),ingredient('Super Sweet',1)])
        self.assertEqual(result['confidence'],'high')
        self.assertEqual(result['profiles'][0],'Berry')
        self.assertNotIn('Candy',result['profiles'])
        result=profiles.suggest('Mystery',ingredients=[ingredient('Cream',4),ingredient('Tobacco',4)])
        self.assertEqual(result['confidence'],'review')

    def test_existing_dessert_tag_is_not_overruled_by_cream(self):
        result=profiles.suggest('Experiment',ingredients=[ingredient('Cream')],tags=['dessert'])
        self.assertEqual(result['profiles'][0],'Dessert')
        result=profiles.suggest('Experiment',ingredients=[ingredient('Cream')],tags=['tobacco'])
        self.assertEqual(result['confidence'],'review')

    def test_description_cannot_supply_false_certainty(self):
        result=profiles.suggest('Experiment','I tried custard but removed it.',[ingredient('Cream',4),ingredient('Lime',4)])
        self.assertEqual(result['confidence'],'review')
        self.assertNotIn('Custard',result['profiles'])


class ProfileIntegrationTests(unittest.TestCase):
    setUp=base.RecipeBuilderCreateTests.setUp
    tearDown=base.RecipeBuilderCreateTests.tearDown
    def _login(self, user_id, username, role):
        with self.client.session_transaction() as state:
            state.clear()
        base.RecipeBuilderCreateTests._login(self,user_id,username,role)

    def create(self, **overrides):
        self._login(1,'member1','member')
        overrides.setdefault('tags','')
        response=self.client.post('/recipes/save',data=base._valid_create_form(**overrides))
        self.assertEqual(response.status_code,302)
        with base.flask_app.app_context():
            return get_db().execute('SELECT id,current_version_id FROM recipes ORDER BY id DESC LIMIT 1').fetchone()

    def test_member_cannot_read_or_apply_private_catalogue(self):
        self.create()
        self.assertNotEqual(self.client.get('/master-admin/recipe-profiles').status_code,200)
        self.assertNotEqual(self.client.post('/master-admin/recipe-profiles').status_code,200)

    def test_bulk_preserves_formula_story_photo_privacy_and_undo(self):
        row=self.create(name='Vanilla Cream',description='A creamy mix',short_description='Short',steep_days='14',private_notes='Private')
        existing=self.create(name='Already selected',profiles=['Fruit'])
        with base.flask_app.app_context():
            db=get_db()
            db.execute('UPDATE recipe_stories SET image_data=? WHERE version_id=?',(b'photo',row['current_version_id']));db.commit()
            before=[tuple(r) for r in db.execute('SELECT * FROM recipe_items')]
            preview=profiles.preview()
            self.assertEqual(len(preview),1)
            self.assertEqual(preview[0]['confidence'],'high')
            count,batch=profiles.apply(profiles.selection_digest(preview),3)
            self.assertEqual(count,1)
            story=db.execute('SELECT * FROM recipe_stories WHERE version_id=?',(row['current_version_id'],)).fetchone()
            self.assertEqual(json.loads(story['profiles_json']),['Cream'])
            self.assertEqual(story['image_data'],b'photo')
            self.assertEqual(story['steep_days'],14)
            self.assertEqual(story['description'],'A creamy mix')
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM recipe_items')])
            self.assertEqual(db.execute('SELECT visibility FROM recipes WHERE id=?',(row['id'],)).fetchone()[0],'private')
            self.assertEqual(json.loads(db.execute('SELECT profiles_json FROM recipe_stories WHERE version_id=?',(existing['current_version_id'],)).fetchone()[0]),['Fruit'])
            self.assertEqual(profiles.undo(batch),1)
            self.assertEqual(len(profiles.preview()),1)
            self.assertEqual(profiles.undo(batch),0)

    def test_stale_preview_rejected_atomically(self):
        row=self.create(name='Vanilla Cream')
        with base.flask_app.app_context():
            db=get_db();digest=profiles.selection_digest(profiles.preview())
            db.execute('UPDATE recipe_items SET amount_percent=2 WHERE version_id=?',(row['current_version_id'],));db.commit()
            with self.assertRaises(ValueError):profiles.apply(digest,3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recipe_profile_audit').fetchone()[0],0)

    def test_undo_preserves_later_owner_profile_edit(self):
        row=self.create(name='Vanilla Cream')
        with base.flask_app.app_context():
            db=get_db();_,batch=profiles.apply(profiles.selection_digest(profiles.preview()),3)
            db.execute('UPDATE recipe_stories SET profiles_json=? WHERE version_id=?',('["Custard"]',row['current_version_id']));db.commit()
            self.assertEqual(profiles.undo(batch),0)
            self.assertEqual(db.execute('SELECT profiles_json FROM recipe_stories WHERE version_id=?',(row['current_version_id'],)).fetchone()[0],'["Custard"]')

    def test_lab_preview_apply_and_token_protection(self):
        self.create(name='Vanilla Cream')
        self._login(3,'Old Goat','Old Goat')
        response=self.client.get('/master-admin/recipe-profiles')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Vanilla Cream',response.data)
        token=re.search(rb'name="preview_token" value="([^"]+)"',response.data)[1].decode()
        bad=self.client.post('/master-admin/recipe-profiles',data=dict(action='apply',preview_token=token+'bad'),follow_redirects=True)
        self.assertIn(b'invalid',bad.data)
        good=self.client.post('/master-admin/recipe-profiles',data=dict(action='apply',preview_token=token),follow_redirects=True)
        self.assertIn(b'Assigned profiles to 1 recipes',good.data)
        self.assertIn(b'Undo this batch',good.data)

    def test_unknown_can_be_manually_assigned(self):
        row=self.create(name='Mystery')
        with base.flask_app.app_context():
            db=get_db();db.execute("UPDATE flavor_reference SET name='Unidentified ' || id");db.commit()
            preview=profiles.preview()
            self.assertEqual(preview[0]['confidence'],'unknown')
            count,_=profiles.apply(profiles.selection_digest(preview),3,row['id'],'Dessert')
            self.assertEqual(count,1)
