import unittest
from unittest.mock import patch
from flowza_bot.http import HTTPError
from flowza_bot.storage import Store, InstanceLock
from flowza_bot.dialogue import Dialogue
from flowza_bot.i18n import CATALOGS
from .helpers import Harness, NOW


class DialogueTests(unittest.TestCase):
    def setUp(self):self.h = Harness()
    def tearDown(self):self.h.close()

    def test_outside_working_hours_returns_to_date_both_languages(self):
        for lang, user in [('ru', 100), ('kk', 200)]:
            self.h.form(lang, user)
            self.h.crm.error = HTTPError(409, {'code': 'outside_working_hours'})
            self.h.click('send', user)
            state = self.h.state(user)
            self.assertEqual(state['step'], 'date')
            self.assertNotIn('payload', state)
            self.h.crm.error = None
            self.h.text('06.10.2026 10:00', user)
            self.h.click('send', user)
            self.assertEqual(self.h.state(user)['step'], 'done')

    def test_complete_both_languages_and_utc(self):
        for lang,user in [('ru',100),('kk',200)]:
            with self.subTest(lang=lang):
                self.h.form(lang,user);self.h.click('send',user)
                s=self.h.state(user);self.assertEqual(s['step'],'done');self.assertEqual(s['lang'],lang)
                p=self.h.crm.requests[-1]
                self.assertEqual(p['order']['start_at'],'2026-10-05T05:00:00+00:00')
                self.assertEqual(p['order']['end_at'],'2026-10-05T06:00:00+00:00')
                self.assertEqual(p['client']['phone'],'+77012345678')
                self.assertEqual(p['client']['external_id'],f'telegram:123:{user}')
                self.assertEqual(p['order']['description'],'Кран ағып тұр')
                self.assertEqual(s['fields'],{})

    def test_languages_have_identical_keys_and_placeholders(self):
        from string import Formatter
        self.assertEqual(CATALOGS['ru'].keys(),CATALOGS['kk'].keys())
        for key in CATALOGS['ru']:
            fields=lambda x:{f for _,f,_,_ in Formatter().parse(x) if f}
            self.assertEqual(fields(CATALOGS['ru'][key]),fields(CATALOGS['kk'][key]),key)

    def test_language_change_preserves_form_and_restart(self):
        self.h.form();before=self.h.state()['fields']
        self.h.click('language');self.h.click('lang:kk')
        self.assertEqual(self.h.state()['fields'],before)
        self.h.store.close();self.h.store=Store(self.h.path)
        self.h.engine=Dialogue(self.h.store,self.h.crm,self.h.config,123,now=lambda:NOW)
        self.assertEqual(self.h.state()['lang'],'kk');self.assertEqual(self.h.state()['step'],'review')
        self.h.text('/start');self.h.click('resume');self.assertEqual(self.h.state()['step'],'review')

    def test_errors_contacts_commands_and_media(self):
        self.h.text('/start');self.h.click('lang:ru');self.h.click('continue')
        self.h.text(None,extra={'photo':[{}]});self.assertEqual(self.h.state()['step'],'name')
        self.h.text('/unknown');self.assertEqual(self.h.state()['fields'],{})
        self.h.text('A');self.h.text('string');self.assertEqual(self.h.state()['step'],'phone')
        self.h.text(None,contact={'user_id':200,'phone_number':'+77012345678'})
        self.assertEqual(self.h.state()['step'],'phone')
        self.h.text(None,contact={'user_id':100,'phone_number':'87012345678'})
        self.assertEqual(self.h.state()['step'],'service')

    def test_bad_dates_and_edit_then_cancel(self):
        self.h.form();self.h.click('edit');self.h.click('edit:date')
        for value in ['wrong','01.10.2026 10:00','05.10.2028 10:00','31.02.2027 10:00']:
            self.h.text(value);self.assertEqual(self.h.state()['step'],'date')
        self.h.text('06.10.2026 10:00');self.assertEqual(self.h.state()['step'],'review')
        self.h.click('cancel');self.assertEqual(self.h.state()['step'],'idle')
        self.assertFalse(self.h.crm.requests)

    def test_timeout_freezes_and_same_key_retry(self):
        self.h.form();self.h.crm.error=HTTPError()
        update=self.h.click('send');payload=self.h.state()['payload']
        self.h.text('/cancel');self.h.text('/new');self.h.text('/back')
        self.assertEqual(self.h.state()['payload'],payload)
        self.h.click('language');self.h.click('lang:kk')
        self.assertEqual(self.h.state()['payload'],payload)
        self.h.crm.error=None;self.h.click('send')
        self.assertEqual(self.h.crm.requests[0],self.h.crm.requests[1])
        self.h.engine.process(update);self.assertEqual(len(self.h.crm.requests),2)

    def test_crash_after_server_commit_before_local_ack(self):
        self.h.form()
        original=self.h.crm.submit
        def created_then_crash(payload):original(payload);raise RuntimeError('simulated crash')
        self.h.crm.submit=created_then_crash
        rev=self.h.state()['revision']
        with self.assertRaises(RuntimeError):self.h.click('send')
        self.assertEqual(self.h.state()['step'],'pending')
        self.h.crm.submit=original
        # Redelivery of the original update retries exactly the persisted request.
        self.h.click('send',revision=rev)
        self.assertEqual(len(self.h.crm.receipts),1);self.assertEqual(self.h.state()['step'],'done')

    def test_stale_double_click_and_new_order(self):
        self.h.form();rev=self.h.state()['revision'];self.h.click('send')
        self.h.click('send',revision=rev);self.assertEqual(len(self.h.crm.requests),1)
        first=self.h.crm.requests[0]['request_id']
        self.h.form();self.h.click('send')
        self.assertNotEqual(first,self.h.crm.requests[1]['request_id'])

    def test_conflict_returns_to_date_with_new_key(self):
        self.h.form();self.h.crm.error=HTTPError(409,{'code':'schedule_conflict'})
        self.h.click('send');first=self.h.crm.requests[0]['request_id']
        self.assertEqual(self.h.state()['step'],'date');self.assertNotIn('payload',self.h.state())
        self.h.text('06.10.2026 10:00');self.h.crm.error=None;self.h.click('send')
        self.assertNotEqual(first,self.h.crm.requests[-1]['request_id'])

    def test_users_are_isolated_and_group_ignored(self):
        self.h.form(user=100);self.h.text('/start',200);self.h.click('lang:kk',200)
        self.assertEqual(self.h.state(100)['step'],'review');self.assertEqual(self.h.state(200)['fields'],{})
        self.h.engine.process({'update_id':999,'message':{'from':{'id':100},'chat':{'id':-10,'type':'group'},'text':'hi'}})
        self.assertEqual(self.h.state(100)['step'],'review')

    def test_instance_lock_and_identity_guard(self):
        lock=InstanceLock(self.h.path)
        try:
            with self.assertRaises(ValueError):InstanceLock(self.h.path)
        finally:lock.close()
        lock=InstanceLock(self.h.path);lock.close()
        self.h.store.bind(1,'master')
        with self.assertRaises(ValueError):self.h.store.bind(2,'master')
