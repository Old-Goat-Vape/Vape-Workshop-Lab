"""VC discovery, parsing and private append-only recipe persistence."""
import unittest
from unittest.mock import Mock, patch
from flask import Flask, session
from tests import test_tvw_api as fixtures
from db import get_db
from scrapers import vc_recipes_dtl_scraper as vc
from models import recipes_repo
from services import recipe_library_presentation as presentation
from services.public_flavour_insights_service import flavour_insights

DETAIL='''<h1>QA Yogurt</h1><h3>A recipe by <a href="/user/2078">OldGoat</a></h3>
<table id="recipe-flavour-table"><tr class="flavour-name"><td class="flavourName"><a>Vanilla - Test</a></td><td></td><td></td><td class="flavour-percent-copy"><b>2.00</b>%</td></tr>
<tr class="flavour-name"><td class="flavourName"><a>Cream - Test</a></td><td></td><td></td><td class="flavour-percent-copy"><b>3.00</b>%</td></tr>
<tr><td>Total Flavour</td><td></td><td></td><td>5.00%</td></tr></table><a>Login</a>'''

class VCDiscoveryTests(unittest.TestCase):
    def test_profile_uses_recipe_datatable_not_static_links(self):
        response=Mock();response.json.return_value={'data':[[44389,'Recipe',123],[44390,'Recipe',999]]}
        client=Mock();client.post.return_value=response
        progress={}
        links=vc._fetch_links_for_user_profile(client,{},user_id='2078',max_recipes=50,start_page=2,progress=progress)
        self.assertEqual(links,['https://vceliquidrecipes.com/recipe/44389','https://vceliquidrecipes.com/recipe/44390'])
        payload=client.post.call_args.kwargs['data']
        self.assertEqual(payload['getUsersRecipes'],'true')
        self.assertEqual(payload['userID'],'2078')
        self.assertEqual(payload['start'],50)
        self.assertEqual(progress['next_page'],3)
        client.get.assert_not_called()

    def test_public_controller_reads_requested_page_and_errors_do_not_advance(self):
        response=Mock();response.json.return_value={'html':'<a href="recipe/44389">Recipe</a>'}
        client=Mock();client.get.return_value=response
        progress={}
        self.assertEqual(len(vc._fetch_links_via_public_controller(client,{},50,start_page=4,progress=progress)),1)
        self.assertEqual(client.get.call_args.kwargs['params']['page'],4)
        self.assertEqual(progress['next_page'],5)
        response.json.return_value={'error':'Unavailable'};progress={}
        with self.assertRaises(ValueError):vc._fetch_links_via_public_controller(client,{},50,progress=progress)
        self.assertEqual(progress,{})

    def test_parser_separates_identity_from_percentage_and_ignores_total(self):
        parsed=vc._parse_recipe_page('https://vceliquidrecipes.com/recipe/44389',DETAIL)
        self.assertIsNone(vc._parse_recipe_page('https://vceliquidrecipes.com/recipe/1','<h1>Login</h1><p>Mix at 5%</p>'))
        self.assertEqual(parsed['author'],'OldGoat')
        self.assertEqual([(r['brand_raw'],r['flavour_name_raw'],r['percentage']) for r in parsed['flavours']], [('Test','Vanilla',2),('Test','Cream',3)])
        self.assertEqual(parsed['total_flavour_pct'],5)
        self.assertEqual(presentation.parse_imported_source_from_notes('Imported VC DTL reference (https://vceliquidrecipes.com/recipe/44389)\nOriginal author: OG\nNotes (steep)')['url'],'https://vceliquidrecipes.com/recipe/44389')


class VCPrivateImportTests(unittest.TestCase):
    setUp=fixtures.TVWApiTests.setUp
    tearDown=fixtures.TVWApiTests.tearDown

    def parsed(self):
        parsed=vc._parse_recipe_page('https://vceliquidrecipes.com/recipe/44389',DETAIL)
        parsed['flavours'][0]['matched_flavor_id']=101
        parsed['flavours'][1]['matched_flavor_id']=101
        return parsed

    def test_private_owned_current_and_idempotent_then_preserves_old_version(self):
        with fixtures.flask_app.app_context():
            db=get_db();parsed=self.parsed()
            result=vc._sync_into_recipe_library(db,parsed,reference_lookup={},actor_id=1)
            rid=result['recipe_id'];db.commit()
            row=dict(db.execute('SELECT * FROM recipes WHERE id=?',(rid,)).fetchone())
            self.assertEqual((row['visibility'],row['created_by'],row['library_type']),('private',1,'reference'))
            vid=row['current_version_id'];self.assertTrue(vid)
            before=[tuple(r) for r in db.execute('SELECT * FROM recipe_items WHERE version_id=?',(vid,))]
            vc._sync_into_recipe_library(db,parsed,reference_lookup={},actor_id=1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recipe_versions WHERE recipe_id=?',(rid,)).fetchone()[0],1)
            db.execute("INSERT INTO mix_sessions(user_id,recipe_version_id,mode) VALUES(1,?,'guided')",(vid,))
            parsed['flavours'][0]['percentage']=4
            vc._sync_into_recipe_library(db,parsed,reference_lookup={},actor_id=1);db.commit()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recipe_versions WHERE recipe_id=?',(rid,)).fetchone()[0],2)
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM recipe_items WHERE version_id=?',(vid,))])
            self.assertNotEqual(db.execute('SELECT current_version_id FROM recipes WHERE id=?',(rid,)).fetchone()[0],vid)
            self.assertFalse(recipes_repo.recipe_dict_visible_in_public_lists(row))
            self.assertEqual(flavour_insights([101])['count'],1)
            self.assertEqual(recipes_repo.count_tvw_public_current_recipes_using_refs([101]),0)

    def test_legacy_metadata_repair_preserves_versions_and_items(self):
        with fixtures.flask_app.app_context():
            db=get_db();db.execute("UPDATE recipes SET recipe_code='VCREF-44389',current_version_id=NULL,visibility='public',created_by=NULL WHERE id=1")
            before=[tuple(r) for r in db.execute('SELECT * FROM recipe_items')]
            vc._repair_private_vc_library_metadata(db,1)
            row=db.execute('SELECT * FROM recipes WHERE id=1').fetchone()
            self.assertEqual((row['visibility'],row['current_version_id'],row['created_by']),('private',1,1))
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM recipe_items')])

    def test_source_private_and_invalid_items_cannot_be_imported(self):
        with fixtures.flask_app.app_context():
            for overrides in ({'is_public':0},{'flavours':[]}):
                parsed=self.parsed();parsed.update(overrides)
                with self.assertRaises(ValueError):vc._sync_into_recipe_library(get_db(),parsed,reference_lookup={},actor_id=1)
            self.assertIsNone(get_db().execute("SELECT id FROM recipes WHERE recipe_code='VCREF-44389'").fetchone())

    def test_targeted_full_pipeline_imports_without_publication(self):
        response=Mock(status_code=200,text=DETAIL,url='https://vceliquidrecipes.com/recipe/44389')
        client=Mock();client.get.return_value=response
        with fixtures.flask_app.app_context(),patch.object(vc.requests,'Session',return_value=client),patch.object(vc.time,'sleep'):
            result=vc.run_vc_dtl_scraper(get_db(),mode='vc_targeted_ids',target_ids=['44389'],actor_id=1,maintenance=False)
            self.assertEqual(result.get('imported_to_recipe_library'),1,result)
            row=get_db().execute("SELECT * FROM recipes WHERE recipe_code='VCREF-44389'").fetchone()
            self.assertEqual(row['visibility'],'private')
            self.assertTrue(row['current_version_id'])
            self.assertEqual(client.get.call_count,1)

    def test_resume_holds_page_after_timeout_then_advances_after_import(self):
        detail=Mock(status_code=200,text=DETAIL,url='https://vceliquidrecipes.com/recipe/44389')
        listing=Mock();listing.json.return_value={'html':'<a href="recipe/44389">Recipe</a>'}
        client=Mock();client.get.side_effect=lambda url,**kwargs: listing if 'controllers/' in url else detail
        with fixtures.flask_app.app_context(),patch.object(vc.requests,'Session',return_value=client),patch.object(vc.time,'sleep'):
            db=get_db()
            with patch.dict(vc.VC_DTL_CONFIG,{'crawl':{'detail_runtime_budget_seconds':0,'min_detail_attempts_before_runtime_stop':1}}):
                vc.run_vc_dtl_scraper(db,actor_id=1)
            self.assertEqual(vc._load_checkpoint(db,mode='vc_public_all')['cursor_page'],1)
            result=vc.run_vc_dtl_scraper(db,actor_id=1)
            self.assertEqual(result['imported_to_recipe_library'],1,result)
            self.assertEqual(vc._load_checkpoint(db,mode='vc_public_all')['cursor_page'],2)
            db.execute("UPDATE recipe_references SET needs_review=1 WHERE source_recipe_id='44389'")
            db.execute("UPDATE vc_import_checkpoints SET cursor_page=1 WHERE mode='vc_public_all'")
            db.commit()
            again=vc.run_vc_dtl_scraper(db,actor_id=1)
            self.assertEqual(again['queued_detail_pages'],0,again)
            self.assertEqual(vc._load_checkpoint(db,mode='vc_public_all')['cursor_page'],2)



class LabAccessTests(unittest.TestCase):
    def test_members_cannot_open_scrapers_but_old_goat_can_run(self):
        from routes.master_admin_console import master_admin_bp
        app=Flask(__name__);app.secret_key='test';app.config['TESTING']=True
        app.add_url_rule('/',endpoint='tvw_community.community_home',view_func=lambda:'Home')
        app.register_blueprint(master_admin_bp)
        client=app.test_client()
        with client.session_transaction() as state:state.update(user_id=1,role='member')
        with patch('models.users_repo.fetch_user_by_id',return_value={'id':1,'password_hash':'x'}), patch('routes.master_admin_console.svc.trigger_scraper',return_value=({'status':'success'},200)) as run:
            self.assertEqual(client.get('/master-admin').status_code,302)
            self.assertEqual(client.post('/master-admin/scrapers/trigger/vc_dtl').status_code,302)
            run.assert_not_called()
            with client.session_transaction() as state:state['role']='Old Goat'
            self.assertEqual(client.post('/master-admin/scrapers/trigger/vc_dtl').status_code,200)
            run.assert_called_once_with('vc_dtl',actor_id=1)
