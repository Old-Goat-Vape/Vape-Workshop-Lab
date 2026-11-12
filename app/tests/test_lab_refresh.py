"""Lab navigation, verified identities and formula parsing regressions."""
import unittest
from unittest.mock import patch
from flask import render_template, session
from bs4 import BeautifulSoup
from tests import test_tvw_api as fixtures
from tests.test_vc_recipe_import import DETAIL
from config.catalogue_identity import canonical_item
from db import get_db
from models import recipes_repo
from services.public_flavour_insights_service import builder_flavour_usage
from scrapers import vc_recipes_dtl_scraper as vc


class LabRefreshTests(unittest.TestCase):
    setUp=fixtures.TVWApiTests.setUp
    tearDown=fixtures.TVWApiTests.tearDown

    def test_original_cap_custard_is_v1_only(self):
        original=canonical_item('Capella','Vanilla Custard')[2]
        self.assertEqual(original,canonical_item('CAP','Vanilla Custard V1')[2])
        self.assertEqual(original,canonical_item('CAP','Vanilla Custard (V1)')[2])
        for brand,name in [('CAP','Vanilla Custard V2'),('CAP (SL)','Vanilla Custard'),('TFA','Vanilla Custard V1')]:
            self.assertNotEqual(original,canonical_item(brand,name)[2])
        self.assertNotEqual(canonical_item('TFA','Vanilla Custard')[2],canonical_item('TFA','Vanilla Custard V1')[2])

    def test_alias_averages_and_choices_without_rewriting_history_or_prices(self):
        with fixtures.flask_app.app_context():
            db=get_db();db.execute("UPDATE flavor_reference SET brand='CAP',name='Vanilla Custard V1' WHERE id=101")
            db.execute('UPDATE recipes SET current_version_id=1 WHERE id=1')
            db.execute("UPDATE flavours SET brand='CAP',canonical_name='Vanilla Custard V1' WHERE id=1")
            db.execute("INSERT INTO flavor_reference(id,brand,name,category,active,price_per_ml) VALUES(102,'CAP','Vanilla Custard','Flavor',1,3)")
            db.execute("INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(2,'CAP','Vanilla Custard','Flavor',102)")
            db.execute('INSERT INTO recipe_items(version_id,ingredient_id,amount_percent) VALUES(1,102,1)');db.commit()
            self.assertEqual(builder_flavour_usage(101)['average'],6)
            self.assertEqual(builder_flavour_usage(102)['count'],1)
            matches=recipes_repo.search_flavour_reference_for_builder(query='Vanilla Custard')
            self.assertEqual([(r['id'],r['name']) for r in matches],[(102,'Vanilla Custard V1')])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM flavor_reference WHERE id IN (101,102)').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT price_per_ml FROM flavor_reference WHERE id=102').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recipe_versions WHERE recipe_id=1').fetchone()[0],1)

    def test_restricted_device_description_and_zero_percent_row(self):
        parsed=vc._parse_recipe_page('https://vceliquidrecipes.com/recipe/42',DETAIL+'<p>Use a restricted direct lung device. This was once a private recipe.</p>')
        self.assertEqual(parsed['is_public'],1)
        zero='<tr class="flavour-name"><td>Black Pepper - Test</td><td>0.00%</td></tr>'
        self.assertEqual(len(vc._parse_recipe_page('https://vceliquidrecipes.com/recipe/42',DETAIL.replace('</table>',zero+'</table>'))['flavours']),2)
        self.assertTrue(vc._source_access_restricted(BeautifulSoup('<div role="alert">This recipe is private.</div>','html.parser')))

    def test_testing_post_never_parses_percentages_from_prose(self):
        html='<h1>Flavour review</h1><table id="recipe-flavour-table"><tr class="flavour-name"><td>No Flavours</td></tr></table><p>Tested at 2.5% and 3.0%</p>'
        self.assertTrue(vc._source_has_empty_formula(html))
        self.assertIsNone(vc._parse_recipe_page('https://vceliquidrecipes.com/recipe/42',html))
        self.assertFalse(vc._source_has_empty_formula('<h1>Sign in</h1>'))

    def test_lab_return_link_and_admin_controls(self):
        with fixtures.flask_app.test_request_context('/lab'):
            session.update(user_id=2,role='Old Goat',username='Old Goat')
            page=BeautifulSoup(render_template('dashboard.html',stats={'total_items':5,'low_stock_count':0,'recent_mixes':30}),'html.parser')
            self.assertEqual(page.select_one('.lab-return-link')['href'],'/community')
            self.assertIn('Back to TVW Calculator',page.get_text())
            self.assertIn('Recipe imports & prices',page.get_text())
        with self.client.session_transaction() as state:state.update(user_id=2,role='Old Goat',username='Old Goat')
        with patch('routes.master_admin_console.svc.get_console_summary',return_value={}),patch('routes.master_admin_console.svc.get_latest_open_stock_take_run',return_value=None),patch('routes.master_admin_console.svc.list_scraper_last_runs',return_value={}),patch('routes.master_admin_console.svc.scraper_health_report',return_value={}),patch('routes.master_admin_console.svc.get_scheduler_status',return_value={}):
            response=self.client.get('/master-admin')
        self.assertEqual(response.status_code,200)
        page=BeautifulSoup(response.get_data(as_text=True),'html.parser')
        self.assertIsNotNone(page.select_one('#vcJobPanel'))
        self.assertIn('Schedule details and diagnostics',page.get_text())
        technical=next(d for d in page.find_all('details') if d.summary and 'Technical run history' in d.summary.get_text())
        self.assertFalse(technical.has_attr('open'))
        with self.client.session_transaction() as state:state.update(user_id=1,role='member',username='member1')
        self.assertEqual(self.client.get('/master-admin').status_code,302)

    def test_tvw_dashboard_photo_in_both_mix_states(self):
        from services import community_discovery_service
        with fixtures.flask_app.test_request_context('/community'):
            session.update(user_id=1,role='member',username='member1')
            context=community_discovery_service.build_community_discovery_context(viewer_user_id=1,viewer_is_old_goat=False)
            for active in (None,dict(id=99,total_steps=10,open_steps=5,recipe_name='Test',target_ml=30,target_pgvg='30/70',version_number='1')):
                page=BeautifulSoup(render_template('tvw_community_home.html',active_guided_mix=active,viewer_is_old_goat=False,**context),'html.parser')
                photo=page.select_one('.tvw-dashboard-photo')
                self.assertTrue(photo['src'].endswith('mixing-bench.webp'))
                self.assertEqual(photo['width'],'1536')
                self.assertNotIn('/lab',[a.get('href') for a in page.find_all('a')])
                self.assertIn('Mixers asking for help',page.get_text())
