import unittest
from flowza_bot.crm import CRM
from flowza_bot.http import HTTPError
from flowza_bot.telegram import Telegram
from .helpers import Harness


class ClientTests(unittest.TestCase):
    def setUp(self):self.h=Harness()
    def tearDown(self):self.h.close()
    def test_refresh_rotates_and_retry_once(self):
        calls=[]
        def transport(method,url,payload=None,headers=None):
            calls.append((url,payload,headers))
            if url.endswith('auth/login/'):return {'access':'old','refresh':'r1'}
            if url.endswith('auth/refresh/'):
                self.assertEqual(payload,{'refresh':'r1'});return {'access':'new','refresh':'r2'}
            if headers['Authorization']=='Bearer old':raise HTTPError(401)
            return {'ok':True}
        crm=CRM(self.h.config,transport);self.assertEqual(crm.call('GET','me/'),{'ok':True})
        self.assertEqual(crm.refresh,'r2');self.assertEqual(len(calls),4)
    def test_catalog_paginates_and_filters(self):
        crm=CRM(self.h.config);paths=[]
        def call(method,path,payload=None):
            paths.append(path)
            if path=='me/':return {'role':'MASTER','master_profile':{'id':'m','is_available':True,'skills':[{'specialization':'1','is_active':True}]}}
            if path.endswith('page=1'):return {'results':[{'id':'2','name':'inactive'}],'next':'https://other-host/?page=2'}
            return {'results':[{'id':'1','name':'active'}],'next':None}
        crm.call=call;self.assertEqual(crm.catalog()['specs'],[{'id':'1','name':'active'}])
        self.assertEqual(paths[-1],'specializations/?page=2')
    def test_malformed_success_is_unknown(self):
        crm=CRM(self.h.config);crm.call=lambda *args:{'id':'invalid'}
        with self.assertRaises(HTTPError) as e:crm.submit({})
        self.assertEqual(e.exception.status,502)
    def test_outbox_survives_send_failure(self):
        self.h.text('/start')
        telegram=Telegram('secret',lambda *a,**kw:(_ for _ in ()).throw(HTTPError()))
        with self.assertRaises(HTTPError):telegram.drain(self.h.store)
        self.assertTrue(self.h.store.pending())
        telegram.transport=lambda *a,**kw:{'ok':True,'result':{}}
        telegram.drain(self.h.store);self.assertEqual(self.h.store.pending(),[])
    def test_sensitive_error_is_sanitized(self):
        self.assertNotIn('secret',str(HTTPError(400,{'password':'secret'})))
