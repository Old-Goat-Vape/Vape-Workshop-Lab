import os
os.environ.setdefault('TVW_ENABLE_LAB_MODULES','true')
import unittest
from urllib.parse import urlencode
from tests import test_tvw_api as base
from db import get_db
from services import flavour_testing_service as tasting, flavour_browse_service as browse

FORM=dict(label='Private test label',kind='single',percentage='2.5',pg_percent='0',device='Personal device name',power_watts='40',age_days='0',strength='weak',impressions='Fresh <fruit>')

class FlavourTastingTests(unittest.TestCase):
    setUp=base.TVWApiTests.setUp
    tearDown=base.TVWApiTests.tearDown

    def login_other(self):
        with self.client.session_transaction() as state:
            state.clear();state.update(user_id=2,username='Old Goat',role='Old Goat')

    def test_navigation_keeps_filters_sort_and_page_and_blocks_external_targets(self):
        return_path='/flavours?'+urlencode(dict(manufacturer='CAP',search='Vanilla',profile='Custard',in_stash='1',sort_by='rating',sort_dir='desc',page='3',page_size='50'))
        page=self.client.get('/flavours/1?'+urlencode(dict(return_to=return_path)))
        self.assertEqual(page.status_code,200)
        from bs4 import BeautifulSoup
        soup=BeautifulSoup(page.data,'html.parser')
        self.assertEqual(soup.find('a',string='← Back to flavour library')['href'],return_path)
        self.assertTrue(any(a.get('href')==return_path and a.get_text(strip=True)=='Flavours' for a in soup.find_all('a')))
        response=self.client.post('/flavours/1/notebook',data=dict(private_notes='Remember',return_to=return_path))
        self.assertIn('return_to=',response.location)
        for target in ('https://evil.example/flavours','//evil.example/flavours','/recipes','http://[','/flavours/1'):
            self.assertEqual(browse.library_return(target),'/flavours')

    def test_card_links_carry_filtered_context(self):
        page=self.client.get('/flavours?manufacturer=Test&search=Vanilla')
        self.assertEqual(page.status_code,200)
        self.assertIn(b'return_to=',page.data)
        self.assertIn(b'manufacturer%3DTest',page.data)

    def test_first_tasting_day_zero_and_followups_are_private(self):
        response=self.client.post('/flavours/1/tests',data=FORM)
        self.assertEqual(response.status_code,302)
        with base.flask_app.app_context():
            test=tasting.tests_for(1,1)[0]
            self.assertEqual(test['percentage'],2.5)
            self.assertEqual(test['pg_percent'],0)
            self.assertEqual(test['observations'][0]['age_days'],0)
            tid=test['id']
        response=self.client.post(f'/flavours/1/tests/{tid}/observe',data=dict(age_days='14',strength='balanced',impressions='Creamier'))
        self.assertEqual(response.status_code,302)
        page=self.client.get('/flavours/1')
        self.assertIn(b'Day 14',page.data);self.assertIn(b'Fresh &lt;fruit&gt;',page.data)
        self.login_other()
        self.assertNotIn(b'Private test label',self.client.get('/flavours/1').data)
        self.assertEqual(self.client.post(f'/flavours/1/tests/{tid}/observe',data=dict(age_days='7',strength='strong')).status_code,404)
        self.assertEqual(self.client.post(f'/flavours/1/tests/{tid}/share',data={}).status_code,404)

    def test_invalid_numbers_rollback_and_keep_input(self):
        for key,value in [('percentage','nan'),('percentage','0'),('power_watts','inf'),('pg_percent','101'),('age_days','1.5'),('strength','fake')]:
            response=self.client.post('/flavours/1/tests',data={**FORM,key:value})
            self.assertEqual(response.status_code,400,(key,value))
            self.assertIn(b'Private test label',response.data)
        with base.flask_app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM flavour_tasting_tests').fetchone()[0],0)

    def test_share_is_explicit_snapshot_idempotent_and_stale_preview_rejected(self):
        with base.flask_app.app_context():
            db=get_db();tid=tasting.create(1,1,FORM)
            test=tasting.tests_for(1,1)[0]
            self.assertEqual(db.execute('SELECT COUNT(*) FROM flavour_notes').fetchone()[0],0)
            self.assertNotIn('Private test label',test['share_preview'])
            self.assertNotIn('Personal device name',test['share_preview'])
            tasting.observe(1,1,tid,dict(age_days='7',strength='balanced',impressions='Later'))
            with self.assertRaises(ValueError):tasting.share(1,1,tid,test['share_digest'])
            test=tasting.tests_for(1,1)[0]
            nid=tasting.share(1,1,tid,test['share_digest'])
            self.assertEqual(tasting.share(1,1,tid,test['share_digest']),nid)
            tasting.observe(1,1,tid,dict(age_days='14',strength='balanced',impressions='PRIVATE FOLLOWUP'))
            note=db.execute('SELECT note FROM flavour_notes WHERE id=?',(nid,)).fetchone()[0]
            self.assertNotIn('PRIVATE FOLLOWUP',note)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM flavour_notes').fetchone()[0],1)
            updated=tasting.tests_for(1,1)[0]
            new_note=tasting.share(1,1,tid,updated['share_digest'])
            self.assertNotEqual(new_note,nid)
            self.assertEqual(tasting.share(1,1,tid,updated['share_digest']),new_note)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM flavour_notes').fetchone()[0],2)

    def test_correction_cannot_change_another_tests_observation(self):
        with base.flask_app.app_context():
            one=tasting.create(1,1,FORM);two=tasting.create(2,1,FORM)
            observation=tasting.tests_for(2,1)[0]['observations'][0]['id']
            with self.assertRaises(LookupError):tasting.observe(1,1,one,dict(observation_id=observation,age_days='3',strength='balanced'))
