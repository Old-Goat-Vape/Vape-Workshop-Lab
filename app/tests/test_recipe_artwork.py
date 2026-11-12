import unittest
from pathlib import Path
from PIL import Image
from flask import render_template_string
from werkzeug.datastructures import MultiDict
from tests import test_recipe_builder_create as base
from db import get_db
from services import recipe_artwork_service as art, recipe_story_service as stories
from models import recipes_repo

class RecipeArtworkTests(unittest.TestCase):
    setUp=base.RecipeBuilderCreateTests.setUp
    tearDown=base.RecipeBuilderCreateTests.tearDown
    _login=base.RecipeBuilderCreateTests._login

    def test_groups_tags_and_unknown_defaults(self):
        for profile,key in art.PROFILE_ART.items():
            self.assertTrue(art.choose_artwork([profile])['filename'].endswith(f'{key}.webp'))
        self.assertEqual(art.choose_artwork('bad json')['filename'],art.DEFAULT_ART)
        self.assertEqual(art.choose_artwork(tags='reference, custard')['profile'],'Custard')
        self.assertEqual(art.choose_artwork(['Fruit','Custard'],tags='dessert')['profile'],'Fruit')
        self.assertEqual(art.choose_artwork(['Custard','Fruit'])['profile'],'Custard')
        self.assertIsNone(art.choose_artwork(tags='not a category')['profile'])

    def test_all_artwork_assets_are_optimized_and_present(self):
        static=Path(__file__).resolve().parents[1]/'static'
        for key in set(art.PROFILE_ART.values()):
            path=static/f'img/recipe-profiles/{key}.webp'
            with Image.open(path) as image:
                self.assertEqual(image.size,(960,540))
            self.assertLess(path.stat().st_size,100000)
        self.assertTrue((static/art.DEFAULT_ART).is_file())

    def test_primary_profile_validation_and_order(self):
        values,errors=stories.parse(MultiDict([('profiles','Fruit'),('profiles','Custard'),('main_profile','Custard')]))
        self.assertFalse(errors);self.assertEqual(values['profiles'],['Custard','Fruit'])
        _,errors=stories.parse(MultiDict([('profiles','Fruit'),('main_profile','Tobacco')]))
        self.assertIn('main_profile',errors)

    def test_mixer_photo_wins_and_macro_has_clear_fallback(self):
        with base.flask_app.test_request_context('/'):
            own=art.recipe_artwork(['Fruit'],has_image=True,version_id=99)
            self.assertFalse(own['automatic']);self.assertIn('/recipes/image/99',own['url'])
            html=render_template_string('{% from "partials/tvw_recipe_picture.html" import recipe_picture %}{{ recipe_picture(["Custard"]) }}')
            self.assertIn('cream.webp',html);self.assertIn('Custard profile artwork',html)
            generic=render_template_string('{% from "partials/tvw_recipe_picture.html" import recipe_picture %}{{ recipe_picture() }}')
            self.assertIn('mixing-bench.webp',generic)

    def test_primary_persists_with_version_and_reopens(self):
        self._login(1,'member1','member')
        response=self.client.post('/recipes/save',data=base._valid_create_form(profiles=['Fruit','Custard'],main_profile='Custard'))
        self.assertEqual(response.status_code,302)
        with base.flask_app.app_context():
            row=get_db().execute('SELECT id,current_version_id FROM recipes ORDER BY id DESC LIMIT 1').fetchone()
            version=row['current_version_id'];self.assertEqual(stories.read(version)['main_profile'],'Custard')
            rows,_=recipes_repo.fetch_tvw_library(viewer_user_id=1,owner_user_id=1)
            saved=next(r for r in rows if r['id']==row['id'])
            self.assertEqual(art.choose_artwork(saved['recipe_profiles_json'])['profile'],'Custard')
        detail=self.client.get(response.location)
        self.assertIn(b'cream.webp',detail.data)
        edit=self.client.get(response.location+'/edit')
        self.assertIn(b'value="Custard" selected',edit.data)
