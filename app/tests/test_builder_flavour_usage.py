"""Exact flavour averages use all visibilities and only each current recipe once."""
import unittest
from tests import test_tvw_api as fixtures
from db import get_db
from services.public_flavour_insights_service import builder_flavour_usage, flavour_insights


class BuilderUsageTests(unittest.TestCase):
    setUp = fixtures.TVWApiTests.setUp
    tearDown = fixtures.TVWApiTests.tearDown

    def seed(self):
        db=get_db()
        db.execute('UPDATE recipes SET active=0')
        db.execute("UPDATE flavor_reference SET brand='CAP',name='Vanilla Custard v1' WHERE id=101")
        for fid,brand,name in ((102,'CAP','Vanilla Custard v2'),(103,'Other','Vanilla Custard v1'),(104,'CAP','Unused')):
            db.execute("INSERT INTO flavor_reference(id,brand,name,category,active) VALUES(?,?,?,'Flavor',1)",(fid,brand,name))
        for rid,pct,visibility in ((2,2,'public'),(3,4,'private'),(4,6,'unlisted')):
            vid=rid*10
            db.execute("INSERT INTO recipes(id,name,created_by,visibility,active,moderation_status,status,current_version_id,library_type) VALUES(?,?,1,?,1,'active','Reference',?,'reference')",(rid,f'SECRET RECIPE {rid}',visibility,vid))
            for version,amount in ((vid,pct),(vid+1,99)):
                db.execute("INSERT INTO recipe_versions(id,recipe_id,version_number) VALUES(?,?,'1')",(version,rid))
                db.execute('INSERT INTO recipe_items(version_id,ingredient_id,amount_percent) VALUES(?,101,?)',(version,amount))
            db.execute('INSERT INTO recipe_items(version_id,ingredient_id,amount_percent) VALUES(?,102,20)',(vid,))
            db.execute('INSERT INTO recipe_items(version_id,ingredient_id,amount_percent) VALUES(?,103,30)',(vid,))
        db.commit()

    def test_average_all_visibilities_exact_brand_version_and_current_only(self):
        with fixtures.flask_app.app_context():
            self.seed()
            self.assertEqual(builder_flavour_usage(101),dict(ingredient_id=101,count=3,average=4))
            self.assertEqual(builder_flavour_usage(102)['average'],20)
            self.assertEqual(builder_flavour_usage(103)['average'],30)
            self.assertEqual(builder_flavour_usage(101)['average'],flavour_insights([101])['average'])
        response=self.client.get('/api/recipes/flavour-usage/101')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json,{'status':'success','usage':{'ingredient_id':101,'count':3,'average':4}})
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertNotIn('SECRET RECIPE',response.get_data(as_text=True))

    def test_duplicate_lines_sum_but_count_recipe_once_and_new_data_is_live(self):
        with fixtures.flask_app.app_context():
            self.seed();db=get_db()
            db.execute('INSERT INTO recipe_items(version_id,ingredient_id,amount_percent) VALUES(20,101,1)');db.commit()
            usage=builder_flavour_usage(101)
            self.assertEqual(usage['count'],3)
            self.assertAlmostEqual(usage['average'],13/3)
            db.execute('UPDATE recipe_items SET amount_percent=8 WHERE version_id=30 AND ingredient_id=101');db.commit()
            self.assertAlmostEqual(builder_flavour_usage(101)['average'],17/3)

    def test_hidden_removed_and_inactive_recipes_and_invalid_values_excluded(self):
        with fixtures.flask_app.app_context():
            self.seed();db=get_db()
            db.execute('UPDATE recipes SET is_hidden=1 WHERE id=2')
            db.execute("UPDATE recipes SET moderation_status='removed' WHERE id=3")
            db.execute('UPDATE recipes SET active=0 WHERE id=4');db.commit()
            self.assertEqual(builder_flavour_usage(101)['count'],0)
            db.execute("UPDATE recipes SET active=1,is_hidden=0,moderation_status='active' WHERE id IN (2,3,4)")
            db.execute('UPDATE recipe_items SET amount_percent=-1 WHERE version_id=20 AND ingredient_id=101')
            db.execute('UPDATE recipe_items SET amount_percent=101 WHERE version_id=30 AND ingredient_id=101')
            db.execute('UPDATE recipe_items SET amount_percent=0 WHERE version_id=40 AND ingredient_id=101');db.commit()
            self.assertEqual(builder_flavour_usage(101),dict(ingredient_id=101,count=0,average=None))

    def test_unavailable_flavours_and_no_data_do_not_get_invented_defaults(self):
        with fixtures.flask_app.app_context():
            self.seed();db=get_db()
            self.assertEqual(builder_flavour_usage(104),dict(ingredient_id=104,count=0,average=None))
            db.execute("INSERT INTO flavours(id,canonical_name,brand,category,source_flavor_ref_id,is_hidden) VALUES(100,'Vanilla Custard v2','CAP','Flavor',102,1)")
            db.execute("INSERT INTO flavor_reference(id,brand,name,category,active) VALUES(105,'Test','Bottle Opener','Hardware',1)")
            db.execute("INSERT INTO flavor_reference(id,brand,name,category,active,is_nicotine) VALUES(106,'Test','Nicotine','Nicotine',1,1)")
            db.execute('UPDATE flavor_reference SET active=0 WHERE id=103');db.commit()
            for fid in (102,103,105,106,999999):self.assertIsNone(builder_flavour_usage(fid))
        self.assertEqual(self.client.get('/api/recipes/flavour-usage/999999').status_code,404)
        self.assertEqual(self.client.get('/api/recipes/flavour-usage/104').json['usage']['average'],None)

    def test_builder_loads_suggestions_before_controller_and_keeps_percent_editable(self):
        from bs4 import BeautifulSoup
        response=self.client.get('/recipes/new')
        self.assertEqual(response.status_code,200)
        page=BeautifulSoup(response.get_data(as_text=True),'html.parser')
        scripts=[tag.get('src','') for tag in page.find_all('script')]
        usage=next(i for i,src in enumerate(scripts) if 'tvw-flavour-usage.js' in src)
        builder=next(i for i,src in enumerate(scripts) if 'tvw-recipe-builder.js' in src)
        self.assertLess(usage,builder)
        template=page.find('template',id='ingredientRowTemplate')
        self.assertIsNotNone(template.select_one('.row-usage-text'))
        self.assertTrue(template.select_one('.row-use-average').has_attr('hidden'))
        percentage=template.select_one('.ingredient-pct')
        self.assertFalse(percentage.has_attr('readonly'))
        self.assertEqual(percentage.get('step'),'any')

    def test_sign_in_required(self):
        with self.client.session_transaction() as state:state.clear()
        self.assertIn(self.client.get('/api/recipes/flavour-usage/101').status_code,(302,401))


if __name__=='__main__':unittest.main()
