import unittest
from unittest.mock import patch
from tests import test_recipe_builder_create as base
from services.stash_recipe_guide_service import draft_rows


class StashGuideTests(unittest.TestCase):
    setUp = base.RecipeBuilderCreateTests.setUp
    tearDown = base.RecipeBuilderCreateTests.tearDown
    _login = base.RecipeBuilderCreateTests._login

    def test_empty_stash_and_login(self):
        self.assertEqual(self.client.get('/recipe-guide').status_code, 302)
        self._login(1, 'member1', 'member')
        response = self.client.get('/recipe-guide')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Set up my stash', response.data)
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')

    def test_real_aggregate_choices_and_pairings_render(self):
        from db import get_db
        self._login(1, 'member1', 'member')
        with base.flask_app.app_context():
            db = get_db()
            ids = [r[0] for r in db.execute('SELECT id FROM flavor_reference ORDER BY id LIMIT 2')]
            db.execute('INSERT INTO flavor_recipe_analytics(reference_id,avg_pct,recipe_count) VALUES(?,2,85)', (ids[0],))
            db.execute('INSERT INTO flavor_pairings(reference_id_a,reference_id_b,recipe_count) VALUES(?,?,7)', ids)
            db.commit()
        with patch('services.stash_recipe_guide_service.fetch_stash_reference_qty_ml', return_value=({rid: 2 for rid in ids}, 'test')):
            response = self.client.get(f'/recipe-guide?main={ids[0]}')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'85 recipes', response.data)
        self.assertIn(b'7 recipes pair this', response.data)
        self.assertIn(b'No usage average', response.data)

    def test_builder_handoff_is_private_editable_and_stock_checked(self):
        self._login(1, 'member1', 'member')
        context = {'choices': [dict(id=1, name='Test fruit', brand='TEST', quantity_ml=1)]}
        with patch('services.stash_recipe_guide_service.build_context', return_value=context):
            response = self.client.get('/recipes/new?guided=1&practice=1&flavour=1&pct_1=2')
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'My stash experiment', response.data)
            self.assertIn(b'ingredient_id: 1', response.data)
            self.assertIn(b'new-practice', response.data)
            self.assertEqual(self.client.get('/recipes/new?guided=1&flavour=2&pct_2=2').status_code, 302)


class DraftValidationTests(unittest.TestCase):
    def test_reject_nonfinite_short_stock_duplicates_and_unowned(self):
        context = {'choices': [dict(id=1, name='Fruit', brand='TEST', quantity_ml=0.2)]}
        for ids, values in [([1], ['nan']), ([1], ['inf']), ([1], ['3']),
                            ([2], ['1']), ([1, 1], ['1', '1']), ([1], []), ([], [])]:
            with self.assertRaises(ValueError):
                draft_rows(context, ids, values)
        self.assertEqual(draft_rows(context, [1], ['2'])[0]['percent'], 2)
