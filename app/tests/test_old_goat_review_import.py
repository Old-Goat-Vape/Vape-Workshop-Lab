import base64
import gzip
import hashlib
import json
from pathlib import Path
import unittest
from tests import test_tvw_api as base
from db import get_db
from services import expert_flavour_review_service as expert
from scripts.build_old_goat_review_bundle import heading_identity,build


def bundle_rows():
    path=Path(expert.__file__).resolve().parents[1]/'data/flavour_reviews/old-goat-2026-11-13.json'
    bundle=json.loads(path.read_text())
    return bundle,json.loads(gzip.decompress(base64.b64decode(bundle['gzip_base64'])))

class OldGoatImportTests(unittest.TestCase):
    setUp=base.TVWApiTests.setUp
    tearDown=base.TVWApiTests.tearDown

    def test_bundle_preserves_prose_and_sources_and_excludes_non_review(self):
        bundle,rows=bundle_rows()
        self.assertEqual(bundle['documents_read'],59)
        self.assertEqual(len(rows),58)
        self.assertEqual(len({r['source_key'] for r in rows}),58)
        self.assertEqual(len(bundle['excluded_documents']),1)
        self.assertFalse(any(r['source_filename'].startswith('Banana Cream') for r in rows))
        for row in rows:
            self.assertEqual(row['content_sha256'],hashlib.sha256(row['review_text'].encode()).hexdigest())
            self.assertEqual(row['reviewer'],'Old Goat')
            self.assertTrue(row['source_url'].startswith('https://docs.google.com/document/d/'))
        toffee=next(r for r in rows if r['flavour']=='Toffee')
        self.assertIn('Final Goat Score: 8.9/10',toffee['review_text'])
        self.assertIn('Steep Time: 10 days',toffee['review_text'])
        self.assertIn('Minimum 5–7 days',toffee['review_text'])

    def test_heading_identity_keeps_reformulations_and_product_lines(self):
        for header,identity in [('Sweet Strawberry RF (CAP)',('CAP','Sweet Strawberry RF')),
          ('Vanilla Custard v2 (CAP)',('CAP','Vanilla Custard v2')),
          ('Whipped Marshmallow (CAP) Silver Line',('CAP (SL)','Whipped Marshmallow')),
          ('Watermelon (Red Summer) — Flavour Art (FA)',('FA','Watermelon (Red Summer)')),
          ('Tobacco Absolute (FE 100%) – Flavor Express',('FE','Tobacco Absolute')),
          ('Bavarian Cream by Flavor West (FW)',('FW','Bavarian Cream')),
          ('FA Zeppola – Flavour Review',('FA','Zeppola'))]:
            self.assertEqual(heading_identity(header),identity)
        with self.assertRaises(ValueError):heading_identity('A random planning note')

    def test_author_specific_reviews_attach_without_merging_rating_scales(self):
        with base.flask_app.app_context():
            db=get_db()
            db.execute("INSERT INTO flavours(id,brand,canonical_name,category) VALUES(200,'FLV','Toffee','Flavor')")
            db.execute("INSERT INTO flavours(id,brand,canonical_name,category) VALUES(201,'CAP','Whipped Marshmallow','Flavor')")
            db.execute("INSERT INTO flavours(id,brand,canonical_name,category) VALUES(202,'CAP (SL)','Whipped Marshmallow','Flavor')")
            db.commit();expert.reconcile_unmatched()
            self.assertEqual(len(expert.for_flavour(200)),1)
            self.assertEqual(expert.for_flavour(200)[0]['reviewer'],'Old Goat')
            self.assertEqual(expert.for_flavour(201),[])
            self.assertTrue(expert.for_flavour(202))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM flavour_reviews').fetchone()[0],0)
        page=self.client.get('/flavours/200')
        self.assertIn(b'Old Goat',page.data);self.assertIn(b'8.9/10',page.data)

    def test_bundles_are_independent_and_repeat_startup_does_not_duplicate(self):
        with base.flask_app.app_context():
            db=get_db()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM expert_flavour_reviews').fetchone()[0],1235)
            self.assertIsNone(expert.import_bundled_once())
            self.assertEqual({r['reviewer']:r['total'] for r in expert.reviewer_totals()},{'Old Goat':58,'SessionDrummer':1177})
            counts,rows,total=expert.report(reviewer='Old Goat')
            self.assertEqual(total,58)
            self.assertTrue(all(r['reviewer']=='Old Goat' for r in rows))
