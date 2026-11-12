import io
import unittest
from PIL import Image
from bs4 import BeautifulSoup
from tests import test_recipe_builder_create as base
from db import get_db
from services import recipe_story_service as stories

class RecipeStoryTests(unittest.TestCase):
    setUp = base.RecipeBuilderCreateTests.setUp
    tearDown = base.RecipeBuilderCreateTests.tearDown
    _login = base.RecipeBuilderCreateTests._login

    def create(self, **kwargs):
        self._login(1,'member1','member')
        response = self.client.post('/recipes/save',data=base._valid_create_form(**kwargs))
        self.assertEqual(response.status_code,302)
        with base.flask_app.app_context():
            row = get_db().execute('SELECT id,current_version_id FROM recipes ORDER BY id DESC LIMIT 1').fetchone()
            return response,dict(row)

    def test_round_trip_story_and_private_notes_do_not_leak(self):
        response,row = self.create(visibility='public',short_description='A biscuit custard',description='The full story',profiles=['Dessert','Custard'],steep_days='14',private_notes='MY SECRET EXPERIMENT')
        page = self.client.get(response.location)
        self.assertEqual(page.status_code,200)
        self.assertIn(b'MY SECRET EXPERIMENT',page.data)
        self.assertIn(b'The full story',page.data)
        self.assertIn(b'14 days',page.data)
        edit = self.client.get(response.location + '/edit')
        self.assertIn(b'A biscuit custard',edit.data)
        self._login(2,'member2','member')
        public = self.client.get(response.location)
        self.assertEqual(public.status_code,200)
        self.assertNotIn(b'MY SECRET EXPERIMENT',public.data)
        self.assertIn(b'The full story',public.data)

    def test_unknown_zero_and_version_history_are_distinct(self):
        _,row = self.create(steep_days='')
        with base.flask_app.app_context():
            self.assertIsNone(stories.read(row['current_version_id'])['steep_days'])
        self.client.post('/recipes/save',data=base._valid_create_form(recipe_id=str(row['id']),steep_days='0',short_description='New version'))
        with base.flask_app.app_context():
            db=get_db();new=db.execute('SELECT current_version_id FROM recipes WHERE id=?',(row['id'],)).fetchone()[0]
            self.assertEqual(stories.read(new)['steep_days'],0)
            self.assertIsNone(stories.read(row['current_version_id'])['steep_days'])
            self.assertEqual(stories.read(row['current_version_id'])['short_description'],'')

    def test_invalid_story_preserves_form_and_does_not_create(self):
        self._login(1,'member1','member')
        for days in ['-1','1.5','NaN','366']:
            response=self.client.post('/recipes/save',data=base._valid_create_form(steep_days=days,short_description='Keep my words'))
            self.assertEqual(response.status_code,200)
            self.assertIn(b'Keep my words',response.data)
            self.assertIn(b'Steep time must',response.data)
        with base.flask_app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM recipe_stories').fetchone()[0],0)

    def test_photo_is_normalized_private_and_preserved_between_versions(self):
        output=io.BytesIO();Image.new('RGB',(100,200),'orange').save(output,'PNG');output.seek(0)
        _,row=self.create(recipe_image=(output,'photo.png'))
        url=f'/recipes/image/{row["current_version_id"]}'
        image=self.client.get(url)
        self.assertEqual(image.status_code,200)
        self.assertEqual(image.mimetype,'image/webp')
        self.assertEqual(Image.open(io.BytesIO(image.data)).size,(1200,675))
        self.assertEqual(image.headers['Cache-Control'],'private, no-store')
        self._login(2,'member2','member')
        self.assertEqual(self.client.get(url).status_code,404)
        self._login(1,'member1','member')
        self.client.post('/recipes/save',data=base._valid_create_form(recipe_id=str(row['id'])))
        with base.flask_app.app_context():
            version=get_db().execute('SELECT current_version_id FROM recipes WHERE id=?',(row['id'],)).fetchone()[0]
        self.assertEqual(self.client.get(f'/recipes/image/{version}').data,image.data)
        self.client.post('/recipes/save',data=base._valid_create_form(recipe_id=str(row['id']),remove_image='1'))
        with base.flask_app.app_context():
            version=get_db().execute('SELECT current_version_id FROM recipes WHERE id=?',(row['id'],)).fetchone()[0]
        self.assertEqual(self.client.get(f'/recipes/image/{version}').status_code,404)
        self.assertEqual(self.client.get(url).status_code,200)

    def test_bad_image_is_rejected_without_losing_words(self):
        self._login(1,'member1','member')
        response=self.client.post('/recipes/save',data=base._valid_create_form(short_description='Keep this',recipe_image=(io.BytesIO(b'<svg onload="alert(1)"></svg>'),'fake.png')))
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Keep this',response.data)
        self.assertIn(b'This image could not be read',response.data)
        with base.flask_app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM recipe_stories').fetchone()[0],0)

    def test_preview_and_drafts_have_unique_fields_and_scoped_key(self):
        self._login(1,'member1','member')
        response=self.client.get('/recipes/new');page=BeautifulSoup(response.data,'html.parser')
        ids=[x['id'] for x in page.select('[id]')]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(page.select_one('#recipeBuilderForm')['enctype'],'multipart/form-data')
        self.assertIn(b'tvw-recipe-draft:1:new',response.data)
        self.assertEqual(len(page.select('input[name="profiles"]')),len(stories.PROFILES))
