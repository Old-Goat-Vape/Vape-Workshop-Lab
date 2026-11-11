"""Regressions for public help requests and completed bench display."""
from bs4 import BeautifulSoup
from pathlib import Path
import subprocess
import unittest
from tests import test_recipe_builder_create as fixtures
from db import get_db
from services import community_discovery_service

class PaulyFeedbackTests(unittest.TestCase):
    setUp = fixtures.RecipeBuilderCreateTests.setUp
    tearDown = fixtures.RecipeBuilderCreateTests.tearDown
    _login = fixtures.RecipeBuilderCreateTests._login

    def create_recipe(self, **overrides):
        self._login(1, 'member1', 'member')
        response = self.client.post('/recipes/save', data=fixtures._valid_create_form(**overrides))
        self.assertEqual(response.status_code, 302)
        with fixtures.flask_app.app_context():
            return dict(get_db().execute('SELECT * FROM recipes ORDER BY id DESC LIMIT 1').fetchone())

    def test_help_create_overrides_private_and_is_discovered(self):
        recipe = self.create_recipe(status='help', visibility='private')
        self.assertEqual(recipe['visibility'], 'public')
        with fixtures.flask_app.test_request_context('/'):
            context = community_discovery_service.build_community_discovery_context(viewer_user_id=2, viewer_is_old_goat=False)
            self.assertIn(recipe['id'], [r['id'] for r in context['help_recipes']])
        self._login(2, 'member2', 'member')
        response = self.client.get('/recipes/' + recipe['slug'])
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="communityFeedback"', response.data)
        for page in (response, self.client.get('/recipes/new')):
            soup = BeautifulSoup(page.data, 'html.parser')
            for script in soup.find_all('script'):
                if script.get('src') or script.get('type') == 'application/json':
                    continue
                subprocess.run(['node', '--check'], input=script.get_text(), text=True, capture_output=True, check=True)
        builder = BeautifulSoup(self.client.get('/recipes/new').data, 'html.parser')
        self.assertEqual(builder.select_one('#recipeStatus').name, 'select')
        self.assertEqual(len(builder.select('#helpVisibilityNote')), 1)


    def test_status_help_publishes_and_nonowner_cannot_change(self):
        recipe = self.create_recipe()
        rid = recipe['id']
        self.assertEqual(self.client.post(f'/api/recipes/{rid}/status', json={'status':'help'}).status_code, 200)
        with fixtures.flask_app.app_context():
            self.assertEqual(get_db().execute('SELECT visibility FROM recipes WHERE id=?',(rid,)).fetchone()[0], 'public')
        self._login(2, 'member2', 'member')
        self.assertEqual(self.client.post(f'/api/recipes/{rid}/status', json={'status':'ready'}).status_code, 403)

    def test_help_discovery_excludes_private_and_hidden_for_admin(self):
        recipe = self.create_recipe(status='help')
        for assignment in ("visibility='private'", "visibility='public', is_hidden=1"):
            with fixtures.flask_app.test_request_context('/'):
                db = get_db()
                db.execute(f'UPDATE recipes SET {assignment} WHERE id=?',(recipe['id'],))
                db.commit()
                context = community_discovery_service.build_community_discovery_context(viewer_user_id=3, viewer_is_old_goat=True)
                self.assertNotIn(recipe['id'], [r['id'] for r in context['help_recipes']])

    def test_comment_without_rating_and_empty_comment_rejected(self):
        recipe = self.create_recipe(status='help')
        self._login(2, 'member2', 'member')
        url = f'/api/recipes/{recipe["id"]}/feedback'
        self.assertEqual(self.client.post(url,json={'body':'Try reducing the sweetener.', 'recipe_version_id':recipe['current_version_id']}).status_code,201)
        items = self.client.get(url).get_json()['items']
        self.assertEqual(items[0]['body'], 'Try reducing the sweetener.')
        self.assertIsNone(items[0]['score_overall'])
        self.assertEqual(self.client.post(url,json={'body':'   '}).status_code,400)
        self.assertEqual(self.client.post(url,json={'body':'Hi','score_overall':11}).status_code,400)

class CompletionDisplayTests(unittest.TestCase):
    def test_completed_bench_hides_grid_and_focuses_report_once(self):
        source = (Path(__file__).parents[1] / 'templates/tvw_guided_mix_session.html').read_text()
        function = source[source.index('  const applySession = (sess) => {'):source.index('  const escHtml = (s) =>')]
        script = r'''
const assert = require('assert');
const element = () => ({classes:new Set(), classList:{toggle(c,v){v?this.owner.classes.add(c):this.owner.classes.delete(c)},contains(c){return this.owner.classes.has(c)},add(c){this.owner.classes.add(c)},remove(c){this.owner.classes.delete(c)}},focus(){this.focuses=(this.focuses||0)+1},scrollIntoView(){}});
const root=element(),mixingPanels=element(),completionSummary=element(),focusCard=element(),usedPanel=element(),todoPanel=element();
for(const e of [root,mixingPanels,completionSummary,focusCard,usedPanel,todoPanel]) e.classList.owner=e;
const document={getElementById:()=>mixingPanels},window={};
let underPourUi=null,rendered=0;
const renderMixSummary=()=>rendered++,syncMixerNotesUi=()=>{},setFocus=()=>{};
'''+function+r'''
applySession({progress:{all_done:true}});
assert(mixingPanels.classes.has('d-none'));assert(root.classes.has('is-complete'));
assert(!completionSummary.classes.has('d-none'));assert.equal(completionSummary.focuses,1);assert.equal(rendered,1);
applySession({progress:{all_done:true}});assert.equal(completionSummary.focuses,1);
applySession({progress:{all_done:false}});assert(!mixingPanels.classes.has('d-none'));
'''
        subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)

    def test_report_keeps_measured_volume_and_names_target_batch(self):
        source = (Path(__file__).parents[1] / 'templates/tvw_guided_mix_session.html').read_text()
        function = source[source.index('  const renderMixSummary = (sess) => {'):source.index('  const syncMixerNotesUi = (sess) => {')]
        script = r'''const assert = require('assert');
const el={innerHTML:'',classList:{remove(){},add(){}}};
const document={getElementById:()=>el},sessionMeta={},escHtml=s=>String(s??'');
'''+function+r'''
const summary={batch_size_ml:10,estimated_final_volume_ml:9.99,recipe_name:'Test',target_pg_percent:30,target_vg_percent:70};
renderMixSummary({mix_summary:summary});
assert(el.innerHTML.includes('10 ml batch complete'));assert(el.innerHTML.includes('9.99 ml'));
assert.equal(summary.estimated_final_volume_ml,9.99);
'''
        subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)
