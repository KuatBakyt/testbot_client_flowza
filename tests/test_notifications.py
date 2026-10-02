import unittest
from dataclasses import replace
from datetime import timedelta
from flowza_bot.notifications import MasterNotifications
from flowza_bot.telegram import Telegram
from flowza_bot.http import HTTPError
from tests.helpers import Harness, NOW

ORDER = 'c9231ecf-22d8-4b11-b6d2-a3dd49c4bb3a'


class NoticesCRM:
    def __init__(self):
        self.error = None
        self.rows = [{'id': 'notice', 'type': 'BOT_ORDER', 'title': 'Новая заявка из Telegram',
                      'created_at': (NOW+timedelta(seconds=1)).isoformat(), 'payload': {'order_id': ORDER}}]
    def call(self, method, path):
        if self.error:
            raise self.error
        if path.startswith('notifications/'):
            return {'results': self.rows, 'next': None}
        if path.startswith('orders/'):
            return {'title': 'Ремонт', 'client': 'client', 'address': 'Алматы, Абая 10', 'district': 'Медеуский',
                    'start_at': (NOW+timedelta(days=1)).isoformat(), 'description': 'Кран течёт'}
        return {'name': 'Әлия', 'phone': '+77012345678'}


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.config = replace(self.h.config, master_chat_id=200, web_url='https://crm.example.test')
        self.crm = NoticesCRM()
    def tearDown(self):
        self.h.close()
    def engine(self):
        return MasterNotifications(self.h.store, self.crm, self.config, now=lambda: NOW)
    def test_notice_is_private_url_only_and_deduplicated_after_restart(self):
        self.engine().poll()
        self.engine().poll()
        messages = self.h.store.pending()
        self.assertEqual(len(messages), 1)
        payload = messages[0][1]['payload']
        self.assertEqual(payload['chat_id'], 200)
        self.assertNotIn('Әлия', payload['text'])
        self.assertNotIn('+77012345678', payload['text'])
        self.assertNotIn('Абая', payload['text'])
        button = payload['reply_markup']['inline_keyboard'][0][0]
        self.assertEqual(button['url'], f'https://crm.example.test/#/orders/{ORDER}')
        self.assertNotIn('callback_data', button)
        self.h.store.sent(messages[0][0])
        self.engine().poll()
        self.assertEqual(self.h.store.pending(), [])
    def test_network_failure_retries_and_old_notices_are_skipped(self):
        engine = self.engine()
        self.crm.error = HTTPError(503)
        engine.poll()
        self.assertEqual(self.h.store.pending(), [])
        self.crm.error = None
        self.crm.rows.append({**self.crm.rows[0], 'id': 'old', 'created_at': (NOW-timedelta(days=1)).isoformat()})
        engine.poll()
        self.assertEqual(len(self.h.store.pending()), 1)
    def test_bad_master_recipient_does_not_block_client_messages(self):
        self.engine().poll()
        with self.h.store.transaction():
            self.h.store.enqueue({'method': 'sendMessage', 'payload': {'chat_id': 100, 'text': 'Client confirmation'}})
        received = []
        def transport(method, url, payload, **kwargs):
            if payload['chat_id'] == 200:
                raise HTTPError(400)
            received.append(payload)
            return {'ok': True, 'result': {}}
        Telegram('test', transport).drain(self.h.store)
        self.assertEqual(len(received), 1)
        self.assertEqual(self.h.store.pending(), [])
    def test_myid_does_not_change_customer_form(self):
        self.h.form()
        before = self.h.state()
        self.h.text('/myid')
        self.assertEqual(self.h.state(), before)
        self.assertIn('MASTER_TELEGRAM_CHAT_ID=100', self.h.store.pending()[-1][1]['payload']['text'])
