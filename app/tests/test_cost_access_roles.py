"""Cost access extends to the owner role without granting unrelated account powers."""
import os
os.environ.setdefault('TVW_ENABLE_LAB_MODULES','true')
import unittest
from unittest.mock import patch
from tests import test_recipe_cost_calculator as base
from services import recipe_service, inventory_service
from utils.auth import can_access_private_costs
from db import get_db


class CostRolePolicyTests(unittest.TestCase):
    def test_exact_owner_role_or_original_account(self):
        self.assertTrue(can_access_private_costs('Santa\'s Little Helper','Old Goat'))
        self.assertTrue(can_access_private_costs(' Old Goat ','member'))
        for role in ('Master','admin','moderator','member','Foreman',None):
            self.assertFalse(can_access_private_costs('Another user',role))

    def test_cost_grant_does_not_change_account_identity(self):
        user=recipe_service.user_context_from_session(dict(username='Santa',role='Old Goat',user_id=2))
        self.assertTrue(user.show_cost)
        self.assertFalse(user.is_old_goat)
        self.assertFalse(recipe_service.require_old_goat(user)[0])


class CostRoleIntegrationTests(unittest.TestCase):
    setUp=base.RecipeCostCalculatorIntegrationTests.setUp
    tearDown=base.RecipeCostCalculatorIntegrationTests.tearDown

    def identity(self, name, role):
        with base.flask_app.app_context():
            db=get_db();db.execute('UPDATE users SET username=?,role=? WHERE id=1',(name,role));db.commit()
        with self.client.session_transaction() as state:
            state.clear();state.update(user_id=1,username=name,role=role)

    def test_named_owner_and_other_owner_role_can_read_and_calculate(self):
        for name,role in [('Old Goat','Old Goat'),("Santa's Little Helper",'Old Goat')]:
            with self.subTest(name=name):
                self.identity(name,role)
                self.assertEqual(self.client.get('/recipes/cost-calculator').status_code,200)
                result=self.client.get('/recipes/cost-calculator/calculate?recipe_id=1&recipe_version_id=1')
                self.assertEqual(result.status_code,200)
                self.assertIn('outputs',result.get_json()['result'])
                self.assertEqual(self.client.get('/recipes/cost-calculator/catalogue-search?q=Biscuit').status_code,200)
                detail=self.client.get('/recipes/1',follow_redirects=True)
                self.assertEqual(detail.status_code,200)
                self.assertIn(b'Cost this recipe',detail.data)

    def test_master_and_member_do_not_gain_private_cost_apis(self):
        for role in ('Master','member','admin'):
            with self.subTest(role=role):
                self.identity('Another user',role)
                for endpoint in ('calculate?recipe_id=1','catalogue-search?q=Biscuit'):
                    response=self.client.get('/recipes/cost-calculator/'+endpoint)
                    self.assertEqual(response.status_code,403)
                for endpoint in ('override','price-override'):
                    self.assertEqual(self.client.post('/recipes/cost-calculator/'+endpoint,json={}).status_code,403)
                page=self.client.get('/recipes/cost-calculator')
                self.assertEqual(page.status_code,302)

    def test_other_old_goat_role_can_save_price_override(self):
        self.identity("Santa's Little Helper",'Old Goat')
        response=self.client.post('/recipes/cost-calculator/price-override',json=dict(recipe_id=1,version_id=1,ingredient_id=201,manual_price_per_ml=7.25))
        self.assertEqual(response.status_code,200)
        response=self.client.get('/recipes/cost-calculator/calculate?recipe_id=1&recipe_version_id=1')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json()['result']['recipe']['ingredients'][0]['price_per_ml'],7.25)

    def test_inventory_private_cost_write_uses_same_role_policy(self):
        with base.flask_app.app_context():
            for name,role,allowed in [('Santa','Old Goat',True),('Old Goat','member',True),('Another','Master',False)]:
                with self.subTest(role=role,name=name),patch.object(inventory_service.inventory_repo,'fetch_library_item_reference_id',return_value=201),patch.object(inventory_service.inventory_repo,'update_reference_fields'),patch.object(inventory_service.inventory_repo,'update_library_item_fields'),patch.object(inventory_service.inventory_repo,'update_library_item_cost_per_g') as write,patch.object(inventory_service,'log_change'):
                    _,status=inventory_service.update_item_from_form(actor_id=1,actor_username=name,actor_role=role,item_id=1,name='Biscuit',brand='INW',category='Flavor',stock=10,unit='ml',cost_per_g='2.5')
                    self.assertEqual(status,200)
                    self.assertEqual(write.called,allowed)
