import os
os.environ.setdefault('TVW_ENABLE_LAB_MODULES','true')
import unittest
from tests import test_tvw_api as base
from db import get_db
from models import recipes_repo
from services import flavour_browse_service as browse

class FlavourRefreshTests(unittest.TestCase):
    setUp=base.TVWApiTests.setUp
    tearDown=base.TVWApiTests.tearDown

    def login(self,uid,name,role):
        with self.client.session_transaction() as state:
            state.clear();state.update(user_id=uid,username=name,role=role)

    def test_cards_do_not_write_stash_and_filters_work_before_pagination(self):
        with base.flask_app.app_context():
            db=get_db()
            db.execute("INSERT INTO flavor_reference(id,brand,name,category,active) VALUES(102,'Test','Strawberry','Flavor',1)")
            db.execute("INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(2,'Test','Strawberry','Flavor',102)")
            db.execute('INSERT INTO user_stash_items(user_id,flavour_id,quantity_ml) VALUES(1,2,17)');db.commit()
            rows,total=recipes_repo.fetch_tvw_flavours(profile='Fruit',in_stash=True,user_id=1,page_size=1)
            self.assertEqual(total,1);self.assertEqual(rows[0]['id'],2);self.assertEqual(rows[0]['quantity_ml'],17)
            self.assertEqual(recipes_repo.fetch_tvw_flavours(in_stash=True,user_id=2)[1],0)
        page=self.client.get('/flavours?profile=Fruit&in_stash=1')
        self.assertEqual(page.status_code,200)
        self.assertIn(b'17.0 ml',page.data)
        self.assertNotIn(b'quantity_ml: 10',page.data)
        self.assertIn(b'#stash-actions',page.data)

    def test_private_notebook_is_separate_per_user_and_escaped(self):
        response=self.client.post('/flavours/1/notebook',data={'private_notes':'My secret <script>alert(1)</script>'})
        self.assertEqual(response.status_code,302)
        page=self.client.get('/flavours/1')
        self.assertIn(b'My secret &lt;script&gt;',page.data)
        self.login(2,'Old Goat','Old Goat')
        page=self.client.get('/flavours/1')
        self.assertNotIn(b'My secret',page.data)
        with base.flask_app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM flavour_notes').fetchone()[0],0)
        self.assertEqual(self.client.post('/flavours/1/notebook',data={'private_notes':'x'*10001}).status_code,400)

    def test_private_cost_is_server_gated(self):
        with base.flask_app.app_context():
            db=get_db();db.execute('UPDATE flavor_reference SET price_per_ml=8.76 WHERE id=101');db.commit()
        for role in ('member','Master','Old Goat'):
            with base.flask_app.app_context():
                db=get_db();db.execute('UPDATE users SET role=? WHERE id=1',(role,));db.commit()
            self.login(1,'member1',role)
            page=self.client.get('/flavours/1')
            self.assertEqual(page.status_code,200)
            self.assertEqual(b'R 8.76 / ml' in page.data,role=='Old Goat')

    def test_lab_linked_stash_and_unknown_family(self):
        with base.flask_app.app_context():
            db=get_db();db.execute('INSERT INTO library_items(reference_id,current_stock,active) VALUES(101,23,1)');db.commit()
            rows,total=recipes_repo.fetch_tvw_flavours(in_stash=True,user_id=2,lab_linked=True)
            self.assertEqual(total,1);self.assertEqual(rows[0]['quantity_ml'],23)
            self.assertEqual(browse.family('Mystery Number Seven'),'')
