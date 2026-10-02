import logging
from .http import json_request, HTTPError


class Telegram:
    def __init__(self, token, transport=json_request):
        self.url = 'https://api.telegram.org/bot' + token + '/'
        self.transport = transport

    def call(self, method, payload=None):
        data = self.transport('POST', self.url + method, payload or {}, timeout=35)
        if not data.get('ok'):
            raise HTTPError(data.get('error_code', 502), data)
        return data['result']

    def updates(self, offset):
        return self.call('getUpdates', {'offset': offset, 'timeout': 25, 'limit': 50,
                                      'allowed_updates': ['message', 'callback_query']})

    def drain(self, store):
        for row_id, item in store.pending():
            try:
                self.call(item['method'], item['payload'])
            except HTTPError as exc:
                # A blocked recipient or expired callback must not stop other clients.
                if item.get('kind') == 'master_notice' and exc.status in (400, 403):
                    logging.warning('Master notification could not be delivered (status=%s); check chat ID, /start and website URL', exc.status)
                    store.sent(row_id)
                    continue
                if exc.status == 403 or (exc.status == 400 and item['method'] == 'answerCallbackQuery'):
                    store.sent(row_id)
                    continue
                raise
            store.sent(row_id)
