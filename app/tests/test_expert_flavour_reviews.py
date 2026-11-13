import unittest
from tests import test_tvw_api as base
from db import get_db
from services import expert_flavour_review_service as expert

class ExpertReviewTests(unittest.TestCase):
    def setUp(self):
        base.TVWApiTests.setUp(self)
        with base.flask_app.app_context():
            db=get_db();db.execute('DELETE FROM expert_flavour_reviews');db.commit()
    tearDown=base.TVWApiTests.tearDown

    def record(self,**values):
        return dict(dict(source_key='one',reviewer='SessionDrummer',code='Test',manufacturer='Test (2026)',
            flavour='Vanilla',source_url='https://example.com/review/2',review_text='Vanilla (Test) 2% (1-1-26) – <script>unsafe()</script>\nOriginal notes. 9.8/10.'),**values)

    def test_idempotent_import_is_attributed_and_not_a_community_vote(self):
        with base.flask_app.app_context():
            self.assertEqual(expert.import_records([self.record()])['matched'],1)
            expert.import_records([self.record()])
            self.assertEqual(len(expert.for_flavour(1)),1)
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM flavour_reviews').fetchone()[0],0)
        page=self.client.get('/flavours/1')
        self.assertEqual(page.status_code,200)
        self.assertIn(b'SessionDrummer',page.data);self.assertIn(b'9.8/10',page.data)
        self.assertIn(b'&lt;script&gt;',page.data);self.assertNotIn(b'<script>unsafe()',page.data)

    def test_versions_and_lines_do_not_collapse(self):
        self.assertNotEqual(expert.identity('CAP','Vanilla Custard V1'),expert.identity('CAP','Vanilla Custard V2'))
        self.assertNotEqual(expert.identity('CAP','Biscuit'),expert.identity('CAP (SL)','Biscuit'))
        self.assertNotEqual(expert.identity('Real Flavors SC','Vanilla'),expert.identity('Real Flavors','Vanilla'))
        self.assertNotEqual(expert.identity('NomNomz Super Concentrates','Vanilla'),expert.identity('Nom Nomz Treatz','Vanilla'))
        self.assertEqual(expert.identity('Wonder Flavours','Vanilla'),expert.identity('WF','Vanilla'))

    def test_missing_and_ambiguous_and_source_conflicts_are_not_attached(self):
        with base.flask_app.app_context():
            db=get_db();db.execute("INSERT INTO flavours(id,brand,canonical_name,category) VALUES(2,'Test','Vanilla','Flavor')");db.commit()
            result=expert.import_records([self.record(),self.record(source_key='missing',flavour='Unknown'),self.record(source_key='conflict',review_text='')])
            self.assertEqual(result,dict(matched=0,ambiguous=1,unmatched=1,source_review_needed=1))
            self.assertEqual(expert.for_flavour(1),[])

    def test_owner_report_permissions_and_reconciliation(self):
        self.assertEqual(self.client.get('/flavour-review-imports').status_code,403)
        self.assertEqual(self.client.post('/flavour-review-imports').status_code,403)
        with base.flask_app.app_context():
            expert.import_records([self.record(flavour='New flavour')])
            db=get_db();db.execute("INSERT INTO flavours(id,brand,canonical_name,category) VALUES(2,'Test','New flavour','Flavor')");db.commit()
        with self.client.session_transaction() as s:
            s.clear();s.update(user_id=2,username='Old Goat',role='Old Goat')
        self.assertEqual(self.client.get('/flavour-review-imports').status_code,200)
        self.assertEqual(self.client.post('/flavour-review-imports').status_code,302)
        with base.flask_app.app_context():self.assertEqual(len(expert.for_flavour(2)),1)

    def test_invalid_sources_rejected(self):
        with base.flask_app.app_context():
            for url in ['javascript:alert(1)','https://user:pass@example.com/review']:
                with self.assertRaises(ValueError):expert.import_records([self.record(source_url=url)])

    def test_bundle_count_unique_sources_and_content_integrity(self):
        import json,gzip,base64
        from pathlib import Path
        bundle=json.loads((Path(expert.__file__).resolve().parents[1]/'data/flavour_reviews/sd-2026-11-13.json').read_text())
        rows=json.loads(gzip.decompress(base64.b64decode(bundle['gzip_base64'])))
        self.assertEqual(len(rows),1177)
        self.assertEqual(len({r['source_key'] for r in rows}),1177)
        self.assertEqual(sum(bool(r['review_text']) for r in rows),1144)
        self.assertTrue(all(r['reviewer']=='SessionDrummer' for r in rows))
