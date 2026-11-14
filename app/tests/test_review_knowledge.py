import base64
import gzip
import json
from pathlib import Path
import unittest
from tests import test_tvw_api as base
from db import get_db
from services import expert_flavour_review_service as expert
from services import flavour_review_knowledge_service as knowledge


class ExtractionTests(unittest.TestCase):
    def test_test_conditions_are_not_recommendations_and_evidence_is_original(self):
        text='Custard (CAP)\nTested at: 3%, 4%, and 5%\nTesting Setup:\nSteep Time: 10 days\nBase Ratio: 70VG/30PG\nNicotine: 3 mg/ml\nSteeping:\nMinimum 2 weeks.\nRecommended Mixing Percentages:\nStandalone: 3-5%\nIn Mixes: 0.5-2%, often around 0.9-1.5%'
        facts=knowledge.extract(text,'Old Goat')
        groups=lambda k:[f for f in facts if f['kind']==k]
        self.assertEqual([f['low'] for f in groups('tested_percent')],[3,4,5])
        self.assertEqual(groups('tested_steep')[0]['high'],10)
        self.assertEqual(groups('steep_days')[0]['high'],14)
        self.assertEqual([(f['low'],f['high']) for f in groups('mix_percent')],[(.5,2),(.9,1.5)])
        self.assertTrue(all(f['evidence'] in text for f in facts))

    def test_narrative_test_durations_do_not_become_advice(self):
        for phrase in ['after a short 1 week steep','I gave it a 2 week steep','Let this one steep for over 2 weeks','after a whopping THREE day steep']:
            facts=knowledge.extract('Vanilla (Test) 3.5% (1-1-26) – '+phrase+'.','SessionDrummer')
            self.assertFalse(any(f['kind']=='steep_days' for f in facts))
            self.assertTrue(any(f['kind']=='tested_steep' for f in facts))
        facts=knowledge.extract('Vanilla (Test) 3.5% (1-1-26) – Needs a minimum 2 week steep.','SessionDrummer')
        self.assertTrue(any(f['kind']=='steep_days' and f['high']==14 for f in facts))

    def test_negations_unknowns_scores_and_pairings_are_preserved(self):
        text='Vanilla\nOff-notes: None detected.\nSweetness: Low\nPairs well with cream, but avoid too much cinnamon.\nEnjoyment Rating: 8.5/10\nSteeping:\nNot a shake-and-vape.\nIn Mixes: Do not exceed 2%.'
        facts=knowledge.extract(text,'Old Goat')
        self.assertFalse(any(f['kind'] in ('steep_days','mix_percent') for f in facts))
        self.assertTrue(any(f['kind']=='pairings' and 'avoid' in f['evidence'] for f in facts))
        score=next(f for f in facts if f['kind']=='rating')
        self.assertEqual((score['value'],score['scale']),(8.5,10))
        self.assertIn('Enjoyment',score['dimension'])
        self.assertFalse(knowledge.extract('','Old Goat'))
        self.assertTrue(any(f['high']==0 for f in knowledge.extract('Steeping:\nShake & vape.','Old Goat') if f['kind']=='steep_days'))

    def test_all_verified_bundled_reviews_have_findings_without_fabricated_sources(self):
        folder=Path(expert.__file__).resolve().parents[1]/'data/flavour_reviews'
        counts={};with_findings=0
        for path in folder.glob('*.json'):
            bundle=json.loads(path.read_text())
            for row in json.loads(gzip.decompress(base64.b64decode(bundle['gzip_base64']))):
                facts=knowledge.extract(row['review_text'],row['reviewer'])
                with_findings+=bool(facts)
                self.assertTrue(all(f['evidence'] in row['review_text'] for f in facts))
                if not row['review_text']:self.assertEqual(facts,[])
        self.assertEqual(with_findings,1557)


class KnowledgeIntegrationTests(unittest.TestCase):
    setUp=base.TVWApiTests.setUp
    tearDown=base.TVWApiTests.tearDown

    def seed(self):
        db=get_db()
        db.execute('DELETE FROM expert_flavour_reviews')
        db.execute('UPDATE flavours SET source_flavor_ref_id=101 WHERE id=1')
        db.commit()
        expert.import_records([dict(source_key='knowledge-test',reviewer='Old Goat',code='Test',manufacturer='Test',
            flavour='Vanilla',source_url='https://example.com/review',review_text='Vanilla (Test)\nTested at: 4%\nTesting Setup:\nSteep Time: 7 days\nSteeping:\nMinimum 14 days.\nRecommended Mixing Percentages:\nIn Mixes: 0.5-2%, often around 0.9-1.5%\nSweetness: Not overly sweet.\nPairs well with cream. <script>bad()</script>')])
        knowledge.refresh()

    def test_backfill_restart_reextract_and_exact_flavour_and_reference_mapping(self):
        with base.flask_app.app_context():
            self.seed();db=get_db()
            self.assertEqual(knowledge.refresh(),0)
            result=knowledge.for_flavours([1])
            self.assertEqual(result['suggested_steep_days'],14)
            self.assertEqual(result['reviewer_count'],1)
            self.assertEqual(len(next(g['facts'] for g in result['groups'] if g['kind']=='mix_percent')),2)
            self.assertEqual(knowledge.for_reference(101)['review_count'],1)
            self.assertEqual(knowledge.for_reference(999999)['review_count'],0)
            db.execute("UPDATE expert_flavour_reviews SET review_text=review_text||'\nStrength: Very potent.'")
            db.commit()
            self.assertEqual(knowledge.refresh(),1)
            self.assertTrue(any(g['kind']=='strength' for g in knowledge.for_flavours([1])['groups']))
            db.execute("UPDATE expert_flavour_reviews SET match_status='ambiguous'");db.commit()
            self.assertEqual(knowledge.for_reference(101)['review_count'],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM flavour_reviews').fetchone()[0],0)

    def test_builder_api_detail_escaping_permissions_and_hidden_flavours(self):
        with base.flask_app.app_context():self.seed()
        response=self.client.get('/api/recipes/flavour-usage/101')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['knowledge']['suggested_steep_days'],14)
        page=self.client.get('/flavours/1')
        self.assertEqual(page.status_code,200)
        self.assertIn(b'What to expect from this flavour',page.data)
        self.assertIn(b'&lt;script&gt;bad()',page.data)
        self.assertNotIn(b'<script>bad()',page.data)
        with base.flask_app.app_context():
            db=get_db();db.execute('UPDATE flavours SET is_hidden=1 WHERE id=1');db.commit()
            self.assertEqual(knowledge.for_reference(101)['review_count'],0)
        with self.client.session_transaction() as state:state.clear()
        self.assertIn(self.client.get('/api/recipes/flavour-usage/101').status_code,(302,401))
