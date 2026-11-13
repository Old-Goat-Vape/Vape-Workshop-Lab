import unittest
from tests import test_tvw_api as base
from db import get_db
from services import purchase_purpose_service as purpose

class PurchasePurposeTests(unittest.TestCase):
    setUp = base.TVWApiTests.setUp
    tearDown = base.TVWApiTests.tearDown

    def add(self, **extra):
        return self.client.post('/flavours/1/purchase-purpose',data=dict(notes='For my biscuit experiment <private>',source_url='https://example.com/review',recipe_id='1',planned_ml='30',**extra))

    def test_shopping_purchase_archive_restore_preserves_details_and_stock(self):
        self.assertEqual(self.add().status_code,302)
        page=self.client.get('/shopping-list')
        self.assertEqual(page.status_code,200)
        self.assertIn(b'biscuit experiment &lt;private&gt;',page.data)
        self.assertIn(b'Recipe1',page.data)
        with base.flask_app.app_context():
            before=[tuple(r) for r in get_db().execute('SELECT * FROM user_stash_items')]
            eid=purpose.entries(1)[0]['id']
        result=self.client.post(f'/shopping-list/{eid}/status',data=dict(status='bought'))
        self.assertIn('#stash-actions',result.location)
        self.assertNotIn(b'biscuit experiment',self.client.get('/shopping-list').data)
        self.assertIn(b'biscuit experiment',self.client.get('/shopping-list?status=bought').data)
        self.client.post(f'/shopping-list/{eid}/status',data=dict(status='archived'))
        self.assertIn(b'biscuit experiment',self.client.get('/shopping-list?status=archived').data)
        self.client.post(f'/shopping-list/{eid}/status',data=dict(status='shopping'))
        with base.flask_app.app_context():
            row=purpose.entries(1)[0]
            self.assertEqual(row['planned_ml'],30)
            self.assertEqual(row['source_url'],'https://example.com/review')
            self.assertEqual(before,[tuple(r) for r in get_db().execute('SELECT * FROM user_stash_items')])

    def test_private_even_from_old_goat_and_cross_owner_mutations_fail(self):
        self.add()
        with self.client.session_transaction() as s:
            s.clear();s.update(user_id=2,username='Old Goat',role='Old Goat')
        self.assertNotIn(b'biscuit experiment',self.client.get('/shopping-list').data)
        self.assertEqual(self.client.post('/shopping-list/1/status',data=dict(status='bought')).status_code,404)
        self.assertEqual(self.client.post('/flavours/1/purchase-purpose?edit=1',data=dict(notes='replace')).status_code,404)

    def test_invalid_source_and_nonfinite_amount_never_saved(self):
        for key,value in [('source_url','javascript:alert(1)'),('source_url','https://user:pass@example.com'),('source_url','http://['),('source_url','https://example.com:bad'),('planned_ml','nan'),('planned_ml','inf'),('planned_ml','-1'),('recipe_id','xyz')]:
            form=dict(notes='Keep this input',source_url='',planned_ml='',recipe_id='')
            form[key]=value
            page=self.client.post('/flavours/1/purchase-purpose',data=form)
            self.assertIn(b'Keep this input',page.data)
        with base.flask_app.app_context(): self.assertEqual(purpose.entries(1),[])

    def test_hidden_recipe_name_not_leaked_and_cannot_be_linked(self):
        self.add()
        with base.flask_app.app_context():
            db=get_db();db.execute("UPDATE recipes SET name='Hidden secret',created_by=2,visibility='private' WHERE id=1");db.commit()
        page=self.client.get('/shopping-list')
        self.assertNotIn(b'Hidden secret',page.data)
        self.assertIn(b'currently unavailable',page.data)
        page=self.client.get('/flavours/1/purchase-purpose?search=Hidden')
        self.assertNotIn(b'Hidden secret',page.data)
        self.assertEqual(self.add().status_code,200)
        with base.flask_app.app_context(): self.assertEqual(len(purpose.entries(1)),1)

    def test_edit_multiple_reasons_and_stash_removal(self):
        self.add();self.add()
        self.assertEqual(self.client.get('/flavours/1/purchase-purpose?edit=1').status_code,200)
        self.client.post('/flavours/1/purchase-purpose?edit=1',data=dict(notes='Changed reason'))
        with base.flask_app.app_context():
            db=get_db();db.execute('DELETE FROM user_stash_items WHERE user_id=1');db.commit()
            self.assertEqual(len(purpose.entries(1)),2)
            self.assertEqual(purpose.stash_summaries(1)[1]['count'],2)
        self.assertIn(b'Changed reason',self.client.get('/shopping-list').data)
        self.assertEqual(self.client.get('/stash').status_code,200)
        self.assertEqual(self.client.get('/flavours/1').status_code,200)

    def test_duplicate_merge_preserves_all_purchase_reasons(self):
        from services import catalogue_cleanup_service as cleanup
        with base.flask_app.app_context():
            db=get_db()
            db.execute("INSERT INTO flavours(id,canonical_name,brand,category,source_flavor_ref_id) VALUES(2,'Vanilla','Test','Flavor',101)")
            from services import expert_flavour_review_service as expert
            expert.import_records([dict(source_key='merge-test',reviewer='Old Goat',manufacturer='Test',code='Test',flavour='Vanilla',source_url='https://example.com/merge-review',review_text='Original review')])
            # Ambiguous duplicate identities must wait for consolidation.
            purpose.save(1,1,dict(notes='First purpose'))
            purpose.save(1,2,dict(notes='Second purpose'))
            plan=next(p for p in cleanup.duplicate_plan(db) if set([p['target_id']]+p['source_ids'])=={1,2})
            cleanup.consolidate_group(db,plan,actor_user_id=2,actor_role='Old Goat')
            db.commit()
            records=purpose.entries(1)
            self.assertEqual(len(records),2)
            self.assertEqual({r['flavour_id'] for r in records},{plan['target_id']})
            self.assertEqual({r['notes'] for r in records},{'First purpose','Second purpose'})
            self.assertEqual(expert.reconcile_unmatched(),1)
            self.assertEqual(len(expert.for_flavour(plan['target_id'])),1)

    def test_anonymous_access_requires_login(self):
        with self.client.session_transaction() as s: s.clear()
        for url in ['/shopping-list','/flavours/1/purchase-purpose']:
            response=self.client.get(url)
            self.assertEqual(response.status_code,302)
            self.assertIn('login',response.location)

    def test_already_bought_reason_does_not_create_shopping_task(self):
        self.add(status='bought')
        self.assertNotIn(b'biscuit experiment',self.client.get('/shopping-list').data)
        self.assertIn(b'biscuit experiment',self.client.get('/shopping-list?status=bought').data)
