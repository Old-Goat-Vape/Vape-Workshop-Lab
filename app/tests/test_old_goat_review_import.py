import base64
import gzip
import hashlib
import json
from pathlib import Path
import unittest
from tests import test_tvw_api as base
from db import get_db
from services import expert_flavour_review_service as expert
from scripts.build_old_goat_review_bundle import heading_identity,build,build_additional,duplicate_key


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
            expected={}
            for path in (Path(expert.__file__).resolve().parents[1]/'data/flavour_reviews').glob('*.json'):
                b=json.loads(path.read_text())
                for row in json.loads(gzip.decompress(base64.b64decode(b['gzip_base64']))):
                    expected[row['reviewer']]=expected.get(row['reviewer'],0)+1
            self.assertEqual(db.execute('SELECT COUNT(*) FROM expert_flavour_reviews').fetchone()[0],sum(expected.values()))
            self.assertIsNone(expert.import_bundled_once())
            self.assertEqual({r['reviewer']:r['total'] for r in expert.reviewer_totals()},expected)
            counts,rows,total=expert.report(reviewer='Old Goat')
            self.assertEqual(total,expected['Old Goat'])
            self.assertTrue(all(r['reviewer']=='Old Goat' for r in rows))

    def test_additional_bundle_coverage_identity_and_original_prose(self):
        path=Path(expert.__file__).resolve().parents[1]/'data/flavour_reviews/old-goat-additional-2026-11-13.json'
        b=json.loads(path.read_text())
        rows=json.loads(gzip.decompress(base64.b64decode(b['gzip_base64'])))
        self.assertEqual(b['documents_read'],358)
        self.assertEqual(sum(b['folder_item_counts'].values()),358)
        self.assertEqual(len(rows),355)
        self.assertEqual(len(b['excluded_documents']),2)
        self.assertEqual(len(b['duplicate_documents']),1)
        _,old=bundle_rows()
        self.assertEqual(len({r['source_key'] for r in rows+old}),413)
        self.assertEqual(len({duplicate_key(r) for r in rows+old}),413)
        for row in rows:
            self.assertEqual(row['content_sha256'],hashlib.sha256(row['review_text'].encode()).hexdigest())
            self.assertEqual(heading_identity(row['review_text'].split('\n')[0].strip()),(row['code'],row['flavour']))
        for h,result in [
            ('Candied Watermelon (Capella Silver Line)',('CAP (SL)','Candied Watermelon')),
            ('Cinnamon Sugar (CAP SL)',('CAP (SL)','Cinnamon Sugar')),
            ('French Vanilla (CAP) v2',('CAP','French Vanilla v2')),
            ('Flavour Art (FA) King (Pazzo) – Flavour Review',('FA PAZZO','King')),
            ('Ace (Pazzo) – Flavour Review',('FA PAZZO','Ace')),
            ('Flavour Art Custard Extra 2 – Flavour Review',('FA','Custard Extra 2')),
            ('by Flavorah (FLV)Native Tobacco',('FLV','Native Tobacco')),
            ('French Vanilla V2 (DIYFS) – DIY Flavour Shack',('DIYFS','French Vanilla V2')),
        ]:self.assertEqual(heading_identity(h),result)

    def test_deduplication_keeps_revisions_versions_and_manufacturers(self):
        def doc(i,heading,body):
            return dict(id=str(i),title='source.docx',content=heading+'\n'+body)
        docs=[doc(1,'Custard (CAP)','Original review.'),
              doc(2,'Custard (CAP)','Original   review.'),
              doc(3,'Custard (CAP)','Revised review.'),
              doc(4,'Custard V2 (CAP)','Original review.'),
              doc(5,'Custard (FLV)','Original review.')]
        b=build_additional(docs,[])
        self.assertEqual(b['records'],4)
        self.assertEqual(len(b['duplicate_documents']),1)
        rows=json.loads(gzip.decompress(base64.b64decode(b['gzip_base64'])))
        again=build_additional(docs,rows)
        self.assertEqual(again['records'],0)
        self.assertEqual(len(again['duplicate_documents']),5)
