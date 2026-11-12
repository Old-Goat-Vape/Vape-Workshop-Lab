"""Public insight provenance and conservative spelling suggestions."""
import unittest
from tests import test_tvw_api as api_fixtures, test_flavour_search as search_fixtures
from db import get_db
from services.public_flavour_insights_service import flavour_insights
from models import recipes_repo
from services import stash_service


class SearchDiscoveryTests(unittest.TestCase):
    setUp=search_fixtures.FlavourSearchTests.setUp
    tearDown=search_fixtures.FlavourSearchTests.tearDown

    def test_alias_and_reordered_words(self):
        with api_fixtures.flask_app.app_context():
            for q in ('Inawera Biscuit','Biscuit INW','INW biscuit'):
                rows=recipes_repo.search_flavour_reference_for_builder(query=q)
                self.assertEqual([r['id'] for r in rows],[101])
                self.assertFalse(rows[0]['suggestion'])

    def test_typo_is_suggestion_and_formulations_stay_separate(self):
        with api_fixtures.flask_app.app_context():
            db=get_db()
            for fid,name in ((201,'Vanilla Custard v1'),(202,'Vanilla Custard v2')):
                db.execute("INSERT INTO flavor_reference(id,brand,name,category,active) VALUES(?,'CAP',?,'Flavor',1)",(fid,name))
                db.execute("INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(?,'CAP',?,'Flavor',?)",(fid,name,fid))
            db.commit()
            rows=recipes_repo.search_flavour_reference_for_builder(query='vanila custard v2')
            self.assertEqual([r['id'] for r in rows],[202])
            self.assertTrue(rows[0]['suggestion'])
            rows,total=recipes_repo.fetch_tvw_flavours(search='Capella vanila custard v2')
            self.assertEqual(total,1)
            self.assertTrue(rows[0]['suggestion'])
            self.assertEqual(stash_service.fetch_stash_quick_add_options(query='vanila custard v2')[0]['id'],202)
            self.assertEqual(recipes_repo.search_flavour_reference_for_builder(query='Vanilla Custard v9'),[])
            # A direct result never brings in fuzzy neighbours.
            self.assertTrue(all(not r['suggestion'] for r in recipes_repo.search_flavour_reference_for_builder(query='Biscuit')))
            self.assertEqual(recipes_repo.search_flavour_reference_for_builder(query="%' OR 1=1 --"),[])

    def test_hidden_and_noningredients_never_suggested(self):
        with api_fixtures.flask_app.app_context():
            db=get_db()
            for fid,name,hidden in ((301,'Vanilla Secret',1),(302,'Cap Bottle Opener',0)):
                db.execute("INSERT INTO flavor_reference(id,brand,name,category,active) VALUES(?,'Test',?,'Flavor',1)",(fid,name))
                db.execute("INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id,is_hidden) VALUES(?,'Test',?,'Flavor',?,?)",(fid,name,fid,hidden))
            db.commit()
            for query in ('vanila secret','cap botle opener'):
                self.assertEqual(recipes_repo.search_flavour_reference_for_builder(query=query),[])
                self.assertEqual(stash_service.fetch_stash_quick_add_options(query=query),[])
                self.assertEqual(recipes_repo.fetch_tvw_flavours(search=query)[1],0)


class PublicInsightsTests(unittest.TestCase):
    setUp=api_fixtures.TVWApiTests.setUp
    tearDown=api_fixtures.TVWApiTests.tearDown

    def seed(self):
        db=get_db()
        db.execute("UPDATE recipes SET visibility='private' WHERE id=1")
        db.execute("INSERT INTO flavor_reference(id,brand,name,category,active) VALUES(102,'Test','Cream','Flavor',1)")
        db.execute("INSERT INTO flavours(id,brand,canonical_name,category,source_flavor_ref_id) VALUES(2,'Test','Cream','Flavor',102)")
        for rid,pct,visibility,hidden in ((2,1,'public',0),(3,3,'public',0),(4,5,'public',0),(5,90,'private',0),(6,80,'unlisted',0),(7,70,'public',1)):
            vid=rid*10
            db.execute("INSERT INTO recipes(id,name,created_by,visibility,is_hidden,moderation_status,active,status,current_version_id) VALUES(?, ?,1,?,?,'active',1,'READY',?)",(rid,'Recipe '+str(rid),visibility,hidden,vid))
            for version,amount in ((vid,pct),(vid+1,99)):
                db.execute("INSERT INTO recipe_versions(id,recipe_id,version_number) VALUES(?,?,'1')",(version,rid))
                db.execute('INSERT INTO recipe_items(version_id,ingredient_id,amount_percent) VALUES(?,101,?)',(version,amount))
            db.execute('INSERT INTO recipe_items(version_id,ingredient_id,amount_percent) VALUES(?,102,2)',(vid,))
        db.commit()

    def test_public_current_once_and_pair_counts(self):
        with api_fixtures.flask_app.app_context():
            self.seed()
            stats=flavour_insights([101])
            self.assertEqual(stats['count'],3)
            self.assertEqual(stats['median'],3)
            self.assertEqual(stats['average'],3)
            self.assertEqual((stats['minimum'],stats['maximum']),(1,5))
            self.assertEqual(sum(b['count'] for b in stats['distribution']),3)
            self.assertEqual([(r['flavour_id'],r['recipe_count']) for r in stats['pairings']],[(2,3)])
            self.assertEqual(recipes_repo.count_tvw_public_current_recipes_using_refs([101]),3)
            row=next(r for r in recipes_repo.fetch_tvw_flavours()[0] if r['id']==1)
            self.assertEqual(row['used_in_count'],3)

    def test_duplicate_lines_sum_once_and_bad_values_not_reported(self):
        with api_fixtures.flask_app.app_context():
            self.seed()
            db=get_db()
            db.execute('INSERT INTO recipe_items(version_id,ingredient_id,amount_percent) VALUES(20,101,1)')
            db.execute('UPDATE recipe_items SET amount_percent=-2 WHERE version_id=30 AND ingredient_id=101')
            db.execute('UPDATE recipe_items SET amount_percent=101 WHERE version_id=40 AND ingredient_id=101')
            db.commit()
            stats=flavour_insights([101])
            self.assertEqual(stats['count'],1)
            self.assertEqual(stats['median'],2)
            self.assertEqual(stats['pairings'][0]['recipe_count'],1)
            self.assertEqual(flavour_insights([])['count'],0)
            self.assertEqual(flavour_insights([99999])['pairings'],[])

    def test_page_labels_source_and_does_not_expose_private_data(self):
        with api_fixtures.flask_app.app_context(): self.seed()
        response=self.client.get('/flavours/1')
        self.assertEqual(response.status_code,200)
        html=response.get_data(as_text=True)
        self.assertIn('3 public TVW recipes',html)
        self.assertIn('Small sample',html)
        self.assertIn('not single-flavour testing recommendations',html)
        self.assertIn('Often used in the same recipe',html)
        self.assertNotIn('Recipe 5',html)
