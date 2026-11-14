import json
import unittest
from bs4 import BeautifulSoup
from unittest.mock import patch
from tests import test_tvw_api as base
from tests.test_recipe_builder_create import _valid_create_form
from db import get_db
from models import recipes_repo
from services import recipe_sharing_service as sharing
from services import what_can_i_make_service as wcm


class RecipeSharingTests(unittest.TestCase):
    def setUp(self):
        base.TVWApiTests.setUp(self)
        with base.flask_app.app_context():
            db=get_db()
            for uid,name in [(3,'PaulyWallnuts'),(4,'OtherMixer')]:
                db.execute("INSERT INTO users(id,username,password_hash,role,status) VALUES(?,?,'x','member','Active')",(uid,name))
            db.execute("UPDATE recipes SET name='PRIVATE SHARING TEST',visibility='private',current_version_id=1 WHERE id=1")
            db.execute("INSERT INTO recipe_stories(version_id,image_data) VALUES(1,?)",(b'owner image',))
            db.execute("INSERT INTO recipe_private_notebooks(recipe_id,user_id,notes) VALUES(1,1,'OWNER NOTEBOOK SECRET')")
            db.commit()
    tearDown=base.TVWApiTests.tearDown

    def login(self,uid):
        names={1:'member1',2:'Old Goat',3:'PaulyWallnuts',4:'OtherMixer'}
        with self.client.session_transaction() as s:
            s.clear();s.update(user_id=uid,username=names[uid],role='Old Goat' if uid==2 else 'member')

    def token(self):
        response=self.client.get('/recipes/1/sharing?q=Pauly')
        self.assertEqual(response.status_code,200)
        return BeautifulSoup(response.data,'html.parser').select_one('[name=sharing_token]')['value']

    def grant(self,permission='view'):
        with base.flask_app.app_context():sharing.change(1,1,3,permission)

    def mix(self,mode='quick'):
        return self.client.post('/api/mix/'+('quick' if mode=='quick' else 'guided/session'),
            json=dict(recipe_id=1,version_id=1,target_ml=10,target_nic_mg_ml=0,pg_ratio=30,vg_ratio=70))

    def form(self,**values):
        return _valid_create_form(recipe_id='1',name='Edited shared recipe',**{
            'ingredient_id[]':['101'],'ingredient_percent[]':['5'],'ingredient_name[]':['Vanilla'],
            'ingredient_brand[]':['Test'],'calc_target_nic':'0',**values})

    def test_owner_search_csrf_grant_and_revoke_form(self):
        token=self.token()
        page=self.client.get('/recipes/1/sharing?q=Pauly')
        self.assertIn(b'PaulyWallnuts',page.data)
        self.assertNotIn(b'password_hash',page.data)
        self.assertEqual(self.client.post('/recipes/1/sharing',data={'user_id':3,'permission':'view'}).status_code,403)
        self.assertEqual(self.client.post('/recipes/1/sharing',data={'user_id':3,'permission':'view','sharing_token':token}).status_code,302)
        self.assertIn(b'PaulyWallnuts',self.client.get('/recipes/1/sharing').data)
        self.client.post('/recipes/1/sharing',data={'user_id':3,'permission':'view','action':'revoke','sharing_token':token})
        with base.flask_app.app_context():
            self.assertIsNone(sharing.access(1,3))
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM recipe_access_audit').fetchone()[0],2)
            self.assertEqual(sharing.search_users('%',1),[])

    def test_view_mix_grant_never_makes_recipe_public_or_shares_private_notebook(self):
        self.login(3)
        self.assertIn(self.client.get('/recipes/1').status_code,(302,404))
        self.assertEqual(self.mix().status_code,404)
        self.grant()
        page=self.client.get('/recipes/1')
        self.assertEqual(page.status_code,200)
        self.assertIn(b'Shared access',page.data)
        self.assertNotIn(b'OWNER NOTEBOOK SECRET',page.data)
        self.assertNotIn(b'>Edit recipe<',page.data)
        self.assertEqual(self.client.get('/recipes/image/1').status_code,200)
        self.assertEqual(self.mix().status_code,200)
        self.assertEqual(self.mix('guided').status_code,201)
        self.assertNotIn(b'PRIVATE SHARING TEST',self.client.get('/recipes?visibility=public').data)
        self.login(4)
        self.assertIn(self.client.get('/recipes/1').status_code,(302,404))
        self.assertEqual(self.client.get('/recipes/image/1').status_code,404)
        self.assertNotIn(b'PRIVATE SHARING TEST',self.client.get('/recipes').data)
        with base.flask_app.app_context():
            self.assertEqual(recipes_repo.fetch_recipe(1)['visibility'],'private')
            self.assertEqual(wcm._fetch_public_recipes_for_scan(limit=20,viewer_user_id=4),[])

    def test_shared_scope_counts_search_stash_and_public_discovery(self):
        self.grant();self.login(3)
        for url in ['/recipes?shared=1','/recipes?shared=1&sort=stash','/recipes?shared=1&search=PRIVATE']:
            page=self.client.get(url)
            self.assertEqual(page.status_code,200)
            self.assertIn(b'PRIVATE SHARING TEST',page.data)
            self.assertIn(b'name="shared" value="1"',page.data)
        with base.flask_app.app_context():
            self.assertEqual(len(wcm._fetch_public_recipes_for_scan(limit=20,viewer_user_id=3)),1)
            self.assertEqual(recipes_repo.fetch_tvw_library(viewer_user_id=3,shared_with_user_id=3)[1],1)
            self.assertEqual(recipes_repo.fetch_tvw_library(viewer_user_id=3,public_discovery_only=True)[1],0)
            self.assertEqual(recipes_repo.fetch_tvw_library(viewer_user_id=4)[1],0)

    def test_editor_can_save_version_but_cannot_publish_or_change_sharing(self):
        self.grant('edit');self.login(3)
        self.assertEqual(self.client.get('/recipes/1/edit').status_code,200)
        self.assertEqual(self.client.get('/recipes/1/sharing').status_code,404)
        self.assertEqual(self.client.post('/recipes/save',data=self.form(visibility='public')).status_code,403)
        self.assertEqual(self.client.post('/recipes/save',data=self.form(status='help')).status_code,403)
        response=self.client.post('/recipes/save',data=self.form())
        self.assertEqual(response.status_code,302)
        with base.flask_app.app_context():
            r=recipes_repo.fetch_recipe(1)
            self.assertEqual(r['visibility'],'private');self.assertEqual(r['created_by'],1)
            self.assertNotEqual(r['current_version_id'],1)
            sharing.change(1,1,3,'view')
            count=get_db().execute('SELECT COUNT(*) FROM recipe_versions').fetchone()[0]
        self.assertEqual(self.client.post('/recipes/save',data=self.form()).status_code,302)
        with base.flask_app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM recipe_versions').fetchone()[0],count)

    def test_revocation_blocks_existing_session_before_mutation(self):
        self.grant();self.login(3)
        response=self.mix('guided');self.assertEqual(response.status_code,201)
        sid=response.json['mix_session_id']
        self.grant('revoke')
        for path,body,service in [
            ('steps/1/complete',{},'complete_guided_step_with_policy'),
            ('events',{},'insert_dynamic_guided_event_step'),
            ('notes',{'user_notes':'blocked'},'save_guided_session_user_notes'),
        ]:
            with patch('routes.tvw_api.guided_mix_session_service.'+service) as mutation:
                self.assertEqual(self.client.post('/api/mix/guided/session/'+str(sid)+'/'+path,json=body).status_code,404)
                mutation.assert_not_called()
        self.assertEqual(self.client.get('/api/mix/guided/session/'+str(sid)).status_code,404)
        self.assertEqual(self.mix().status_code,404)
        self.assertNotIn(b'PRIVATE SHARING TEST',self.client.get('/recipes?shared=1').data)

    def test_owner_batch_notes_are_not_shared_with_recipe(self):
        self.grant()
        response=self.mix('guided');sid=response.json['mix_session_id']
        with base.flask_app.app_context():
            db=get_db()
            db.execute('UPDATE mix_sessions SET completed_at=CURRENT_TIMESTAMP,result_json=? WHERE id=?',
                (json.dumps({'user_notes':'OWNER BATCH SECRET'}),sid))
            db.execute('UPDATE mix_session_steps SET completed_at=CURRENT_TIMESTAMP WHERE mix_session_id=?',(sid,))
            db.commit()
        self.assertIn(b'OWNER BATCH SECRET',self.client.get('/api/recipes/1/mixer-notes').data)
        self.login(3)
        for url in ['/recipes/1','/recipes/1/mix-logs','/api/recipes/1/mixer-notes','/api/recipes/1/batch-history']:
            response=self.client.get(url);self.assertEqual(response.status_code,200)
            self.assertNotIn(b'OWNER BATCH SECRET',response.data)

    def test_only_owner_grants_and_hidden_or_blocked_accounts_do_not_bypass(self):
        with base.flask_app.app_context():
            with self.assertRaises(PermissionError):sharing.change(1,3,4,'edit')
            with self.assertRaises(ValueError):sharing.change(1,1,1,'view')
            with self.assertRaises(ValueError):sharing.change(1,1,9999,'view')
            db=get_db();db.execute('UPDATE users SET is_banned=1 WHERE id=3');db.commit()
            with self.assertRaises(ValueError):sharing.change(1,1,3,'view')
            db.execute('UPDATE users SET is_banned=0 WHERE id=3');db.commit()
            sharing.change(1,1,3,'view')
            for field,value in [('is_hidden',1),('active',0),('moderation_status','removed')]:
                db.execute('UPDATE recipes SET '+field+'=? WHERE id=1',(value,));db.commit()
                self.assertFalse(recipes_repo.can_view_recipe_in_tvw_community(recipes_repo.fetch_recipe(1),viewer_user_id=3,viewer_is_old_goat=False))
                db.execute("UPDATE recipes SET is_hidden=0,active=1,moderation_status='active' WHERE id=1");db.commit()
        self.login(2)
        self.assertEqual(self.client.get('/recipes/1').status_code,200)
        self.assertEqual(self.client.get('/recipes/1/sharing').status_code,404)
